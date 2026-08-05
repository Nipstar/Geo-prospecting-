"""Shared helpers for the ingestion layer: dedup, domains, blocklist, upsert."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .. import config, db

_BLOCKLIST_PATH = config.ROOT / "chains_blocklist.txt"
_blocklist_cache: list[str] | None = None


def load_blocklist() -> list[str]:
    global _blocklist_cache
    if _blocklist_cache is None:
        _blocklist_cache = []
        if _BLOCKLIST_PATH.exists():
            for line in _BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    _blocklist_cache.append(line.lower())
    return _blocklist_cache


def is_chain(name: str) -> bool:
    name_l = (name or "").lower()
    return any(chain in name_l for chain in load_blocklist())


# --- Sector verification ----------------------------------------------------
# The Places search (both the official API and the Apify actor) is a free-text
# relevance search, not a category filter — "solicitors in Fareham" can and
# does return a bank branch or an estate agent that merely came up as related.
# Nothing downstream checked this, so wrong-sector places (e.g. Barclays Bank,
# an estate agent) flowed straight through into the mailing pipeline stamped
# with sector="solicitors". This gate cross-checks the place's own Google
# category data (or, failing that, the business name) against what the sector
# actually implies, before a company is inserted.
#
# Deliberately conservative: only sectors with a mapping below are gated at
# all (unmapped sectors pass through unchanged rather than risk false
# rejections on a vertical no one has curated types for yet), and a name-level
# keyword hit is enough to pass even with no category data — the goal is
# catching obvious mismatches (a bank turning up for "solicitors"), not
# perfectly classifying every result.
SECTOR_TYPE_MAP: dict[str, dict[str, list[str]]] = {
    "solicitors": {
        # Google Places "types"/"primaryType" values (official API) and the
        # Apify actor's "categoryName"/"categories" strings that indicate a
        # genuine legal-services business.
        "types": ["lawyer", "legal_services", "law_firm", "notary_public"],
        # Substrings checked against the business name as a fallback when no
        # category data is present (e.g. the Apify actor omitted it).
        "name_keywords": ["solicitor", "law", "legal", "llp", "notary",
                           "conveyanc", "barrister"],
    },
}


def _place_categories(item: dict) -> list[str]:
    """Best-effort category strings from either source shape: the official
    Places API (types/primaryType) or the Apify compass/crawler-google-places
    actor (categoryName/categories)."""
    cats: list[str] = []
    for key in ("types", "categories"):
        val = item.get(key)
        if isinstance(val, list):
            cats.extend(str(v) for v in val)
        elif isinstance(val, str) and val:
            cats.append(val)
    for key in ("primaryType", "categoryName"):
        val = item.get(key)
        if isinstance(val, str) and val:
            cats.append(val)
    return [c.lower().replace("_", " ") for c in cats]


def sector_mismatch(sector: str, name: str, item: dict) -> bool:
    """True if this place looks like the wrong business type for `sector`.

    Conservative by design — see SECTOR_TYPE_MAP comment. Returns False
    (i.e. "let it through") whenever the sector isn't mapped, or there's any
    signal at all (category or name) suggesting a match."""
    rules = SECTOR_TYPE_MAP.get((sector or "").strip().lower())
    if not rules:
        return False
    cats = _place_categories(item)
    if any(t.replace("_", " ") in c for c in cats for t in rules["types"]):
        return False
    name_l = (name or "").lower()
    if any(kw in name_l for kw in rules["name_keywords"]):
        return False
    # No category data and no name hint either way — only flag as a mismatch
    # if we positively found unrelated categories (e.g. "bank",
    # "real_estate_agency"); an empty/unknown category list is not proof of
    # anything, so let it through rather than risk silently dropping a real
    # solicitor whose listing lacks both signals.
    return bool(cats)


def domain_of(website: str | None) -> str:
    """Bare registrable domain from a URL or host string ('' if none)."""
    if not website:
        return ""
    website = website.strip()
    if "://" not in website:
        website = "http://" + website
    host = (urlparse(website).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalise_name(name: str) -> str:
    """For fuzzy comparison: lowercase, '&'->'and', strip suffixes/punctuation."""
    n = (name or "").lower().replace("&", "and")
    n = re.sub(r"\b(ltd|limited|llp|plc|co)\b", "", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def find_duplicate(conn, name: str, town: str | None, website: str | None):
    """Return an existing company row matching by (name, town) or by domain."""
    existing = db.find_company(conn, name, town)
    if existing:
        return existing
    dom = domain_of(website)
    if dom:
        existing = db.find_company_by_domain(conn, dom)
        if existing:
            return existing
    # Fuzzy name-only fallback within the same town.
    if town:
        target = normalise_name(name)
        for row in db.companies_in_town_sector(conn, town, ""):
            if normalise_name(row["name"]) == target:
                return row
    return None
