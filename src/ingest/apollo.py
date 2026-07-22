"""Apollo owner-name enrichment (US market only).

For US companies with a website but no named person: find the owner via Apollo
(name + title + LinkedIn), optionally reveal a verified work email for the
US cold-email channel, and attach them. Replaces the Sunbiz/Apify path — better
hit-rate, cheaper, no Apify credit.

US-scoped by design: the caller passes a US county/town, and email is only ever
written for these rows. UK leads are never touched here.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .. import db
from ..clients import apollo

_TITLE_RANK = [
    "OWNER", "BROKER OWNER", "MANAGING BROKER", "BROKER", "FOUNDER",
    "CO-FOUNDER", "PRESIDENT", "PRINCIPAL", "CEO", "MANAGING", "PARTNER",
]
# US state name/abbr -> the county value stored at ingest (Places writes the
# full state name into `county`).
_STATE_COUNTY = {
    "FL": "Florida", "FLORIDA": "Florida",
    "TX": "Texas", "TEXAS": "Texas",
}


def _domain(website: str | None) -> str | None:
    if not website:
        return None
    host = urlparse(website if "//" in website else f"http://{website}").netloc
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _valid_email(person: dict) -> str | None:
    email = (person.get("email") or "").strip().lower()
    if not email or "not_unlocked" in email or "domain.com" == email.split("@")[-1]:
        return None
    if person.get("email_status") in ("unavailable",):
        return None
    return email


def _phone(person: dict) -> str | None:
    org = person.get("organization") or {}
    if org.get("phone"):
        return str(org["phone"])
    for p in person.get("phone_numbers") or []:
        if isinstance(p, dict) and p.get("raw_number"):
            return str(p["raw_number"])
    return None


def _rank(person: dict) -> int:
    t = (person.get("title") or "").upper()
    for i, key in enumerate(_TITLE_RANK):
        if key in t:
            return i
    return 99


def run_apollo_enrich(limit: int = 25, state: str | None = None,
                      town: str | None = None, reveal_email: bool = True,
                      dry_run: bool = False) -> dict[str, int]:
    conn = db.get_connection()
    db.ensure_person_contact(conn)

    where = ["c.website IS NOT NULL AND c.website <> ''",
             "NOT EXISTS (SELECT 1 FROM people p WHERE p.company_id=c.id AND p.name IS NOT NULL)"]
    params: list = []
    if state:
        county = _STATE_COUNTY.get(state.strip().upper(), state)
        where.append("c.county = ?")
        params.append(county)
    if town:
        where.append("c.town = ?")
        params.append(town)
    sql = f"SELECT c.* FROM companies c WHERE {' AND '.join(where)} ORDER BY c.id LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    matched = added = emails = no_domain = no_owner = errors = 0
    try:
        for co in rows:
            domain = _domain(co["website"])
            if not domain:
                no_domain += 1
                continue
            try:
                people = apollo.search_owner(domain)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ! {co['name']}: search error ({exc})")
                continue
            if not people:
                no_owner += 1
                print(f"  ? {co['name']} ({domain}): no owner-titled person")
                continue
            best = sorted(people, key=_rank)[0]
            name = (best.get("name")
                    or f"{best.get('first_name','')} {best.get('last_name','')}".strip())
            if not name:
                no_owner += 1
                continue
            title = best.get("title") or "Owner"
            linkedin = best.get("linkedin_url") or None
            email = _valid_email(best)
            phone = _phone(best)

            # Reveal a verified email if the search didn't include one.
            if reveal_email and not email and best.get("first_name") and best.get("last_name"):
                try:
                    m = apollo.match_person(best["first_name"], best["last_name"], domain)
                    if m:
                        email = _valid_email(m) or email
                        phone = phone or _phone(m)
                except Exception:  # noqa: BLE001
                    pass

            matched += 1
            if email:
                emails += 1
            tag = f"{name} ({title})" + (f" <{email}>" if email else "")
            print(f"  + {co['name']} -> {tag}")
            if dry_run:
                continue
            db.insert_person(conn, company_id=co["id"], name=name, role=title,
                             linkedin_url=linkedin, email=email, phone=phone,
                             person_source="apollo")
            added += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return {"processed": len(rows), "matched": matched, "people_added": added,
            "emails_found": emails, "no_domain": no_domain, "no_owner": no_owner,
            "errors": errors}
