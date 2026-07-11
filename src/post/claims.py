"""Claim handling for postal letters.

No web server in this project. The short URL is redirected/logged by the
operator's external n8n + Contabo stack; codes come back either one at a time
(`cli claim --code XYZ123`) or as a CSV export (`cli claim import claims.csv`).

Expected n8n webhook payload (documented for wiring, see README):
    { "claim_code": "XYZ123", "claimed_at": "2026-07-11T10:22:00Z",
      "user_agent": "...", "ip": "..." }
n8n appends claim_code (one per line) to claims.csv, imported here.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .. import db


def claim_code(code: str) -> dict:
    """Mark a letter claimed and move its company to 'replied'."""
    code = code.strip().upper()
    conn = db.get_connection()
    try:
        letter = db.get_letter_by_code(conn, code)
        if letter is None:
            return {"ok": False, "error": f"No letter with claim code {code}"}
        if letter["status"] == "claimed":
            return {"ok": True, "already": True, "company_id": letter["company_id"]}
        db.update_letter(
            conn, letter["id"], status="claimed",
            claimed_at=datetime.now().isoformat(timespec="seconds"),
        )
        company = db.get_company(conn, letter["company_id"])
        if company["status"] in ("new", "checked", "in_sequence"):
            try:
                db.advance_status(conn, company["id"], "replied", event="letter_claimed")
            except db.InvalidTransition:
                pass
        return {"ok": True, "company_id": letter["company_id"], "company": company["name"]}
    finally:
        conn.close()


def import_claims(path: str | Path) -> dict:
    """Import claim codes from a CSV (one code per row, or a 'claim_code' column)."""
    path = Path(path)
    claimed = missing = already = 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        # Support both a bare list and a header row.
        sample = fh.read(200)
        fh.seek(0)
        if "claim_code" in sample.lower():
            reader = csv.DictReader(fh)
            codes = [row.get("claim_code") or row.get("claim_code".upper()) for row in reader]
        else:
            codes = [row[0] for row in csv.reader(fh) if row]
    for code in codes:
        if not code:
            continue
        res = claim_code(code)
        if not res["ok"]:
            missing += 1
        elif res.get("already"):
            already += 1
        else:
            claimed += 1
    return {"claimed": claimed, "already": already, "missing": missing}


def approve_letter(letter_id: int) -> dict:
    conn = db.get_connection()
    try:
        letter = db.get_letter(conn, letter_id)
        if letter is None:
            return {"ok": False, "error": f"No letter {letter_id}"}
        if letter["status"] != "drafted":
            return {"ok": False, "error": f"Letter {letter_id} is '{letter['status']}', not 'drafted'"}
        db.update_letter(conn, letter_id, status="approved")
        return {"ok": True, "letter_id": letter_id, "status": "approved"}
    finally:
        conn.close()


def expire_unclaimed() -> dict:
    """Stop rule: after the follow-up letter, unclaimed companies close as
    'no_claim_2_letters'. Called opportunistically by the queue/stats."""
    conn = db.get_connection()
    closed = 0
    try:
        from datetime import date, timedelta

        from .. import config

        cutoff = (date.today() - timedelta(days=config.LETTER_FOLLOWUP_DAYS)).isoformat()
        rows = conn.execute(
            """SELECT c.id FROM companies c
               WHERE c.channel = 'post' AND c.status NOT IN ('closed_lost','client','replied',
                     'check_delivered','audit_proposed')
                 AND (SELECT COUNT(*) FROM letters l WHERE l.company_id = c.id AND l.letter_no = 2) > 0
                 AND (SELECT MAX(date(l.sent_at)) FROM letters l WHERE l.company_id = c.id) <= ?
                 AND NOT EXISTS (SELECT 1 FROM letters l WHERE l.company_id = c.id AND l.status = 'claimed')""",
            (cutoff,),
        ).fetchall()
        for r in rows:
            try:
                db.advance_status(conn, r["id"], "closed_lost", event="no_claim_2_letters")
                closed += 1
            except db.InvalidTransition:
                pass
    finally:
        conn.close()
    return {"closed": closed}
