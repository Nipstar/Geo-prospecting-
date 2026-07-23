"""Generate static claim pages (one per prospect) into claim-site/dist/<slug>/index.html.

Reads the pipeline DB, renders claim-site/template.html per company that has a
visibility check. Deploy dist/ + functions/ to Cloudflare Pages.

Usage:
  uv run python claim-site/build.py                # all checked companies
  uv run python claim-site/build.py --status checked --limit 20
  CAL_LINK=andy/15min uv run python claim-site/build.py
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import html as _html  # noqa: E402
import re as _re  # noqa: E402
from jinja2 import Template  # noqa: E402
from markupsafe import Markup  # noqa: E402
from src import db  # noqa: E402
from src.visibility import prompts  # noqa: E402
from src.config import FREE_CHECK_QUERIES  # noqa: E402
from src.visibility.report import slugify  # noqa: E402

ENGINES = [
    ("chatgpt_score", "ChatGPT"), ("claude_score", "Claude"),
    ("gemini_score", "Gemini"), ("perplexity_score", "Perplexity"),
    ("ai_overview_score", "Google AI Overview"),
]
ENGINE_LABEL = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini",
                "perplexity": "Perplexity", "ai_overview": "Google AI Overview"}
CAL_LINK = os.getenv("CAL_LINK", "antek-automation/30min")
SECTOR_WORD = {"solicitors": "solicitor", "accountants": "accountant"}
_STOP = {"ltd", "limited", "llp", "solicitors", "solicitor", "law", "legal",
         "the", "and", "&", "co", "associates", "partners"}


def _core_name(name: str) -> str:
    toks = [t for t in _re.split(r"\s+", name) if t.lower().strip(".,&") not in _STOP]
    return " ".join(toks).strip() or name


def _highlight(text: str, firm: str, competitors: list[str]) -> Markup:
    esc = _html.escape(text or "")
    for comp in competitors:
        comp = comp.strip()
        if len(comp) < 3:
            continue
        esc = _re.sub(_re.escape(_html.escape(comp)),
                      lambda m: f"<mark>{m.group(0)}</mark>", esc, flags=_re.I)
    core = _core_name(firm)
    for token in {firm, core}:
        token = token.strip()
        if len(token) < 3:
            continue
        esc = _re.sub(_re.escape(_html.escape(token)),
                      lambda m: f'<mark class="you">{m.group(0)}</mark>', esc, flags=_re.I)
    return Markup(esc)


# Directories / portals / awards / generic words that look like names but aren't
# a local competitor. Kept out of the rival chips.
_CHIP_STOP = {
    "In", "The", "However", "County", "Broker", "Google", "Best", "Top", "Real",
    "Estate", "Agent", "Agents", "AI", "These", "Their", "This", "That", "Group",
    "Owner", "Recognized", "A", "An", "Zillow", "Realtor", "RealTrends", "HomeLight",
    "Redfin", "Trulia", "Yelp", "Facebook", "Forbes", "Verified", "Award", "Awards",
    "Magazine", "Homes", "Property", "Properties", "Realty", "MLS", "Team", "Teams",
    "Note", "Choosing", "Some", "Several", "Many", "For", "When", "If", "You", "Your",
    # phrase-fragment words that are never part of a firm/agent name
    "According", "Context", "Important", "So", "Who", "What", "Here", "Overall",
    "Based", "Key", "Takeaway", "Takeaways", "Leader", "Volume", "High", "Consider",
    "Depends", "Overview", "Summary", "Also", "While", "Because", "They", "It",
    "Its", "Known", "Highly", "Rated", "Reviews", "Review", "Star", "Stars", "With",
    "And", "Or", "But", "As", "Of", "To", "Is", "Are", "Was", "Well", "Very",
    "Quick", "Question", "Questions", "Wesley", "Chapel", "Palm", "Harbor",
    "Brandon", "Riverview", "Lutz", "Lakes", "Miami", "Northdale", "Sarasota",
    "Bradenton", "Dunedin", "Largo", "Seminole", "Jacksonville", "Ocala",
}
_CHIP_RE = __import__("re").compile(r"[A-Z][a-zA-Z&'.\-]+(?:\s+[A-Z][a-zA-Z&'.\-]+){1,3}")


def _rival_chips(text, firm, town, limit=5):
    """Firm/agent names AI surfaced, pulled from the answer text. Tightened to
    skip directories/awards/geo/generic words. Best-effort; for the intrigue row."""
    core = _core_name(firm).lower()
    tl = (town or "").lower()
    chips = []
    for m in _CHIP_RE.findall(text or ""):
        c = m.strip(" .,-")
        low = c.lower()
        words = c.split()
        if len(c) < 6 or core and core in low or tl and tl in low:
            continue
        if any(w in _CHIP_STOP for w in words):
            continue
        if any(w.lower() in ("florida", "fl", "tampa", "petersburg", "clearwater",
                             "pinellas", "gulf", "coast", "bay") for w in words):
            continue
        if c not in chips:
            chips.append(c)
    return chips[:limit]


def _quotes(conn, queries, firm="", comps=None, limit=3):
    """Self-verify quotes for THIS company only. Competitor names are
    highlighted and each quote is flagged with whether the firm was named, so
    the 'AI recommended them, not you' gut-punch is visible, not buried."""
    comps = comps or []
    core = _core_name(firm).lower()
    out = []
    for q in queries:
        rows = conn.execute(
            "select engine, response_text from probe_cache "
            "where query=? and response_text is not null and response_text<>''", (q,),
        ).fetchall()
        # Pick the most substantive answer for this query: a real AI answer that
        # names firms, not an empty NO_AI_OVERVIEW placeholder. Longest wins.
        best = None
        for r in rows:
            txt = (r["response_text"] or "").strip()
            if not txt or txt.startswith("NO_AI_OVERVIEW"):
                continue
            if best is None or len(txt) > len(best[1]):
                best = (r["engine"], txt)
        if best is None:
            continue
        engine, raw = best[0], best[1].replace("\n", " ")
        appears = bool(core) and core in raw.lower()
        short = textwrap.shorten(raw, 480)
        out.append({
            "query": q,
            "engine": ENGINE_LABEL.get(engine, engine),
            "text": _highlight(short, firm, comps),
            "appears": appears,
            "raw": raw,
        })
        if len(out) >= limit:
            break
    return out


def build(status: str | None, limit: int) -> list[str]:
    conn = db.get_connection()
    db.ensure_slugs(conn)
    tpl = Template((HERE / "template.html").read_text())
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    # copy static assets (real logo) to the site root
    logo = HERE / "logo.svg"
    if logo.exists():
        (dist / "logo.svg").write_bytes(logo.read_bytes())

    where = "EXISTS (SELECT 1 FROM visibility_checks v WHERE v.company_id=c.id)"
    params: list = []
    if status:
        where += " AND c.status=?"
        params.append(status)
    rows = conn.execute(
        f"SELECT c.* FROM companies c WHERE {where} ORDER BY c.id LIMIT ?",
        (*params, limit),
    ).fetchall()

    made = []
    for co in rows:
        v = conn.execute(
            "select * from visibility_checks where company_id=? order by id desc limit 1",
            (co["id"],),
        ).fetchone()
        engines = [{"label": lbl, "score": int(v[col] or 0), "appears": (v[col] or 0) > 0}
                   for col, lbl in ENGINES]
        sector = (co["sector"] or "").lower()
        director = conn.execute(
            "select name from people where company_id=? and person_source='companies_house_officer' "
            "and name is not null order by id limit 1", (co["id"],)).fetchone()
        director = director["name"] if director else ""
        competitors = v["competitor_named"] or ""
        comp_list = [c.strip() for c in competitors.split(",") if c.strip()]
        top_competitor = comp_list[0] if comp_list else ""
        quotes = _quotes(conn, prompts.build_queries(co, limit=FREE_CHECK_QUERIES),
                         firm=co["name"], comps=comp_list)
        rivals_named = sum(1 for q in quotes if not q["appears"])
        sw = SECTOR_WORD.get(sector, sector or "firm")
        rival_chips = _rival_chips(" ".join(q["raw"] for q in quotes),
                                   co["name"], co["town"] or "")
        town_d = co["town"] or "your area"
        mentioned = v["platforms_mentioned"] or 0
        # Stakes line — frames the score as lost enquiries (funnel: shows WHAT, not HOW).
        if mentioned == 0:
            stakes = (f"When someone in {town_d} asks ChatGPT, Gemini or Perplexity for a "
                      f"{sw}, {co['name']} does not come up at all.")
        else:
            stakes = (f"{co['name']} shows up on {mentioned} of {v['platforms_tested']} AI "
                      f"engines — but that still leaves gaps a competitor is filling.")
        if top_competitor:
            stakes += f" {top_competitor} appears where you don't, and those enquiries go to them."
        # Gap checklist — WHAT's wrong (absent engines + competitor), never HOW to fix.
        gaps = [f"Invisible on {e['label']} when buyers ask for a {sw}"
                for e in engines if not e["appears"]][:3]
        if top_competitor:
            gaps.append(f"{top_competitor} is the firm AI names instead of you")
        html = tpl.render(
            firm=co["name"], town=town_d,
            website_display=(co["website"] or "").replace("https://", "").replace("http://", "").rstrip("/"),
            rating=co["places_rating"] or "", reviews=co["places_reviews"] or "",
            phone=co["phone"] or "", director=director,
            score=int(round(v["composite_score"])),
            mentioned=mentioned, tested=v["platforms_tested"],
            sector_word=sw, stakes=stakes, gaps=gaps, rivals_named=rivals_named,
            preview=True, rival_chips=rival_chips,
            engines=engines, competitors=competitors, top_competitor=top_competitor,
            quotes=quotes, slug=(co["slug"] or slugify(co["name"])), cal_link=CAL_LINK,
        )
        slug = co["slug"] or slugify(co["name"])
        page_dir = dist / slug
        page_dir.mkdir(exist_ok=True)
        (page_dir / "index.html").write_text(html)
        made.append(slug)

    # --- full report pages (/report/<slug>) ---
    report_tpl = Template((HERE / "report_template.html").read_text())
    (dist / "report").mkdir(exist_ok=True)
    for co in rows:
        v = conn.execute("select * from visibility_checks where company_id=? order by id desc limit 1",
                         (co["id"],)).fetchone()
        comp_str = v["competitor_named"] or ""
        comp_list = [c.strip() for c in comp_str.split(",") if c.strip()]
        core = _core_name(co["name"]).lower()
        # THIS company's queries only — look each up in the probe cache.
        by_query: dict[str, dict] = {}
        for q in prompts.build_queries(co):
            crows = conn.execute(
                "select engine, response_text from probe_cache where query=?", (q,)
            ).fetchall()
            if crows:
                by_query[q] = {cr["engine"]: cr["response_text"] or "" for cr in crows}
        questions = []
        for q, engs in by_query.items():
            elist = []
            for ekey, resp in engs.items():
                appears = bool(resp) and (core in resp.lower() or co["name"].lower() in resp.lower())
                elist.append({"label": ENGINE_LABEL.get(ekey, ekey),
                              "appears": appears,
                              "html": _highlight(resp, co["name"], comp_list)})
            questions.append({"query": q, "engines": elist})
        sector = (co["sector"] or "").lower()
        rhtml = report_tpl.render(
            firm=co["name"], town=co["town"] or "your area",
            website_display=(co["website"] or "").replace("https://", "").replace("http://", "").rstrip("/"),
            rating=co["places_rating"] or "", reviews=co["places_reviews"] or "",
            score=int(round(v["composite_score"])), mentioned=v["platforms_mentioned"], tested=v["platforms_tested"],
            sector_word=SECTOR_WORD.get(sector, sector or "firm"),
            competitors=comp_str or "other firms", questions=questions, cal_link=CAL_LINK,
            slug=(co["slug"] or slugify(co["name"])),
        )
        rdir = dist / "report" / (co["slug"] or slugify(co["name"]))
        rdir.mkdir(exist_ok=True)
        (rdir / "index.html").write_text(rhtml)

    conn.close()
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default=None)
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()
    made = build(a.status, a.limit)
    print(f"Built {len(made)} claim pages -> claim-site/dist/")
    for s in made:
        print(f"  /{s}")
    print(f"\nCal link: {CAL_LINK}  (set CAL_LINK env to change)")
