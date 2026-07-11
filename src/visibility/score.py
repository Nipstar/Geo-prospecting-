"""Visibility scoring (geo-slab 70/30 rubric).

Runs the five-engine hybrid check, detects brand mentions and competitors with
the shared geo-slab core, gates competitor names so no junk or self-mention
reaches a letter, and writes one visibility_checks row with a plain headline
finding. Composite score is the blunt geo-slab formula:

    (platforms_mentioned / platforms_tested) * 70
  + (prompts_mentioned  / prompts_total)     * 30

If every engine errors (0 platforms tested) it raises rather than fabricating a
0/100 result — a genuine invisibility and a dead API key must never look alike.
"""
from __future__ import annotations

from datetime import date

from .. import config, db
from ..ingest.util import domain_of
from . import ai_query, competitor_gate, probes, prompts

# Engine key -> visibility_checks column.
ENGINE_COLUMN = {
    "ChatGPT": "chatgpt_score",
    "Claude": "claude_score",
    "Gemini": "gemini_score",
    "Perplexity": "perplexity_score",
    "ai_overview": "ai_overview_score",
}


class VisibilityProbeError(RuntimeError):
    """Raised when no engine returned a live answer (API failure, not a result)."""


def _known_competitor_names(conn, company) -> list[str]:
    others = db.companies_in_town_sector(
        conn, company["town"] or "", company["sector"] or "", exclude_id=company["id"]
    )
    return [o["name"] for o in others]


def score_company(conn, company, queries=None, engines=None, check_type="mini") -> dict:
    """Run/read probes, score with the 70/30 rubric, write a check row."""
    queries = queries or prompts.build_queries(company)
    engines = engines or config.CHECK_ENGINES
    name = company["name"]
    domain = domain_of(company["website"])
    universe = _known_competitor_names(conn, company)

    per_engine: dict[str, dict] = {e: {"answered": 0, "mentioned": 0} for e in engines}
    competitor_counts: dict[str, dict] = {}
    total_cost = 0.0

    for engine in engines:
        for query in queries:
            res = probes.run_probe(conn, engine, query)
            if not res["answered"]:
                continue
            per_engine[engine]["answered"] += 1
            total_cost += res.get("cost_usd", 0.0)
            det = ai_query.detect_brand_mention(res["text"], name, domain)
            if det["mentioned"]:
                per_engine[engine]["mentioned"] += 1
            # General competitor extraction (list/bold, multi-word firms).
            for comp in ai_query.extract_competitors(res["text"], name):
                key = ai_query.normalize_brand_name(comp)
                slot = competitor_counts.setdefault(key, {"name": comp, "mentions": 0})
                slot["mentions"] += 1
            # Plus a direct scan for known DB-cohort firms — catches verified
            # local rivals the generic extractor drops (e.g. single-word brands).
            for uname in universe:
                if ai_query.detect_brand_mention(res["text"], uname, "")["mentioned"]:
                    key = ai_query.normalize_brand_name(uname)
                    slot = competitor_counts.setdefault(key, {"name": uname, "mentions": 0})
                    slot["mentions"] += 1

    platforms_tested = sum(1 for e in engines if per_engine[e]["answered"] > 0)
    platforms_mentioned = sum(1 for e in engines if per_engine[e]["mentioned"] > 0)
    prompts_total = sum(per_engine[e]["answered"] for e in engines)
    prompts_mentioned = sum(per_engine[e]["mentioned"] for e in engines)

    if platforms_tested == 0:
        raise VisibilityProbeError(
            "No live AI responses captured (every engine errored — check "
            "OPENROUTER_API_KEY / credits / network). No check written; the check "
            "reports only genuine AI answers, it does not fabricate a result."
        )

    composite = 0.0
    composite += (platforms_mentioned / platforms_tested) * 70
    if prompts_total:
        composite += (prompts_mentioned / prompts_total) * 30
    composite = round(composite, 1)

    # Per-engine mention rate 0-100 for the report table.
    engine_scores = {
        e: (round(100 * per_engine[e]["mentioned"] / per_engine[e]["answered"], 1)
            if per_engine[e]["answered"] else None)
        for e in engines
    }

    # Rank competitors by mentions, then select. The strict geo-slab gate rejects
    # risky single-word names, but a name we have independently verified as a real
    # firm in our own town+sector cohort is safe to admit even if single-word
    # (e.g. estate-agent brands like Connells). Cohort names lead (universe boost).
    ranked = sorted(competitor_counts.values(), key=lambda c: -c["mentions"])
    raw_names = [c["name"] for c in ranked]
    town = company["town"] or ""
    strict = competitor_gate.valid_competitors(raw_names, brand=name)
    trusted = [
        n for n in raw_names
        if competitor_gate.is_in_universe(n, universe)
        and not competitor_gate.is_self_mention(n, name)
    ]
    top: list[str] = []
    for n in trusted + strict:
        cleaned = competitor_gate.clean_company_name(n, town)
        if cleaned and cleaned not in top:
            top.append(cleaned)
    competitor_named = ", ".join(top[:2]) if top else None

    headline = _headline_finding(company, queries, platforms_mentioned, top)

    row = {
        "run_date": date.today().isoformat(),
        "check_type": check_type,
        "composite_score": composite,
        "platforms_tested": platforms_tested,
        "platforms_mentioned": platforms_mentioned,
        "cost_usd": round(total_cost, 5),
        "headline_finding": headline,
        "competitor_named": competitor_named,
    }
    for e, col in ENGINE_COLUMN.items():
        if e in engine_scores:
            row[col] = engine_scores[e]
    check_id = db.insert_visibility_check(conn, company["id"], **row)

    if company["status"] == "new":
        try:
            db.advance_status(conn, company["id"], "checked", event="mini_check")
        except db.InvalidTransition:
            pass

    # Refresh pitchability now the visibility gap is known.
    from . import pitchability
    pitchability.score_company(conn, db.get_company(conn, company["id"]))

    return {
        "check_id": check_id,
        "composite": composite,
        "engine_scores": engine_scores,
        "platforms_tested": platforms_tested,
        "platforms_mentioned": platforms_mentioned,
        "headline": headline,
        "competitor_named": competitor_named,
        "competitors": top[:5],
        "cost_usd": round(total_cost, 5),
    }


def _join_names(names: list[str]) -> str:
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _headline_finding(company, queries, platforms_mentioned, competitors) -> str:
    """One plain sentence for the opener. Deterministic, no hype, no em dashes."""
    town = company["town"] or "their area"
    phrase = competitor_gate.noun_phrase(company["sector"] or company["name"])
    comp_str = _join_names(competitors[:2]) if competitors else "other firms"
    n = len(queries)
    if platforms_mentioned == 0:
        return (
            f"Asked ChatGPT, Claude, Gemini and Perplexity for {phrase} in {town} "
            f"{n} different ways. {comp_str} came up. {company['name']} did not "
            f"appear once."
        )
    if platforms_mentioned <= 2:
        return (
            f"Checked how {company['name']} shows up when people ask AI for {phrase} "
            f"in {town}. You appeared on {platforms_mentioned} of the engines, "
            f"{comp_str} on more."
        )
    return (
        f"Checked how {company['name']} shows up across ChatGPT, Claude, Gemini "
        f"and Perplexity for {phrase} in {town}. Present on most, but there are "
        f"gaps worth closing."
    )
