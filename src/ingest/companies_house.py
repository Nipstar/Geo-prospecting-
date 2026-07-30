"""Companies House enrichment.

Matches companies, stores registration data, pulls active directors as postal
addressees, and flags dissolved companies to skip. Respects the 600-requests /
5-minute limit with a simple sliding-window rate limiter.
"""
from __future__ import annotations

import re
import time
from collections import deque
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

import requests

from .. import config, db
from . import util

API_BASE = "https://api.company-information.service.gov.uk"

_POSTCODE_RE = re.compile(r"([A-Z]{1,2}\d[A-Z\d]?)\s*\d[A-Z]{2}", re.I)


def token_sort_ratio(a: str, b: str) -> float:
    """Order-insensitive fuzzy match: sorts each name's tokens before
    comparing, so 'Fry Clifford & Co' and 'Clifford Fry and Co Ltd' score
    high despite word order / legal-suffix differences. Ported from
    geo-slab's companies_house.py (2026-07-29)."""
    norm = lambda s: " ".join(sorted(util.normalise_name(s).split()))
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def postcode_district(address: str) -> str:
    """Outward code only ('SO21' from 'SO21 2AB') — a coarse but useful
    signal that two addresses are in the same area even when the full
    postcode differs (registered office vs trading address)."""
    m = _POSTCODE_RE.search((address or "").upper())
    return m.group(1) if m else ""


def match_confidence(trading_name: str, trading_town: str, candidate: dict[str, Any]) -> float:
    """0-1 confidence a CH search result is the right company. Name
    token-sort ratio is the primary signal; a postcode/locality-district
    match nudges borderline cases up. Ported from geo-slab's
    companies_house.py match_confidence() (2026-07-29)."""
    name_ratio = token_sort_ratio(trading_name, candidate.get("title", ""))
    addr = candidate.get("address_snippet") or ""
    score = name_ratio
    if trading_town and trading_town.lower() in addr.lower():
        score = min(1.0, score + 0.15)
    elif postcode_district(trading_town) and postcode_district(trading_town) == postcode_district(addr):
        score = min(1.0, score + 0.1)
    return round(score, 3)


class RateLimiter:
    """Sliding window: at most `limit` calls per `window` seconds."""

    def __init__(self, limit: int, window: int) -> None:
        self.limit = limit
        self.window = window
        self.calls: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self.calls and now - self.calls[0] > self.window:
            self.calls.popleft()
        if len(self.calls) >= self.limit:
            sleep_for = self.window - (now - self.calls[0]) + 0.1
            time.sleep(max(sleep_for, 0))
        self.calls.append(time.monotonic())


_limiter = RateLimiter(*config.COMPANIES_HOUSE_RATE)


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not config.COMPANIES_HOUSE_API_KEY:
        raise RuntimeError("COMPANIES_HOUSE_API_KEY is not set.")
    _limiter.wait()
    resp = requests.get(
        f"{API_BASE}{path}",
        params=params,
        auth=(config.COMPANIES_HOUSE_API_KEY, ""),
        timeout=20,
    )
    if resp.status_code == 429:
        time.sleep(5)
        return _get(path, params)
    resp.raise_for_status()
    return resp.json()


def _months_since(iso_date: str) -> int | None:
    try:
        d = datetime.fromisoformat(iso_date).date()
    except (ValueError, TypeError):
        return None
    today = date.today()
    return (today.year - d.year) * 12 + (today.month - d.month)


def _map_company_type(ch_type: str | None) -> str:
    ch_type = (ch_type or "").lower()
    if "llp" in ch_type:
        return "llp"
    if "ltd" in ch_type or "private-limited" in ch_type or "plc" in ch_type:
        return "ltd"
    return "unknown"


def _format_address(addr: dict[str, Any]) -> str:
    parts = [
        addr.get("premises"),
        addr.get("address_line_1"),
        addr.get("address_line_2"),
        addr.get("locality"),
        addr.get("region"),
        addr.get("postal_code"),
    ]
    return ", ".join(p for p in parts if p)


def best_candidate(name: str, town: str, items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    """Pick the highest-confidence CH search result. Ported from geo-slab's
    companies_house.py (2026-07-29) — replaces exact-normalised-name-only
    matching, which missed near-exact variants (punctuation, legal-suffix
    differences, word order) and silently mislabelled them as unmatched sole
    traders instead of flagging for review."""
    best, best_score = None, 0.0
    for item in items:
        score = match_confidence(name, town, item)
        if score > best_score:
            best, best_score = item, score
    return best, best_score


def match_company(conn, company: Any) -> dict[str, Any] | None:
    """Search CH for a company and store registration data. Returns a summary.

    Confidence bands (ported from geo-slab, 2026-07-29):
      >= 0.8  auto-accept — fetch profile + directors as before.
      0.5-0.8 review band — store the candidate + confidence, flag for human
              review (`ch_review_flag`), but do NOT auto-fetch directors: a
              wrong-director letter is worse than a slower enrichment. List
              via `cli ingest ch --review`.
      < 0.5   treat as unmatched sole trader, same as the old behaviour.
    """
    name = company["name"]
    town = company["town"] or ""
    data = _get("/search/companies", {"q": name, "items_per_page": 10})
    best, confidence = best_candidate(name, town, data.get("items", []))

    if best is None or confidence < 0.5:
        # No confident match: treat as a sole trader working assumption.
        update = {
            "company_type": "sole_trader",
            "ch_status": "unmatched",
            "ch_review_flag": 1,
            "ch_match_confidence": confidence,
        }
        db.update_company(conn, company["id"], **update)
        return {"matched": False, "company_type": "sole_trader", "flagged": True,
                "confidence": confidence}

    if confidence < 0.8:
        # Review band: record the candidate but don't commit to it yet.
        db.update_company(conn, company["id"], **{
            "ch_status": "review",
            "ch_review_flag": 1,
            "ch_match_confidence": confidence,
        })
        return {"matched": False, "review": True, "confidence": confidence,
                "candidate_name": best.get("title"),
                "candidate_no": best.get("company_number")}

    company_no = best.get("company_number")
    profile = _get(f"/company/{company_no}")
    ch_status = profile.get("company_status", "")
    incorporation = profile.get("date_of_creation")
    sic = ", ".join(profile.get("sic_codes", []) or [])
    reg_addr = _format_address(profile.get("registered_office_address", {}) or {})
    company_type = _map_company_type(profile.get("type"))

    update = {
        "companies_house_no": company_no,
        "company_type": company_type,
        "incorporation_date": incorporation,
        "sic_codes": sic or None,
        "registered_address": reg_addr or None,
        "ch_status": ch_status,
        "ch_review_flag": 0,
        "ch_match_confidence": confidence,
    }
    db.update_company(conn, company["id"], **update)

    if ch_status != "active":
        # Skip and mark: move dissolved etc. out of the pipeline.
        try:
            db.advance_status(conn, company["id"], "closed_lost", event="ch_inactive")
        except db.InvalidTransition:
            db.update_company(conn, company["id"], status="closed_lost")

    months = _months_since(incorporation) if incorporation else None
    trigger_new = months is not None and months <= 18
    return {
        "matched": True,
        "company_no": company_no,
        "company_type": company_type,
        "ch_status": ch_status,
        "new_business": trigger_new,
        "confidence": confidence,
    }


def get_directors(conn, company: Any) -> int:
    """Create people rows from active, non-corporate officers. Returns count."""
    company_no = company["companies_house_no"]
    if not company_no:
        return 0
    data = _get(f"/company/{company_no}/officers", {"register_type": "directors"})
    created = 0
    officers = data.get("items", []) or []
    for officer in officers:
        if officer.get("resigned_on"):
            continue
        if officer.get("officer_role", "").startswith("corporate"):
            continue
        name = officer.get("name", "").strip()
        if not name:
            continue
        # CH lists surname-first as "SMITH, John"; render human-friendly.
        if ", " in name:
            surname, forename = name.split(", ", 1)
            name = f"{forename.title()} {surname.title()}"
        if db.find_person(conn, company["id"], name, None):
            continue
        db.insert_person(
            conn, company["id"],
            name=name,
            role=officer.get("officer_role", "director"),
            person_source="companies_house_officer",
            connection_status="n/a",
        )
        created += 1
    return created


def review_queue(conn=None) -> list[dict[str, Any]]:
    """Companies whose best CH match landed in the 0.5-0.8 confidence band —
    needs a human glance before trusting the director name on a letter.
    Ported from geo-slab's companies_house.py `--review` (2026-07-29)."""
    own = conn is None
    conn = conn or db.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, town, ch_match_confidence FROM companies "
            "WHERE ch_status = 'review' AND ch_review_flag = 1 "
            "ORDER BY ch_match_confidence DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def run_ch(status: str = "new", limit: int = 50, dry_run: bool = False) -> dict[str, int]:
    conn = db.get_connection()
    matched = flagged = directors = skipped = new_biz = review = 0
    try:
        rows = db.get_companies_by_status(conn, status, limit)
        for company in rows:
            if company["companies_house_no"]:
                continue  # already enriched
            try:
                if dry_run:
                    print(f"  would match CH: {company['name']} ({company['town']})")
                    matched += 1
                    continue
                result = match_company(conn, company)
                if result is None:
                    continue
                if result.get("review"):
                    review += 1
                    print(f"  ~ {company['name']}: possible match '{result['candidate_name']}' "
                          f"({result['confidence']}) — needs review, no director fetched")
                    continue
                if not result["matched"]:
                    flagged += 1
                    print(f"  ? {company['name']}: no match, flagged sole_trader")
                    continue
                matched += 1
                if result.get("new_business"):
                    new_biz += 1
                if result["ch_status"] != "active":
                    skipped += 1
                    print(f"  x {company['name']}: {result['ch_status']}, skipped")
                    continue
                fresh = db.get_company(conn, company["id"])
                d = get_directors(conn, fresh)
                directors += d
                tag = " [new business]" if result.get("new_business") else ""
                print(f"  + {company['name']}: {result['company_type']}, {d} director(s){tag}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {company['name']}: {exc}")
    finally:
        conn.close()
    return {
        "matched": matched, "flagged_sole_trader": flagged,
        "directors_added": directors, "skipped_inactive": skipped,
        "new_businesses": new_biz, "review_band": review,
    }
