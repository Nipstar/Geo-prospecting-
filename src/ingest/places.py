"""Google Places prospecting via the Apify compass/crawler-google-places actor.

Discovers companies by sector + town, maps them into the companies table, dedups
on (name, town) and website domain, and skips national chains.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .. import config, db
from . import util

ACTOR_ID = "compass/crawler-google-places"

# Official Google Places API (New) — Text Search. Returns up to 20 places per
# page, paginated via nextPageToken. No Apify credit needed.
_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.websiteUri,"
    "places.nationalPhoneNumber,places.rating,places.userRatingCount,"
    "places.businessStatus,places.addressComponents,nextPageToken"
)


def _addr_component(place: dict[str, Any], comp_type: str) -> str:
    for comp in place.get("addressComponents") or []:
        if comp_type in (comp.get("types") or []):
            return comp.get("longText") or comp.get("shortText") or ""
    return ""


def _run_places_api(sector: str, town: str, max_results: int,
                    country_code: str) -> list[dict[str, Any]]:
    """Official Places API (New) Text Search. Returns actor-shaped dicts so
    _map_place works unchanged. Skips non-operational places."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    body: dict[str, Any] = {
        "textQuery": f"{sector} in {town}",
        "regionCode": country_code.upper(),
        "pageSize": 20,
    }
    items: list[dict[str, Any]] = []
    for _ in range(5):  # up to 5 pages (100), capped by max_results below
        resp = requests.post(_PLACES_URL, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for p in data.get("places") or []:
            if (p.get("businessStatus") or "OPERATIONAL") != "OPERATIONAL":
                continue
            items.append({
                "title": (p.get("displayName") or {}).get("text", ""),
                "website": p.get("websiteUri") or "",
                "city": _addr_component(p, "locality")
                        or _addr_component(p, "postal_town"),
                "state": _addr_component(p, "administrative_area_level_1"),
                "address": p.get("formattedAddress") or "",
                "phone": p.get("nationalPhoneNumber") or "",
                "totalScore": p.get("rating"),
                "reviewsCount": p.get("userRatingCount"),
            })
            if len(items) >= max_results:
                return items
        token = data.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
    return items


def _run_actor(sector: str, town: str, max_results: int, country_code: str = "gb",
               location_name: str = "United Kingdom") -> list[dict[str, Any]]:
    """Call the Apify actor and return raw place dicts."""
    from apify_client import ApifyClient

    if not config.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is not set.")
    client = ApifyClient(config.APIFY_TOKEN)
    # locationQuery geocodes to a bounded area so the map crawler stays put.
    # Without it the actor wanders nationwide and returns out-of-area results.
    run_input = {
        "searchStringsArray": [f"{sector} in {town}"],
        "locationQuery": f"{town}, {location_name}",
        "maxCrawledPlacesPerSearch": max_results,
        "language": "en",
        "countryCode": country_code,
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
        # Full street address from Places = a mailable address (esp. US, where
        # there's no Companies House registered office). UK letters still prefer
        # the CH registered office when enrichment overwrites this.
        "registered_address": (item.get("address")
                               or ", ".join(p for p in [item.get("street"), item.get("city"),
                                                        item.get("state"), item.get("postalCode")] if p)
                               or ""),
        "sector": sector,
        "phone": item.get("phone") or item.get("phoneUnformatted"),
        "places_rating": item.get("totalScore") or item.get("rating"),
        "places_reviews": item.get("reviewsCount") or item.get("reviews"),
        "source": "apify_places",
        "source_date": date.today().isoformat(),
        "status": "new",
    }


def run_places_search(
    sector: str, town: str, max_results: int = 50, dry_run: bool = False,
    country: str | None = None,
) -> dict[str, int]:
    """Prospect a sector in a town. Returns counts. dry_run prints, no writes.

    country: name/code (e.g. "US"); defaults to CHECK_COUNTRY / UK, so existing
    UK behaviour is unchanged when omitted.
    """
    country_code, location_name = config.country_geo(country)
    # Prefer the official Places API when a key is set (no Apify credit); else
    # fall back to the Apify crawler actor.
    if config.GOOGLE_PLACES_API_KEY:
        raw = _run_places_api(sector, town, max_results, country_code)
    else:
        raw = _run_actor(sector, town, max_results, country_code=country_code,
                         location_name=location_name)
    conn = db.get_connection()
    inserted = skipped_chain = skipped_dupe = backfilled = 0
    try:
        for item in raw:
            mapped = _map_place(item, sector, town)
            if not mapped["name"]:
                continue
            if util.is_chain(mapped["name"]):
                skipped_chain += 1
                continue
            dup = util.find_duplicate(conn, mapped["name"], mapped["town"], mapped["website"])
            if dup:
                skipped_dupe += 1
                # Back-fill a missing mailing address from Places (e.g. sole
                # traders with no Companies House registered office).
                if mapped.get("registered_address") and not (dup["registered_address"] or "").strip():
                    if not dry_run:
                        db.update_company(conn, dup["id"], registered_address=mapped["registered_address"])
                    backfilled += 1
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
        "backfilled_address": backfilled,
    }
