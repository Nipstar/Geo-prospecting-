# geo-outreach

LinkedIn and postal client acquisition for Antek Automation's GEO (Generative
Engine Optimisation) services. The lead magnet is a free AI Visibility Check.
Conversion path: free check → paid GEO audit → monthly retainer.

The system drafts, queues and tracks. It never sends on your behalf. All
LinkedIn sends are manual, and nothing posts to Stannp without an explicit
approval step.

## The funnel

```
Apify / CSV / Airtable → Companies House enrichment → SQLite pipeline
→ mini visibility check (the opener finding) → CHANNEL ROUTER
   ├── person has a LinkedIn URL → 3-touch LinkedIn sequence (you copy + send)
   └── no LinkedIn person        → personalised letter to a director (Stannp)
→ reply / claim → deliver the free AI Visibility Check PDF
→ follow-up → paid GEO audit → retainer
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies
cp .env.example .env          # then fill in your keys
uv run python -m src.db       # create data/pipeline.db (also auto-runs on any command)
```

WeasyPrint (PDF rendering) needs system libraries on some machines:
`libpango`, `libcairo`, `libgdk-pixbuf`, `libffi`. On Debian/Ubuntu:
`sudo apt-get install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev`.

### Keys

| Key | Used for |
|---|---|
| `OPENROUTER_API_KEY` | all LLM calls except the ChatGPT probe (routing in `src/config.py`) |
| `OPENAI_API_KEY` | the ChatGPT visibility probe only (real engine) |
| `SERPAPI_KEY` | Google AI Overview probe |
| `APIFY_TOKEN` | Google Places prospecting |
| `COMPANIES_HOUSE_API_KEY` | directors, registration, SIC codes |
| `STANNP_API_KEY` | postal letters |
| `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID` | Airtable import |
| `CLAIM_BASE_URL` | short-URL base for letter claim links |

## Fastest path to first send

Prompts 1-4 with the Airtable import get your existing estate-agent records
checked and sequenced without any new scraping:

```bash
uv run cli ingest csv path/to/estate-agents.csv     # or: cli ingest airtable --table "Estate Agents"
uv run cli ingest ch --status new --limit 50         # directors + registration
uv run cli route                                     # linkedin vs post
uv run cli check mini --status new --limit 10        # the opener findings
uv run cli draft --batch --status checked --limit 10 # the sequences
uv run cli queue                                     # today's work list
```

## Daily routine (about 30 minutes)

1. `uv run cli queue` — respond to replies first, always. Within the hour.
2. Send due touch 2s and 3s, log each: `uv run cli sent --touch-id N`.
3. Send up to 15 new connection notes from the drafted pool.
4. Log any accepts: `uv run cli accepted --person-id N`.
5. Deliver promised checks: `uv run cli check full --company-id N --yes` then
   `uv run cli delivered --company-id N`.

For the postal side: approve drafted letters (`cli post approve --letter-id N`),
then send the batch (`cli post send --approved --yes`). Nothing posts without
that approve step.

## Weekly routine

```bash
uv run cli stats                       # funnel + metrics, split by channel
uv run cli stats weekly                # writes output/reports/weekly-YYYY-WW.md
uv run cli ingest places --sector "solicitors" --town Winchester --max 50
uv run cli check mini --status new     # overnight
uv run cli draft --batch               # in the morning
```

## Commands

```
ingest places|csv|airtable|enrich|ch   prospecting + enrichment
route                                   set channel (linkedin | post)
person add                              manual person record
check mini|full|show                    visibility checks
draft [--person-id | --batch]           3-touch LinkedIn sequence
audit --touch-id                        pre-send message audit
opener --company-id --profile           three opener options from profile text
log-reply --person-id --text            classify + draft a response
sent / accepted / delivered             log pipeline actions
audit-proposed / audit-paid / retainer  log revenue events
followup / followup-nudge / closed      timing + close
queue                                   the daily work list
post draft|approve|send|followup        postal channel
claim code|import                       letter claim handling
stats [--channel] / stats weekly        reporting
```

Slash commands (in Claude Code): `/prospect`, `/check`, `/draft`, `/audit`,
`/opener`, `/reply`, `/today`, `/stats`, `/week`.

## Channel routing

`cli route` sets `companies.channel`:

- Any person on the company has a `linkedin_url` → `linkedin`.
- No LinkedIn person, and the company has been in the pipeline 7+ days (or
  `--force`) → `post`. The letter is addressed to a Companies House director
  (Ltd) or the proprietor / "The Owner" (sole trader).

## Letter claims (external wiring)

There is no web server in this project. The short URL (`CLAIM_BASE_URL/{code}`)
is redirected and logged by your existing n8n + Contabo stack. Expected webhook
payload:

```json
{ "claim_code": "XYZ123", "claimed_at": "2026-07-11T10:22:00Z",
  "user_agent": "...", "ip": "..." }
```

Simplest wiring: an n8n workflow appends each `claim_code` (one per line) to a
`claims.csv`, which you import:

```bash
uv run cli claim import claims.csv     # or a single one: cli claim code XYZ123
```

A claim moves the company to `replied` and surfaces "deliver free check" in the
daily queue.

## Compliance

- All LinkedIn sends are manual. This system never automates sending.
- Prospect data is B2B, legitimate-interest basis. `source` and `source_date`
  are logged on every record.
- Apify is used for Google Places and public web data only. LinkedIn scraping is
  off by default and not built into this project.
- Antek Automation is not a limited company. Nothing generated writes "Ltd".

## Layout

```
src/db.py            schema, migrations, helpers, status engine
src/config.py        model routing, caps, cadence, paths
src/llm.py           OpenRouter wrapper (llm.complete(task, ...))
src/ingest/          places, csv, airtable, enrich, companies_house, router
src/visibility/      prompts, probes, score, report (the check engine)
src/messages/        generate, audit, replies, pipeline, queue, voice
src/post/            letter, stannp, claims (postal channel)
src/reports/         brand.py (Antek tokens), stats.py (funnel + weekly)
.claude/skills/      antek-outreach-voice
.claude/commands/    slash-command wrappers
```
