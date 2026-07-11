"""Three visibility probes: ChatGPT (OpenAI direct), Perplexity (sonar via
OpenRouter), Google AI Overview (SerpAPI). Raw responses are cached in
probe_cache keyed on (query, engine, date) so re-runs are free.
"""
from __future__ import annotations

import time
from datetime import date

import requests

from .. import config, db, llm


def _cached(conn, query: str, engine: str) -> str | None:
    return db.probe_cache_get(conn, query, engine, date.today().isoformat())


def _store(conn, query: str, engine: str, text: str) -> None:
    db.probe_cache_put(conn, query, engine, date.today().isoformat(), text)


def _backoff(attempt: int) -> None:
    time.sleep(2 ** attempt)


# --- ChatGPT (OpenAI direct) ----------------------------------------------
def probe_chatgpt(conn, query: str) -> str:
    cached = _cached(conn, query, "chatgpt")
    if cached is not None:
        return cached
    from openai import OpenAI

    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set (needed for the ChatGPT probe).")
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    prompt = (
        f"{query}. Answer as you normally would for a UK user, naming specific "
        "businesses you would recommend."
    )
    text = ""
    for attempt in range(3):
        try:
            try:
                resp = client.chat.completions.create(
                    model=config.OPENAI_PROBE_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=500,
                )
            except Exception:
                resp = client.chat.completions.create(
                    model=config.OPENAI_PROBE_MODEL_FALLBACK,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=500,
                )
            text = resp.choices[0].message.content or ""
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            _backoff(attempt)
    _store(conn, query, "chatgpt", text)
    return text


# --- Perplexity (sonar via OpenRouter) ------------------------------------
def probe_perplexity(conn, query: str) -> str:
    cached = _cached(conn, query, "perplexity")
    if cached is not None:
        return cached
    prompt = (
        f"{query}. Answer for a UK user, naming specific businesses you would "
        "recommend."
    )
    text = ""
    for attempt in range(3):
        try:
            text = llm.complete(
                "perplexity_probe", user=prompt, temperature=0.3, max_tokens=500
            )
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            _backoff(attempt)
    _store(conn, query, "perplexity", text)
    return text


# --- Google AI Overview (SerpAPI) -----------------------------------------
def probe_ai_overview(conn, query: str) -> str:
    cached = _cached(conn, query, "ai_overview")
    if cached is not None:
        return cached
    if not config.SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY is not set (needed for the AI Overview probe).")
    text = ""
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://serpapi.com/search",
                params={
                    "q": query,
                    "location": "United Kingdom",
                    "gl": "uk",
                    "hl": "en",
                    "api_key": config.SERPAPI_KEY,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            text = _extract_ai_overview(data)
            break
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            _backoff(attempt)
    _store(conn, query, "ai_overview", text)
    return text


def _extract_ai_overview(data: dict) -> str:
    """Pull the AI Overview block text if present, else fall back to organic
    result titles so scoring still has signal."""
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
    # No AI Overview shown: capture organic titles as a weaker signal.
    titles = [r.get("title", "") for r in data.get("organic_results", [])[:8]]
    return "NO_AI_OVERVIEW\n" + "\n".join(t for t in titles if t)


PROBES = {
    "chatgpt": probe_chatgpt,
    "perplexity": probe_perplexity,
    "ai_overview": probe_ai_overview,
}
