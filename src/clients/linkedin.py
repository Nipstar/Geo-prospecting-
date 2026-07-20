"""LinkedIn owner lookup via harvestapi Apify actors.

Two-step: find the company page by name, then pull its owner/principal-titled
employees. Best for businesses WITH a LinkedIn company page (misses solo
operators, which is expected). Returns name + profile URL only — no address.
"""
from __future__ import annotations

from typing import Any

from .. import config

SEARCH_ACTOR = "harvestapi/linkedin-company-search"
EMPLOYEE_ACTOR = "harvestapi/linkedin-company-employees"
OWNER_TITLES = ["owner", "broker", "managing broker", "broker owner", "founder",
                "co-founder", "president", "principal", "ceo", "managing member",
                "managing director", "partner", "director"]


def _client():
    from apify_client import ApifyClient
    if not config.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is not set.")
    return ApifyClient(config.APIFY_TOKEN)


def search_company(name: str, location: str | None = None, max_items: int = 3) -> list[dict[str, Any]]:
    client = _client()
    run_input: dict[str, Any] = {"searchQuery": name, "maxItems": max_items, "scraperMode": "short"}
    if location:
        run_input["locations"] = [location]
    run = client.actor(SEARCH_ACTOR).call(run_input=run_input)
    return list(client.dataset(run.default_dataset_id).iterate_items())


def company_owners(company_url: str, max_items: int = 6) -> list[dict[str, Any]]:
    client = _client()
    run = client.actor(EMPLOYEE_ACTOR).call(run_input={
        "companies": [company_url],
        "jobTitles": OWNER_TITLES,
        "maxItems": max_items,
        "profileScraperMode": "Full ($8 per 1k)",
    })
    return list(client.dataset(run.default_dataset_id).iterate_items())
