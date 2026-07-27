"""Florida DBPR (MyFloridaLicense) real-estate licensee lookup.

Free, public source — the most reliable owner-name path for FL real-estate
agents, who are licensed individuals. Drives the DBPR licensing portal with
Playwright (the portal is a stateful ASP app; a headless browser is far more
robust than reverse-engineering its POST flow).

Public API:
    search_name(last, first="", *, city="")  -> list[Licensee]
    search_org(org, *, city="")              -> list[Licensee]
    real_estate_only(records)                -> list[Licensee]   (filter helper)

Each Licensee is a dict: {name, name_human, license_type, license_no, status, is_active}.
Names come from DBPR as "LAST, FIRST MIDDLE"; `name_human` is "First Middle Last".
"""
from __future__ import annotations

import os
import re
from typing import Any

_BASE = "https://www.myfloridalicense.com/wl11.asp"
_RE_LICENSE = re.compile(r"real estate", re.I)
_ACTIVE = re.compile(r"current|active", re.I)


def _browsers_path() -> None:
    """Point Playwright at the writable shared browser cache if present."""
    shared = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(shared):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = shared


def _humanise(name: str) -> str:
    """'KARR, SUZI ANN' -> 'Suzi Ann Karr'. Title-cased, comma reordered."""
    name = (name or "").strip()
    if "," in name:
        last, rest = name.split(",", 1)
        name = f"{rest.strip()} {last.strip()}"
    return " ".join(w.capitalize() for w in name.split())


def _parse_results(page) -> list[dict[str, Any]]:
    """Parse the DBPR mode=2 results table into structured licensee records.

    Result rows have 4-5 cells: License Type | Name | Name Type | License# | Status.
    """
    out: list[dict[str, Any]] = []
    for tr in page.query_selector_all("tr"):
        cells = [td.inner_text().strip() for td in tr.query_selector_all("td")]
        cells = [c for c in cells if c]
        if len(cells) < 4:
            continue
        lic_type, name = cells[0], cells[1]
        # A real result row has a "LAST, FIRST" name in cell 2 and a licence type in cell 1.
        if "," not in name or len(lic_type) > 60 or "\n" in name:
            continue
        status = cells[-1].split("\n")[0].strip()   # drop the expiry line
        lic_no = next((c for c in cells[2:] if re.search(r"\d", c) and len(c) < 20), "")
        out.append({
            "license_type": lic_type,
            "name": name,
            "name_human": _humanise(name),
            "license_no": lic_no,
            "status": status,
            "is_active": bool(_ACTIVE.search(status)),
        })
    return out


def _run_search(fill: dict[str, str]) -> list[dict[str, Any]]:
    """Drive the two-step DBPR name/org search and return parsed records."""
    _browsers_path()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            pg = b.new_page()
            pg.set_default_timeout(30000)
            # Step 1: pick "Name" search type
            pg.goto(f"{_BASE}?mode=0&SID=", timeout=30000)
            pg.wait_for_load_state("domcontentloaded")
            pg.check("input[value='Name']")
            pg.wait_for_timeout(300)
            pg.query_selector("button[name='SelectSearchType']").click()
            pg.wait_for_load_state("domcontentloaded")
            pg.wait_for_timeout(800)
            # Step 2: fill name/org fields that exist
            for field, value in fill.items():
                if value and pg.query_selector(f"input[name='{field}']"):
                    pg.fill(f"input[name='{field}']", value)
            # Wait for the results-submit button (the ASP form can render slowly).
            btn = None
            for sel in ("button[name='Search1']", "button:has-text('Search')"):
                try:
                    pg.wait_for_selector(sel, timeout=6000)
                    btn = pg.query_selector(sel)
                    if btn:
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not btn:
                return []   # form didn't render — treat as no result, caller retries next run
            btn.click()
            pg.wait_for_load_state("domcontentloaded")
            pg.wait_for_timeout(2200)
            return _parse_results(pg)
        finally:
            b.close()


def search_name(last: str, first: str = "", *, city: str = "") -> list[dict[str, Any]]:
    """Search DBPR by licensee last (+ optional first) name."""
    return _run_search({"LastName": last.strip(), "FirstName": first.strip(),
                        "City": city.strip()})


def search_org(org: str, *, city: str = "") -> list[dict[str, Any]]:
    """Search DBPR by organisation / business name."""
    return _run_search({"OrgName": org.strip(), "City": city.strip()})


def real_estate_only(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only real-estate license types; active ones first."""
    re_recs = [r for r in records if _RE_LICENSE.search(r["license_type"])]
    re_recs.sort(key=lambda r: (not r["is_active"],))
    return re_recs
