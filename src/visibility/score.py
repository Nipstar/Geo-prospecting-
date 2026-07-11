"""Visibility scoring.

Self-contained implementation of the geo-slab rubric (github.com/Nipstar/
geo-slab): per engine, does the company appear across the buyer-intent queries;
which competitors appear instead; a weighted composite 0-100. Produces one
visibility_checks row with a plain-English headline finding.

Engine weights reflect buyer reality: ChatGPT is where most people ask now, so
it carries the most weight.
"""
from __future__ import annotations

import re
from datetime import date

from .. import db, llm
from ..ingest.util import domain_of, normalise_name
from . import prompts, probes

ENGINE_WEIGHTS = {"chatgpt": 0.45, "perplexity": 0.30, "ai_overview": 0.25}


def _mention(company_name: str, domain: str, text: str) -> bool:
    """Fuzzy, case-insensitive presence test. Handles '& ' vs 'and'."""
    if not text:
        return False
    norm_text = normalise_name(text)
    norm_name = normalise_name(company_name)
    if norm_name and norm_name in norm_text:
        return True
    # Domain root (e.g. 'acmelettings' from acmelettings.co.uk).
    if domain:
        root = domain.split(".")[0]
        if root and root in norm_text.replace(" ", ""):
            return True
    return False


# Words that look like business names but are noise when extracting competitors.
_STOP = {
    "The", "Best", "Top", "Google", "Reviews", "Review", "Estate", "Agents",
    "Agent", "Solicitors", "Accountants", "Property", "Homes", "Services",
    "Company", "Ltd", "Limited", "Group", "UK", "AI", "Overview",
}


def _extract_candidate_names(text: str) -> list[str]:
    """Pull capitalised multi-word phrases that look like business names."""
    if not text or text.startswith("NO_AI_OVERVIEW"):
        candidates = []
    else:
        candidates = re.findall(r"\b([A-Z][A-Za-z&']+(?:\s+[A-Z][A-Za-z&']+){0,2})\b", text)
    out: list[str] = []
    for c in candidates:
        words = [w for w in c.split() if w not in _STOP]
        if not words:
            continue
        cleaned = " ".join(words)
        if len(cleaned) > 2 and cleaned not in out:
            out.append(cleaned)
    return out


def _known_competitors(conn, company) -> dict[str, str]:
    """{normalised_name: display_name} of other DB companies in same town+sector."""
    others = db.companies_in_town_sector(
        conn, company["town"] or "", company["sector"] or "", exclude_id=company["id"]
    )
    return {normalise_name(o["name"]): o["name"] for o in others}


def score_company(conn, company, queries=None, engines=None) -> dict:
    """Run/read probes, score, write a mini visibility_checks row. Returns it."""
    queries = queries or prompts.build_queries(company)
    engines = engines or ["chatgpt", "perplexity", "ai_overview"]
    name = company["name"]
    domain = domain_of(company["website"])
    known = _known_competitors(conn, company)

    per_engine_hits: dict[str, list[bool]] = {e: [] for e in engines}
    competitors_found: dict[str, int] = {}  # display name -> count
    unknown_found: dict[str, int] = {}

    for query in queries:
        for engine in engines:
            text = probes.PROBES[engine](conn, query)
            per_engine_hits[engine].append(_mention(name, domain, text))
            # Competitors: known DB names first, then unknown candidates.
            norm_text = normalise_name(text)
            for norm, display in known.items():
                if norm and norm in norm_text:
                    competitors_found[display] = competitors_found.get(display, 0) + 1
            for cand in _extract_candidate_names(text):
                nc = normalise_name(cand)
                if nc == normalise_name(name):
                    continue
                if nc in known:
                    continue
                unknown_found[cand] = unknown_found.get(cand, 0) + 1

    # Per-engine mention rate 0-100.
    engine_scores: dict[str, float] = {}
    for engine, hits in per_engine_hits.items():
        engine_scores[engine] = round(100 * sum(hits) / len(hits), 1) if hits else 0.0

    composite = round(
        sum(engine_scores.get(e, 0.0) * w for e, w in ENGINE_WEIGHTS.items()), 1
    )

    # Rank competitors by how often they appeared. Known DB competitors are the
    # most credible "who is winning" names.
    ranked_known = sorted(competitors_found.items(), key=lambda kv: -kv[1])
    ranked_unknown = sorted(unknown_found.items(), key=lambda kv: -kv[1])
    top_competitors = [n for n, _ in ranked_known[:3]] or [n for n, _ in ranked_unknown[:3]]
    competitor_named = ", ".join(top_competitors[:2]) if top_competitors else None

    headline = _headline_finding(company, queries, engine_scores, top_competitors)

    check_id = db.insert_visibility_check(
        conn, company["id"],
        run_date=date.today().isoformat(),
        check_type="mini",
        chatgpt_score=engine_scores.get("chatgpt"),
        perplexity_score=engine_scores.get("perplexity"),
        ai_overview_score=engine_scores.get("ai_overview"),
        composite_score=composite,
        headline_finding=headline,
        competitor_named=competitor_named,
    )
    # Advance new -> checked.
    if company["status"] == "new":
        try:
            db.advance_status(conn, company["id"], "checked", event="mini_check")
        except db.InvalidTransition:
            pass
    return {
        "check_id": check_id,
        "composite": composite,
        "engine_scores": engine_scores,
        "headline": headline,
        "competitor_named": competitor_named,
        "competitors": top_competitors,
    }


def _headline_finding(company, queries, engine_scores, competitors) -> str:
    """One plain sentence for the opener. Deterministic template, no hype."""
    town = company["town"] or "their area"
    sector = (company["sector"] or "business").rstrip("s")
    appeared = engine_scores.get("chatgpt", 0) > 0
    comp_str = _join_names(competitors[:2]) if competitors else "other firms"
    n = len(queries)
    if not appeared and engine_scores.get("chatgpt", 100) == 0:
        return (
            f"Asked ChatGPT for the best {sector} in {town} {n} different ways. "
            f"{comp_str} came up. {company['name']} did not appear once."
        )
    if engine_scores.get("chatgpt", 0) < 50:
        return (
            f"Asked ChatGPT for the best {sector} in {town} {n} different ways. "
            f"{company['name']} showed up in some answers, {comp_str} in more."
        )
    return (
        f"Checked how {company['name']} shows up when people ask AI for a "
        f"{sector} in {town}. Decent on ChatGPT, thin on Perplexity and Google's "
        f"AI results."
    )


def _join_names(names: list[str]) -> str:
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]
