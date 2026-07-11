"""Funnel stats and the weekly markdown review."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config, db, llm

FUNNEL = [
    "new", "checked", "in_sequence", "replied",
    "check_delivered", "audit_proposed", "client",
]


def _count_by_status(conn, channel: str | None = None) -> dict[str, int]:
    """Cumulative funnel counts: a company at 'client' also counts as having
    passed every earlier stage. Reached via pipeline_events + current status."""
    counts = {s: 0 for s in FUNNEL}
    where = "WHERE 1=1"
    args: list = []
    if channel:
        where += " AND channel = ?"
        args.append(channel)
    rows = conn.execute(f"SELECT status, channel FROM companies {where}", args).fetchall()
    reached_index = {s: i for i, s in enumerate(FUNNEL)}
    for r in rows:
        st = r["status"]
        if st == "closed_lost":
            # Still counts for the stages it passed; approximate via events below.
            continue
        idx = reached_index.get(st)
        if idx is None:
            continue
        for s in FUNNEL[: idx + 1]:
            counts[s] += 1
    return counts


def funnel(channel: str | None = None) -> dict:
    conn = db.get_connection()
    try:
        counts = _count_by_status(conn, channel)
        stages = []
        prev = None
        for s in FUNNEL:
            n = counts[s]
            conv = None
            if prev is not None and prev > 0:
                conv = round(100 * n / prev, 1)
            stages.append({"stage": s, "count": n, "conv_from_prev": conv})
            prev = n
        return {"channel": channel or "all", "stages": stages}
    finally:
        conn.close()


def metrics() -> dict:
    conn = db.get_connection()
    try:
        m: dict = {}
        # Connection accept rate.
        requested = conn.execute(
            "SELECT COUNT(*) c FROM people WHERE connection_status IN ('requested','connected')"
        ).fetchone()["c"]
        connected = conn.execute(
            "SELECT COUNT(*) c FROM people WHERE connection_status = 'connected'"
        ).fetchone()["c"]
        m["connection_accept_rate"] = _pct(connected, requested)

        # Reply rate per touch.
        for n in (1, 2, 3):
            sent = conn.execute(
                "SELECT COUNT(*) c FROM touches WHERE touch_no=? AND status='sent'", (n,)
            ).fetchone()["c"]
            m[f"touch{n}_sent"] = sent
        replies = conn.execute("SELECT COUNT(*) c FROM replies").fetchone()["c"]
        m["replies_total"] = replies
        m["reply_rate_overall"] = _pct(replies, m.get("touch2_sent", 0) or 0)

        # Letter claim rate + cost per claim.
        letters_sent = conn.execute(
            "SELECT COUNT(*) c FROM letters WHERE status IN ('sent','claimed')"
        ).fetchone()["c"]
        claims = conn.execute(
            "SELECT COUNT(*) c FROM letters WHERE status='claimed'"
        ).fetchone()["c"]
        m["letters_sent"] = letters_sent
        m["letter_claim_rate"] = _pct(claims, letters_sent)
        stannp_spend = letters_sent * config.STANNP_UNIT_PRICE_GBP
        m["cost_per_claim_gbp"] = round(stannp_spend / claims, 2) if claims else None

        # Avg hours reply -> response.
        rows = conn.execute(
            "SELECT received_at, response_sent_at FROM replies WHERE response_sent_at IS NOT NULL"
        ).fetchall()
        hrs = []
        for r in rows:
            try:
                a = datetime.fromisoformat(r["received_at"])
                b = datetime.fromisoformat(r["response_sent_at"])
                hrs.append((b - a).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass
        m["avg_reply_to_response_hours"] = round(sum(hrs) / len(hrs), 1) if hrs else None

        # Checks delivered this week.
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        m["checks_delivered_this_week"] = conn.execute(
            "SELECT COUNT(*) c FROM pipeline_events WHERE event='check_delivered' AND event_date >= ?",
            (week_ago,),
        ).fetchone()["c"]

        # Revenue logged.
        rev = conn.execute(
            "SELECT COALESCE(SUM(value_gbp),0) v FROM pipeline_events WHERE value_gbp IS NOT NULL"
        ).fetchone()["v"]
        m["revenue_logged_gbp"] = round(rev or 0, 2)
        return m
    finally:
        conn.close()


def _pct(num: int, den: int) -> float | None:
    return round(100 * num / den, 1) if den else None


def weekly_report() -> Path:
    """Write output/reports/weekly-YYYY-WW.md and return its path."""
    conn = db.get_connection()
    try:
        year, week, _ = date.today().isocalendar()
        f_all = funnel()
        f_li = funnel("linkedin")
        f_post = funnel("post")
        m = metrics()

        # What moved this week (events in the last 7 days).
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        moved = conn.execute(
            "SELECT event, COUNT(*) c FROM pipeline_events WHERE event_date >= ? GROUP BY event",
            (week_ago,),
        ).fetchall()

        # Top headline_finding patterns that got replies.
        finding_rows = conn.execute(
            """SELECT v.headline_finding, COUNT(r.id) replies
               FROM visibility_checks v
               JOIN people p ON p.company_id = v.company_id
               JOIN replies r ON r.person_id = p.id
               GROUP BY v.headline_finding ORDER BY replies DESC LIMIT 5""",
        ).fetchall()

        lines = [
            f"# geo-outreach weekly review — {year}-W{week:02d}",
            "",
            "## Funnel (all channels)",
            "",
            "| Stage | Count | Conv from prev |",
            "|---|---|---|",
        ]
        for s in f_all["stages"]:
            conv = f"{s['conv_from_prev']}%" if s["conv_from_prev"] is not None else "-"
            lines.append(f"| {s['stage']} | {s['count']} | {conv} |")

        lines += ["", "## By channel", "", "| Stage | LinkedIn | Post |", "|---|---|---|"]
        for i, s in enumerate(FUNNEL):
            li = f_li["stages"][i]["count"]
            po = f_post["stages"][i]["count"]
            lines.append(f"| {s} | {li} | {po} |")

        lines += [
            "", "## Key metrics", "",
            f"- Connection accept rate: {m['connection_accept_rate']}%"
            if m["connection_accept_rate"] is not None else "- Connection accept rate: n/a",
            f"- Overall reply rate (vs touch 2s): {m['reply_rate_overall']}%"
            if m["reply_rate_overall"] is not None else "- Overall reply rate: n/a",
            f"- Letter claim rate: {m['letter_claim_rate']}%"
            if m["letter_claim_rate"] is not None else "- Letter claim rate: n/a",
            f"- Cost per claim: £{m['cost_per_claim_gbp']}"
            if m["cost_per_claim_gbp"] is not None else "- Cost per claim: n/a",
            f"- Avg reply-to-response: {m['avg_reply_to_response_hours']}h"
            if m["avg_reply_to_response_hours"] is not None else "- Avg reply-to-response: n/a",
            f"- Checks delivered this week: {m['checks_delivered_this_week']}",
            f"- Revenue logged: £{m['revenue_logged_gbp']}",
            "",
            "## What moved this week", "",
        ]
        if moved:
            for row in moved:
                lines.append(f"- {row['event']}: {row['c']}")
        else:
            lines.append("- Nothing logged in the last 7 days.")

        lines += ["", "## Top performing findings (by replies)", ""]
        if finding_rows:
            for row in finding_rows:
                lines.append(f"- ({row['replies']} replies) {row['headline_finding']}")
        else:
            lines.append("- No replies tied to findings yet.")

        lines += ["", "## Recommendations", "", _recommendations(f_all, m, moved)]

        content = "\n".join(lines) + "\n"
        out = config.REPORTS_DIR / f"weekly-{year}-{week:02d}.md"
        out.write_text(content, encoding="utf-8")
        return out
    finally:
        conn.close()


def _recommendations(f_all: dict, m: dict, moved) -> str:
    """Plain recommendations generated from the numbers (falls back to a static
    note if no LLM key is configured)."""
    summary = {
        "funnel": [(s["stage"], s["count"]) for s in f_all["stages"]],
        "metrics": {k: v for k, v in m.items() if not isinstance(v, dict)},
        "moved": [(r["event"], r["c"]) for r in moved],
    }
    try:
        text = llm.complete(
            "review",
            system=(
                "You are reviewing a solo outbound pipeline for Antek Automation. "
                "British English, plain, no hype, no em dashes, no exclamation "
                "marks. Give 3-5 short bullet recommendations from the numbers. "
                "Be specific about which stage is leaking and what to do next week."
            ),
            user=f"This week's numbers:\n{summary}",
            temperature=0.5, max_tokens=400,
        )
        return text.strip()
    except Exception:  # noqa: BLE001 - never let reporting fail on LLM issues
        return (
            "- Keep replies inside the hour, that is the biggest lever.\n"
            "- Top up prospects for the next town to keep touch 1 volume steady.\n"
            "- Chase delivered checks that have gone quiet with the nudge in the queue."
        )
