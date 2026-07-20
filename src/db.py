"""SQLite data layer for geo-outreach.

Thin wrapper, no ORM. Provides: connection management, a migrations runner,
typed insert/update/get helpers per table, and the pipeline status engine with
guarded transitions that log pipeline_events rows.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config

# generic tokens dropped from the short URL slug
_SLUG_DROP = {
    "the", "solicitors", "solicitor", "ltd", "llp", "limited", "llc", "inc", "co",
    "company", "pa", "pllc", "pl", "law", "legal", "and", "realty", "realtor",
    "realtors", "realestate", "brokerage", "broker", "brokers", "group",
    "associates", "properties", "property", "estate", "estates",
}


def short_slug(name: str) -> str:
    """A short, human, URL-safe slug: business name minus generic filler,
    capped at the first 3 meaningful words. e.g. 'RP Singh Solicitors' -> 'rp-singh'."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    parts = [p for p in s.split("-") if p]
    kept = [p for p in parts if p not in _SLUG_DROP] or parts
    return "-".join(kept[:3]) or (s or "lead")


def ensure_slugs(conn: sqlite3.Connection) -> None:
    """Guarantee every company has a stable, unique short `slug`. Idempotent."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(companies)")]
    if "slug" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN slug TEXT")
    taken = {r[0] for r in conn.execute(
        "SELECT slug FROM companies WHERE slug IS NOT NULL AND slug <> ''")}
    for co in conn.execute("SELECT id, name, slug FROM companies").fetchall():
        if co["slug"]:
            continue
        base = short_slug(co["name"])
        s, i = base, 2
        while s in taken:
            s, i = f"{base}-{i}", i + 1
        taken.add(s)
        conn.execute("UPDATE companies SET slug=? WHERE id=?", (s, co["id"]))
    conn.commit()

# --- Schema ----------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    website TEXT,
    town TEXT,
    county TEXT,
    sector TEXT,
    primary_service TEXT,           -- plain-words main service, from enrichment
    phone TEXT,
    places_rating REAL,
    places_reviews INTEGER,
    companies_house_no TEXT,
    company_type TEXT,              -- ltd | sole_trader | llp | unknown
    incorporation_date TEXT,
    sic_codes TEXT,
    registered_address TEXT,
    ch_status TEXT,                 -- active | dissolved etc, skip non-active
    channel TEXT,                   -- linkedin | post | null until routed
    pitchability_score REAL,        -- 0-100, who to pitch first (geo-slab rubric)
    pitchability_tier TEXT,         -- premium | standard | skip
    ch_review_flag INTEGER DEFAULT 0,   -- needs manual review (unmatched / new)
    followup_date TEXT,             -- next-contact date for not_now replies
    source TEXT,
    source_date TEXT,
    status TEXT DEFAULT 'new',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT,
    role TEXT,
    linkedin_url TEXT,
    person_source TEXT,             -- airtable | manual | companies_house_officer
    connection_status TEXT DEFAULT 'none',  -- none | requested | connected | n/a
    accepted_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS visibility_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    run_date TEXT,
    check_type TEXT,                -- mini | full
    chatgpt_score REAL,
    claude_score REAL,
    gemini_score REAL,
    perplexity_score REAL,
    ai_overview_score REAL,
    composite_score REAL,           -- 0-100, geo-slab 70/30 rubric
    platforms_tested INTEGER,
    platforms_mentioned INTEGER,
    cost_usd REAL,
    headline_finding TEXT,          -- one line for the opener
    competitor_named TEXT,          -- who DID show up
    report_path TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS touches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    touch_no INTEGER,               -- 1, 2, 3
    channel TEXT,                   -- linkedin | post
    drafted_at TEXT,
    sent_at TEXT,
    due_date TEXT,
    message_text TEXT,
    status TEXT DEFAULT 'drafted',  -- drafted | queued | sent | skipped
    FOREIGN KEY (person_id) REFERENCES people(id)
);

CREATE TABLE IF NOT EXISTS letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    person_id INTEGER,
    letter_no INTEGER DEFAULT 1,    -- 1 = first letter, 2 = follow-up
    claim_code TEXT UNIQUE,         -- short unique code for QR / URL tracking
    pdf_path TEXT,
    stannp_id TEXT,
    drafted_at TEXT,
    sent_at TEXT,
    delivery_est TEXT,
    status TEXT DEFAULT 'drafted',  -- drafted | approved | sent | claimed | expired
    claimed_at TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (person_id) REFERENCES people(id)
);

CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    received_at TEXT,
    reply_text TEXT,
    intent TEXT,                    -- interested | objection | referral | not_now
    read_line TEXT,                 -- one-line read of what they mean
    urgency TEXT,
    response_drafted TEXT,
    response_sent_at TEXT,
    FOREIGN KEY (person_id) REFERENCES people(id)
);

CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    event TEXT,                     -- check_sent | audit_booked | audit_paid | retainer
    event_date TEXT,
    value_gbp REAL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS probe_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT,
    engine TEXT,
    run_date TEXT,
    response_text TEXT,
    UNIQUE (query, engine, run_date)
);

CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_channel ON companies(channel);
CREATE INDEX IF NOT EXISTS idx_people_company ON people(company_id);
CREATE INDEX IF NOT EXISTS idx_touches_person ON touches(person_id);
CREATE INDEX IF NOT EXISTS idx_checks_company ON visibility_checks(company_id);
"""

# --- Status engine ---------------------------------------------------------
# Valid forward transitions on companies.status. Any status may fall to
# closed_lost. replied is reachable from in_sequence (LinkedIn) or checked/new
# (a postal claim jumps straight to replied).
STATUS_ORDER = [
    "new",
    "checked",
    "in_sequence",
    "replied",
    "check_delivered",
    "audit_proposed",
    "client",
    "closed_lost",
]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "new": {"checked", "in_sequence", "replied", "closed_lost"},
    "checked": {"in_sequence", "replied", "closed_lost"},
    "in_sequence": {"replied", "closed_lost"},
    "replied": {"check_delivered", "closed_lost"},
    "check_delivered": {"audit_proposed", "closed_lost"},
    "audit_proposed": {"client", "closed_lost"},
    "client": {"closed_lost"},
    "closed_lost": set(),
}


class InvalidTransition(Exception):
    """Raised when a status change is not permitted by the pipeline rules."""


# --- Connection ------------------------------------------------------------
def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Return a connection with row factory and foreign keys on."""
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first schema shipped. Backfilled onto existing DBs so
# migrations stay a no-op safe to run on every command. (table, column, decl).
_ADDED_COLUMNS = [
    ("companies", "pitchability_score", "REAL"),
    ("companies", "pitchability_tier", "TEXT"),
    ("visibility_checks", "claude_score", "REAL"),
    ("visibility_checks", "gemini_score", "REAL"),
    ("visibility_checks", "platforms_tested", "INTEGER"),
    ("visibility_checks", "platforms_mentioned", "INTEGER"),
    ("visibility_checks", "cost_usd", "REAL"),
]


def run_migrations(conn: sqlite3.Connection | None = None) -> None:
    """Create every table and index, then add any columns introduced later.
    Idempotent — safe to run on every command."""
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(SCHEMA)
        for table, column, decl in _ADDED_COLUMNS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
    finally:
        if own:
            conn.close()


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- Generic helpers -------------------------------------------------------
def _insert(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> int:
    cols = ", ".join(data.keys())
    marks = ", ".join(["?"] * len(data))
    cur = conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values())
    )
    conn.commit()
    return int(cur.lastrowid)


def _update(conn: sqlite3.Connection, table: str, row_id: int, data: dict[str, Any]) -> None:
    if not data:
        return
    sets = ", ".join(f"{k} = ?" for k in data)
    conn.execute(
        f"UPDATE {table} SET {sets} WHERE id = ?", (*data.values(), row_id)
    )
    conn.commit()


def _get(conn: sqlite3.Connection, table: str, row_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()


# --- Companies -------------------------------------------------------------
def insert_company(conn: sqlite3.Connection, **fields: Any) -> int:
    fields.setdefault("source_date", _today())
    fields.setdefault("status", "new")
    return _insert(conn, "companies", fields)


def get_company(conn: sqlite3.Connection, company_id: int) -> Optional[sqlite3.Row]:
    return _get(conn, "companies", company_id)


def update_company(conn: sqlite3.Connection, company_id: int, **fields: Any) -> None:
    _update(conn, "companies", company_id, fields)


def get_companies_by_status(
    conn: sqlite3.Connection, status: str, limit: int | None = None
) -> list[sqlite3.Row]:
    q = "SELECT * FROM companies WHERE status = ? ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, (status,)).fetchall()


def find_company(
    conn: sqlite3.Connection, name: str, town: str | None = None
) -> Optional[sqlite3.Row]:
    """Dedup lookup by (name, town), case-insensitive."""
    if town:
        return conn.execute(
            "SELECT * FROM companies WHERE lower(name)=lower(?) AND lower(coalesce(town,''))=lower(?)",
            (name, town),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM companies WHERE lower(name)=lower(?)", (name,)
    ).fetchone()


def find_company_by_domain(conn: sqlite3.Connection, domain: str) -> Optional[sqlite3.Row]:
    if not domain:
        return None
    return conn.execute(
        "SELECT * FROM companies WHERE website LIKE ?", (f"%{domain}%",)
    ).fetchone()


def companies_in_town_sector(
    conn: sqlite3.Connection, town: str, sector: str, exclude_id: int | None = None
) -> list[sqlite3.Row]:
    q = "SELECT * FROM companies WHERE lower(coalesce(town,''))=lower(?) AND lower(coalesce(sector,''))=lower(?)"
    args: list[Any] = [town or "", sector or ""]
    if exclude_id:
        q += " AND id != ?"
        args.append(exclude_id)
    return conn.execute(q, tuple(args)).fetchall()


# --- People ----------------------------------------------------------------
def insert_person(conn: sqlite3.Connection, company_id: int, **fields: Any) -> int:
    fields["company_id"] = company_id
    fields.setdefault("connection_status", "none")
    return _insert(conn, "people", fields)


def get_person(conn: sqlite3.Connection, person_id: int) -> Optional[sqlite3.Row]:
    return _get(conn, "people", person_id)


def update_person(conn: sqlite3.Connection, person_id: int, **fields: Any) -> None:
    _update(conn, "people", person_id, fields)


def get_people_for_company(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM people WHERE company_id = ? ORDER BY id", (company_id,)
    ).fetchall()


def find_person(
    conn: sqlite3.Connection, company_id: int, name: str | None, linkedin_url: str | None
) -> Optional[sqlite3.Row]:
    if linkedin_url:
        row = conn.execute(
            "SELECT * FROM people WHERE lower(coalesce(linkedin_url,''))=lower(?)",
            (linkedin_url,),
        ).fetchone()
        if row:
            return row
    if name:
        return conn.execute(
            "SELECT * FROM people WHERE company_id=? AND lower(coalesce(name,''))=lower(?)",
            (company_id, name),
        ).fetchone()
    return None


# --- Visibility checks -----------------------------------------------------
def insert_visibility_check(conn: sqlite3.Connection, company_id: int, **fields: Any) -> int:
    fields["company_id"] = company_id
    fields.setdefault("run_date", _today())
    return _insert(conn, "visibility_checks", fields)


def update_check(conn: sqlite3.Connection, check_id: int, **fields: Any) -> None:
    _update(conn, "visibility_checks", check_id, fields)


def latest_check(
    conn: sqlite3.Connection, company_id: int, check_type: str | None = None
) -> Optional[sqlite3.Row]:
    q = "SELECT * FROM visibility_checks WHERE company_id = ?"
    args: list[Any] = [company_id]
    if check_type:
        q += " AND check_type = ?"
        args.append(check_type)
    q += " ORDER BY id DESC LIMIT 1"
    return conn.execute(q, tuple(args)).fetchone()


# --- Touches ---------------------------------------------------------------
def insert_touch(conn: sqlite3.Connection, person_id: int, **fields: Any) -> int:
    fields["person_id"] = person_id
    fields.setdefault("drafted_at", _now())
    fields.setdefault("status", "drafted")
    return _insert(conn, "touches", fields)


def get_touch(conn: sqlite3.Connection, touch_id: int) -> Optional[sqlite3.Row]:
    return _get(conn, "touches", touch_id)


def update_touch(conn: sqlite3.Connection, touch_id: int, **fields: Any) -> None:
    _update(conn, "touches", touch_id, fields)


def get_touches_for_person(conn: sqlite3.Connection, person_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM touches WHERE person_id = ? ORDER BY touch_no", (person_id,)
    ).fetchall()


def touches_due(conn: sqlite3.Connection, touch_no: int, on_date: str | None = None) -> list[sqlite3.Row]:
    """Drafted/queued touches of a given number due on or before a date."""
    on_date = on_date or _today()
    return conn.execute(
        """SELECT t.*, p.name AS person_name, p.company_id
           FROM touches t JOIN people p ON p.id = t.person_id
           WHERE t.touch_no = ? AND t.status IN ('drafted','queued')
             AND t.due_date IS NOT NULL AND t.due_date <= ?
           ORDER BY t.due_date""",
        (touch_no, on_date),
    ).fetchall()


# --- Letters ---------------------------------------------------------------
def insert_letter(conn: sqlite3.Connection, company_id: int, **fields: Any) -> int:
    fields["company_id"] = company_id
    fields.setdefault("drafted_at", _now())
    fields.setdefault("status", "drafted")
    return _insert(conn, "letters", fields)


def get_letter(conn: sqlite3.Connection, letter_id: int) -> Optional[sqlite3.Row]:
    return _get(conn, "letters", letter_id)


def update_letter(conn: sqlite3.Connection, letter_id: int, **fields: Any) -> None:
    _update(conn, "letters", letter_id, fields)


def get_letters_by_status(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM letters WHERE status = ? ORDER BY id", (status,)
    ).fetchall()


def get_letter_by_code(conn: sqlite3.Connection, claim_code: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM letters WHERE claim_code = ?", (claim_code,)
    ).fetchone()


# --- Replies ---------------------------------------------------------------
def insert_reply(conn: sqlite3.Connection, person_id: int, **fields: Any) -> int:
    fields["person_id"] = person_id
    fields.setdefault("received_at", _now())
    return _insert(conn, "replies", fields)


def get_reply(conn: sqlite3.Connection, reply_id: int) -> Optional[sqlite3.Row]:
    return _get(conn, "replies", reply_id)


def update_reply(conn: sqlite3.Connection, reply_id: int, **fields: Any) -> None:
    _update(conn, "replies", reply_id, fields)


def open_replies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Replies with no response sent yet, oldest first."""
    return conn.execute(
        """SELECT r.*, p.name AS person_name, p.company_id
           FROM replies r JOIN people p ON p.id = r.person_id
           WHERE r.response_sent_at IS NULL
           ORDER BY r.received_at""",
    ).fetchall()


# --- Pipeline events + status engine ---------------------------------------
def log_event(
    conn: sqlite3.Connection, company_id: int, event: str, value_gbp: float | None = None
) -> int:
    return _insert(
        conn,
        "pipeline_events",
        {
            "company_id": company_id,
            "event": event,
            "event_date": _today(),
            "value_gbp": value_gbp,
        },
    )


def advance_status(
    conn: sqlite3.Connection,
    company_id: int,
    new_status: str,
    event: str | None = None,
    value_gbp: float | None = None,
) -> None:
    """Move a company to new_status, guarding against invalid transitions and
    logging a pipeline_events row. Idempotent no-op if already at new_status."""
    company = get_company(conn, company_id)
    if company is None:
        raise ValueError(f"No company with id {company_id}")
    current = company["status"] or "new"
    if current == new_status:
        return
    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidTransition(
            f"Cannot move company {company_id} from '{current}' to '{new_status}'"
        )
    update_company(conn, company_id, status=new_status)
    if event:
        log_event(conn, company_id, event, value_gbp)


def probe_cache_get(conn: sqlite3.Connection, query: str, engine: str, run_date: str) -> str | None:
    row = conn.execute(
        "SELECT response_text FROM probe_cache WHERE query=? AND engine=? AND run_date=?",
        (query, engine, run_date),
    ).fetchone()
    return row["response_text"] if row else None


def probe_cache_put(conn: sqlite3.Connection, query: str, engine: str, run_date: str, text: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO probe_cache (query, engine, run_date, response_text) VALUES (?,?,?,?)",
        (query, engine, run_date, text),
    )
    conn.commit()


if __name__ == "__main__":
    run_migrations()
    print(f"Migrations applied. DB at {config.DB_PATH}")
