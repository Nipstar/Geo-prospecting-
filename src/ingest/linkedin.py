"""LinkedIn owner enrichment (name + profile URL) via harvestapi Apify actors.

For companies with no named person yet: find the LinkedIn company page (fuzzy
name match), pull owner/principal-titled employees, attach the best one. This
also activates the LinkedIn DM channel (route sends people-with-a-URL there).

Gives NAME + profile URL only, not a postal address. Confidence-gated so a weak
match falls back to "The Owner" rather than mis-naming.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from .. import db
from ..clients import linkedin

_NOISE = re.compile(
    r"\b(the|llc|inc|corp|corporation|co|company|pa|pllc|ltd|llp|group|associates|"
    r"realty|realtor|realtors|real estate|brokerage|brokers?|properties|solicitors?|law|&)\b",
    re.I,
)
_OWNER_RANK = ["OWNER", "BROKER", "FOUNDER", "PRINCIPAL", "PRESIDENT", "CEO",
               "MANAGING", "PARTNER", "DIRECTOR"]


def _norm(s: str) -> str:
    s = _NOISE.sub(" ", s or "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def _first(d: dict, *keys) -> str:
    for k in keys:
        v = d.get(k)
        if v:
            return v if isinstance(v, str) else str(v)
    return ""


def _pick_owner(people: list[dict]) -> tuple[str, str, str] | None:
    """(name, title, profile_url) for the most owner-like employee."""
    cand = []
    for p in people:
        name = _first(p, "fullName", "name")
        if not name:
            fn, ln = _first(p, "firstName"), _first(p, "lastName")
            name = f"{fn} {ln}".strip()
        if not name:
            continue
        # headline carries the role; keep the bit before "at"/newline for a tidy title
        headline = _first(p, "position", "title", "jobTitle", "headline", "occupation")
        title = re.split(r"\s+at\s+|\n|,", headline)[0].strip() if headline else ""
        url = _first(p, "linkedinUrl", "profileUrl", "url", "publicUrl")
        cand.append((name, title, url, headline))

    def rank(c):
        t = (c[3] or "").upper()  # rank on full headline
        for i, key in enumerate(_OWNER_RANK):
            if key in t:
                return i
        return 99
    cand.sort(key=rank)
    if cand:
        n, t, u, _h = cand[0]
        return n, t, u
    return None


def run_linkedin_enrich(limit: int = 25, town: str | None = None, dry_run: bool = False) -> dict[str, int]:
    conn = db.get_connection()
    where = """SELECT c.* FROM companies c
               WHERE NOT EXISTS (SELECT 1 FROM people p WHERE p.company_id = c.id AND p.name IS NOT NULL)"""
    params: list = []
    if town:
        where += " AND c.town = ?"
        params.append(town)
    where += " ORDER BY c.id LIMIT ?"
    params.append(limit)
    rows = conn.execute(where, params).fetchall()

    matched = added = no_company = no_owner = 0
    try:
        for co in rows:
            # NB: harvestapi's location filter expects a LinkedIn geo id, not a
            # plain state/county name (that returns 0), so we match on name only.
            try:
                companies = linkedin.search_company(co["name"], max_items=3)
            except Exception as exc:  # noqa: BLE001
                print(f"  ? {co['name']}: search error ({exc})")
                no_company += 1
                continue
            target = _norm(co["name"])
            best, best_ratio = None, 0.0
            for e in companies:
                nm = _first(e, "name", "title", "companyName")
                r = difflib.SequenceMatcher(None, target, _norm(nm)).ratio()
                if r > best_ratio:
                    best_ratio, best = r, e
            if not best or best_ratio < 0.62:
                no_company += 1
                print(f"  ? {co['name']}: no confident LinkedIn company match "
                      f"(best {best_ratio:.2f})")
                continue
            url = _first(best, "linkedinUrl", "url", "link", "companyUrl", "profileUrl")
            if not url:
                no_company += 1
                continue
            try:
                owners = linkedin.company_owners(url, max_items=6)
            except Exception as exc:  # noqa: BLE001
                print(f"  ? {co['name']}: employees error ({exc})")
                no_owner += 1
                continue
            picked = _pick_owner(owners)
            if not picked:
                no_owner += 1
                print(f"  ? {co['name']}: company found, no owner-titled employee")
                continue
            name, title, purl = picked
            matched += 1
            print(f"  + {co['name']} -> {name} ({title or 'owner'}) [{best_ratio:.2f}] {purl}")
            if dry_run:
                continue
            db.insert_person(conn, company_id=co["id"], name=name, role=title or "Owner",
                             linkedin_url=purl, person_source="linkedin")
            added += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return {"processed": len(rows), "matched": matched, "people_added": added,
            "no_company": no_company, "no_owner": no_owner}
