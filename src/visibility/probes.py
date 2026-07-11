"""Visibility probes (hybrid strategy).

Four consumer flagships via one OpenRouter key (ChatGPT, Claude, Gemini,
Perplexity) plus Google AI Overview via SerpAPI. Each probe returns
{text, cost_usd, answered}. Raw responses are cached in probe_cache keyed on
(query, engine, date) so re-runs are free.
"""
from __future__ import annotations

import time
from datetime import date

import requests

from .. import config, db
from . import ai_query


def _cached(conn, query: str, engine: str) -> str | None:
    return db.probe_cache_get(conn, query, engine, date.today().isoformat())


def _store(conn, query: str, engine: str, text: str) -> None:
    db.probe_cache_put(conn, query, engine, date.today().isoformat(), text)


def probe_model(conn, engine: str, query: str) -> dict:
    """One of the four OpenRouter platforms. engine is a CHECK_MODELS key."""
    cached = _cached(conn, query, engine)
    if cached is not None:
        answered = bool(cached) and not cached.startswith("ERROR")
        return {"text": "" if not answered else cached, "cost_usd": 0.0, "answered": answered}
    model = config.CHECK_MODELS[engine]
    time.sleep(0.2)  # gentle rate limit
    res = ai_query.query_openrouter_full(query, model)
    if not res:
        _store(conn, query, engine, "ERROR")
        return {"text": "", "cost_usd": 0.0, "answered": False}
    _store(conn, query, engine, res["text"])
    return {"text": res["text"], "cost_usd": res.get("cost_usd", 0.0), "answered": True}


def probe_ai_overview(conn, query: str) -> dict:
    """Google AI Overview via SerpAPI."""
    engine = config.AI_OVERVIEW_ENGINE
    cached = _cached(conn, query, engine)
    if cached is not None:
        answered = bool(cached) and not cached.startswith("ERROR")
        return {"text": "" if not answered else cached, "cost_usd": 0.0, "answered": answered}
    if not config.SERPAPI_KEY:
        # AI Overview is optional in the hybrid; skip cleanly if no key.
        return {"text": "", "cost_usd": 0.0, "answered": False}
    text = ""
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://serpapi.com/search",
                params={
                    "q": query, "location": "United Kingdom",
                    "gl": "uk", "hl": "en", "api_key": config.SERPAPI_KEY,
                },
                timeout=30,
            )
            resp.raise_for_status()
            text = _extract_ai_overview(resp.json())
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                _store(conn, query, engine, "ERROR")
                return {"text": "", "cost_usd": 0.0, "answered": False}
            time.sleep(2 ** attempt)
    _store(conn, query, engine, text)
    answered = not text.startswith("NO_AI_OVERVIEW") or bool(text.replace("NO_AI_OVERVIEW", "").strip())
    return {"text": text, "cost_usd": config.COST_PER_PROBE["ai_overview"], "answered": answered}


def _extract_ai_overview(data: dict) -> str:
    """Pull the AI Overview block text if present, else fall back to organic
    titles so scoring still has signal."""
    ai = data.get("ai_overview")
    if isinstance(ai, dict):
        chunks = []
        for block in ai.get("text_blocks", []) or []:
            if block.get("snippet"):
                chunks.append(block["snippet"])
            for item in block.get("list", []) or []:
                if item.get("snippet"):
                    chunks.append(item["snippet"])
        for ref in ai.get("references", []) or []:
            if ref.get("title"):
                chunks.append(ref["title"])
        if chunks:
            return "\n".join(chunks)
    titles = [r.get("title", "") for r in data.get("organic_results", [])[:8]]
    return "NO_AI_OVERVIEW\n" + "\n".join(t for t in titles if t)


def run_probe(conn, engine: str, query: str) -> dict:
    """Dispatch a single (engine, query) probe."""
    if engine == config.AI_OVERVIEW_ENGINE:
        return probe_ai_overview(conn, query)
    return probe_model(conn, engine, query)
