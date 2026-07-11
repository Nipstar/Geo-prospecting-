"""The daily queue.

Builds the ordered work list for the 30-minute operating routine:
a. Replies awaiting response (oldest first, red if over REPLY_URGENT_HOURS)
b. Touch 2s due today
c. Touch 3s due today
d. New connection notes ready to send (capped at DAILY_CONNECTION_CAP)
e. Free checks promised but not yet delivered
+ POST section: letters awaiting approval, follow-ups due, claims awaiting check
+ Follow-up nudges for checks delivered > CHECK_FOLLOWUP_DAYS ago
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .. import config, db


def _hours_since(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    return (datetime.now() - dt).total_seconds() / 3600


def build_queue() -> dict:
    conn = db.get_connection()
    today = date.today().isoformat()
    try:
        q: dict = {}

        # a. Open replies, oldest first.
        replies = []
        for r in db.open_replies(conn):
            person = db.get_person(conn, r["person_id"])
            company = db.get_company(conn, person["company_id"])
            hrs = _hours_since(r["received_at"])
            replies.append({
                "reply_id": r["id"], "person_id": r["person_id"],
                "person": person["name"], "company": company["name"],
                "intent": r["intent"], "urgency": r["urgency"],
                "hours": round(hrs, 1), "red": hrs > config.REPLY_URGENT_HOURS,
                "reply_text": r["reply_text"],
                "drafted": r["response_drafted"],
                "cmd": f"cli sent-reply --reply-id {r['id']}",
            })
        q["replies"] = replies

        # b + c. Touches due.
        q["touch2"] = [_touch_item(conn, t) for t in db.touches_due(conn, 2, today)]
        q["touch3"] = [_touch_item(conn, t) for t in db.touches_due(conn, 3, today)]

        # d. New connection notes ready (touch 1 drafted, not yet sent), capped.
        rows = conn.execute(
            """SELECT t.*, p.name AS person_name, p.company_id
               FROM touches t JOIN people p ON p.id = t.person_id
               JOIN companies c ON c.id = p.company_id
               WHERE t.touch_no = 1 AND t.status = 'drafted' AND c.channel = 'linkedin'
               ORDER BY COALESCE(c.pitchability_score, 0) DESC, t.id LIMIT ?""",
            (config.DAILY_CONNECTION_CAP,),
        ).fetchall()
        q["new_connections"] = [_touch_item(conn, t) for t in rows]
        q["connection_cap"] = config.DAILY_CONNECTION_CAP

        # e. Free checks promised (status 'replied') but not delivered.
        promised = db.get_companies_by_status(conn, "replied")
        q["checks_promised"] = [
            {"company_id": c["id"], "company": c["name"],
             "cmd": f"cli check full --company-id {c['id']} && cli delivered --company-id {c['id']}"}
            for c in promised
        ]

        # Follow-up nudge: check_delivered > N days, no audit_proposed.
        cutoff = (date.today() - timedelta(days=config.CHECK_FOLLOWUP_DAYS)).isoformat()
        nudge_rows = conn.execute(
            """SELECT c.* FROM companies c
               JOIN pipeline_events e ON e.company_id = c.id AND e.event = 'check_delivered'
               WHERE c.status = 'check_delivered' AND e.event_date <= ?
               GROUP BY c.id""",
            (cutoff,),
        ).fetchall()
        q["check_nudges"] = [
            {"company_id": c["id"], "company": c["name"],
             "cmd": f"cli followup-nudge --company-id {c['id']}"}
            for c in nudge_rows
        ]

        # POST section.
        q["post"] = _post_section(conn)
        return q
    finally:
        conn.close()


def _touch_item(conn, t) -> dict:
    person = db.get_person(conn, t["person_id"])
    company = db.get_company(conn, person["company_id"])
    return {
        "touch_id": t["id"], "touch_no": t["touch_no"],
        "person": person["name"], "company": company["name"],
        "linkedin": person["linkedin_url"],
        "text": t["message_text"],
        "cmd": f"cli sent --touch-id {t['id']}",
    }


def _post_section(conn) -> dict:
    today = date.today().isoformat()
    drafted = db.get_letters_by_status(conn, "drafted")
    approved = db.get_letters_by_status(conn, "approved")
    # Follow-ups due: sent letters unclaimed past the window with no letter_no 2.
    cutoff = (date.today() - timedelta(days=config.LETTER_FOLLOWUP_DAYS)).isoformat()
    followups = conn.execute(
        """SELECT l.* FROM letters l
           WHERE l.status = 'sent' AND l.letter_no = 1
             AND date(l.sent_at) <= ?
             AND NOT EXISTS (
               SELECT 1 FROM letters l2 WHERE l2.company_id = l.company_id AND l2.letter_no = 2)
           ORDER BY l.sent_at""",
        (cutoff,),
    ).fetchall()
    claimed = conn.execute(
        """SELECT l.*, c.name AS company FROM letters l JOIN companies c ON c.id = l.company_id
           WHERE l.status = 'claimed' AND c.status = 'replied' ORDER BY l.claimed_at""",
    ).fetchall()
    return {
        "drafted_awaiting_approval": [
            {"letter_id": l["id"], "company_id": l["company_id"],
             "cmd": f"cli post approve --letter-id {l['id']}"} for l in drafted
        ],
        "approved_ready_to_send": [
            {"letter_id": l["id"], "company_id": l["company_id"]} for l in approved
        ],
        "followups_due": [
            {"letter_id": l["id"], "company_id": l["company_id"],
             "cmd": f"cli post followup --company-id {l['company_id']}"} for l in followups
        ],
        "claims_awaiting_check": [
            {"company_id": l["company_id"], "company": l["company"],
             "cmd": f"cli check full --company-id {l['company_id']} && cli delivered --company-id {l['company_id']}"}
            for l in claimed
        ],
    }
