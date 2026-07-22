"""Apollo.io owner lookup (US).

Two calls:
  search_owner(domain)  -> owner-titled people at that company (name, title,
                           linkedin, maybe locked email)
  match_person(...)     -> reveal a verified work email for one person

Returns name + title + linkedin (+ optional email/phone). US-market only; the
ingest layer gates on that. No Apify, no scraping — official Apollo REST API.
"""
from __future__ import annotations

from typing import Any

import requests

from .. import config

BASE = "https://api.apollo.io/api/v1"

# Titles that identify the owner/principal of a small brokerage, best-first.
OWNER_TITLES = [
    "owner", "broker owner", "managing broker", "broker", "founder",
    "co-founder", "president", "principal", "ceo", "managing member",
    "managing director", "partner",
]


def _headers() -> dict[str, str]:
    if not config.APOLLO_API_KEY:
        raise RuntimeError("APOLLO_API_KEY is not set.")
    return {
        "X-Api-Key": config.APOLLO_API_KEY,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }


def search_owner(domain: str, per_page: int = 5) -> list[dict[str, Any]]:
    """People Search scoped to one company domain + owner-ish titles."""
    payload = {
        "q_organization_domains_list": [domain],
        "person_titles": OWNER_TITLES,
        "page": 1,
        "per_page": per_page,
    }
    resp = requests.post(f"{BASE}/mixed_people/search", json=payload,
                         headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("people", []) or []


def match_person(first_name: str, last_name: str, domain: str,
                 reveal_email: bool = True) -> dict[str, Any] | None:
    """People Enrichment — reveal a verified work email for one person."""
    payload = {
        "first_name": first_name,
        "last_name": last_name,
        "domain": domain,
        "reveal_personal_emails": reveal_email,
    }
    resp = requests.post(f"{BASE}/people/match", json=payload,
                         headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("person")
