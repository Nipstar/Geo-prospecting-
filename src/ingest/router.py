"""Channel router: sets companies.channel to 'linkedin' or 'post'.

Rule: if any person on the company has a linkedin_url -> linkedin. Otherwise,
once the company has been in the pipeline for the routing grace period (or is
explicitly forced), route to post. Postal addressee is a Companies House
director (Ltd) or the proprietor / "The Owner" (sole trader).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .. import db

# Companies with no LinkedIn person are held this many days before routing to
# post, matching the "no LinkedIn presence within 7 days" rule.
POST_GRACE_DAYS = 7


def _has_linkedin_person(conn, company_id: int) -> bool:
    people = db.get_people_for_company(conn, company_id)
    return any((p["linkedin_url"] or "").strip() for p in people)


def _days_in_pipeline(company) -> int:
    raw = company["source_date"] or company["created_at"]
    if not raw:
        return 999
    try:
        d = datetime.fromisoformat(str(raw)[:10]).date()
    except ValueError:
        return 999
    return (date.today() - d).days


def route_all(dry_run: bool = False, force: bool = False) -> dict[str, int]:
    """Route every unrouted active company. force ignores the grace period."""
    conn = db.get_connection()
    linkedin = post = held = 0
    try:
        rows = conn.execute(
            "SELECT * FROM companies WHERE channel IS NULL "
            "AND status NOT IN ('closed_lost','client')"
        ).fetchall()
        for company in rows:
            if company["ch_status"] and company["ch_status"] not in ("active", "unmatched", None):
                continue  # dissolved etc, leave unrouted
            if _has_linkedin_person(conn, company["id"]):
                channel = "linkedin"
            elif force or _days_in_pipeline(company) >= POST_GRACE_DAYS:
                channel = "post"
            else:
                held += 1
                continue
            if dry_run:
                print(f"  {company['name']}: -> {channel}")
            else:
                db.update_company(conn, company["id"], channel=channel)
            if channel == "linkedin":
                linkedin += 1
            else:
                post += 1
    finally:
        conn.close()
    return {"linkedin": linkedin, "post": post, "held_for_grace": held}
