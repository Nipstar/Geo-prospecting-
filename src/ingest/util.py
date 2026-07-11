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
