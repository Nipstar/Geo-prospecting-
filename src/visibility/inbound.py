"""Inbound AI Visibility Check (the landing-page path).

A visitor submits the free-check form; WF9 (n8n) posts it to the /scan service
which calls run_inbound(). Unlike the outbound CLI, there is no prospect record
yet and no trade — so this classifies the trade from the site, then reuses the
exact same engine as `cli check full`: enrich (classify) → build_full_report
(probes + score + PDF). Inbound leads land in the same pipeline.db as outbound
(status 'checked', source 'landing'), so one CRM view covers both channels.

Dedup: if the domain already has a full report from the last 7 days, the cached
PDF is returned instead of burning fresh probe spend.
"""
from __future__ import annotations

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from .. import config, db
from ..ingest import enrich
from ..ingest.util import domain_of
from . import probes, prompts, report, score

DEDUP_DAYS = 7
PREWARM_WORKERS = 6

# Rough UK postcode shape (full or partial-with-inward). Used only to decide
# whether a location field needs postcode->town resolution, not for validation.
_UK_POSTCODE = re.compile(r"^[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}$")


def _normalize_location(location: str) -> tuple[str, str | None]:
    """Resolve the location field to (town, county) for the probe prompts.

    Users type a postcode, which makes prompts read 'best plumber in SP10 2PX' —
    geographic nonsense that scores a false 0. If the input looks like a UK
    postcode, resolve it via postcodes.io (free, no key): `parish` is the town
    for a real place (Andover), `admin_district` (the council, e.g. Test Valley)
    the fallback for unparished cities; `admin_county` or `region` gives the
    county. Non-postcode input is returned as the town, county None.
    # ponytail: postcodes.io only; if it's down we pass the input through rather
    # than block the scan — worst case is the same as today.
    """
    loc = (location or "").strip()
    if not loc or not _UK_POSTCODE.match(loc):
        return loc, None
    try:
        r = requests.get(f"https://api.postcodes.io/postcodes/{loc}", timeout=8)
        if r.ok:
            res = r.json().get("result") or {}
            town = (res.get("parish") or res.get("admin_district") or "").strip()
            county = (res.get("admin_county") or res.get("region") or "").strip() or None
            if town:
                return town, county
    except requests.RequestException:
        pass
    return loc, None


def _prewarm_probes(company) -> None:
    """Fetch all (engine, query) probes concurrently so the sequential
    score_company loop reads them from probe_cache instead of making 25 slow
    network calls in series. Keeps a cold inbound scan under Cloudflare's 120s
    proxy timeout. Each thread uses its own SQLite connection (writes serialise
    on the file lock); a failed prefetch just falls back to a live call in
    score_company, so this only ever speeds things up."""
    queries = prompts.build_queries(company)
    engines = config.CHECK_ENGINES
    jobs = [(e, q) for e in engines for q in queries]

    def _one(job) -> None:
        engine, query = job
        c = db.get_connection()
        try:
            c.execute("PRAGMA busy_timeout=8000")
            probes.run_probe(c, engine, query)
        except Exception:  # noqa: BLE001
            pass
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=PREWARM_WORKERS) as ex:
        list(ex.map(_one, jobs))


def _within_days(run_date: str | None, days: int) -> bool:
    if not run_date:
        return False
    try:
        d = datetime.strptime(run_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - d) <= timedelta(days=days)


def _pdf_b64(path_str: str | None) -> str:
    if not path_str:
        return ""
    p = Path(path_str)
    if not p.exists():
        return ""
    return base64.b64encode(p.read_bytes()).decode("ascii")


def _result_payload(company, result: dict, *, cached: bool, pdf_b64: str,
                    operator_pdf_b64: str = "") -> dict:
    tested = result.get("platforms_tested") or 0
    mentioned = result.get("platforms_mentioned") or 0
    competitors = result.get("competitors") or []
    return {
        "status": "done",
        "cached": cached,
        "company_id": company["id"],
        "business_name": company["name"],
        "domain": domain_of(company["website"]),
        "town": company["town"] or "",
        "trade": (company["sector"] or company["primary_service"] or "").strip(),
        "visibility_score": result.get("composite"),
        "platforms_tested": tested,
        "platforms_mentioned": mentioned,
        "mention_rate": round(mentioned / tested, 2) if tested else 0,
        "top_competitor": competitors[0] if competitors else None,
        "competitors": competitors,
        "competitor_named": result.get("competitor_named"),
        "headline": result.get("headline"),
        "report_path": result.get("report_path"),
        "pdf_b64": pdf_b64,
        "operator_pdf_b64": operator_pdf_b64,
        "cost_usd": result.get("cost_usd"),
    }


def run_inbound(company_name: str, website: str, location: str = "",
                email: str = "", name: str = "", phone: str = "", trade: str = "") -> dict:
    """Classify, scan, score and render a PDF for an inbound lead. Returns a flat
    dict for n8n (WF9) to hand to Brevo. Raises score.VisibilityProbeError if
    every engine errors (never fabricates a 0/100). If `trade` is given (from the
    form) it drives the probe prompts and classification is skipped."""
    company_name = (company_name or "").strip() or (domain_of(website) or "This business")
    trade = (trade or "").strip()
    location, county = _normalize_location(location)  # postcode -> (town, county); disambiguates prompts
    conn = db.get_connection()
    try:
        db.run_migrations(conn)
        domain = domain_of(website)

        # Dedup: same domain checked in the last 7 days → return the cached PDF.
        existing = db.find_company_by_domain(conn, domain) if domain else None
        if existing:
            chk = db.latest_check(conn, existing["id"])
            if chk and chk["report_path"] and _within_days(chk["run_date"], DEDUP_DAYS):
                cached_result = {
                    "composite": chk["composite_score"],
                    "platforms_tested": chk["platforms_tested"],
                    "platforms_mentioned": chk["platforms_mentioned"],
                    "competitors": [chk["competitor_named"]] if chk["competitor_named"] else [],
                    "competitor_named": chk["competitor_named"],
                    "headline": chk["headline_finding"],
                    "report_path": chk["report_path"],
                    "cost_usd": chk["cost_usd"],
                }
                return _result_payload(
                    db.get_company(conn, existing["id"]), cached_result,
                    cached=True, pdf_b64=_pdf_b64(chk["report_path"]),
                    operator_pdf_b64=_pdf_b64(report.operator_report_path(chk["report_path"])),
                )
            company_id = existing["id"]
            fields = {"phone": phone or existing["phone"],
                      "town": location or existing["town"], "source": "landing"}
            if county:
                fields["county"] = county
            if trade:  # form-provided trade wins over any prior classification
                fields["sector"] = trade
                fields["primary_service"] = trade
            db.update_company(conn, company_id, **fields)
        else:
            company_id = db.insert_company(
                conn, name=company_name, website=website or None,
                town=location or None, county=county or None, phone=phone or None,
                sector=trade or None, primary_service=trade or None,
                source="landing", status="new",
            )

        # Only classify the trade from the homepage when the form did not give one.
        # If the site is unreachable the check still runs on the town + trade.
        if not trade:
            try:
                enrich.enrich_company(conn, db.get_company(conn, company_id))
            except Exception:  # noqa: BLE001
                pass

        company = db.get_company(conn, company_id)
        _prewarm_probes(company)  # parallel prefetch → keeps the scan under ~30s
        result = report.build_full_report(conn, company)  # probes (cached) + score + PDF
        return _result_payload(
            db.get_company(conn, company_id), result,
            cached=False, pdf_b64=_pdf_b64(result["report_path"]),
            operator_pdf_b64=_pdf_b64(result.get("operator_report_path")),
        )
    finally:
        conn.close()


# ── Self-check (offline — monkeypatches network/render) ────────────────────
def _demo() -> None:
    import tempfile

    # location normalizer (offline: passthrough + postcode-shape detection only)
    assert _normalize_location("Andover") == ("Andover", None)
    assert _normalize_location("  ") == ("", None)
    assert _UK_POSTCODE.match("SP10 2PX") and not _UK_POSTCODE.match("Andover")

    from . import report as report_mod

    tmp = Path(tempfile.mkdtemp()) / "check.db"
    conn = db.get_connection(tmp)
    db.run_migrations(conn)
    conn.close()
    # Point the module at the temp DB for the whole run.
    from .. import config
    config.DB_PATH = tmp

    calls = {"n": 0}
    pdf = Path(tempfile.mkdtemp()) / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4 self-check")

    def fake_report(conn, company):
        # Stand in for probes+score+render: write a real check row (so dedup can
        # find it) and a stub PDF, then return the same shape as the real fn.
        calls["n"] += 1
        db.insert_visibility_check(
            conn, company["id"], run_date=date.today().isoformat(), check_type="full",
            composite_score=25.0, platforms_tested=4, platforms_mentioned=1,
            competitor_named="Beta Plumbing", headline_finding="Beta Plumbing came up.",
            report_path=str(pdf), cost_usd=0.02,
        )
        return {
            "report_path": str(pdf),
            "composite": 25.0,
            "platforms_tested": 4,
            "platforms_mentioned": 1,
            "competitors": ["Beta Plumbing", "Gamma Heating"],
            "competitor_named": "Beta Plumbing",
            "headline": "Beta Plumbing came up. You did not.",
            "cost_usd": 0.02,
        }

    def fake_enrich(conn, company):
        db.update_company(conn, company["id"], sector="plumbing")
        return {"sector": "plumbing", "primary_service": ""}

    orig_report, orig_enrich = report_mod.build_full_report, enrich.enrich_company
    orig_prewarm = globals()["_prewarm_probes"]
    report_mod.build_full_report = fake_report
    enrich.enrich_company = fake_enrich
    globals()["_prewarm_probes"] = lambda company: None  # no network in the self-check
    try:
        r1 = run_inbound("Dave Plumbing", "daveplumbing.co.uk", "Basingstoke")
        assert r1["status"] == "done" and r1["cached"] is False, r1
        assert r1["visibility_score"] == 25.0, r1
        assert r1["mention_rate"] == 0.25, r1
        assert r1["top_competitor"] == "Beta Plumbing", r1
        assert r1["trade"] == "plumbing", r1
        # Second submit for the same domain skips the probe run (dedup ≤7d).
        before = calls["n"]
        r2 = run_inbound("Dave Plumbing", "daveplumbing.co.uk", "Basingstoke")
        assert r2["cached"] is True, r2
        assert calls["n"] == before, "dedup should not re-run the report"
    finally:
        report_mod.build_full_report = orig_report
        enrich.enrich_company = orig_enrich
        globals()["_prewarm_probes"] = orig_prewarm
    print("inbound self-check passed")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        _demo()
