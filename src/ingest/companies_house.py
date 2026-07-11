"""Companies House enrichment.

Matches companies, stores registration data, pulls active directors as postal
addressees, and flags dissolved companies to skip. Respects the 600-requests /
5-minute limit with a simple sliding-window rate limiter.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import date, datetime
from typing import Any

import requests

from .. import config, db
from . import util

API_BASE = "https://api.company-information.service.gov.uk"


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


def match_company(conn, company: Any) -> dict[str, Any] | None:
    """Search CH for a company and store registration data. Returns a summary."""
    name = company["name"]
    town = company["town"] or ""
    data = _get("/search/companies", {"q": name, "items_per_page": 10})
    target = util.normalise_name(name)
    best = None
    for item in data.get("items", []):
        if util.normalise_name(item.get("title", "")) == target:
            addr = (item.get("address_snippet") or "").lower()
            # Prefer a locality match when available.
            if not town or town.lower() in addr:
                best = item
                break
            best = best or item
    if best is None:
        # No confident match: treat as a sole trader working assumption.
        update = {
            "company_type": "sole_trader",
            "ch_status": "unmatched",
            "ch_review_flag": 1,
        }
        db.update_company(conn, company["id"], **update)
        return {"matched": False, "company_type": "sole_trader", "flagged": True}

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


def run_ch(status: str = "new", limit: int = 50, dry_run: bool = False) -> dict[str, int]:
    conn = db.get_connection()
    matched = flagged = directors = skipped = new_biz = 0
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
        "new_businesses": new_biz,
    }
