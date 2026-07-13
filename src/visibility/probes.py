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


def _flatten_aio_row(row: dict) -> str:
    """Turn one Apify actor result row into probe text. Empty AIO keeps the
    NO_AI_OVERVIEW prefix + organic-ish signal (reference titles) so the
    downstream answered/mention logic behaves exactly as before."""
    chunks: list[str] = []
    for block in row.get("text_blocks") or []:
        if isinstance(block, dict):
            for key in ("snippet", "text", "title"):
                if block.get(key):
                    chunks.append(str(block[key]))
            for item in block.get("list") or []:
                if isinstance(item, dict) and item.get("snippet"):
                    chunks.append(str(item["snippet"]))
        elif isinstance(block, str):
            chunks.append(block)
    for ref in row.get("references") or []:
        if isinstance(ref, dict) and ref.get("title"):
            chunks.append(str(ref["title"]))
    if row.get("ai_overview_present") and chunks:
        return "\n".join(chunks)
    return "NO_AI_OVERVIEW\n" + "\n".join(chunks)


def _apify_aio_fetch(queries: list[str]) -> dict[str, str] | None:
    """One batched Apify run for many queries. Returns {query: text} or None
    on failure. Batching amortises the actor's per-run setup fee."""
    actor = config.APIFY_AIO_ACTOR
    url = (f"https://api.apify.com/v2/acts/{actor}/"
           f"run-sync-get-dataset-items?token={config.APIFY_TOKEN}")
    payload = {"queries": queries, "gl": "gb", "hl": "en",
               "location": "United Kingdom"}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=180)
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                raise ValueError("unexpected actor response shape")
            out: dict[str, str] = {}
            for row in rows:
                q = (row.get("query") or "").strip()
                if q:
                    out[q] = _flatten_aio_row(row)
            return out
        except Exception:  # noqa: BLE001
            if attempt == 2:
                return None
            time.sleep(2 ** attempt)
    return None


def prefetch_ai_overviews(conn, queries: list[str]) -> None:
    """Warm the probe cache for all AIO queries in ONE Apify run (one setup
    fee instead of five). Call before the per-query probe loop; run_probe then
    hits cache. Silently no-ops without APIFY_TOKEN or if all cached."""
    if not config.APIFY_TOKEN:
        return
    engine = config.AI_OVERVIEW_ENGINE
    todo = [q for q in queries if _cached(conn, q, engine) is None]
    if not todo:
        return
    results = _apify_aio_fetch(todo)
    if results is None:
        return  # per-query path will retry / fall back
    for q in todo:
        _store(conn, q, engine, results.get(q, "NO_AI_OVERVIEW"))


def probe_ai_overview(conn, query: str) -> dict:
    """Google AI Overview. Apify actor primary (resolves Google's deferred
    page-token generation automatically); SerpAPI fallback with the deferred
    token followed by hand; skip cleanly if neither key is set."""
    engine = config.AI_OVERVIEW_ENGINE
    cached = _cached(conn, query, engine)
    if cached is not None:
        answered = bool(cached) and not cached.startswith("ERROR")
        return {"text": "" if not answered else cached, "cost_usd": 0.0, "answered": answered}
    text = ""
    if config.APIFY_TOKEN:
        results = _apify_aio_fetch([query])
        if results is not None:
            text = results.get(query, "NO_AI_OVERVIEW")
    if not text and config.SERPAPI_KEY:
        text = _serpapi_aio(query)
        if text == "ERROR":
            _store(conn, query, engine, "ERROR")
            return {"text": "", "cost_usd": 0.0, "answered": False}
    if not text:
        # AI Overview is optional in the hybrid; skip cleanly if no keys.
        return {"text": "", "cost_usd": 0.0, "answered": False}
    _store(conn, query, engine, text)
    answered = not text.startswith("NO_AI_OVERVIEW") or bool(text.replace("NO_AI_OVERVIEW", "").strip())
    return {"text": text, "cost_usd": config.COST_PER_PROBE["ai_overview"], "answered": answered}


def _serpapi_aio(query: str) -> str:
    """SerpAPI fallback. Crucially: when the main SERP only returns a
    page_token (Google deferred generation — the common case), follow it with
    a second engine=google_ai_overview request. The old code skipped this, so
    most real AI Overviews came back NO_AI_OVERVIEW."""
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://serpapi.com/search",
                params={
                    "q": query, "location": "United Kingdom",
                    "gl": "gb", "hl": "en", "api_key": config.SERPAPI_KEY,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            ai = data.get("ai_overview")
            token = ai.get("page_token") if isinstance(ai, dict) else None
            if token and not (isinstance(ai, dict) and ai.get("text_blocks")):
                resp2 = requests.get(
                    "https://serpapi.com/search",
                    params={"engine": "google_ai_overview",
                            "page_token": token,
                            "api_key": config.SERPAPI_KEY},
                    timeout=30,
                )
                resp2.raise_for_status()
                data2 = resp2.json()
                if data2.get("ai_overview"):
                    data = data2
            return _extract_ai_overview(data)
        except Exception:  # noqa: BLE001
            if attempt == 2:
                return "ERROR"
            time.sleep(2 ** attempt)
    return "ERROR"


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
