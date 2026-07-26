"""Send letters via PostGrid (server-side render — no local PDF).

For each eligible company: parse its mailing address, render the branded letter
HTML (address blocks suppressed — PostGrid stamps its own address page), and
create a PostGrid letter. Records the order id + status back on the `letters`
row. Test key => sandbox (nothing mailed); the PDF preview URL is still returned.

Design (see repo memory):
  - metadata {companyId, campaign} + skip companies already posted for a campaign
  - skip contacts with no postcode
  - country-valid size (GB -> a4, else us_letter); mailing_class defaults to
    first_class (auto-picks the carrier)
  - idempotency key per letter so retries never double-send
  - 429/503 -> exponential backoff
Individual sends (not the batch endpoint) so mixed countries are fine.
"""
from __future__ import annotations

import re
import time
import uuid

from .. import config, db
from ..clients import postgrid
from . import letter as letter_mod

_UK_PC = re.compile(r"[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$", re.I)
_US_STATE_ZIP = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?$")

# Abbreviations that str.title() would mangle (e.g. "Llp" → "LLP")
_ADDR_ABBRS = ("LLP", "PLC", "Ltd", "NHS", "UK", "GB", "LLC", "LTD")


def _title_addr(s: str) -> str:
    """Title-case a human-facing address component.

    Companies House stores addresses in ALL CAPS; this converts them to
    readable title case while restoring known abbreviations.
    Postcodes are left unchanged (callers should not pass them here).
    """
    if not s:
        return s
    result = s.title()
    # str.title() capitalises letters after digits: "1St" → "1st"
    result = re.sub(r'(\d)([A-Z])', lambda m: m.group(1) + m.group(2).lower(), result)
    for abbr in _ADDR_ABBRS:
        # Replace the title()-mangled form with the correct abbreviation
        result = re.sub(r'\b' + abbr.title() + r'\b', abbr, result)
    return result


def _ensure_cols(conn) -> None:
    for col in ("postgrid_id TEXT", "postgrid_status TEXT"):
        name = col.split()[0]
        try:
            conn.execute(f"ALTER TABLE letters ADD COLUMN {col}")
        except Exception:  # noqa: BLE001 — already exists
            pass
        _ = name
    conn.commit()


def _parse_address(reg: str | None) -> dict | None:
    """Parse a registered/Places address string into PostGrid contact fields."""
    parts = [p.strip() for p in (reg or "").split(",") if p.strip()]
    if len(parts) < 2:
        return None
    last = parts[-1]

    m = _US_STATE_ZIP.search(last)
    if m:  # US: "..., City, ST 12345"
        state, postal = m.group(1), m.group(2)
        pre = last[: m.start()].strip()
        city = pre or (parts[-2] if len(parts) >= 2 else "")
        body = parts[:-1] if pre else parts[:-2]
        line1 = ", ".join(body) or parts[0]
        return {"line1": _title_addr(line1), "city": _title_addr(city),
                "state": state, "postal": postal, "country": "US"}

    pc = _UK_PC.search(last)
    if pc:  # UK: "..., Town, POSTCODE"  or  "..., Town POSTCODE"
        postal = pc.group(0).upper()  # postcodes always uppercase
        pre = last[: pc.start()].strip().rstrip(",")
        if pre:
            city, body = pre, parts[:-1]
        else:
            city, body = (parts[-2] if len(parts) >= 2 else ""), parts[:-2]
        line1 = ", ".join(body) or parts[0]
        return {"line1": _title_addr(line1), "city": _title_addr(city),
                "state": "", "postal": postal, "country": "GB"}
    return None


def _contact(co, addr: dict) -> dict:
    out = dict(addr)
    # Title-case company name only if it is ALL CAPS (Companies House style)
    name = co["name"]
    out["company"] = _title_addr(name) if name == name.upper() else name
    return out


def run_postgrid_send(limit: int = 25, campaign: str = "geo-1",
                      mailing_class: str | None = None,
                      test: bool | None = None, dry_run: bool = False,
                      sleep: float = 0.4) -> dict:
    conn = db.get_connection()
    _ensure_cols(conn)

    # Eligible: has an address, has a visibility check (letter needs it), and not
    # already posted for this campaign.
    rows = conn.execute(
        """
        SELECT c.* FROM companies c
        WHERE c.registered_address IS NOT NULL AND c.registered_address <> ''
          AND EXISTS (SELECT 1 FROM visibility_checks v WHERE v.company_id = c.id)
          AND NOT EXISTS (
            SELECT 1 FROM letters l
            WHERE l.company_id = c.id AND l.postgrid_id IS NOT NULL
              AND l.status = 'posted')
        ORDER BY c.id LIMIT ?
        """, (limit,)).fetchall()

    is_test = postgrid.is_test_key() if test is None else test
    sent = skipped_no_addr = skipped_no_pc = errors = 0
    previews: list[str] = []
    try:
        for co in rows:
            addr = _parse_address(co["registered_address"])
            if not addr:
                skipped_no_addr += 1
                print(f"  ? {co['name']}: unparseable address")
                continue
            if not addr.get("postal"):
                skipped_no_pc += 1
                continue
            size = "a4" if addr["country"] == "GB" else "us_letter"

            # Prefer portal template IDs (PostGrid-editable) over inline HTML.
            tmpl_uk = config.POSTGRID_TEMPLATE_UK
            tmpl_us = config.POSTGRID_TEMPLATE_US
            tmpl_id = (tmpl_uk if addr["country"] == "GB" else tmpl_us) or None

            if tmpl_id:
                try:
                    merge, meta = letter_mod.build_merge_variables(conn, co)
                except ValueError as exc:
                    errors += 1
                    print(f"  ! {co['name']}: {exc}")
                    continue
                # Build {{addrBlock}}: a single newline-separated string rendered
                # with white-space:pre-line in the template.  PostGrid uppercases
                # {{to.*}} standard vars for postal compliance so we avoid them in
                # the letter body and use this custom var instead.
                co_name = (co["name"] or "").strip()
                addr_name = (meta.get("addressee") or "").strip()
                co_name_fmt = _title_addr(co_name) if co_name == co_name.upper() else co_name
                # Suppress company line when it IS the named person (sole trader)
                # e.g. "Sebastian Acosta, Realtor in Miami" starts with "Sebastian Acosta"
                addr_first = addr_name.split(",")[0].strip().lower() if addr_name else ""
                show_company = not (
                    addr_name and addr_name != "The Owner" and
                    addr_first and co_name.lower().startswith(addr_first)
                )
                if addr["country"] == "GB":
                    addr_line = f"{addr['line1']}, {addr['city']} {addr['postal']}"
                else:
                    addr_line = f"{addr['line1']}, {addr['city']}, {addr['state']} {addr['postal']}"
                block_lines = [addr_name]
                if show_company:
                    block_lines.append(co_name_fmt)
                block_lines.append(addr_line)
                merge["addrBlock"] = "\n".join(line for line in block_lines if line)
                html_arg = None
                tmpl_arg = tmpl_id
                merge_arg = merge
            else:
                try:
                    html_arg, meta = letter_mod.render_letter_html(conn, co, stamped_address=True)
                except ValueError as exc:
                    errors += 1
                    print(f"  ! {co['name']}: {exc}")
                    continue
                tmpl_arg = None
                merge_arg = None

            order = None
            for attempt in range(4):
                try:
                    order = postgrid.create_letter(
                        _contact(co, addr), config.POSTGRID_FROM,
                        html=html_arg, template=tmpl_arg, merge=merge_arg,
                        color=True,
                        size=size, mailing_class=mailing_class,
                        metadata={"companyId": co["id"], "campaign": campaign},
                        idempotency_key=f"{campaign}-{co['id']}")
                    break
                except postgrid.PostGridError as exc:
                    if exc.status in (429, 503) and attempt < 3:
                        time.sleep(2 ** attempt)  # backoff
                        continue
                    errors += 1
                    print(f"  ! {co['name']}: {exc.type} — {exc.message}")
                    break
            if not order:
                continue

            oid, status = order.get("id"), order.get("status")
            if order.get("url"):
                previews.append(order["url"])
            print(f"  + {co['name']} -> {oid} [{status}]{' TEST' if is_test else ''}")
            if not dry_run:
                db.insert_letter(
                    conn, co["id"], person_id=meta["person_id"], letter_no=1,
                    claim_code=meta["claim_code"], status="posted",
                    postgrid_id=oid, postgrid_status=status)
            sent += 1
            time.sleep(sleep)
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return {"eligible": len(rows), "sent": sent, "test_mode": is_test,
            "skipped_no_address": skipped_no_addr, "skipped_no_postcode": skipped_no_pc,
            "errors": errors, "preview_urls": previews[:5]}
