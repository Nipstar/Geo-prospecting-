"""Shared AI-query core — now a thin adapter over antek-geo-core.

Brand detection, competitor extraction and the cost-tracked OpenRouter call live
in the shared Geo-core library (github.com/Nipstar/Geo-core) so geo-prospecting
and geo-slab can never drift on how they score visibility. This module aligns
Geo-core's env-driven settings to this repo's config and re-exports the same
public surface the rest of the repo already imports.
"""
from __future__ import annotations

from antek_geo_core import brand as _brand
from antek_geo_core import providers as _providers
from antek_geo_core import settings as _core_settings

from .. import config

# Align the shared core to this repo's config so behaviour is byte-identical.
_core_settings.OPENROUTER_API_KEY = config.OPENROUTER_API_KEY
_core_settings.WEB_SEARCH = config.WEB_SEARCH
_core_settings.WEB_SEARCH_MAX_RESULTS = config.WEB_SEARCH_MAX_RESULTS
_core_settings.X_TITLE = "geo-outreach"

OPENROUTER_URL = _providers.OPENROUTER_URL

# Public surface (unchanged for callers) — sourced from Geo-core.
normalize_brand_name = _brand.normalize_brand_name
detect_brand_mention = _brand.detect_brand_mention
extract_competitors = _brand.extract_competitors
query_openrouter_full = _providers.query_openrouter_full

__all__ = [
    "OPENROUTER_URL", "normalize_brand_name", "detect_brand_mention",
    "extract_competitors", "query_openrouter_full",
]
