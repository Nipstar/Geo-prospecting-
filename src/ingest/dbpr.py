"""Florida DBPR real-estate licensee enrichment.

The most reliable owner-name path for FL real-estate agents: they are licensed
individuals, so their legal name is public in the DBPR/MyFloridaLicense registry.

Strategy per company (FL, real-estate, no dbpr-sourced person yet):
  1. Derive a candidate name — from an existing web-scraped person, else from
     the business name itself ("Lou Haubner Realty" -> Lou Haubner).
  2. If we have a candidate, search DBPR by that name and VERIFY: keep the
     active real-estate licensee whose surname matches. Upgrade to the legal
     name (e.g. "Lou Haubner" -> "Louis R Jr Haubner") + attach license status.
  3. If no candidate name, search DBPR by organisation name; accept only a
     single unambiguous active real-estate match.

Free, zero API cost. Falls back cleanly to "The Owner" when nothing matches.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..clients import dbpr
from ..post.letter import _extract_name_from_company


def _candidate_name(conn, company) -> str | None:
    """Best existing name signal for a company: a web-scraped person, else the
    person embedded in the business name."""
    for p in db.get_people_for_company(conn, company["id"]):
        if p["name"] and (p["person_source"] or "").startswith(("web", "linkedin")):
            return p["name"]
    return _extract_name_from_company(company["name"] or "")


def _surname(name: str) -> str:
    """Surname from either 'LAST, FIRST MID' (DBPR) or 'First Last' (candidate)."""
    n = name or ""
    if "," in n:                       # DBPR format: surname is before the comma
        head = n.split(",", 1)[0].strip()
        toks = [t for t in head.split() if t]
        return toks[-1].lower() if toks else ""
    toks = [t for t in n.split() if t]  # "First Last": surname is the last token
    return toks[-1].lower() if toks else ""


def _first(name: str) -> str:
    """First forename token, lowercased. Handles 'LAST, FIRST' and 'First Last'."""
    n = name or ""
    if "," in n:
        n = n.split(",", 1)[1]
    toks = [t for t in n.split() if t]
    return toks[0].lower() if toks else ""


def _match(records: list[dict], candidate: str | None) -> dict | None:
    """Pick the best active real-estate licensee from DBPR results.

    Match on surname (reliable); disambiguate on forename by prefix so
    nicknames line up ("Matt" -> "Matthew", "Suzi" -> "Suzanne")."""
    re_recs = dbpr.real_estate_only(records)
    if not re_recs:
        return None
    active = [r for r in re_recs if r["is_active"]]
    pool = active or re_recs
    if candidate:
        cand_sur = _surname(candidate)
        same = [r for r in pool if _surname(r["name"]) == cand_sur]
        if not same:
            return None  # no surname match -> don't guess
        cf = _first(candidate)
        if cf:
            # Require forename alignment (prefix, either direction) so nicknames
            # line up ("Matt"~"Matthew") but a different person is never accepted.
            pref = [r for r in same if _first(r["name"]).startswith(cf[:3])
                    or cf.startswith(_first(r["name"])[:3])]
            pref.sort(key=lambda r: (not r["is_active"],))
            return pref[0] if pref else None
        # no forename signal: only a lone surname match is safe
        return same[0] if len(same) == 1 else None
    # no candidate: accept only an unambiguous single active match
    return active[0] if len(active) == 1 else None


def run_dbpr_enrich(state: str = "FL", limit: int = 25,
                    dry_run: bool = False) -> dict[str, int]:
    conn = db.get_connection()
    db.ensure_person_contact(conn)
    rows = conn.execute(
        """SELECT c.* FROM companies c
           WHERE c.county IN ('Florida','FL')
             AND (c.sector LIKE '%real estate%' OR c.primary_service LIKE '%real estate%'
                  OR c.name LIKE '%realt%' OR c.name LIKE '%real estate%')
             AND NOT EXISTS (SELECT 1 FROM people p WHERE p.company_id=c.id
                             AND p.person_source LIKE 'dbpr%')
           ORDER BY c.id DESC LIMIT ?""", (limit,)).fetchall()

    processed = verified = via_org = no_match = errors = 0
    try:
        for co in rows:
            processed += 1
            candidate = _candidate_name(conn, co)
            try:
                if candidate:
                    toks = candidate.replace(",", " ").split()
                    last = toks[-1]
                    # Search by SURNAME ONLY — DBPR forename search is exact and
                    # trips on nicknames (Matt vs Matthew). Disambiguate in _match.
                    rec = _match(dbpr.search_name(last), candidate)
                    kind = "verify"
                else:
                    rec = _match(dbpr.search_org(co["name"]), None)
                    kind = "org"
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ! {co['name']}: {str(exc)[:80]}")
                continue
            if not rec:
                no_match += 1
                print(f"  ? {co['name']}: no DBPR real-estate match"
                      f"{' (cand '+candidate+')' if candidate else ''}")
                continue
            if kind == "verify":
                verified += 1
            else:
                via_org += 1
            tag = f"{rec['name_human']} — {rec['license_type']} [{rec['status']}]"
            print(f"  + {co['name']} -> {tag}  [{kind}]")
            if not dry_run:
                db.insert_person(conn, company_id=co["id"], name=rec["name_human"],
                                 role="Licensed Real Estate Agent",
                                 person_source=f"dbpr:{kind}")
                conn.commit()
    finally:
        conn.close()
    return {"processed": processed, "verified": verified, "via_org": via_org,
            "no_match": no_match, "errors": errors}
