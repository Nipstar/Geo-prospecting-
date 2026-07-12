"""Shared AI-query core: brand detection, competitor extraction, and a
cost-tracked OpenRouter call.

Adapted from geo-slab's scripts/lib/ai_query_core.py (github.com/Nipstar/
geo-slab) so the two products score visibility the same way. Pure stdlib +
requests, no LLM orchestration.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import requests

from .. import config

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_HEADERS_EXTRA = {
    "HTTP-Referer": "https://antekautomation.com",
    "X-Title": "geo-outreach",
}


# --- Brand detection -------------------------------------------------------
def normalize_brand_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().strip())


def detect_brand_mention(text: str, brand_name: str, url: str = "") -> dict:
    """Regex brand detection. Returns mentioned/count/positions/sentiment."""
    if not text or not brand_name:
        return {"mentioned": False, "count": 0, "positions": [], "sentiment": "neutral"}

    brand_normalized = normalize_brand_name(brand_name)
    domain = ""
    if url:
        domain = urlparse(url if "//" in url else f"//{url}").netloc.replace("www.", "")

    patterns = [re.compile(r"\b" + re.escape(brand_name) + r"\b", re.IGNORECASE)]
    if domain:
        patterns.append(re.compile(re.escape(domain), re.IGNORECASE))
    if len(brand_name.split()) > 1:
        patterns.append(re.compile(r"\b" + re.escape(brand_normalized) + r"\b", re.IGNORECASE))

    count = 0
    positions: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            count += 1
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 60)
            positions.append(text[start:end].strip())

    sentiment = "neutral"
    if count > 0:
        text_lower = text.lower()
        positive = ["recommend", "best", "great", "excellent", "top", "leading",
                    "trusted", "reliable", "popular", "preferred", "outstanding"]
        negative = ["avoid", "poor", "worst", "bad", "terrible", "unreliable",
                    "scam", "complaint", "issue", "problem"]
        pos = sum(1 for w in positive if w in text_lower)
        neg = sum(1 for w in negative if w in text_lower)
        if pos > neg:
            sentiment = "positive"
        elif neg > pos:
            sentiment = "negative"

    return {"mentioned": count > 0, "count": count, "positions": positions[:5], "sentiment": sentiment}


def extract_competitors(text: str, brand_name: str) -> list[str]:
    """Pull competitor-like names from list items and bold text. A first-pass
    filter; run results through competitor_gate for letter/report use."""
    competitors: set[str] = set()
    brand_norm = normalize_brand_name(brand_name)

    list_pattern = re.compile(
        r"(?:^|\n)\s*(?:\d+[\.\)]\s*\**|[-*]\s*\**)"
        r"([A-Z][A-Za-z0-9\s&'-]{2,40}?)(?:\**\s*[-–—:]|\**\s*\n|\**$)",
        re.MULTILINE,
    )
    bold_pattern = re.compile(r"\*\*([A-Z][A-Za-z0-9\s&'-]{2,40}?)\*\*")

    for pat in (list_pattern, bold_pattern):
        for match in pat.finditer(text):
            name = match.group(1).strip().rstrip("*").strip()
            if normalize_brand_name(name) != brand_norm and len(name) > 2:
                competitors.add(name)

    noise = {
        "the best", "the top", "the most", "in conclusion", "for example",
        "in summary", "key features", "main benefits", "important factors",
        "here are", "some options", "final thoughts", "pros and cons",
        "word of mouth", "online search", "local directories", "local directory",
        "online reviews", "google reviews", "google search", "google maps",
        "social media", "personal recommendations", "recommendations",
        "check reviews", "ask friends", "search online", "review sites",
        "review platforms", "local search", "trade associations", "gas safe register",
        "location", "fees", "pricing", "cost", "costs", "experience",
        "qualifications", "professional qualifications", "credentials",
        "accreditation", "accreditations", "services offered", "services",
        "professional associations", "professional bodies", "professional body",
        "value for money", "specialization", "specialisation", "reputation",
        "availability", "communication", "references", "reviews and ratings",
        "local business directories", "local recommendations",
        "ask for recommendations", "range of services", "client reviews",
        "industry experience", "areas of expertise", "expertise", "self",
    }
    directories = {
        "trustpilot", "yell", "yelp", "checkatrade", "bark", "bark.com",
        "google", "google business", "google my business", "bing", "facebook",
        "linkedin", "thomson local", "yellow pages", "yellowpages", "192.com",
        "freeindex", "cylex", "scoot", "houzz", "which", "which?", "tripadvisor",
        "rightmove", "zoopla", "onthemarket",
        "chatgpt", "openai", "perplexity", "gemini", "claude", "reddit", "quora",
    }
    postcode_frag = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?(\s*\d[A-Z]{2})?$", re.I)
    signals = ("ltd", "llp", "& co", "accountant", "accountancy", "associates",
               "partners", "group", "chartered", "bookkeep", "advisor", "advisory",
               "solutions", "consultancy", "consulting", "financial", "plc", "limited",
               "estates", "lettings", "homes", "property", "solicitors")
    generic = {
        "experience", "qualifications", "qualification", "directories", "directory",
        "consultation", "consultations", "referrals", "referral", "reviews", "review",
        "testimonials", "testimonial", "fees", "fee", "pricing", "price", "cost", "costs",
        "networks", "network", "communication", "style", "expertise", "specialisation",
        "specialization", "availability", "reputation", "consideration", "considerations",
        "formation", "services", "service", "contact", "details", "recommendations",
        "recommendation", "friends", "family", "structure", "initial",
        "online", "local", "other", "businesses", "business", "clear", "consider",
        "what", "ask", "and", "for", "with", "your", "type", "value", "money",
    }

    def is_firm(name: str) -> bool:
        s = name.strip()
        low = s.lower()
        if low in noise or low in directories or len(s) <= 3 or postcode_frag.match(s):
            return False
        if any(sig in low for sig in signals):
            return True
        toks = [t for t in s.split() if t != "&"]
        if len(toks) < 2 or len(toks) > 4:
            return False
        if any(t.lower() in generic for t in toks):
            return False
        return all(t[:1].isupper() for t in toks)

    return sorted(c for c in competitors if is_firm(c))


# --- OpenRouter call (cost-tracked) ---------------------------------------
def _payload(model: str, prompt: str) -> dict:
    """Chat-completions body. Attaches OpenRouter's web plugin so the model
    answers from live search (config.WEB_SEARCH), except Perplexity/sonar which
    already searches natively — no point paying for the plugin on top."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1000,
        "temperature": 0.7,
        "usage": {"include": True},
    }
    if config.WEB_SEARCH and "perplexity" not in model.lower() and "sonar" not in model.lower():
        body["plugins"] = [{"id": "web", "max_results": config.WEB_SEARCH_MAX_RESULTS}]
    return body


def query_openrouter_full(prompt: str, model: str, api_key: str | None = None) -> Optional[dict]:
    """Call OpenRouter, return {text, cost_usd, tokens}. Requests usage
    accounting so cost is logged per check. Returns None on error."""
    api_key = api_key or config.OPENROUTER_API_KEY
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **_HEADERS_EXTRA,
            },
            json=_payload(model, prompt),
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        return {
            "text": data["choices"][0]["message"]["content"],
            "cost_usd": float(usage.get("cost", 0.0) or 0.0),
            "tokens": usage.get("total_tokens", 0),
        }
    except Exception as exc:  # noqa: BLE001
        import sys
        print(f"[OpenRouter/{model}] error: {exc}", file=sys.stderr)
        return None


if __name__ == "__main__":  # self-check: web plugin gating
    if config.WEB_SEARCH:
        assert "plugins" in _payload("openai/gpt-5.2-chat", "hi"), "web model should get plugin"
        assert "plugins" not in _payload("perplexity/sonar", "hi"), "perplexity skips plugin"
    assert _payload("openai/gpt-5.2-chat", "hi")["model"] == "openai/gpt-5.2-chat"
    print("ai_query self-check ok (web_search=%s)" % config.WEB_SEARCH)
