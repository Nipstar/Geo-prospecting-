"""Prospeo owner lookup (US) — drop-in alternative to Apollo.

Same surface as clients/apollo.py so the enricher (ingest/apollo.py) can use
either behind ENRICH_PROVIDER:

  search_owner(domain)                      -> owner-titled people at a domain
  match_person(first, last, domain, person) -> reveal a verified work email

Both return dicts shaped like Apollo's `person` (name / title / linkedin_url /
email / email_status / phone_numbers / organization) so the enricher's ranking
and email/phone extraction work unchanged. Extra internal keys (_person_id) are
carried on the search result so match_person can reveal the cheapest/most
accurate way.

Prospeo REST API — https://prospeo.io/api-docs  (auth header `X-KEY`)

  POST /search-person   — flat body:
    {"page":1,"filters":{"company":{"websites":{"include":[domain]}},
                          "person_seniority":{"include":["Founder/Owner"]}}}
    -> {"results":[{"person":{...},"company":{...}}], "pagination":{...}}

  POST /enrich-person   — body wrapped in "data":
    {"data":{ first_name+last_name+company_website | linkedin_url | email | person_id }}
    -> {"person":{... "email":{"email":..,"status":"VERIFIED","revealed":true} ...}}

Credits: search bills per revealed result; enrich = 1 credit per email found
(10 per mobile). Search returns a MASKED email until revealed, so we treat
masked emails as "no email" and let match_person do the paid reveal on the one
owner we actually want.
"""
from __future__ import annotations

from typing import Any

import requests

from .. import config

BASE = "https://api.prospeo.io"

# Prospeo person_seniority enum values that indicate the principal/owner.
OWNER_SENIORITY = ["Founder/Owner"]

# Email verification states with no usable address.
_BAD_EMAIL_STATUS = {"UNAVAILABLE", "INVALID", "NOT_FOUND"}


def _headers() -> dict[str, str]:
    if not config.PROSPEO_API_KEY:
        raise RuntimeError("PROSPEO_API_KEY is not set.")
    return {"X-KEY": config.PROSPEO_API_KEY, "Content-Type": "application/json"}


def _pick_email(person: dict) -> tuple[str | None, str | None]:
    """Prospeo `email` is {email, status, revealed}. Masked/unrevealed -> none."""
    e = person.get("email")
    if not isinstance(e, dict):
        return None, None
    addr = e.get("email")
    status = str(e.get("status") or "").upper()
    # Masked (e.g. "g****@x.com") or not yet revealed -> not usable.
    if not addr or "*" in addr or e.get("revealed") is False:
        return None, ("unavailable" if status in _BAD_EMAIL_STATUS else None)
    return addr, ("unavailable" if status in _BAD_EMAIL_STATUS else status.lower())


def _pick_mobile(person: dict) -> str | None:
    m = person.get("mobile")
    if isinstance(m, dict):
        num = m.get("mobile")
        return num if num and "*" not in str(num) else None
    return m or None


def _person_from(person: dict, company: dict | None = None) -> dict[str, Any]:
    """Normalise a Prospeo person object into Apollo's `person` shape."""
    company = company or {}
    first = person.get("first_name") or ""
    last = person.get("last_name") or ""
    full = person.get("full_name") or f"{first} {last}".strip()
    email, status = _pick_email(person)
    mobile = _pick_mobile(person)
    return {
        "first_name": first,
        "last_name": last,
        "name": full,
        "title": person.get("current_job_title") or "",
        "linkedin_url": person.get("linkedin_url"),
        "email": email,
        "email_status": status,
        "phone_numbers": [{"raw_number": mobile}] if mobile else [],
        "organization": {"phone": company.get("phone")},
        "_person_id": person.get("person_id"),  # for cheap re-enrichment
    }


# ── Public surface (mirrors clients/apollo.py) ─────────────────────────────

def search_owner(domain: str, per_page: int = 5) -> list[dict[str, Any]]:
    """Search Person scoped to one company website + owner seniority."""
    payload = {
        "page": 1,
        "filters": {
            "company": {"websites": {"include": [domain]}},
            "person_seniority": {"include": OWNER_SENIORITY},
        },
    }
    resp = requests.post(f"{BASE}/search-person", json=payload,
                         headers=_headers(), timeout=40)
    if resp.status_code == 400 and resp.json().get("error_code") == "NO_RESULTS":
        return []  # domain not in Prospeo's DB — expected for small local firms
    resp.raise_for_status()
    results = resp.json().get("results", []) or []
    out = []
    for item in results:
        p = item.get("person") if isinstance(item, dict) else None
        if p:
            out.append(_person_from(p, item.get("company")))
    return out[:per_page]


def match_person(first_name: str, last_name: str, domain: str,
                 reveal_email: bool = True,
                 person: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Enrich Person — reveal a verified work email. Uses the cheapest key
    available: person_id (from search) > linkedin_url > name+company_website."""
    data: dict[str, Any]
    if person and person.get("_person_id"):
        data = {"person_id": person["_person_id"]}
    elif person and person.get("linkedin_url"):
        data = {"linkedin_url": person["linkedin_url"]}
    else:
        data = {"first_name": first_name, "last_name": last_name,
                "company_website": domain}
    resp = requests.post(f"{BASE}/enrich-person", json={"data": data},
                         headers=_headers(), timeout=40)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        return None
    p = body.get("person")
    return _person_from(p) if isinstance(p, dict) else None
