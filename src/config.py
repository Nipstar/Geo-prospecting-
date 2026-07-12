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

# Rough $ per probe call for the pre-batch cost guard. OpenRouter cost is also
# tracked live per call via usage accounting; these are just the estimate.
COST_PER_PROBE = {
    "ChatGPT": 0.01, "Claude": 0.01, "Gemini": 0.003,
    "Perplexity": 0.005, "ai_overview": 0.02,
}

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
QUERIES_PER_COMPANY = 5
COMPANIES_HOUSE_RATE = (600, 300)  # 600 requests per 300 seconds (5 min)

# --- Follow-up nudge -------------------------------------------------------
CHECK_FOLLOWUP_DAYS = 5           # delivered checks with no audit_proposed
