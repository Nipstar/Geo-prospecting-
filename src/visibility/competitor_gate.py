"""Competitor validation + company-name cleaning — now re-exported from
antek-geo-core (github.com/Nipstar/Geo-core).

The gate lives in the shared library so the same vocabulary runs through both
geo-prospecting and geo-slab. This module re-exports it unchanged so existing
`from . import competitor_gate` call sites keep working.
"""
from antek_geo_core.competitors import (  # noqa: F401
    AGGREGATOR_DENYLIST,
    AGGREGATOR_SUBSTRINGS,
    CATEGORY_WORDS,
    DEFAULT_NOUN_PHRASE,
    FIRM_SIGNALS,
    GENERIC_WORDS,
    PAGE_LABEL_DENYLIST,
    QUESTION_WORDS,
    VERTICAL_NOUN_PHRASES,
    clean_company_name,
    first_valid_competitor,
    is_in_universe,
    is_self_mention,
    is_valid_competitor,
    noun_phrase,
    valid_competitors,
)
