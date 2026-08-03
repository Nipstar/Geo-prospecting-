"""Buyer-intent query builder — a thin company-row adapter over the shared
Geo-core prompt engine (github.com/Nipstar/Geo-core), plus a local (non-upstream)
global/software variant for companies that aren't a "near me" local business.

The local prompt phrasing + term normalisation live in antek-geo-core so the
free tease (this repo) and the paid audit (geo-slab) generate identical prompts
for identical inputs — the query-parity half of the zero-discrepancy guarantee.
This module keeps the repo's `build_queries(company_row)` signature and
delegates the wording to `antek_geo_core.prompts.build_prompts`.

`build_prompts_global()` below is geo-prospecting-local (not in the pinned
antek-geo-core package) — it covers international/B2B SaaS targets (e.g. an
audit tool sold to agencies worldwide) where "best X in {town}" is nonsense.
Auto-selected by `build_queries()` when a company row has no town, the same
"pick the right grain automatically" pattern already used for
town-vs-county disambiguation.
"""
from __future__ import annotations

from antek_geo_core.prompts import build_prompts, normalise_term  # noqa: F401

from ..config import CHECK_COUNTRY, FREE_CHECK_QUERIES

GLOBAL_QUERIES_PER_COMPANY = 5


def _industry(company) -> str:
    try:
        svc = company["primary_service"]
    except (IndexError, KeyError):
        svc = None
    return (svc or company["sector"] or "local business").strip()


def build_prompts_global(category: str, limit: int | None = None) -> list[str]:
    """Buyer-intent prompts for an international / non-geo product category —
    software, tools, platforms sold to a global or national audience rather
    than "near me" local search. No town/county grammar; `category` is used
    as typed (e.g. "AI transformation audit software for consultants and
    agencies"), same free-tease/paid-audit query-count convention as the
    local builder (limit defaults to FREE_CHECK_QUERIES, full set is 5)."""
    category = (category or "software").strip()
    prompts = [
        f"What's the best {category}?",
        f"Can you recommend a good {category}?",
        f"What are the top-rated {category} options?",
        f"I need {category}, what should I use?",
        f"Compare the leading {category} tools",
    ]
    n = limit if limit is not None else FREE_CHECK_QUERIES
    return prompts[:max(1, min(n, GLOBAL_QUERIES_PER_COMPANY))]


def build_queries(company, limit: int | None = None) -> list[str]:
    """Buyer-intent prompts for a DB company row. No town on the row means an
    international/software target — auto-switches to the generic global
    builder instead of forcing a "the local area" placeholder into a
    near-me-style prompt. Otherwise hands off to the shared Geo-core engine
    with town/county. Default limit = FREE_CHECK_QUERIES."""
    industry = _industry(company)
    town_raw = (company["town"] or "").strip()
    if not town_raw:
        return build_prompts_global(industry, limit=limit)
    try:
        county = (company["county"] or "").strip()
    except (IndexError, KeyError):
        county = ""
    n = limit if limit is not None else FREE_CHECK_QUERIES
    return build_prompts(industry, town_raw, county, country=CHECK_COUNTRY, limit=n)
