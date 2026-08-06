---
name: full-prospect-pipeline
description: Run the entire geo-outreach prospecting pipeline for a new industry + area, from ingest through to a PostGrid send, with mandatory upfront scoping questions and a mandatory final send confirmation. Use when the operator gives an industry and a town/region and wants a new batch built end to end, or asks to "run the prospecting pipeline" / "prospect run".
---

# Full Prospect Pipeline

Orchestrates the existing `cli` subcommands (this skill does not replace any
of them) into one staged run: ingest -> route -> enrich -> visibility check
-> draft -> bundle -> **stop and ask** -> PostGrid send. Every stage already
exists as a working, tested command — this skill's job is sequencing them
correctly and never skipping a guardrail under time pressure.

**Hard rules, no exceptions, regardless of how the operator phrases the
request:**
1. PostGrid send never fires without an explicit confirmation shown *after*
   the operator has seen the full bundle (counts, sample letters, cost).
2. Web-owner scrape (`ingest web-owner`) is always the first and default
   enrichment path. `ingest apollo` / `ingest dbpr` / `ingest sunbiz` only run
   if the operator explicitly opts in to fill a *remaining* gap after the
   scrape — never as the first pass, never automatically. `ingest sunbiz` is
   additionally hard-gated behind `ALLOW_SUNBIZ=1` in the environment.
3. Franchise exclusion (`franchises.is_franchise()`) already runs at 3
   checkpoints in the codebase (ingest, route, draft) — never bypass or
   short-circuit any of them even if a stage is being re-run.
4. Client exclusion (`companies.status='client'`) is filtered by the
   ingest/route/draft queries already — before Stage 1, always ask the
   operator for any conflict-of-interest company names to add to that list
   manually if not already `status='client'` in the DB.
5. Long-running stages (ingest/enrich/check on >20 companies) go via the
   `worker` agent in ~15-20 record chunks, per-record commits, and every
   worker result is cross-checked against a direct `sqlite3` query on
   `data/pipeline.db` before being reported as fact — worker self-reports
   have been wrong or incomplete before.

## Stage 0 — Scope (AskUserQuestion, mandatory, before any command runs)

Ask, batched in one call:
1. **Industry** (free text, e.g. "solicitors", "real estate agents").
2. **Area** — specific towns, or a region to expand. If a region, propose a
   town list back to the operator for confirmation before Stage 1 — do not
   silently pick towns.
3. **Country** — UK or US. This decides the postal channel's confidence:
   - UK: Companies House officer data available for postal addressee (via
     `ingest ch`), full confidence.
   - US: no Companies House equivalent. Postal channel (if wanted at all)
     relies on the web-owner-scrape name path only — lower confidence, and
     LinkedIn is the safe default channel. Say this explicitly, don't let it
     pass silently.
4. **Target volume** — a named-lead count (e.g. "150-200") and a hard max
   ingest cap. Never run unbounded.
5. **Dry run first?** — default yes for a new industry/area combination.
6. **Conflict-of-interest exclusions** — any company/client names to exclude
   up front, regardless of current DB `status`.

Write back a one-paragraph scope summary (towns, expected volume, channel,
exclusions, cost ballpark) and get an explicit go-ahead before Stage 1.

## Stage 1 — Ingest

```
uv run cli ingest places --sector "<industry>" --town "<town>" --max <n> [--country US]
```
Once per town in scope. Franchise + client-status filtering already applies
inside `route`/draft later, but sanity-check the raw ingest count against the
target volume before moving on — if it's wildly over/under, stop and tell the
operator rather than pressing ahead.

## Stage 2 — Route

```
uv run cli route
```
Assigns `channel` (linkedin vs post) per company. Run `--dry-run` first if
Stage 0 chose the cautious path, review the split, then run for real.

## Stage 3 — Enrich (name + email)

**Always this first, for every company without a named person:**
```
uv run cli ingest web-owner --state "<state/county>" --limit 15   # repeat in chunks via worker
```
Only after the free scrape has run to exhaustion for this batch, if there is
still a material gap AND the operator has explicitly opted in:
```
uv run cli ingest apollo ...     # targeted paid reveal on already-named companies only
uv run cli ingest dbpr ...       # FL-specific fallback, rarely useful (see CLAUDE.md)
ALLOW_SUNBIZ=1 uv run cli ingest sunbiz ...   # last resort, explicit env gate required
```
For companies still missing an email after a name is known, use the
email-only backfill (does not touch name/title, only fills email, same
scrape-first policy):
```
uv run cli ingest email-backfill --state "<state>" --limit 15   # repeat via worker
```

### Token-cost discipline for Stages 3-4 (enrichment + visibility check)

Two separate cost surfaces here — do not conflate them:

- **The actual scrape/extract call is already cheap.** `web_owner.py`'s owner
  extraction runs server-side on `openai/gpt-4o-mini` via OpenRouter (hardcoded
  in `_extract_owner()`), off the orchestrating agent's context entirely. No
  change needed there — it's a few cents per company, not a token concern.
- **The waste is orchestration overhead**, not the scrape itself: dispatching
  a full Sonnet-tier `worker` agent turn for every ~15-company chunk means a
  reasoning-tier model spends tokens deciding to run a mechanical bash loop
  that requires zero judgement. Two fixes, both mandatory for batches over
  ~50 companies:
  1. **One continuous resumable loop per dispatch, not many small ones.**
     Give the worker (or run directly) a single Python loop that keeps
     calling `run_web_owner_enrich()` / `run_email_backfill()` /
     `score_company()` in a `while True` until the query returns nothing left
     matching (per-company `conn.commit()`, same pattern as
     `/tmp/fl_check_loop.py` this project has used before) — not a fresh
     agent dispatch per 15-company slice.
  2. **Use the cheapest capable tier for the dispatch itself.** Purely
     mechanical loop-and-report work (no judgement, no writing) does not need
     the `worker` teammate's default model. Prefer the `Agent` tool directly
     with `model: "haiku"` (or `effort: "low"`) for these runs instead of
     `mission-cli create --agent worker`, reserving the Sonnet-tier `worker`
     teammate for chunks that actually need judgement (e.g. resolving a
     stuck/ambiguous entity match, debugging a script failure mid-run).
     `mission-cli`/the `worker` teammate has no per-dispatch model override —
     if the mechanical-loop pattern still needs a background agent (long
     enough to exceed a single turn), use the `Agent` tool with an explicit
     cheap-model override rather than `mission-cli create`.

## Stage 4 — Visibility check

```
uv run cli check mini --town "<towns, comma-separated>" --country <UK|US> --limit 15 --yes
```
Chunked via worker for volume. Cost is shown/estimated automatically by the
command itself (`_estimate_probe_cost`) — surface it to the operator before
each chunk if running many.

For a single named company with custom prompts instead of a batch/town
sweep (operator gives one business + specific questions), use the
`ai-visibility-check-manual` skill instead of this stage — different
flow, same underlying `score.score_company()` call, but with a hard rule
about not letting a brand-name query skew the composite score.

## Stage 5 — Draft content

```
uv run cli draft --batch --status checked --limit <n>     # LinkedIn sequences
uv run cli post draft --limit <n>                          # postal letters, --max-score defaults to 50
```
Both already apply the franchise defensive check and `clean_display_name()`
at render time — do not write `company["name"]` / `co["name"]` directly
anywhere new added to this flow.

`cli post draft` skips any company already scoring 50+ on AI visibility by
default (a "you're invisible" letter is a bad pitch to someone already
visible — wasted postage). Only override `--max-score` if the operator
explicitly asks for a different cutoff or to disable it.

### Letter footer / sender address (standing rule)

- **UK letters**: footer address is Chantry House, 38 Chantry Way, Andover,
  SP10 1LZ.
- **US letters**: footer address is 4 Highlands Road, Andover, Hampshire,
  SP10 2PX.
- **PostGrid return/from address**: always 4 Highlands Road, Andover,
  Hampshire, SP10 2PX, regardless of which market the letter is for
  (`POSTGRID_FROM_LINE1` etc. in `src/config.py` — do not change this for US
  vs UK).
- Address appears in the **footer only**, never the header — header is logo
  only (see `src/post/postgrid_html.py` HEADER comment).

Then build claim pages:
```
CAL_LINK=antek-automation/30min CLAIM_SITE_URL=https://go.antekautomation.com \
  uv run python claim-site/build.py --limit 2000
cd claim-site && cp -r functions dist/functions && npx wrangler pages deploy dist --project-name antek-claim --branch main
```

## Stage 6 — Bundle for review

```
uv run python claim-site/mailing_csv.py --region us      # or --region uk, or no flag for both
```
Writes `data/output/mailing-list-<region>.csv` with `clean_display_name()`
already applied to the business-name column (fixed 2026-07-29 — this file
previously wrote the raw DB name, the same bug class caught earlier in the
letters/claim-pages pipeline; always verify this column is clean before
handing a CSV to the operator).

Also zip the drafted letter PDFs for this batch and push both to a GitHub
release (`gh release create`/`gh release upload --clobber`), per the standing
file-storage workaround (binaries as GitHub releases, not Drive).

Present to the operator:
- Total ingested, named, emailed, lettered, LinkedIn-sequenced.
- 3-5 sample rendered letters/messages for spot-check.
- Projected PostGrid cost (per-letter rate x count, UK/US split by class).
- CSV + PDF zip + claim-site URL.

## Stage 7 — Final send gate (mandatory, cannot be pre-answered in Stage 0)

Only after Stage 6's summary has actually been shown. AskUserQuestion:
- Send all now / send a small test batch first (e.g. 5) / hold, don't send.

```
uv run cli postgrid-send --limit <n> --campaign <name> --dry-run   # validate first, always
uv run cli postgrid-send --limit <n> --campaign <name>             # sandbox key by default
uv run cli postgrid-send --limit <n> --campaign <name> --live      # only once explicitly confirmed live
```
If `--dry-run` surfaces address/merge-var failures, report them and do not
offer the live-send option until fixed — a broken send should never be a
choice on the table.

If a test batch was chosen, re-run Stage 7's ask before sending the
remainder.

## Guardrail checklist (verify before ever calling `postgrid-send` for real)

- [ ] Franchise check confirmed at ingest, route, and draft (all 3, not 2).
- [ ] No company with `status='client'` in the outgoing batch.
- [ ] No Sunbiz/Apollo/DBPR bulk run without explicit operator opt-in this run.
- [ ] CSV business-name column passed through `clean_display_name()`.
- [ ] Worker-reported counts cross-checked against `sqlite3 data/pipeline.db`.
- [ ] US postal reliance on web-scrape naming (not Companies House) was
      flagged to the operator, not silently treated as UK-equivalent confidence.
- [ ] `--dry-run` passed clean before any live PostGrid send.
