"""geo-outreach command line. Everything runs through here: `uv run cli ...`.

Module imports are done lazily inside commands so a missing optional dependency
or API key never breaks unrelated commands.
"""
from __future__ import annotations

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import config, db

console = Console()


@click.group()
def cli() -> None:
    """Antek geo-outreach: LinkedIn + postal client acquisition for GEO."""
    db.run_migrations()


# =========================================================================
# INGEST
# =========================================================================
@cli.group()
def ingest() -> None:
    """Prospect ingestion: places, csv, airtable, enrich, companies house."""


@ingest.command("places")
@click.option("--sector", required=True)
@click.option("--town", required=True)
@click.option("--max", "max_results", default=50, type=int)
@click.option("--dry-run", is_flag=True)
def ingest_places(sector: str, town: str, max_results: int, dry_run: bool) -> None:
    from .ingest import places

    res = places.run_places_search(sector, town, max_results, dry_run=dry_run)
    console.print(res)


@ingest.command("csv")
@click.argument("path")
@click.option("--dry-run", is_flag=True)
def ingest_csv(path: str, dry_run: bool) -> None:
    from .ingest import csv_import

    res = csv_import.import_csv(path, dry_run=dry_run)
    console.print(res)


@ingest.command("airtable")
@click.option("--table", required=True)
@click.option("--dry-run", is_flag=True)
def ingest_airtable(table: str, dry_run: bool) -> None:
    from .ingest import airtable_sync

    res = airtable_sync.sync_table(table, dry_run=dry_run)
    console.print(res)


@ingest.command("enrich")
@click.option("--limit", default=20, type=int)
@click.option("--dry-run", is_flag=True)
def ingest_enrich(limit: int, dry_run: bool) -> None:
    from .ingest import enrich

    res = enrich.run_enrich(limit, dry_run=dry_run)
    console.print(res)


@ingest.command("ch")
@click.option("--status", default="new")
@click.option("--limit", default=50, type=int)
@click.option("--dry-run", is_flag=True)
def ingest_ch(status: str, limit: int, dry_run: bool) -> None:
    from .ingest import companies_house

    res = companies_house.run_ch(status, limit, dry_run=dry_run)
    console.print(res)


@cli.command("pitchability")
@click.option("--limit", default=None, type=int)
def pitchability_cmd(limit: int | None) -> None:
    """Recompute pitchability (who to pitch first) for all live companies."""
    from .visibility import pitchability

    tally = pitchability.rescore_all(limit)
    console.print(f"[green]Rescored.[/green] premium={tally['premium']} "
                  f"standard={tally['standard']} skip={tally['skip']}")


@cli.command("route")
@click.option("--dry-run", is_flag=True)
@click.option("--force", is_flag=True, help="Ignore the 7-day grace before routing to post.")
def route(dry_run: bool, force: bool) -> None:
    """Set channel per the routing rule (linkedin if a person has a URL, else post)."""
    from .ingest import router

    res = router.route_all(dry_run=dry_run, force=force)
    console.print(res)


@cli.group()
def person() -> None:
    """Manual person records."""


@person.command("add")
@click.option("--company-id", required=True, type=int)
@click.option("--name", default="")
@click.option("--role", default="")
@click.option("--linkedin", default="")
def person_add(company_id: int, name: str, role: str, linkedin: str) -> None:
    conn = db.get_connection()
    try:
        pid = db.insert_person(
            conn, company_id, name=name or None, role=role or None,
            linkedin_url=linkedin or None, person_source="manual",
            connection_status="none" if linkedin else "n/a",
        )
        console.print(f"[green]Added person {pid} to company {company_id}[/green]")
    finally:
        conn.close()


# =========================================================================
# CHECK (visibility)
# =========================================================================
@cli.group()
def check() -> None:
    """Visibility checks: mini (opener finding) and full (PDF report)."""


def _estimate_probe_cost(n_companies: int) -> float:
    per = sum(config.COST_PER_PROBE.values()) * config.FREE_CHECK_QUERIES
    return round(per * n_companies, 2)


@check.command("mini")
@click.option("--company-id", type=int)
@click.option("--status", default=None)
@click.option("--limit", default=10, type=int)
@click.option("--yes", is_flag=True, help="Skip the cost confirmation.")
def check_mini(company_id: int | None, status: str | None, limit: int, yes: bool) -> None:
    from .visibility import score

    conn = db.get_connection()
    try:
        if company_id:
            targets = [db.get_company(conn, company_id)]
        elif status:
            targets = db.get_companies_by_status(conn, status, limit)
        else:
            console.print("[red]Pass --company-id or --status.[/red]")
            return
        targets = [t for t in targets if t]
        est = _estimate_probe_cost(len(targets))
        console.print(f"[yellow]About to run mini checks on {len(targets)} companies. "
                      f"Estimated API cost: ~${est}[/yellow]")
        if not yes and not click.confirm("Proceed?"):
            return
        for company in targets:
            try:
                res = score.score_company(conn, company)
            except score.VisibilityProbeError as exc:
                console.print(f"[red]{company['name']}: {exc}[/red]")
                continue
            console.print(Panel(
                f"[bold]{company['name']}[/bold]  score {res['composite']}/100  "
                f"({res['platforms_mentioned']}/{res['platforms_tested']} engines)\n{res['headline']}",
                title=f"mini check · {company['town']}",
            ))
    finally:
        conn.close()


@check.command("full")
@click.option("--company-id", required=True, type=int)
@click.option("--yes", is_flag=True)
def check_full(company_id: int, yes: bool) -> None:
    from .visibility import report, score as score_mod

    est = _estimate_probe_cost(1)
    console.print(f"[yellow]Full check runs fresh probes + builds a PDF. Est cost ~${est}[/yellow]")
    if not yes and not click.confirm("Proceed?"):
        return
    conn = db.get_connection()
    try:
        company = db.get_company(conn, company_id)
        try:
            res = report.build_full_report(conn, company)
        except score_mod.VisibilityProbeError as exc:
            console.print(f"[red]{exc}[/red]")
            return
        console.print(f"[green]Report: {res['report_path']}[/green] (score {res['composite']}/100)")
    finally:
        conn.close()


@check.command("show")
@click.option("--company-id", required=True, type=int)
def check_show(company_id: int) -> None:
    conn = db.get_connection()
    try:
        c = db.get_company(conn, company_id)
        chk = db.latest_check(conn, company_id)
        if not chk:
            console.print("[yellow]No check yet.[/yellow]")
            return
        t = Table(title=f"{c['name']} — latest {chk['check_type']} check")
        t.add_column("field"); t.add_column("value")
        for k in ("run_date", "composite_score", "platforms_mentioned", "platforms_tested",
                  "chatgpt_score", "claude_score", "gemini_score", "perplexity_score",
                  "ai_overview_score", "cost_usd", "competitor_named", "report_path"):
            t.add_row(k, str(chk[k]))
        console.print(t)
        console.print(Panel(chk["headline_finding"] or "", title="headline finding"))
    finally:
        conn.close()


# =========================================================================
# DRAFT / AUDIT (messages)
# =========================================================================
@cli.command("draft")
@click.option("--person-id", type=int)
@click.option("--batch", is_flag=True)
@click.option("--status", default="checked")
@click.option("--limit", default=10, type=int)
def draft(person_id: int | None, batch: bool, status: str, limit: int) -> None:
    """Draft a 3-touch LinkedIn sequence."""
    from .messages import generate

    if batch:
        results = generate.draft_batch(status, limit)
        console.print(f"[green]Drafted {len(results)} sequences.[/green]")
        return
    if not person_id:
        console.print("[red]Pass --person-id or --batch.[/red]")
        return
    res = generate.draft_sequence(person_id)
    for n in (1, 2, 3):
        console.print(Panel(res[f"touch{n}"], title=f"Touch {n}"))
    console.print(f"[dim]Strongest: {res['strongest_element']} | Watch: {res['weak_spots']}[/dim]")


@cli.command("audit")
@click.option("--touch-id", required=True, type=int)
def audit(touch_id: int) -> None:
    """Pre-send audit of a drafted touch."""
    from .messages import audit as audit_mod

    res = audit_mod.audit_touch(touch_id)
    console.print(Panel(str(res["flags"]), title="flags"))
    console.print(f"[bold]Verdict:[/bold] {res['verdict']}")
    console.print(Panel(res["tightened"] or "(none)", title="tightened"))
    console.print(f"[dim]{res['notes']}[/dim]")


@cli.command("opener")
@click.option("--company-id", required=True, type=int)
@click.option("--profile", required=True, help="Pasted LinkedIn profile text.")
def opener(company_id: int, profile: str) -> None:
    """Three opener options from pasted profile text + a company id."""
    from .messages import voice
    from . import llm

    conn = db.get_connection()
    try:
        company = db.get_company(conn, company_id)
        chk = db.latest_check(conn, company_id)
    finally:
        conn.close()
    user = (
        f"Company: {company['name']} ({company['town']}, {company['sector']}). "
        f"Finding: {chk['headline_finding'] if chk else 'none yet'}.\n\n"
        f"Their LinkedIn profile text:\n{profile}\n\n"
        "Give three touch-1 connection-note options (each under 280 chars, no "
        "pitch, no link). Mark the strongest and say why in one line. "
        "Return JSON: {\"options\": [\"...\",\"...\",\"...\"], \"strongest\": 1, \"why\": \"...\"}"
    )
    raw = llm.complete("generate", system=voice.system_prompt(), user=user,
                       temperature=0.7, max_tokens=500, json_mode=True)
    data = llm.parse_json(raw)
    for i, opt in enumerate(data.get("options", []), 1):
        marker = " [bold green]<- strongest[/bold green]" if i == data.get("strongest") else ""
        console.print(Panel(opt, title=f"Option {i}{marker}"))
    console.print(f"[dim]{data.get('why','')}[/dim]")


# =========================================================================
# PIPELINE / CRM
# =========================================================================
@cli.command("log-reply")
@click.option("--person-id", required=True, type=int)
@click.option("--text", required=True)
def log_reply_cmd(person_id: int, text: str) -> None:
    """Classify an inbound reply and draft a response."""
    from .messages import replies

    res = replies.log_reply(person_id, text)
    console.print(f"[bold]Intent:[/bold] {res['intent']}  [bold]Urgency:[/bold] {res['urgency']}")
    console.print(f"[dim]{res['read_line']}[/dim]")
    console.print(Panel(res["drafted"], title="drafted response"))


@cli.command("sent")
@click.option("--touch-id", required=True, type=int)
def sent(touch_id: int) -> None:
    """Log a LinkedIn touch as sent."""
    from .messages import pipeline

    console.print(pipeline.mark_touch_sent(touch_id))


@cli.command("sent-reply")
@click.option("--reply-id", required=True, type=int)
def sent_reply(reply_id: int) -> None:
    """Mark a reply's response as sent (records reply-to-response time)."""
    from datetime import datetime

    conn = db.get_connection()
    try:
        db.update_reply(conn, reply_id, response_sent_at=datetime.now().isoformat(timespec="seconds"))
        console.print(f"[green]Reply {reply_id} response marked sent.[/green]")
    finally:
        conn.close()


@cli.command("accepted")
@click.option("--person-id", required=True, type=int)
def accepted(person_id: int) -> None:
    """Mark a connection accepted (schedules touch 2)."""
    from .messages import pipeline

    console.print(pipeline.mark_accepted(person_id))


@cli.command("delivered")
@click.option("--company-id", required=True, type=int)
def delivered(company_id: int) -> None:
    """Mark the free check delivered."""
    from .messages import pipeline

    console.print(pipeline.mark_check_delivered(company_id))


@cli.command("audit-proposed")
@click.option("--company-id", required=True, type=int)
@click.option("--value", type=float, default=None)
def audit_proposed(company_id: int, value: float | None) -> None:
    from .messages import pipeline

    console.print(pipeline.mark_audit_proposed(company_id, value))


@cli.command("audit-paid")
@click.option("--company-id", required=True, type=int)
@click.option("--value", required=True, type=float)
def audit_paid(company_id: int, value: float) -> None:
    from .messages import pipeline

    console.print(pipeline.mark_audit_paid(company_id, value))


@cli.command("retainer")
@click.option("--company-id", required=True, type=int)
@click.option("--value", required=True, type=float)
def retainer(company_id: int, value: float) -> None:
    from .messages import pipeline

    console.print(pipeline.log_retainer(company_id, value))


@cli.command("closed")
@click.option("--company-id", required=True, type=int)
@click.option("--reason", default="manual")
def closed(company_id: int, reason: str) -> None:
    from .messages import pipeline

    console.print(pipeline.mark_closed(company_id, reason))


@cli.command("followup")
@click.option("--company-id", required=True, type=int)
@click.option("--date", "fdate", required=True)
def followup(company_id: int, fdate: str) -> None:
    """Set a reconnect date on a company (not_now replies)."""
    from .messages import pipeline

    console.print(pipeline.set_followup(company_id, fdate))


@cli.command("followup-nudge")
@click.option("--company-id", required=True, type=int)
def followup_nudge(company_id: int) -> None:
    """Draft an in-voice nudge for a delivered-but-quiet check."""
    from .messages import generate

    res = generate.draft_check_nudge(company_id)
    console.print(Panel(res["nudge"], title=f"nudge · {res['company']}"))


# =========================================================================
# QUEUE (daily)
# =========================================================================
@cli.command("queue")
def queue_cmd() -> None:
    """The daily work list, in order."""
    from .messages import queue as queue_mod

    q = queue_mod.build_queue()
    _print_queue(q)


def _print_queue(q: dict) -> None:
    console.rule("[bold]a. Replies awaiting response")
    if not q["replies"]:
        console.print("[dim]none[/dim]")
    for r in q["replies"]:
        colour = "red" if r["red"] else "yellow"
        console.print(f"[{colour}]{r['hours']}h[/{colour}] {r['company']} — {r['person']} "
                      f"({r['intent']}, {r['urgency']})")
        console.print(Panel(r["drafted"] or "(draft with log-reply)", title="response"))
        console.print(f"[dim]{r['cmd']}[/dim]")

    for label, key in (("b. Touch 2 due", "touch2"), ("c. Touch 3 due", "touch3"),
                       ("d. New connection notes", "new_connections")):
        console.rule(f"[bold]{label}")
        items = q[key]
        if not items:
            console.print("[dim]none[/dim]")
        for it in items:
            console.print(f"[bold]{it['company']}[/bold] — {it['person']}")
            console.print(Panel(it["text"] or "", title=f"touch {it['touch_no']}"))
            console.print(f"[dim]{it['cmd']}[/dim]")

    console.rule("[bold]e. Free checks promised, not delivered")
    for it in q["checks_promised"] or []:
        console.print(f"{it['company']}  [dim]{it['cmd']}[/dim]")
    if not q["checks_promised"]:
        console.print("[dim]none[/dim]")

    console.rule("[bold]Follow-up nudges (check delivered, gone quiet)")
    for it in q["check_nudges"] or []:
        console.print(f"{it['company']}  [dim]{it['cmd']}[/dim]")
    if not q["check_nudges"]:
        console.print("[dim]none[/dim]")

    post = q["post"]
    console.rule("[bold]POST — letters awaiting approval")
    for it in post["drafted_awaiting_approval"] or []:
        console.print(f"company {it['company_id']}  [dim]{it['cmd']}[/dim]")
    if not post["drafted_awaiting_approval"]:
        console.print("[dim]none[/dim]")
    console.rule("[bold]POST — follow-ups due")
    for it in post["followups_due"] or []:
        console.print(f"company {it['company_id']}  [dim]{it['cmd']}[/dim]")
    if not post["followups_due"]:
        console.print("[dim]none[/dim]")
    console.rule("[bold]POST — claims awaiting check delivery")
    for it in post["claims_awaiting_check"] or []:
        console.print(f"{it['company']}  [dim]{it['cmd']}[/dim]")
    if not post["claims_awaiting_check"]:
        console.print("[dim]none[/dim]")


# =========================================================================
# POST (letters)
# =========================================================================
@cli.group()
def post() -> None:
    """Postal channel: draft, approve, send, follow-up."""


@post.command("draft")
@click.option("--limit", default=25, type=int)
@click.option("--dry-run", is_flag=True)
def post_draft(limit: int, dry_run: bool) -> None:
    from .post import letter

    res = letter.draft_letters_for_post(limit, dry_run=dry_run)
    console.print(f"[green]Drafted {len(res)} letters.[/green]")


@post.command("followup")
@click.option("--company-id", required=True, type=int)
def post_followup(company_id: int) -> None:
    from .post import letter

    res = letter.draft_followup(company_id)
    console.print(f"[green]Follow-up letter {res['letter_id']} -> {res['pdf_path']}[/green]")


@post.command("approve")
@click.option("--letter-id", required=True, type=int)
def post_approve(letter_id: int) -> None:
    from .post import claims

    console.print(claims.approve_letter(letter_id))


@post.command("send")
@click.option("--approved", is_flag=True, help="Send all approved letters.")
@click.option("--yes", is_flag=True, help="Actually post via Stannp.")
def post_send(approved: bool, yes: bool) -> None:
    from .post import stannp

    if not approved:
        console.print("[red]Pass --approved to send the approved batch.[/red]")
        return
    console.print(stannp.send_approved(yes=yes))


@cli.group()
def claim() -> None:
    """Letter claim handling."""


@claim.command("code")
@click.argument("code")
def claim_code_cmd(code: str) -> None:
    from .post import claims

    console.print(claims.claim_code(code))


@claim.command("import")
@click.argument("path")
def claim_import(path: str) -> None:
    from .post import claims

    console.print(claims.import_claims(path))


# =========================================================================
# STATS / REPORTING
# =========================================================================
@cli.group(invoke_without_command=True)
@click.option("--channel", default=None, type=click.Choice(["linkedin", "post"]))
@click.pass_context
def stats(ctx: click.Context, channel: str | None) -> None:
    """Funnel table + key metrics. `stats weekly` writes the markdown review."""
    if ctx.invoked_subcommand is not None:
        return
    from .reports import stats as stats_mod

    def _funnel_table(chan: str | None) -> None:
        f = stats_mod.funnel(chan)
        t = Table(title=f"Funnel ({f['channel']})")
        t.add_column("stage"); t.add_column("count", justify="right"); t.add_column("conv", justify="right")
        for s in f["stages"]:
            conv = f"{s['conv_from_prev']}%" if s["conv_from_prev"] is not None else "-"
            t.add_row(s["stage"], str(s["count"]), conv)
        console.print(t)

    if channel:
        _funnel_table(channel)
    else:
        _funnel_table("linkedin")
        _funnel_table("post")
        _funnel_table(None)

    m = stats_mod.metrics()
    mt = Table(title="Metrics")
    mt.add_column("metric"); mt.add_column("value", justify="right")
    for k, v in m.items():
        mt.add_row(k, str(v))
    console.print(mt)


@stats.command("weekly")
def stats_weekly() -> None:
    """Write the weekly markdown review to output/reports/weekly-YYYY-WW.md."""
    from .reports import stats as stats_mod

    path = stats_mod.weekly_report()
    console.print(f"[green]Weekly review: {path}[/green]")


if __name__ == "__main__":
    cli()
