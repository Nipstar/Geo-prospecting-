"""Owner-enrichment provider switch.

Both clients (apollo, prospeo) expose the same surface —
`search_owner(domain)` and `match_person(first, last, domain)` returning
Apollo-shaped person dicts — so the enricher stays provider-agnostic.

Pick with ENRICH_PROVIDER=apollo|prospeo (default apollo).
"""
from __future__ import annotations

from .. import config
from . import apollo, prospeo

_PROVIDERS = {"apollo": apollo, "prospeo": prospeo}


def provider_name() -> str:
    name = (config.ENRICH_PROVIDER or "apollo").strip().lower()
    return name if name in _PROVIDERS else "apollo"


def provider():
    """Return the selected client module (apollo or prospeo)."""
    return _PROVIDERS[provider_name()]
