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
OUTPUT_DIR = ROOT / "output"
REPORTS_DIR = OUTPUT_DIR / "reports"
QUEUE_DIR = OUTPUT_DIR / "queue"
LETTERS_DIR = OUTPUT_DIR / "letters"

for _d in (DATA_DIR, REPORTS_DIR, QUEUE_DIR, LETTERS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Secrets (loaded from .env) --------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
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
    "generate": "anthropic/claude-sonnet-4",
    # report and letter copy
    "report_copy": "anthropic/claude-sonnet-4",
    # weekly review narrative
    "review": "anthropic/claude-sonnet-4",
    # perplexity visibility probe (sonar via OpenRouter)
    "perplexity_probe": "perplexity/sonar",
}

# The ChatGPT visibility probe hits OpenAI directly (must be the real engine).
OPENAI_PROBE_MODEL = "gpt-4o-mini-search-preview"
# Fallback if the search-capable model is unavailable on the account.
OPENAI_PROBE_MODEL_FALLBACK = "gpt-4o-mini"

# Rough $ per 1K tokens for the cost guard (order of magnitude, not billing).
COST_PER_1K_TOKENS = {
    "openai/gpt-4o-mini": 0.0006,
    "anthropic/claude-sonnet-4": 0.009,
    "perplexity/sonar": 0.001,
    OPENAI_PROBE_MODEL: 0.0006,
}
# Rough $ per probe call for batch cost estimates.
COST_PER_PROBE = {"chatgpt": 0.01, "perplexity": 0.005, "ai_overview": 0.02}

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
