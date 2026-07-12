"""Full AI Visibility Check deliverable: runs probes fresh, scores, and renders
a branded HTML -> PDF into output/reports/{company-slug}.pdf.
"""
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config, db
from ..reports import brand
from . import ai_query, prompts, score

TEMPLATE_DIR = Path(__file__).parent / "templates"

_KEY2LABEL = {
    "ChatGPT": "ChatGPT", "Claude": "Claude", "Gemini": "Gemini",
    "Perplexity": "Perplexity", "ai_overview": "Google AI Overview",
}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "company").lower()).strip("-")
    return s or "company"


def operator_report_path(client_report_path: str | None) -> str | None:
    """The operator PDF sits beside the client PDF: {slug}-operator.pdf. Derived
    (not stored) so cached hits can find it without a schema change."""
    if not client_report_path:
        return None
    p = Path(client_report_path)
    return str(p.with_name(f"{p.stem}-operator{p.suffix}"))


def _highlight(text: str, *terms: str) -> str:
    """HTML-escape then wrap brand/domain hits in <mark> (case-insensitive)."""
    esc = html.escape(text or "")
    for term in terms:
        term = (term or "").strip()
        if term:
            esc = re.sub(re.escape(html.escape(term)),
                         lambda m: f"<mark>{m.group(0)}</mark>", esc, flags=re.IGNORECASE)
    return esc


def build_operator_report(conn, company, queries, *, cost=0.0, cached=False, competitors=None) -> str:
    """Internal all-data report: every engine's full answer per question, brand
    highlighted, plus the score math. Rendered to {slug}-operator.pdf."""
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT engine, query, response_text FROM probe_cache "
        "WHERE run_date=? AND query IN (%s)" % ",".join("?" * len(queries)),
        [today, *queries],
    ).fetchall()
    resp = {(_KEY2LABEL.get(r["engine"], r["engine"]), r["query"]): (r["response_text"] or "")
            for r in rows}
    name, url = company["name"], company["website"]
    domain = urlparse(url if url and "//" in url else f"//{url or ''}").netloc.replace("www.", "")
    engines = list(_KEY2LABEL.values())

    grid, detail = {e: {} for e in engines}, []
    for e in engines:
        items = []
        for i, q in enumerate(queries, 1):
            txt = resp.get((e, q))  # None => engine never answered this query
            hit = bool(ai_query.detect_brand_mention(txt, name, url)["mentioned"]) if txt else None
            grid[e][q] = hit
            items.append({"n": i, "query": q, "mentioned": bool(hit),
                          "text_html": _highlight(txt, name, domain) if txt else "(no answer returned)"})
        detail.append({"engine": e, "answers": items})

    platforms_tested = sum(1 for e in engines if any(grid[e][q] is not None for q in queries))
    platforms_mentioned = sum(1 for e in engines if any(grid[e][q] for q in queries))
    prompts_total = sum(1 for e in engines for q in queries if grid[e][q] is not None)
    prompts_mentioned = sum(1 for e in engines for q in queries if grid[e][q])
    denom_p = platforms_tested or 1
    denom_q = prompts_total or 1
    composite = round((platforms_mentioned / denom_p) * 70 + (prompts_mentioned / denom_q) * 30, 1)

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                      autoescape=select_autoescape(["html"]))
    html_out = env.get_template("operator_report.html.j2").render(
        font_faces=brand.font_face_css(), base_css=brand.base_css(), tokens=brand.TOKENS,
        company=company, trade=(company["primary_service"] if _has(company, "primary_service") else None),
        composite=composite, run_date=date.today().strftime("%d %B %Y"),
        cost=cost, cached=cached, engines=engines, queries=queries, grid=grid, detail=detail,
        platforms_tested=platforms_tested, platforms_mentioned=platforms_mentioned,
        prompts_total=prompts_total, prompts_mentioned=prompts_mentioned,
        competitors=competitors or [],
    )
    out_path = operator_report_path(str(config.REPORTS_DIR / f"{slugify(company['name'])}.pdf"))
    _render_pdf(html_out, Path(out_path))
    return out_path


def _addressee(conn, company) -> str:
    people = db.get_people_for_company(conn, company["id"])
    for p in people:
        if p["name"]:
            return p["name"]
    return "The Owner"


def build_full_report(conn, company) -> dict:
    """Run a fresh full check and render the PDF. Returns paths and scores."""
    queries = prompts.build_queries(company)
    result = score.score_company(
        conn, company, queries=queries, engines=config.CHECK_ENGINES, check_type="full"
    )

    # The just-inserted row carries per-engine scores; build the report table.
    check = db.latest_check(conn, company["id"])
    engine_labels = [
        ("ChatGPT", "chatgpt_score"),
        ("Claude", "claude_score"),
        ("Gemini", "gemini_score"),
        ("Perplexity", "perplexity_score"),
        ("Google AI Overview", "ai_overview_score"),
    ]
    engine_table = [
        {"engine": label, "score": check[col], "tested": check[col] is not None}
        for label, col in engine_labels
    ]
    sector_word = (company["primary_service"] if _has(company, "primary_service") else None) \
        or (company["sector"] or "local business")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(
        font_faces=brand.font_face_css(),
        base_css=brand.base_css(),
        tokens=brand.TOKENS,
        company=company,
        composite=result["composite"],
        headline=result["headline"],
        run_date=date.today().strftime("%d %B %Y"),
        addressee=_addressee(conn, company),
        queries=queries,
        engines=["ChatGPT", "Claude", "Gemini", "Perplexity", "Google AI Overview"],
        engine_table=engine_table,
        competitors=result["competitors"],
        sector_word=sector_word,
    )

    out_path = config.REPORTS_DIR / f"{slugify(company['name'])}.pdf"
    _render_pdf(html, out_path)

    # Internal all-data report (every engine's full answer) beside the client PDF.
    op_path = build_operator_report(
        conn, company, queries,
        cost=result.get("cost_usd", 0.0), cached=False, competitors=result.get("competitors"),
    )

    db.update_check(conn, check["id"], check_type="full", report_path=str(out_path))
    return {"report_path": str(out_path), "operator_report_path": op_path, **result}


def _render_pdf(html: str, out_path: Path) -> None:
    from weasyprint import HTML

    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(out_path))


def _has(row, key) -> bool:
    try:
        return row[key] is not None
    except (IndexError, KeyError):
        return False


if __name__ == "__main__":  # self-check for the pure helpers
    assert operator_report_path("/x/reports/antek.pdf") == "/x/reports/antek-operator.pdf"
    assert operator_report_path(None) is None
    h = _highlight("Call Antek Automation today", "Antek Automation")
    assert "<mark>Antek Automation</mark>" in h
    assert "<script>" not in _highlight("<script>x</script>", "x")  # escaped
    print("report self-check ok")
