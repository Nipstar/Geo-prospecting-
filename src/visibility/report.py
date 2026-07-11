"""Full AI Visibility Check deliverable: runs probes fresh, scores, and renders
a branded HTML -> PDF into output/reports/{company-slug}.pdf.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config, db
from ..reports import brand
from . import prompts, score

TEMPLATE_DIR = Path(__file__).parent / "templates"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "company").lower()).strip("-")
    return s or "company"


def _addressee(conn, company) -> str:
    people = db.get_people_for_company(conn, company["id"])
    for p in people:
        if p["name"]:
            return p["name"]
    return "The Owner"


def build_full_report(conn, company) -> dict:
    """Run a fresh full check and render the PDF. Returns paths and scores."""
    queries = prompts.build_queries(company)
    engines = ["chatgpt", "perplexity", "ai_overview"]
    result = score.score_company(conn, company, queries=queries, engines=engines)

    # Rewrite the just-inserted mini row to check_type='full' + store path.
    check = db.latest_check(conn, company["id"])
    engine_table = [
        {"engine": "ChatGPT", "score": check["chatgpt_score"] or 0},
        {"engine": "Perplexity", "score": check["perplexity_score"] or 0},
        {"engine": "Google AI Overview", "score": check["ai_overview_score"] or 0},
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
        engines=["ChatGPT", "Perplexity", "Google AI Overview"],
        engine_table=engine_table,
        competitors=result["competitors"],
        sector_word=sector_word,
    )

    out_path = config.REPORTS_DIR / f"{slugify(company['name'])}.pdf"
    _render_pdf(html, out_path)

    db.update_check(conn, check["id"], check_type="full", report_path=str(out_path))
    return {"report_path": str(out_path), **result}


def _render_pdf(html: str, out_path: Path) -> None:
    from weasyprint import HTML

    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(out_path))


def _has(row, key) -> bool:
    try:
        return row[key] is not None
    except (IndexError, KeyError):
        return False
