"""Florida Sunbiz enrichment via the parseforge Apify actor.

Sunbiz (search.sunbiz.org) is Cloudflare-protected, so we can't scrape it
directly — the Apify actor runs through residential proxies. Returns Florida
business-entity records (corporate name, status, principal/mailing address,
registered agent, officers).
"""
from __future__ import annotations

from typing import Any

from .. import config

ACTOR_ID = "parseforge/sunbiz-florida-business-scraper"


def search_entities(name: str, max_items: int = 8) -> list[dict[str, Any]]:
    """Search Sunbiz by entity name; returns raw entity dicts (with details)."""
    from apify_client import ApifyClient

    if not config.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is not set.")
    client = ApifyClient(config.APIFY_TOKEN)
    run = client.actor(ACTOR_ID).call(run_input={
        "searchTerm": name,
        "searchType": "EntityName",
        "maxItems": max_items,
        "includeDetails": True,
    })
    return list(client.dataset(run.default_dataset_id).iterate_items())
