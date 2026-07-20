"""Florida owner/officer enrichment from Sunbiz — the US analogue of the UK
Companies House step. For FL companies with no named person yet, find the best
active Sunbiz entity match and attach an officer name + a better mailing address.

Unmatched companies keep their Google Places address and fall back to
"The Owner" in the letter — so enrichment only ever improves a lead.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from .. import db
from ..clients import sunbiz

# words that add noise to a business-name match
_NOISE = re.compile(
    r"\b(the|llc|l\.l\.c\.?|inc|inc\.|corp|corporation|co|company|pa|p\.a\.?|pllc|pl|ltd|"
    r"group|associates|realty|realtor|realtors|real estate|brokerage|brokers?|"
    r"properties|property|llp|&)\b",
    re.I,
)
_OFFICER_RANK = ["PRES", "OWNER", "MGR", "MANAGING", "MEMBER", "CEO", "PRINCIPAL",
                 "DIRECTOR", "VP", "VICE", "SEC", "TREAS"]


def _norm(s: str) -> str:
    s = _NOISE.sub(" ", s or "")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _humanise_name(name: str) -> str:
    """Sunbiz officer names come as 'LAST, FIRST MIDDLE' in caps -> 'First Middle Last'."""
    name = (name or "").strip()
    if "," in name:
        last, rest = name.split(",", 1)
        name = f"{rest.strip()} {last.strip()}"
    # title-case but keep short suffixes tidy
    return " ".join(w.capitalize() for w in name.split())


def _fmt_addr(a: Any) -> str:
    if isinstance(a, dict):
        parts = [a.get("street"), a.get("city"), a.get("state"), a.get("zip")]
        return ", ".join(p for p in parts if p)
    return (a or "").strip() if isinstance(a, str) else ""


def _best_officer(entity: dict) -> tuple[str | None, str]:
    """(officer_name or None, best_address). Prefers principal-like titles."""
    officers = [o for o in (entity.get("officers") or []) if isinstance(o, dict) and o.get("name")]

    def rank(o: dict) -> int:
        t = (o.get("title") or "").upper()
        for i, key in enumerate(_OFFICER_RANK):
            if key in t:
                return i
        return 99

    officers.sort(key=rank)
    princ = _fmt_addr(entity.get("principalAddress")) or _fmt_addr(entity.get("mailingAddress"))
    if officers:
        o = officers[0]
        return _humanise_name(o.get("name")), (_fmt_addr(o.get("address")) or princ)
    return None, princ


def run_sunbiz_enrich(state: str = "FL", limit: int = 50, dry_run: bool = False) -> dict[str, int]:
    """Enrich FL companies that have no named person yet."""
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT c.* FROM companies c
           WHERE (c.county IN ('FL', 'Florida') OR c.town LIKE '%, FL%')
             AND NOT EXISTS (SELECT 1 FROM people p WHERE p.company_id = c.id)
           ORDER BY c.id LIMIT ?""",
        (limit,),
    ).fetchall()

    matched = officers_added = addr_updated = no_match = 0
    try:
        for co in rows:
            try:
                ents = sunbiz.search_entities(co["name"], max_items=8)
            except Exception as exc:  # noqa: BLE001
                print(f"  ? {co['name']}: sunbiz error ({exc})")
                no_match += 1
                continue
            active = [e for e in ents if (e.get("status") or "").upper() == "ACTIVE"]
            cand = active or ents
            target = _norm(co["name"])
            best, best_ratio = None, 0.0
            for e in cand:
                r = difflib.SequenceMatcher(None, target, _norm(e.get("corporateName", ""))).ratio()
                if r > best_ratio:
                    best_ratio, best = r, e
            # 0.70 threshold: better to fall back to "The Owner" than mis-address.
            if not best or best_ratio < 0.70:
                no_match += 1
                print(f"  ? {co['name']}: no confident Sunbiz match "
                      f"(best {best.get('corporateName') if best else '-'} {best_ratio:.2f})")
                continue

            name, addr = _best_officer(best)
            matched += 1
            tag = "ACTIVE" if best in active else best.get("status", "?")
            print(f"  + {co['name']} -> {best.get('corporateName')} [{tag} {best_ratio:.2f}] "
                  f"owner={name or '(none, use address)'}")
            if dry_run:
                continue
            if name:
                db.insert_person(conn, company_id=co["id"], name=name,
                                 role="Sunbiz officer", person_source="sunbiz_officer")
                officers_added += 1
            if addr:
                db.update_company(conn, co["id"], registered_address=addr)
                addr_updated += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return {"processed": len(rows), "matched": matched, "officers_added": officers_added,
            "addresses_updated": addr_updated, "no_match": no_match}
