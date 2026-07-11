"""Homepage enrichment: classify sector + extract the primary service in plain
words using the cheap 'classify' model."""
from __future__ import annotations

import re
from typing import Any

import requests

from .. import db, llm

UA = "Mozilla/5.0 (compatible; AntekOutreachBot/1.0; +https://antekautomation.com)"

SYSTEM = (
    "You classify UK small-business websites. Given page text, return strict "
    "JSON: {\"sector\": \"...\", \"primary_service\": \"...\"}. Sector is a short "
    "category like 'estate agents', 'solicitors', 'accountants', 'plumbing'. "
    "primary_service is the single main service in plain British English, e.g. "
    "'residential lettings' or 'conveyancing'. No hype words."
)


def _fetch_homepage(url: str) -> str:
    if "://" not in url:
        url = "https://" + url
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=10)
    resp.raise_for_status()
    html = resp.text
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
    h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    parts = []
    if title:
        parts.append("TITLE: " + re.sub(r"\s+", " ", title.group(1)).strip())
    if desc:
        parts.append("DESCRIPTION: " + re.sub(r"\s+", " ", desc.group(1)).strip())
    for h in h1s[:5]:
        parts.append("H1: " + re.sub(r"<[^>]+>", " ", h).strip())
    return "\n".join(parts)[:2000]


def enrich_company(conn, company: Any) -> dict[str, str] | None:
    """Classify one company from its homepage. Returns the fields written."""
    website = company["website"]
    if not website:
        return None
    text = _fetch_homepage(website)
    if not text.strip():
        return None
    raw = llm.complete("classify", system=SYSTEM, user=text, temperature=0.2, max_tokens=200, json_mode=True)
    data = llm.parse_json(raw)
    fields = {
        "sector": (data.get("sector") or company["sector"] or "").strip() or None,
        "primary_service": (data.get("primary_service") or "").strip() or None,
    }
    db.update_company(conn, company["id"], **{k: v for k, v in fields.items() if v})
    return {"sector": fields["sector"] or "", "primary_service": fields["primary_service"] or ""}


def run_enrich(limit: int = 20, dry_run: bool = False) -> dict[str, int]:
    conn = db.get_connection()
    done = failed = 0
    try:
        rows = conn.execute(
            "SELECT * FROM companies WHERE website IS NOT NULL AND website != '' "
            "AND (sector IS NULL OR sector = '') ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        for company in rows:
            try:
                if dry_run:
                    print(f"  would enrich: {company['name']} ({company['website']})")
                    done += 1
                    continue
                result = enrich_company(conn, company)
                if result:
                    print(f"  {company['name']}: {result['sector']} / {result['primary_service']}")
                    done += 1
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {company['name']}: {exc}")
                failed += 1
    finally:
        conn.close()
    return {"enriched": done, "failed": failed}
