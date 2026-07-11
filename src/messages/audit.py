"""Pre-send message audit.

Deterministic flag detection (fast, free) plus an LLM tightening pass. Flags:
about_me_lines, generic_lines, oversized_ask, hype_words, automated_tells, plus
a tightened rewrite. If the draft is fundamentally weak, it says "start over".
"""
from __future__ import annotations

from .. import db, llm
from . import voice

AUDIT_SYSTEM = (
    voice.system_prompt()
    + "\n\nYou are auditing a draft outbound message before it is sent. Be "
    "strict. If the draft is generic, pitchy, breaks a voice rule, or would "
    "read the same with another company name pasted in, your rewrite field must "
    "be exactly \"start over\" and verdict must be \"start over\"."
)

AUDIT_USER = """Audit this draft message.

CONTEXT: {context}

DRAFT:
{text}

Return STRICT JSON only:
{{"verdict": "ok" | "tighten" | "start over",
  "tightened": "the improved message, or 'start over'",
  "notes": "one or two lines on what you changed and why"}}"""


def _deterministic_flags(text: str) -> dict:
    low = text.lower()
    hype = sorted({w for w in voice.HYPE_WORDS if w in low})
    tells = sorted({t for t in voice.AUTOMATED_TELLS if t in low})
    asks = sorted({a for a in voice.MEETING_ASKS if a in low})
    # About-me lines: lines that start with "I"/"We"/"At Antek" and pitch.
    about_me = []
    generic = []
    for line in [l.strip() for l in text.splitlines() if l.strip()]:
        ll = line.lower()
        if ll.startswith(("i ", "we ", "at antek", "our ", "i'm ", "i've ")):
            if any(p in ll for p in ("help", "offer", "provide", "specialise", "passionate", "solutions")):
                about_me.append(line)
        if any(g in ll for g in ("hope this finds", "love to connect", "explore how", "touch base")):
            generic.append(line)
    return {
        "hype_words": hype,
        "automated_tells": tells,
        "oversized_ask": asks,
        "about_me_lines": about_me,
        "generic_lines": generic,
        "em_dash": "—" in text,
        "exclamation": "!" in text,
    }


def audit_message(text: str, context: str = "LinkedIn outbound message") -> dict:
    """Return deterministic flags + an LLM tightened rewrite."""
    flags = _deterministic_flags(text)
    raw = llm.complete(
        "generate", system=AUDIT_SYSTEM,
        user=AUDIT_USER.format(context=context, text=text),
        temperature=0.4, max_tokens=500, json_mode=True,
    )
    data = llm.parse_json(raw)
    return {
        "flags": flags,
        "verdict": data.get("verdict", "tighten"),
        "tightened": (data.get("tightened") or "").strip(),
        "notes": data.get("notes", ""),
        "hard_fail": bool(flags["em_dash"] or flags["exclamation"] or flags["hype_words"]),
    }


def audit_touch(touch_id: int) -> dict:
    conn = db.get_connection()
    try:
        touch = db.get_touch(conn, touch_id)
        if touch is None:
            raise ValueError(f"No touch with id {touch_id}")
        ctx = f"LinkedIn touch {touch['touch_no']}"
        return audit_message(touch["message_text"] or "", context=ctx)
    finally:
        conn.close()
