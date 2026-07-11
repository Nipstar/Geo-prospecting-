"""Competitor validation + company-name cleaning.

Adapted from geo-slab's scripts/prospect_config.py (github.com/Nipstar/geo-slab),
the battle-tested gate that stops a prospecting letter naming junk ("Client
Reviews", "Google Maps Search") or listing a firm as its own rival. Kept as a
plain module of literals + small pure functions so the same vocabulary runs
through scoring, reports and letters.
"""
from __future__ import annotations

import re

# --- Per-vertical noun phrase ---------------------------------------------
# Matched by substring against the raw industry string, first hit wins, so
# "accountants basingstoke" -> "an accountancy firm".
VERTICAL_NOUN_PHRASES = {
    "accountanc": "an accountancy firm",
    "accountant": "an accountancy firm",
    "accounting": "an accountancy firm",
    "bookkeep": "an accountancy firm",
    "dental": "a dental practice",
    "dentist": "a dental practice",
    "orthodont": "a dental practice",
    "estate agent": "an estate agency",
    "letting": "a letting agency",
    "family law": "a law firm",
    "solicitor": "a law firm",
    "conveyanc": "a law firm",
    "legal": "a law firm",
    "law": "a law firm",
    "plumb": "a firm like yours",
    "electric": "a firm like yours",
    "roof": "a firm like yours",
    "builder": "a firm like yours",
    "trade": "a firm like yours",
}
DEFAULT_NOUN_PHRASE = "a firm like yours"


def noun_phrase(industry: str) -> str:
    """The noun phrase to follow 'recommend ...' for this vertical."""
    s = (industry or "").lower()
    for key, phrase in VERTICAL_NOUN_PHRASES.items():
        if key in s:
            return phrase
    return DEFAULT_NOUN_PHRASE


# --- Competitor validation gate -------------------------------------------
AGGREGATOR_DENYLIST = {
    "trustpilot", "yell", "yell.com", "yelp", "checkatrade", "reviews.io",
    "clutch", "bark", "bark.com", "google", "google business",
    "google my business", "google maps", "google maps search", "bing",
    "facebook", "linkedin", "instagram", "twitter", "thomson local",
    "yellow pages", "yellowpages", "192.com", "freeindex", "cylex", "scoot",
    "houzz", "which", "which?", "tripadvisor", "reddit", "quora", "nextdoor",
    "citizens advice", "accountantsup", "unbiased", "comparison sites",
    "get quotes", "get a quote", "local facebook groups",
    "social media and community groups", "rightmove", "zoopla", "onthemarket",
    "chatgpt", "openai", "perplexity", "gemini", "claude", "copilot",
}
AGGREGATOR_SUBSTRINGS = (
    "google", "facebook", "linkedin", "trustpilot", "yell.com", "yelp",
    "comparison site", "review site", "review platform", "maps search",
    "get quotes", "social media", "yellow pages", "rightmove", "zoopla",
)

PAGE_LABEL_DENYLIST = {
    "services offered", "about us", "about", "contact", "contact us", "home",
    "our team", "our services", "services", "testimonials", "menu",
    "opening hours", "privacy policy", "terms", "blog", "news", "faq", "faqs",
    "hourly rates", "regulatory compliance", "accounting firms", "technology use",
    "personal rapport", "client base", "why recommended", "proactive advice",
    "bookkeeping", "client reviews", "range of services", "initial consultation",
}

GENERIC_WORDS = {
    "rates", "compliance", "regulatory", "firms", "firm", "use", "technology",
    "personal", "rapport", "client", "base", "advice", "reviews", "review",
    "ratings", "rating", "tips", "tip", "general", "services", "service",
    "fees", "fee", "pricing", "price", "cost", "costs", "quotes", "quote",
    "experience", "qualifications", "qualification", "communication",
    "availability", "reputation", "expertise", "associations", "association",
    "directories", "directory", "recommendations", "recommendation",
    "considerations", "consideration", "credentials", "accreditation",
    "specialisation", "specialization", "referrals", "referral",
}
QUESTION_WORDS = {
    "how", "why", "what", "when", "where", "who", "which", "some", "general",
    "other", "ask", "find", "finding", "choosing", "choose", "top", "best",
    "consider", "considering", "check", "look",
}
FIRM_SIGNALS = (
    "ltd", "llp", "limited", "plc", "& co", "accountancy", "accountant",
    "chartered", "associates", "partners", "solicitors", "surgery", "practice",
    "consultancy", "advisory", "estates", "lettings", "homes",
)


def is_valid_competitor(name: str) -> bool:
    """True only when `name` looks like a real rival firm, not a directory,
    review site, or a scraped page heading."""
    s = (name or "").strip().rstrip(".").strip()
    low = s.lower()
    if len(s) <= 3:
        return False
    if low in AGGREGATOR_DENYLIST or low in PAGE_LABEL_DENYLIST:
        return False
    if any(sub in low for sub in AGGREGATOR_SUBSTRINGS):
        return False
    toks = [t for t in re.split(r"\s+", s) if t and t != "&"]
    if not toks:
        return False
    if toks[0].lower() in QUESTION_WORDS:
        return False
    if any(sig in low for sig in FIRM_SIGNALS) and len(toks) >= 2:
        return True
    if not (2 <= len(toks) <= 4):
        return False
    if any(t.lower() in GENERIC_WORDS for t in toks):
        return False
    return all(t[:1].isupper() for t in toks)


# --- Company-name cleaning -------------------------------------------------
CATEGORY_WORDS = {
    "accountant", "accountants", "accountancy", "accounting", "chartered",
    "bookkeeping", "bookkeeper", "bookkeepers", "tax", "taxation",
    "dental", "dentist", "dentists", "orthodontist", "orthodontics",
    "solicitor", "solicitors", "law", "legal", "conveyancing", "conveyancers",
    "estate", "estates", "agent", "agents", "letting", "lettings",
    "plumber", "plumbers", "plumbing", "electrician", "electricians",
    "electrical", "roofing", "roofer", "roofers", "builder", "builders",
    "building", "landscaper", "landscapers", "landscaping", "services",
}


def _core_tokens(name: str) -> set:
    """Distinctive tokens of a firm name: drop category words, legal suffixes,
    and filler so 'Lawrence Young Accountants' -> {lawrence, young}."""
    toks = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]
    drop = CATEGORY_WORDS | {"ltd", "limited", "llp", "plc", "co", "the", "and",
                             "group", "associates", "partners"}
    return {t for t in toks if t not in drop and len(t) > 1}


def is_self_mention(competitor: str, brand: str) -> bool:
    """True when 'competitor' is really the prospect's own name (a listing
    variant): distinctive tokens of one are a subset of the other. Stops a firm
    being listed as its own rival."""
    cc, bc = _core_tokens(competitor), _core_tokens(brand)
    if not cc or not bc:
        return False
    shared = cc & bc
    return bool(shared) and (shared == cc or shared == bc)


def first_valid_competitor(names: list[str], brand: str = "") -> str | None:
    """First name in priority order passing the gate and not the prospect's own
    name variant, else None."""
    for n in names:
        if is_valid_competitor(n) and not (brand and is_self_mention(n, brand)):
            return n.strip().rstrip(".").strip()
    return None


def valid_competitors(names: list[str], brand: str = "") -> list[str]:
    """All names passing the gate, self-mentions of `brand` removed, order kept."""
    out: list[str] = []
    for n in names:
        if is_valid_competitor(n) and not (brand and is_self_mention(n, brand)):
            cleaned = n.strip().rstrip(".").strip()
            if cleaned not in out:
                out.append(cleaned)
    return out


def is_in_universe(name: str, cohort_names: list[str]) -> bool:
    """Positive booster: is this competitor one of the real firms already pulled
    for the campaign (same town+sector cohort in our DB)? Case-insensitive
    substring either direction. Cohort membership boosts, never suppresses."""
    low = (name or "").strip().lower()
    if not low:
        return False
    for c in cohort_names:
        cl = (c or "").strip().lower()
        if cl and (cl in low or low in cl):
            return True
    return False


def clean_company_name(name: str, town: str = "") -> str:
    """Strip GBP category/location cruft from a listing name.

    'The Accounting Studio - Accountant Southampton' -> 'The Accounting Studio'
    Clean names ('Troy Accounting Ltd') are returned unchanged.
    """
    s = (name or "").strip()
    if not s:
        return s
    tl = (town or "").strip().lower()

    def _cat(tok: str) -> bool:
        return tok.lower().strip(",.&()") in CATEGORY_WORDS

    segs = re.split(r"\s+[-–—]\s+", s)
    kept = []
    for seg in segs:
        toks = seg.split()
        if any(not _cat(t) and t.lower().strip(",.&()") != tl for t in toks):
            kept.append(seg)
    s = " - ".join(kept) if kept else s

    if tl:
        toks = s.split()
        low = [t.lower().strip(",.&()") for t in toks]
        drop = set()
        for i, t in enumerate(low):
            if t == tl:
                drop.add(i)
                j = i - 1
                while j >= 0 and low[j] in CATEGORY_WORDS:
                    drop.add(j); j -= 1
                j = i + 1
                while j < len(low) and low[j] in CATEGORY_WORDS:
                    drop.add(j); j += 1
        toks = [t for i, t in enumerate(toks) if i not in drop]
        s = " ".join(toks)

    s = s.strip(" -–—")
    return s if s else (name or "").strip()
