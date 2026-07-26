"""Prospeo owner lookup (US) — drop-in alternative to Apollo.

Exposes the SAME surface as clients/apollo.py so the enricher (ingest/apollo.py)
can use either behind ENRICH_PROVIDER:

  search_owner(domain)              -> owner-titled people at a domain
  match_person(first, last, domain) -> reveal a verified work email

Both return dicts shaped like Apollo's `person` (name / title / linkedin_url /
email / email_status / phone_numbers / organization) so the enricher's ranking
and email/phone extraction work unchanged.

Prospeo REST API — https://prospeo.io/api-docs
  POST https://api.prospeo.io/search-person   (filters: company_domain, job_title)
  POST https://api.prospeo.io/enrich-person   (first_name+last_name+company_domain, or linkedin_url)
Auth: header `X-KEY: <api_key>`. Simple JSON in/out.

Response nesting differs slightly per plan/version; the `_person_from` /
`_pick_email` helpers read defensively across the common shapes. If a live call
returns 4xx, only the request bodies in search_owner/match_person need adjusting.
"""
from __future__ import annotations

from typing import Any

import requests

from .. import config

BASE = "https://api.prospeo.io"

# Same owner-first title vocabulary as Apollo, so ranking is identical.
OWNER_TITLES = [
    "owner", "broker owner", "managing broker", "broker", "founder",
    "co-founder", "president", "principal", "ceo", "managing member",
    "managing director", "partner",
]

# Prospeo email verification states we treat as unusable → mapped to the
# "unavailable" sentinel the enricher already rejects.
_BAD_EMAIL_STATUS = {"INVALID", "DISPOSABLE", "UNKNOWN"}


def _headers() -> dict[str, str]:
    if not config.PROSPEO_API_KEY:
        raise RuntimeError("PROSPEO_API_KEY is not set.")
    return {
        "X-KEY": config.PROSPEO_API_KEY,
        "Content-Type": "application/json",
    }


def _dig(obj: Any, *names: str) -> Any:
    """First non-empty value found for any of `names`, searched one level deep."""
    if not isinstance(obj, dict):
        return None
    for n in names:
        if obj.get(n) not in (None, "", [], {}):
            return obj[n]
    return None


def _pick_email(obj: dict) -> tuple[str | None, str | None]:
    """Return (email, status). Handles email as a string or a nested object."""
    raw = _dig(obj, "email", "professional_email", "work_email")
    if isinstance(raw, dict):
        email = _dig(raw, "email", "value", "address")
        status = (_dig(raw, "status", "email_status", "verification", "result") or "")
    else:
        email = raw
        status = (_dig(obj, "email_status", "email_verification") or "")
    if not email:
        return None, None
    status = str(status).upper()
    # Fold clearly-bad states into the sentinel the enricher already drops.
    return str(email), ("unavailable" if status in _BAD_EMAIL_STATUS else status.lower())


def _pick_mobile(obj: dict) -> str | None:
    raw = _dig(obj, "mobile", "phone", "mobile_phone", "phone_number")
    if isinstance(raw, dict):
        return _dig(raw, "number", "raw_number", "value", "international")
    return raw


def _person_from(obj: dict) -> dict[str, Any]:
    """Normalise a Prospeo person/profile object into Apollo's `person` shape."""
    first = _dig(obj, "first_name", "firstName") or ""
    last = _dig(obj, "last_name", "lastName") or ""
    full = _dig(obj, "full_name", "name") or f"{first} {last}".strip()
    email, status = _pick_email(obj)
    mobile = _pick_mobile(obj)
    company = obj.get("company") if isinstance(obj.get("company"), dict) else {}
    person: dict[str, Any] = {
        "first_name": first,
        "last_name": last,
        "name": full,
        "title": _dig(obj, "job_title", "title", "position") or "",
        "linkedin_url": _dig(obj, "linkedin_url", "linkedin", "linkedinUrl"),
        "email": email,
        "email_status": status,
        "phone_numbers": [{"raw_number": mobile}] if mobile else [],
        "organization": {"phone": _dig(company, "phone")},
    }
    return person


def _profiles(payload: dict) -> list[dict]:
    """Pull the list of people from a search response across shapes."""
    resp = payload.get("response", payload)
    if isinstance(resp, list):
        return resp
    for key in ("profiles", "results", "people", "data", "items"):
        val = resp.get(key) if isinstance(resp, dict) else None
        if isinstance(val, list):
            return val
    return []


# ── Public surface (mirrors clients/apollo.py) ─────────────────────────────

def search_owner(domain: str, per_page: int = 5) -> list[dict[str, Any]]:
    """Search Person scoped to one company domain + owner-ish titles."""
    payload = {
        "filters": {
            "company_domain": [domain],
            "job_title": OWNER_TITLES,
        },
        "limit": per_page,
        "page": 1,
    }
    resp = requests.post(f"{BASE}/search-person", json=payload,
                         headers=_headers(), timeout=30)
    resp.raise_for_status()
    return [_person_from(p) for p in _profiles(resp.json())]


def match_person(first_name: str, last_name: str, domain: str,
                 reveal_email: bool = True) -> dict[str, Any] | None:
    """Enrich Person — reveal a verified work email for one named person."""
    payload = {
        "first_name": first_name,
        "last_name": last_name,
        "company_domain": domain,
    }
    resp = requests.post(f"{BASE}/enrich-person", json=payload,
                         headers=_headers(), timeout=30)
    resp.raise_for_status()
    body = resp.json()
    person = body.get("response", body)
    if not isinstance(person, dict) or not person:
        return None
    return _person_from(person)
