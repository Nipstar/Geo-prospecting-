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


def _quotes(conn, limit=3):
    rows = conn.execute(
        "select query, engine, response_text from probe_cache order by id"
    ).fetchall()
    out, seen = [], set()
    for r in rows:
        q = r["query"]
        if q in seen or not r["response_text"]:
            continue
        seen.add(q)
        out.append({
            "query": q,
            "engine": r["engine"],
            "text": textwrap.shorten(r["response_text"].replace("\n", " "), 260),
        })
        if len(out) >= limit:
            break
    return out


def build(status: str | None, limit: int) -> list[str]:
    conn = db.get_connection()
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

    quotes = _quotes(conn)
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
        top_competitor = competitors.split(",")[0].strip() if competitors else ""
        html = tpl.render(
            firm=co["name"], town=co["town"] or "your area",
            website_display=(co["website"] or "").replace("https://", "").replace("http://", "").rstrip("/"),
            rating=co["places_rating"] or "", reviews=co["places_reviews"] or "",
            phone=co["phone"] or "", director=director,
            score=int(round(v["composite_score"])),
            mentioned=v["platforms_mentioned"], tested=v["platforms_tested"],
            sector_word=SECTOR_WORD.get(sector, sector or "firm"),
            engines=engines, competitors=competitors, top_competitor=top_competitor,
            quotes=quotes, slug=slugify(co["name"]), cal_link=CAL_LINK,
        )
        slug = slugify(co["name"])
        page_dir = dist / slug
        page_dir.mkdir(exist_ok=True)
        (page_dir / "index.html").write_text(html)
        made.append(slug)

    # --- full report pages (/report/<slug>) ---
    report_tpl = Template((HERE / "report_template.html").read_text())
    probe_rows = conn.execute("select query, engine, response_text from probe_cache order by id").fetchall()
    by_query: dict[str, dict] = {}
    for r in probe_rows:
        by_query.setdefault(r["query"], {})[r["engine"]] = r["response_text"] or ""
    (dist / "report").mkdir(exist_ok=True)
    for co in rows:
        v = conn.execute("select * from visibility_checks where company_id=? order by id desc limit 1",
                         (co["id"],)).fetchone()
        comp_str = v["competitor_named"] or ""
        comp_list = [c.strip() for c in comp_str.split(",") if c.strip()]
        core = _core_name(co["name"]).lower()
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
            slug=slugify(co["name"]),
        )
        rdir = dist / "report" / slugify(co["name"])
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
