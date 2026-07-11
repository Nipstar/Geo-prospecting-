"""LinkedIn 3-touch sequence generation.

Pulls a person, their company and its mini visibility check, then drafts touches
1-3 in the Antek voice and writes them with cadence-based due dates.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config, db, llm
from . import voice

USER_TEMPLATE = """Draft a 3-touch LinkedIn sequence for this prospect.

PERSON: {person_name} ({role})
COMPANY: {company_name}
TOWN: {town}
SECTOR: {sector}
SERVICE: {service}

Their mini AI visibility check:
- Headline finding: {headline}
- Competitor that showed up: {competitor}
- Composite AI visibility score: {score}/100

Write:
- touch1: connection note, under 280 characters, one specific observation, no
  pitch, no link.
- touch2: lead with the headline finding above stated plainly, name the
  competitor, then offer the free full check, ask for a reply. Max 5 short lines.
- touch3: short easy-out, one line on what Antek does, accept the no. Max 4 lines.

Return STRICT JSON only:
{{"touch1": "...", "touch2": "...", "touch3": "...",
  "strongest_element": "...", "weak_spots": "..."}}"""


def _service(company) -> str:
    try:
        return company["primary_service"] or company["sector"] or "their service"
    except (IndexError, KeyError):
        return company["sector"] or "their service"


def draft_sequence(person_id: int, write: bool = True) -> dict:
    """Generate and (by default) persist a 3-touch sequence for a person."""
    conn = db.get_connection()
    try:
        person = db.get_person(conn, person_id)
        if person is None:
            raise ValueError(f"No person with id {person_id}")
        company = db.get_company(conn, person["company_id"])
        check = db.latest_check(conn, company["id"])
        if check is None:
            raise ValueError(
                f"No visibility check for company {company['id']} "
                f"({company['name']}). Run `cli check mini` first."
            )

        user = USER_TEMPLATE.format(
            person_name=person["name"] or "there",
            role=person["role"] or "owner",
            company_name=company["name"],
            town=company["town"] or "their town",
            sector=company["sector"] or "business",
            service=_service(company),
            headline=check["headline_finding"] or "",
            competitor=check["competitor_named"] or "other firms",
            score=check["composite_score"] if check["composite_score"] is not None else "?",
        )
        raw = llm.complete(
            "generate", system=voice.system_prompt(), user=user,
            temperature=0.7, max_tokens=900, json_mode=True,
        )
        data = llm.parse_json(raw)

        result = {
            "touch1": data.get("touch1", "").strip(),
            "touch2": data.get("touch2", "").strip(),
            "touch3": data.get("touch3", "").strip(),
            "strongest_element": data.get("strongest_element", ""),
            "weak_spots": data.get("weak_spots", ""),
            "person": person, "company": company,
        }
        if write:
            _write_touches(conn, person, result)
            _write_queue_file(person, company, result)
            # new/checked -> in_sequence when the sequence is drafted.
            if company["status"] in ("new", "checked"):
                try:
                    db.advance_status(conn, company["id"], "in_sequence", event="sequenced")
                except db.InvalidTransition:
                    pass
        return result
    finally:
        conn.close()


def _write_touches(conn, person, result) -> None:
    today = date.today().isoformat()
    # Touch 1 due today. Touch 2 due null until acceptance. Touch 3 null until
    # touch 2 sent. These are set by the status-engine commands later.
    existing = {t["touch_no"]: t for t in db.get_touches_for_person(conn, person["id"])}
    plan = [
        (1, result["touch1"], today),
        (2, result["touch2"], None),
        (3, result["touch3"], None),
    ]
    for touch_no, text, due in plan:
        if touch_no in existing:
            db.update_touch(conn, existing[touch_no]["id"], message_text=text, status="drafted")
        else:
            db.insert_touch(
                conn, person["id"], touch_no=touch_no, channel="linkedin",
                message_text=text, due_date=due, status="drafted",
            )


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "person").lower()).strip("-")
    return s or "person"


def _write_queue_file(person, company, result) -> Path:
    """Plain-text file for easy copy on an iPad."""
    name = person["name"] or company["name"]
    fname = f"{date.today().isoformat()}-{_slug(name)}.txt"
    path = config.QUEUE_DIR / fname
    body = f"""{company['name']} — {name} ({person['role'] or 'owner'})
{'=' * 60}

TOUCH 1 (connection note):
{result['touch1']}

TOUCH 2 (after they accept):
{result['touch2']}

TOUCH 3 (no reply, 3-4 days later):
{result['touch3']}

--
Strongest: {result['strongest_element']}
Watch: {result['weak_spots']}
"""
    path.write_text(body, encoding="utf-8")
    return path


NUDGE_USER = """Draft a short follow-up nudge for a prospect whose free AI
Visibility Check was delivered a few days ago with no next step yet.

COMPANY: {company_name} ({town})
One specific finding from their report: {headline}
Competitor that showed up: {competitor}

Reference that one finding specifically. Ask if they had a chance to look. Offer
the full GEO audit as the obvious next step. 3-4 short lines, no pressure.
Return STRICT JSON: {{"nudge": "..."}}"""


def draft_check_nudge(company_id: int) -> dict:
    """Draft an in-voice nudge for a delivered-but-quiet check."""
    conn = db.get_connection()
    try:
        company = db.get_company(conn, company_id)
        check = db.latest_check(conn, company_id)
        raw = llm.complete(
            "generate", system=voice.system_prompt(),
            user=NUDGE_USER.format(
                company_name=company["name"], town=company["town"] or "their town",
                headline=check["headline_finding"] if check else "",
                competitor=check["competitor_named"] if check else "a competitor",
            ),
            temperature=0.6, max_tokens=300, json_mode=True,
        )
        data = llm.parse_json(raw)
        return {"company": company["name"], "nudge": data.get("nudge", "").strip()}
    finally:
        conn.close()


def draft_batch(status: str = "checked", limit: int = 10) -> list[dict]:
    """Draft sequences for people whose company is at a given status and who
    have a LinkedIn URL (linkedin channel)."""
    conn = db.get_connection()
    person_ids: list[int] = []
    try:
        rows = conn.execute(
            """SELECT p.id FROM people p JOIN companies c ON c.id = p.company_id
               WHERE c.status = ? AND c.channel = 'linkedin'
                 AND p.linkedin_url IS NOT NULL AND p.linkedin_url != ''
               ORDER BY c.id LIMIT ?""",
            (status, limit),
        ).fetchall()
        person_ids = [r["id"] for r in rows]
    finally:
        conn.close()
    results = []
    for pid in person_ids:
        try:
            results.append(draft_sequence(pid))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! person {pid}: {exc}")
    return results
