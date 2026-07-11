"""Buyer-intent query generation.

Uses geo-slab's five frozen discovery prompts (spec §7, github.com/Nipstar/
geo-slab) so the free check asks the questions a real customer asks. Driven by
the company's primary service (or sector) as the industry noun, plus town and
county.
"""
from __future__ import annotations

from ..config import QUERIES_PER_COMPANY


def _art(word: str) -> str:
    """'a' / 'an' by leading vowel. Rare edge cases (hour, MOT) don't arise for
    the service nouns used here."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def _industry(company) -> str:
    try:
        svc = company["primary_service"]
    except (IndexError, KeyError):
        svc = None
    return (svc or company["sector"] or "local business").strip()


def build_queries(company) -> list[str]:
    """Return the frozen 5 buyer-intent prompts for a company."""
    industry = _industry(company)
    town = company["town"] or "the local area"
    try:
        county = company["county"] or town
    except (IndexError, KeyError):
        county = town
    prompts = [
        f"Who is the best {industry} in {town}?",
        f"Recommend {_art(industry)} {industry} near {town}",
        f"I need {_art(industry)} {industry} in {county}, who should I call?",
        f"{industry} {town} reviews — who do you recommend?",
        f"Compare {industry}s in {town}",
    ]
    return prompts[:QUERIES_PER_COMPANY]
