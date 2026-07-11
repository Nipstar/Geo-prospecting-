"""Shared voice assets for message generation and auditing.

Loads the antek-outreach-voice SKILL.md so the same rules drive generation, the
pre-send audit and reply drafting. Also holds the offer facts and the banned
word/pattern lists used by the deterministic audit.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .. import config

SKILL_PATH = (
    config.ROOT / ".claude" / "skills" / "antek-outreach-voice" / "SKILL.md"
)

OFFER_FACTS = """OFFER FACTS (never exceed these):
- Roughly 85% of UK SMEs have zero measurable AI visibility.
- Free AI Visibility Check: how the business appears across ChatGPT, Perplexity
  and Google AI Overviews for their service and town, as a short branded report.
- Paid follow-on: full GEO audit, then implementation retainer.
- Only verified proof: 100% Share of AI Voice for "ai voice agents andover"
  measured with Local Falcon. Invent nothing else.
- Antek Automation is not a limited company. Never write "Ltd" or "Limited"."""

HYPE_WORDS = [
    "elevate", "leverage", "supercharge", "game-changer", "game changer",
    "unlock", "revolutionise", "revolutionize", "seamless", "cutting-edge",
    "cutting edge", "transform", "synergy", "synergies", "empower",
    "world-class", "best-in-class", "next-level",
]

AUTOMATED_TELLS = [
    "i hope this finds you well",
    "i noticed we share",
    "i see we're both",
    "quick question",
    "reaching out because",
    "i came across your profile",
    "as a fellow",
]

MEETING_ASKS = [
    "30 minutes", "20 minutes", "15 minutes", "hop on a call", "book a call",
    "schedule a call", "grab 15", "calendar", "calendly", "this thursday",
    "next week for a call",
]


@lru_cache(maxsize=1)
def skill_text() -> str:
    if SKILL_PATH.exists():
        return SKILL_PATH.read_text(encoding="utf-8")
    return ""


def system_prompt(extra: str = "") -> str:
    """Build a system prompt from the skill file + offer facts."""
    parts = [
        "You write outbound in the Antek Automation voice. Follow these rules "
        "exactly.",
        skill_text(),
        OFFER_FACTS,
    ]
    if extra:
        parts.append(extra)
    return "\n\n".join(p for p in parts if p.strip())
