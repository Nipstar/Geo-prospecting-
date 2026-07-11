"""Buyer-intent query generation.

Five queries per company from sector + town, templated per sector with a
sensible generic default.
"""
from __future__ import annotations

from ..config import QUERIES_PER_COMPANY

# Per-sector query templates. {town} and {service} are filled in. The generic
# default is used for any sector not listed.
SECTOR_TEMPLATES: dict[str, list[str]] = {
    "estate agents": [
        "best estate agent in {town}",
        "who should I use to sell my house in {town}",
        "recommended letting agents {town}",
        "top rated estate agents near {town}",
        "which estate agent has the best reviews in {town}",
    ],
    "solicitors": [
        "best solicitor in {town}",
        "who should I use for conveyancing in {town}",
        "recommended family law solicitors {town}",
        "top rated solicitors near {town}",
        "which law firm has the best reviews in {town}",
    ],
    "accountants": [
        "best accountant in {town}",
        "who should I use for small business accounts in {town}",
        "recommended accountants for the self employed {town}",
        "top rated accountants near {town}",
        "which accountancy firm has the best reviews in {town}",
    ],
    "plumbing": [
        "best plumber in {town}",
        "who should I call for a boiler repair in {town}",
        "recommended heating engineers {town}",
        "emergency plumber near {town}",
        "which plumbing company has the best reviews in {town}",
    ],
}

GENERIC = [
    "best {service} in {town}",
    "who should I use for {service} in {town}",
    "recommended {service} near {town}",
    "top rated {service} in {town}",
    "which {service} company has the best reviews in {town}",
]


def _service_word(company) -> str:
    return (
        (company["primary_service"] if _has(company, "primary_service") else None)
        or company["sector"]
        or "local service"
    )


def _has(row, key) -> bool:
    try:
        return row[key] is not None
    except (IndexError, KeyError):
        return False


def build_queries(company) -> list[str]:
    """Return up to QUERIES_PER_COMPANY buyer-intent queries for a company."""
    town = company["town"] or "the local area"
    sector = (company["sector"] or "").lower().strip()
    service = _service_word(company)
    templates = SECTOR_TEMPLATES.get(sector, GENERIC)
    queries = [t.format(town=town, service=service) for t in templates]
    return queries[:QUERIES_PER_COMPANY]
