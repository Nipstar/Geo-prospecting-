"""Buyer-intent query builder — now a thin company-row adapter over the shared
Geo-core prompt engine (github.com/Nipstar/Geo-core).

The prompt phrasing + term normalisation live in antek-geo-core so the free
tease (this repo) and the paid audit (geo-slab) generate identical prompts for
identical inputs — the query-parity half of the zero-discrepancy guarantee.
This module keeps the repo's `build_queries(company_row)` signature and delegates
the wording to `antek_geo_core.prompts.build_prompts`.
"""
from __future__ import annotations

from antek_geo_core.prompts import build_prompts, normalise_term  # noqa: F401

from ..config import CHECK_COUNTRY, FREE_CHECK_QUERIES


def _industry(company) -> str:
    try:
        svc = company["primary_service"]
    except (IndexError, KeyError):
        svc = None
    return (svc or company["sector"] or "local business").strip()


def build_queries(company, limit: int | None = None) -> list[str]:
    """Buyer-intent prompts for a DB company row. Extracts industry/town/county
    then hands off to the shared engine. Default limit = FREE_CHECK_QUERIES."""
    industry = _industry(company)
    town = (company["town"] or "the local area").strip()
    try:
        county = (company["county"] or "").strip()
    except (IndexError, KeyError):
        county = ""
    n = limit if limit is not None else FREE_CHECK_QUERIES
    return build_prompts(industry, town, county, country=CHECK_COUNTRY, limit=n)
