"""Google Places prospecting via the Apify compass/crawler-google-places actor.

Discovers companies by sector + town, maps them into the companies table, dedups
on (name, town) and website domain, and skips national chains.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .. import config, db
from . import util

ACTOR_ID = "compass/crawler-google-places"


def _run_actor(sector: str, town: str, max_results: int) -> list[dict[str, Any]]:
    """Call the Apify actor and return raw place dicts."""
    from apify_client import ApifyClient

    if not config.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is not set.")
    client = ApifyClient(config.APIFY_TOKEN)
    # locationQuery geocodes to a bounded area so the map crawler stays put.
    # Without it the actor wanders nationwide and returns out-of-area results.
    run_input = {
        "searchStringsArray": [f"{sector} in {town}"],
        "locationQuery": f"{town}, UK",
        "maxCrawledPlacesPerSearch": max_results,
        "language": "en",
        "countryCode": "gb",
        "skipClosedPlaces": True,
    }
    run = client.actor(ACTOR_ID).call(run_input=run_input)
    items: list[dict[str, Any]] = []
    for item in client.dataset(run.default_dataset_id).iterate_items():
        items.append(item)
    return items


def _map_place(item: dict[str, Any], sector: str, town: str) -> dict[str, Any]:
    return {
        "name": (item.get("title") or item.get("name") or "").strip(),
        "website": item.get("website") or "",
        "town": item.get("city") or town,
        "county": item.get("state") or item.get("county"),
        "sector": sector,
        "phone": item.get("phone") or item.get("phoneUnformatted"),
        "places_rating": item.get("totalScore") or item.get("rating"),
        "places_reviews": item.get("reviewsCount") or item.get("reviews"),
        "source": "apify_places",
        "source_date": date.today().isoformat(),
        "status": "new",
    }


def run_places_search(
    sector: str, town: str, max_results: int = 50, dry_run: bool = False
) -> dict[str, int]:
    """Prospect a sector in a town. Returns counts. dry_run prints, no writes."""
    raw = _run_actor(sector, town, max_results)
    conn = db.get_connection()
    inserted = skipped_chain = skipped_dupe = 0
    try:
        for item in raw:
            mapped = _map_place(item, sector, town)
            if not mapped["name"]:
                continue
            if util.is_chain(mapped["name"]):
                skipped_chain += 1
                continue
            if util.find_duplicate(conn, mapped["name"], mapped["town"], mapped["website"]):
                skipped_dupe += 1
                continue
            if dry_run:
                print(f"  + {mapped['name']} ({mapped['town']}) {mapped['website']}")
                inserted += 1
            else:
                db.insert_company(conn, **mapped)
                inserted += 1
    finally:
        conn.close()
    return {
        "found": len(raw),
        "inserted": inserted,
        "skipped_chain": skipped_chain,
        "skipped_dupe": skipped_dupe,
    }
