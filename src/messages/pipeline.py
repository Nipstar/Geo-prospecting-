"""Pipeline command layer: guarded status transitions used by the CLI.

Thin functions over db.advance_status plus the domain rules for touch cadence,
connection acceptance, check delivery, and the stop rules (no touch 4).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .. import config, db


def _due(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def mark_accepted(person_id: int) -> dict:
    """Connection accepted: schedule touch 2, set connection_status."""
    conn = db.get_connection()
    try:
        person = db.get_person(conn, person_id)
        if person is None:
            raise ValueError(f"No person with id {person_id}")
        db.update_person(
            conn, person_id,
            connection_status="connected",
            accepted_at=datetime.now().isoformat(timespec="seconds"),
        )
        touch2 = next(
            (t for t in db.get_touches_for_person(conn, person_id) if t["touch_no"] == 2),
            None,
        )
        due = _due(config.TOUCH2_DELAY_DAYS)
        if touch2:
            db.update_touch(conn, touch2["id"], due_date=due, status="queued")
        return {"person_id": person_id, "touch2_due": due}
    finally:
        conn.close()


def mark_touch_sent(touch_id: int) -> dict:
    """Log a touch as sent. Sending touch 2 schedules touch 3. Sending touch 3
    with no reply triggers the stop rule."""
    conn = db.get_connection()
    try:
        touch = db.get_touch(conn, touch_id)
        if touch is None:
            raise ValueError(f"No touch with id {touch_id}")
        db.update_touch(
            conn, touch_id, status="sent",
            sent_at=datetime.now().isoformat(timespec="seconds"),
        )
        info = {"touch_id": touch_id, "touch_no": touch["touch_no"]}
        person = db.get_person(conn, touch["person_id"])

        if touch["touch_no"] == 1:
            db.update_person(conn, person["id"], connection_status="requested")
        elif touch["touch_no"] == 2:
            touch3 = next(
                (t for t in db.get_touches_for_person(conn, person["id"]) if t["touch_no"] == 3),
                None,
            )
            if touch3:
                due = _due(config.TOUCH3_DELAY_DAYS)
                db.update_touch(conn, touch3["id"], due_date=due, status="queued")
                info["touch3_due"] = due
        elif touch["touch_no"] == 3:
            # Stop rule: after touch 3 with no reply, close as no_reply_3_touches.
            company = db.get_company(conn, person["company_id"])
            if company["status"] == "in_sequence":
                try:
                    db.advance_status(
                        conn, company["id"], "closed_lost", event="no_reply_3_touches"
                    )
                    info["closed"] = "no_reply_3_touches"
                except db.InvalidTransition:
                    pass
        return info
    finally:
        conn.close()


def mark_check_delivered(company_id: int) -> dict:
    conn = db.get_connection()
    try:
        db.advance_status(conn, company_id, "check_delivered", event="check_delivered")
        return {"company_id": company_id, "status": "check_delivered"}
    finally:
        conn.close()


def mark_audit_proposed(company_id: int, value_gbp: float | None = None) -> dict:
    conn = db.get_connection()
    try:
        db.advance_status(conn, company_id, "audit_proposed", event="audit_booked", value_gbp=value_gbp)
        return {"company_id": company_id, "status": "audit_proposed"}
    finally:
        conn.close()


def mark_audit_paid(company_id: int, value_gbp: float) -> dict:
    conn = db.get_connection()
    try:
        db.advance_status(conn, company_id, "client", event="audit_paid", value_gbp=value_gbp)
        return {"company_id": company_id, "status": "client"}
    finally:
        conn.close()


def log_retainer(company_id: int, value_gbp: float) -> dict:
    conn = db.get_connection()
    try:
        db.log_event(conn, company_id, "retainer", value_gbp)
        return {"company_id": company_id, "event": "retainer", "value_gbp": value_gbp}
    finally:
        conn.close()


def mark_closed(company_id: int, reason: str = "manual") -> dict:
    conn = db.get_connection()
    try:
        try:
            db.advance_status(conn, company_id, "closed_lost", event=f"closed_{reason}")
        except db.InvalidTransition:
            db.update_company(conn, company_id, status="closed_lost")
            db.log_event(conn, company_id, f"closed_{reason}")
        return {"company_id": company_id, "status": "closed_lost", "reason": reason}
    finally:
        conn.close()


def set_followup(company_id: int, followup_date: str) -> dict:
    conn = db.get_connection()
    try:
        db.update_company(conn, company_id, followup_date=followup_date)
        return {"company_id": company_id, "followup_date": followup_date}
    finally:
        conn.close()
