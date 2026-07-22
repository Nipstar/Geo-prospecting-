"""Central configuration for geo-outreach.

Everything tunable lives here: model routing, daily caps, cadence, paths and
per-service pricing for cost guards. Modules import from here rather than
hard-coding values, so the whole system can be retuned in one file.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "pipeline.db"
OUTPUT_DIR = DATA_DIR / "output"
REPORTS_DIR = OUTPUT_DIR / "reports"
QUEUE_DIR = OUTPUT_DIR / "queue"
LETTERS_DIR = OUTPUT_DIR / "letters"

for _d in (DATA_DIR, REPORTS_DIR, QUEUE_DIR, LETTERS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Secrets (loaded from .env) --------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
# Official Google Places API (New) key. When set, ingest uses it instead of the
# Apify crawler actor — no Apify credit needed for prospecting.
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
# Apollo.io — US owner-name enrichment (name/title/phone, optional email reveal).
# Used for the US market only; feeds letters + LinkedIn. Email is stored for the
# US cold-email channel but never sent to UK leads.
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")
STANNP_API_KEY = os.getenv("STANNP_API_KEY", "")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")
CLAIM_BASE_URL = os.getenv("CLAIM_BASE_URL", "https://antek.link").rstrip("/")

# --- Model routing ---------------------------------------------------------
# Every llm.complete(task, ...) call resolves its model here. Swap models in
# one place. These are OpenRouter model identifiers (OpenAI-compatible API).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MODELS: dict[str, str] = {
    # cheap + fast: enrichment classification, reply classification
    "classify": "openai/gpt-4o-mini",
    # quality writing: message generation, message audit
    "generate": "anthropic/claude-sonnet-5",
    # report and letter copy
    "report_copy": "anthropic/claude-sonnet-5",
    # weekly review narrative
    "review": "anthropic/claude-sonnet-5",
}

# --- Visibility check platforms (hybrid probe strategy) --------------------
# The free AI Visibility Check queries the consumer-facing flagships a real
# prospect's customers actually get, all through one OpenRouter key, plus Google
# AI Overview via SerpAPI as a fifth signal. Model slugs verified in geo-slab
# against the OpenRouter models list (2026-07-03); OpenRouter slugs drift, so
# re-check here if a platform starts returning 404.
CHECK_MODELS = {
    "ChatGPT":    "openai/gpt-5.2-chat",
    "Claude":     "anthropic/claude-sonnet-5",
    "Gemini":     "google/gemini-2.5-flash",
    "Perplexity": "perplexity/sonar",
}
# Google AI Overview is probed via SerpAPI, not a chat model.
AI_OVERVIEW_ENGINE = "ai_overview"
# Full engine order for a check (the four models above + AI Overview).
CHECK_ENGINES = list(CHECK_MODELS.keys()) + [AI_OVERVIEW_ENGINE]

# Web search: attach OpenRouter's web plugin so the chat models answer from live
# search results, not just training data — matches how real AI products (ChatGPT
# search, Gemini grounding) actually answer. Perplexity/sonar already searches
# natively so it's skipped. Costs ~$0.02/probe extra. Toggle with CHECK_WEB_SEARCH=0.
WEB_SEARCH = os.getenv("CHECK_WEB_SEARCH", "1").strip().lower() not in ("0", "false", "no", "")
WEB_SEARCH_MAX_RESULTS = int(os.getenv("CHECK_WEB_SEARCH_MAX", "5"))

# Country appended to every probe prompt so an ambiguous town (Andover UK vs
# Andover, Massachusetts) does not make the model ask "which country?" and skip
# the answer. UK-focused by default; override with CHECK_COUNTRY (e.g. "US").
CHECK_COUNTRY = os.getenv("CHECK_COUNTRY", "UK").strip()

# Geo-targeting map: country name/code -> (SerpAPI/Apify gl code, human location).
# Add entries here to support new markets.
_COUNTRY_GEO = {
    "UK": ("gb", "United Kingdom"), "GB": ("gb", "United Kingdom"),
    "GREAT BRITAIN": ("gb", "United Kingdom"), "UNITED KINGDOM": ("gb", "United Kingdom"),
    "US": ("us", "United States"), "USA": ("us", "United States"),
    "UNITED STATES": ("us", "United States"),
}


def country_geo(country: str | None = None) -> tuple[str, str]:
    """Return (gl_code, location_name) for search geo-targeting.

    Falls back to CHECK_COUNTRY, then UK, so existing UK behaviour is unchanged
    when no country is supplied.
    """
    key = (country or CHECK_COUNTRY or "UK").strip().upper()
    return _COUNTRY_GEO.get(key, ("gb", "United Kingdom"))

# Rough $ per probe call for the pre-batch cost guard. OpenRouter cost is also
# tracked live per call via usage accounting; these are just the estimate.
# ai_overview: Apify johnvc actor, $0.01 setup + $0.015/retrieval; batched
# prefetch amortises setup so ~$0.017/query typical, $0.04 worst (deferred).
COST_PER_PROBE = {
    "ChatGPT": 0.01, "Claude": 0.01, "Gemini": 0.003,
    "Perplexity": 0.005, "ai_overview": 0.02,
}

# Apify actor for Google AI Overview probes (handles Google's deferred
# page-token generation automatically — the thing SerpApi makes you do by
# hand). Actor id uses ~ separator per Apify API convention.
APIFY_AIO_ACTOR = os.getenv("APIFY_AIO_ACTOR", "johnvc~google-ai-overview-api")

# AI Overview probe provider: "serpapi" (uses SERPAPI_KEY, no Apify credit) or
# "apify" (johnvc actor, better deferred-token handling but needs Apify credit).
# Default serpapi so checks never depend on Apify balance.
AIO_PROVIDER = os.getenv("AIO_PROVIDER", "serpapi").strip().lower()

# --- LinkedIn cadence and caps ---------------------------------------------
DAILY_CONNECTION_CAP = 15           # new connection notes per day (human-scale)
TOUCH2_DELAY_DAYS = 2               # after connection accepted
TOUCH3_DELAY_DAYS = 4               # after touch 2 sent
REPLY_URGENT_HOURS = 4             # queue flags replies older than this red

# --- Postal cadence --------------------------------------------------------
LETTER_FOLLOWUP_DAYS = 21          # unclaimed letters get one follow-up
STANNP_TEST_MODE = True            # never live-post by accident
STANNP_UNIT_PRICE_GBP = 0.79      # per letter, for cost estimates

# --- Visibility check ------------------------------------------------------
QUERIES_PER_COMPANY = 5   # full prompt set — paid audit tier

# The free lead-magnet check runs a subset of the buyer-intent prompts
# (best / recommend / reviews). Full 5-prompt sweep is a paid-audit feature;
# 3 keeps OpenRouter spend per free check roughly 40% lower.
FREE_CHECK_QUERIES = int(os.getenv("FREE_CHECK_QUERIES", "3"))
COMPANIES_HOUSE_RATE = (600, 300)  # 600 requests per 300 seconds (5 min)

# --- Follow-up nudge -------------------------------------------------------
CHECK_FOLLOWUP_DAYS = 5           # delivered checks with no audit_proposed
