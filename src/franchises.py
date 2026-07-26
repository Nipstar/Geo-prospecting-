"""Franchise / big-brand detection — skip offices with no single local owner.

Large real-estate franchises (Keller Williams, RE/MAX, Berkshire Hathaway…) are
corporate offices: there's no single owner to name, they rarely convert on a
cold owner-pitch, and their sites list dozens of agents. We skip them for
owner-enrichment and (optionally) for prospecting targeting.

Match is a lowercase substring test on the business name. Entries are distinctive
enough to avoid false positives (multi-word or unambiguous brand tokens).
"""
from __future__ import annotations

import re

# US + intl real-estate franchise / national-brand markers.
FRANCHISE_BRANDS = [
    "keller williams", "kw realty", "re/max", "remax", "re max",
    "berkshire hathaway", "bhhs", "coldwell banker", "century 21", "century21",
    "douglas elliman", "sotheby", "compass real estate", "compass realty",
    "exp realty", "exp commercial", "realty one group", "weichert",
    "better homes and gardens", "howard hanna", "homesmart", "united real estate",
    "corcoran", "engel & völkers", "engel & volkers", "engel and volkers",
    "christie's international", "christies international", "era real estate",
    "windermere", "long & foster", "long and foster", "john l scott",
    "baird & warner", "@properties", "nexthome", "real broker", "realty executives",
    "redfin", "opendoor", "zillow",
]

# Word-boundary brands that are too short/ambiguous for a plain substring.
_BOUNDARY = [r"\bcompass\b", r"\bexp\b", r"\bera\b"]
_BOUNDARY_RE = [re.compile(p, re.I) for p in _BOUNDARY]


def is_franchise(name: str | None) -> bool:
    if not name:
        return False
    low = name.lower()
    if any(b in low for b in FRANCHISE_BRANDS):
        return True
    # Only treat the short/ambiguous ones as franchises when paired with a
    # real-estate word, to avoid false hits.
    if re.search(r"realty|real estate|homes|properties|group", low):
        if any(rx.search(low) for rx in _BOUNDARY_RE):
            return True
    return False
