"""Reply classification and response drafting in the Antek voice."""
from __future__ import annotations

from datetime import date

from .. import db, llm
from . import voice

CLASSIFY_SYSTEM = (
    "You classify replies to B2B outbound. Return strict JSON only: "
    '{"intent": "interested|objection|referral|not_now", '
    '"read_line": "one line on what they actually mean", '
    '"urgency": "hour|today|week"}. '
    "urgency 'hour' for warm/interested, 'today' for objections and referrals, "
    "'week' for not_now."
)

CLASSIFY_USER = "Classify this reply:\n\n{text}"

RESPONSE_SYSTEM = voice.system_prompt(
    "You are drafting the reply to their message. Match their words. Keep it to "
    "3-4 short lines. British English, no hype, no em dashes, no exclamation marks."
)

RESPONSE_TEMPLATES = {
    "interested": (
        "They are interested. Acknowledge their words. Give one concrete reason a "
        "20-minute call pays for itself. If the free check has not been delivered "
        "yet, offer to send it first. End with an easy scheduling ask. "
        "Return JSON: {\"response\": \"...\"}"
    ),
    "objection": (
        "They raised an objection. Take it seriously, answer honestly, no "
        "defensiveness, end with a low-pressure open question. Give TWO versions. "
        "Return JSON: {\"warmer\": \"...\", \"direct\": \"...\"}"
    ),
    "referral": (
        "They offered a referral. Thank them graciously and ask for the warm "
        "intro. Return JSON: {\"response\": \"...\"}"
    ),
    "not_now": (
        "The timing is wrong for them. Respect it, leave the door open, ask when "
        "to reconnect. Return JSON: {\"response\": \"...\", "
        "\"suggested_followup_date\": \"YYYY-MM-DD or empty\"}"
    ),
}


def classify_reply(text: str) -> dict:
    raw = llm.complete(
        "classify", system=CLASSIFY_SYSTEM, user=CLASSIFY_USER.format(text=text),
        temperature=0.2, max_tokens=200, json_mode=True,
    )
    data = llm.parse_json(raw)
    intent = data.get("intent", "objection")
    if intent not in RESPONSE_TEMPLATES:
        intent = "objection"
    return {
        "intent": intent,
        "read_line": data.get("read_line", ""),
        "urgency": data.get("urgency", "today"),
    }


def draft_response(reply_id: int) -> dict:
    conn = db.get_connection()
    try:
        reply = db.get_reply(conn, reply_id)
        if reply is None:
            raise ValueError(f"No reply with id {reply_id}")
        person = db.get_person(conn, reply["person_id"])
        company = db.get_company(conn, person["company_id"])
        check = db.latest_check(conn, company["id"])
        delivered = company["status"] in ("check_delivered", "audit_proposed", "client")

        intent = reply["intent"] or "objection"
        instruction = RESPONSE_TEMPLATES.get(intent, RESPONSE_TEMPLATES["objection"])
        context = (
            f"Person: {person['name']}. Company: {company['name']} ({company['town']}). "
            f"Free check already delivered: {delivered}. "
            f"Their finding: {check['headline_finding'] if check else 'n/a'}. "
            f"Their reply: {reply['reply_text']}"
        )
        raw = llm.complete(
            "generate", system=RESPONSE_SYSTEM,
            user=f"{instruction}\n\nCONTEXT:\n{context}",
            temperature=0.6, max_tokens=500, json_mode=True,
        )
        data = llm.parse_json(raw)

        # Persist a canonical drafted response (join both objection versions).
        if intent == "objection":
            drafted = f"[warmer]\n{data.get('warmer','')}\n\n[direct]\n{data.get('direct','')}"
        else:
            drafted = data.get("response", "")
        db.update_reply(conn, reply_id, response_drafted=drafted)

        if intent == "not_now":
            fu = data.get("suggested_followup_date", "").strip()
            if fu:
                db.update_company(conn, company["id"], followup_date=fu)

        # A reply moves the company to 'replied' if it isn't further along.
        if company["status"] in ("new", "checked", "in_sequence"):
            try:
                db.advance_status(conn, company["id"], "replied", event="reply_received")
            except db.InvalidTransition:
                pass
        return {"intent": intent, "drafted": drafted, "data": data}
    finally:
        conn.close()


def log_reply(person_id: int, text: str) -> dict:
    """Classify, store, and draft a response for an inbound reply."""
    conn = db.get_connection()
    try:
        cls = classify_reply(text)
        reply_id = db.insert_reply(
            conn, person_id, reply_text=text, intent=cls["intent"],
            read_line=cls["read_line"], urgency=cls["urgency"],
        )
    finally:
        conn.close()
    drafted = draft_response(reply_id)
    return {"reply_id": reply_id, **cls, **drafted}
