"""Buyer-intent query generation.

The five frozen discovery intents from geo-slab (spec §7, github.com/Nipstar/
geo-slab) — best / recommend / who to call / reviews / compare — phrased the
way a real customer types them. Raw industry strings ("legal services") are
normalised to the search noun a person actually uses ("solicitor") before the
templates fire, and pluralisation is grammar-safe so "servicess" can never
appear in a client-facing report.
"""
from __future__ import annotations

from ..config import CHECK_COUNTRY, FREE_CHECK_QUERIES, QUERIES_PER_COMPANY


def _art(word: str) -> str:
    """'a' / 'an' by leading vowel. Rare edge cases (hour, MOT) don't arise for
    the service nouns used here."""
    return "an" if word[:1].lower() in "aeiou" else "a"


# --- Search-noun normalisation ---------------------------------------------
# Substring-matched against the raw industry string, first hit wins (same
# pattern as competitor_gate.VERTICAL_NOUN_PHRASES). Values are the singular
# and plural a real person types into ChatGPT. Order matters: put longer /
# more specific keys before shorter ones. UK vocabulary throughout.
SEARCH_NOUNS: list[tuple[str, str, str]] = [
    # professional services
    ("conveyanc", "conveyancing solicitor", "conveyancing solicitors"),
    ("family law", "family solicitor", "family solicitors"),
    ("solicitor", "solicitor", "solicitors"),
    ("legal", "solicitor", "solicitors"),
    ("law", "solicitor", "solicitors"),
    ("accountanc", "accountant", "accountants"),
    ("accountant", "accountant", "accountants"),
    ("accounting", "accountant", "accountants"),
    ("bookkeep", "bookkeeper", "bookkeepers"),
    ("financial advi", "financial adviser", "financial advisers"),
    ("financial planning", "financial adviser", "financial advisers"),
    ("mortgage", "mortgage broker", "mortgage brokers"),
    ("insurance", "insurance broker", "insurance brokers"),
    ("recruit", "recruitment agency", "recruitment agencies"),
    ("marketing", "marketing agency", "marketing agencies"),
    ("web design", "web design agency", "web design agencies"),
    ("it support", "IT support company", "IT support companies"),
    ("it service", "IT support company", "IT support companies"),
    ("architect", "architect", "architects"),
    ("survey", "surveyor", "surveyors"),
    # property
    ("estate agen", "estate agent", "estate agents"),
    ("letting", "letting agent", "letting agents"),
    ("property manage", "property management company",
     "property management companies"),
    ("real estate", "estate agent", "estate agents"),
    # health
    ("orthodont", "dentist", "dentists"),
    ("dental", "dentist", "dentists"),
    ("dentist", "dentist", "dentists"),
    ("veterinar", "vet", "vets"),
    ("physio", "physiotherapist", "physiotherapists"),
    ("optic", "optician", "opticians"),
    ("chiropract", "chiropractor", "chiropractors"),
    ("osteopath", "osteopath", "osteopaths"),
    # trades
    ("plumb", "plumber", "plumbers"),
    ("electric", "electrician", "electricians"),
    ("roof", "roofer", "roofers"),
    ("heating engineer", "heating engineer", "heating engineers"),
    ("boiler", "heating engineer", "heating engineers"),
    ("heating", "heating engineer", "heating engineers"),
    ("builder", "builder", "builders"),
    ("building contractor", "builder", "builders"),
    ("construction", "builder", "builders"),
    ("landscap", "landscaper", "landscapers"),
    ("garden", "gardener", "gardeners"),
    ("locksmith", "locksmith", "locksmiths"),
    ("pest control", "pest control company", "pest control companies"),
    ("cleaning", "cleaning company", "cleaning companies"),
    ("removal", "removals company", "removals companies"),
    ("scaffold", "scaffolder", "scaffolders"),
    ("decorat", "painter and decorator", "painters and decorators"),
    # automotive
    ("mot", "MOT garage", "MOT garages"),
    ("car repair", "garage", "garages"),
    ("auto repair", "garage", "garages"),
    ("vehicle repair", "garage", "garages"),
    ("garage", "garage", "garages"),
    # print / office
    ("managed print", "managed print provider", "managed print providers"),
    ("photocopier", "photocopier supplier", "photocopier suppliers"),
]


def _plural(word: str) -> str:
    """Grammar-safe pluralisation. Never produces 'servicess'."""
    w = word.strip()
    lower = w.lower()
    if lower.endswith("s"):
        return w
    if lower.endswith("y") and lower[-2:-1] not in "aeiou":
        return w[:-1] + "ies"
    if lower.endswith(("ch", "sh", "x", "z")):
        return w + "es"
    return w + "s"


def normalise_term(industry: str) -> tuple[str, str, bool]:
    """Return (singular, plural, countable) for the raw industry string.

    countable=False means an unmapped service phrase ("waste management
    services") — templates must avoid 'a X' grammar and blind plurals."""
    raw = (industry or "").strip()
    s = raw.lower()
    for key, sing, plur in SEARCH_NOUNS:
        if key in s:
            return sing, plur, True
    countable = not (s.endswith("s") or "service" in s or s.endswith("ing"))
    term = raw
    if not countable and s.endswith(" services"):
        term = raw[: -len(" services")].rstrip()
    elif not countable and s.endswith(" service"):
        term = raw[: -len(" service")].rstrip()
    return term, _plural(term), countable


def _industry(company) -> str:
    try:
        svc = company["primary_service"]
    except (IndexError, KeyError):
        svc = None
    return (svc or company["sector"] or "local business").strip()


def build_queries(company, limit: int | None = None) -> list[str]:
    """Buyer-intent prompts for a company, phrased the way a real person
    types them. Ordered by conversion signal — best / recommend / reviews
    first — so the free check (limit=FREE_CHECK_QUERIES, default 3) keeps the
    strongest intents and the paid audit (limit=QUERIES_PER_COMPANY) adds
    who-to-call and compare."""
    industry = _industry(company)
    town = (company["town"] or "the local area").strip()
    try:
        county = (company["county"] or "").strip()
    except (IndexError, KeyError):
        county = ""
    # Disambiguate the town the way a person would: with the county
    # ("Andover, Hampshire"), not ", UK". Fall back to CHECK_COUNTRY only
    # when no county is known, so Andover MA never bleeds in.
    if county and county.lower() != town.lower():
        place = f"{town}, {county}"
        region = county
    else:
        place = ", ".join(p for p in [town, CHECK_COUNTRY] if p)
        region = place
    sing, plur, countable = normalise_term(industry)
    if countable:
        prompts = [
            f"Who's the best {sing} in {place}?",
            f"Can you recommend a good {sing} near {town}?",
            f"Which {plur} in {town} have the best reviews?",
            f"I need {_art(sing)} {sing} in {region}, who should I call?",
            f"Compare {plur} in {place}",
        ]
    else:
        prompts = [
            f"Who's the best for {sing} in {place}?",
            f"Can you recommend somewhere for {sing} near {town}?",
            f"Which {sing} companies in {town} have the best reviews?",
            f"I need {sing} in {region}, who should I call?",
            f"Compare {sing} companies in {place}",
        ]
    n = limit if limit is not None else FREE_CHECK_QUERIES
    return prompts[:max(1, min(n, QUERIES_PER_COMPANY))]
