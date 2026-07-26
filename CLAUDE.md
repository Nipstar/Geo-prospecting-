# CLAUDE.md — geo-outreach

PROJECT: geo-outreach. LinkedIn outbound system for Antek Automation's GEO
(Generative Engine Optimisation) services. Lead magnet is a free AI Visibility
Check. Conversion path: free check, paid GEO audit, monthly retainer.

OPERATOR: Andy Norman, founder of Antek Automation, Andover, Hampshire.
Certified Retell AI Partner. 30+ years in field service and managed print.
Antek Automation is NOT a limited company. Never write "Antek Automation Ltd"
or "Limited" anywhere.

## VOICE RULES (apply to every generated message and report)

- Direct British English. Short sentences. Plain words.
- No em dashes. No exclamation marks. No hype words: elevate, leverage,
  supercharge, game-changer, unlock, revolutionise.
- Write like a peer. Contractions are fine.
- First messages ask for a reply, never a meeting.
- Max 4-5 short lines per LinkedIn message.

## OFFER FACTS (never exaggerate beyond these)

- Research finding: roughly 85% of UK SMEs have zero measurable AI visibility.
- Free AI Visibility Check: how the prospect's business appears across ChatGPT,
  Perplexity and Google AI Overviews for their service and town, delivered as a
  short branded report.
- Paid follow-on: full GEO audit, then implementation retainer.
- Verified proof: 100% Share of AI Voice for "ai voice agents andover" measured
  with Local Falcon. Do not invent client results.

## COMPLIANCE

- All LinkedIn sends are manual. This system never automates sending. It drafts,
  queues and tracks. The operator copies and sends.
- Log source and source_date on every prospect record (PECR/GDPR hygiene, B2B
  legitimate interest basis).
- Apify is for Google Places, Companies House style enrichment and public web
  data. LinkedIn scraping is off by default.

## CONVENTIONS

- `uv run` for everything. All CLI via `src/cli.py` (entry point `cli`).
- SQLite only, no ORM. Single file at `data/pipeline.db`.
- Reports and letters use the Antek brand system defined in
  `src/reports/brand.py` (coral #CD5C3C, cream #E8DCC8, sage #C8D8D0,
  charcoal #2C2C2C, Outfit display, DM Sans body, JetBrains Mono, zero
  border-radius, hard offset shadows).
- Model routing lives in `src/config.py` MODELS. Every module calls
  `llm.complete(task, ...)` so models swap in one place.
- Visibility check (hybrid strategy, aligned with geo-slab): four consumer
  flagships via one OpenRouter key (`config.CHECK_MODELS`: ChatGPT
  gpt-5.2-chat, Claude sonnet-5, Gemini 2.5-flash, Perplexity sonar) plus Google
  AI Overview via SerpAPI. Composite is the geo-slab 70/30 rubric
  (platforms + prompts). If every engine errors, the check raises rather than
  writing a fake 0/100. Competitor names pass `visibility/competitor_gate.py`
  (brand-aware, no self-mentions, no directories). Rubric, brand detection and
  the gate are adapted from github.com/Nipstar/geo-slab.
- Pitchability (`visibility/pitchability.py`, geo-slab rubric) orders the queue
  and batch drafting so the best leads go first.

## PIPELINE STATUSES (companies.status)

`new → checked → in_sequence → replied → check_delivered → audit_proposed →
client → closed_lost`

## CHANNEL ROUTING

One pipeline, two delivery channels, decided by `cli route`:
- A person record with a `linkedin_url` → LinkedIn 3-touch sequence.
- No LinkedIn person → postal letter to a named director (Companies House for
  Ltds, proprietor / "The Owner" for sole traders). Post is the safe channel
  for both. Letters carry the same headline finding plus a QR code and short URL.

## POSTGRID LETTER SYSTEM

Letters sent via PostGrid using editable portal templates (not inline HTML).

### Templates (sandbox — rotate to live key before volume send)
- UK A4:    `POSTGRID_TEMPLATE_UK`  (set in .env)
- US Letter: `POSTGRID_TEMPLATE_US` (set in .env)
- Re-upload after any HTML change: `python scripts/create_postgrid_templates.py`
- Edit visually: https://app.postgrid.com/templates

### Merge variables (passed via `build_merge_variables()` in `src/post/letter.py`)
| Variable      | Value |
|---------------|-------|
| `addresseeName` | Named person or "The Owner" |
| `salutation`    | "Mr Smith" / "Ms Jones" / "Sir or Madam" — never "The Owner" |
| `addrBlock`     | Pre-formatted newline-separated address block (white-space:pre-line) |
| `headline`      | AI finding sentence |
| `town`          | Company town |
| `sector`        | Service noun, always pluralised ("solicitors" not "solicitor") |
| `dateStr`       | "26 July 2026" |
| `claimUrl`      | Full https:// claim URL |

### Key constraints discovered
- PostGrid uppercases ALL `{{to.*}}` contact vars internally (postal compliance).
  Use custom `{{addrBlock}}` merge var instead — built in `postgrid_send.py`.
- PostGrid template validator rejects Handlebars block helpers (`{{#if}}`).
  All conditional logic (e.g. suppress company line for sole traders) must be
  done server-side in Python before passing merge vars.
- `color=True` is hardcoded in the send loop — all letters print in colour.
- `addressPlacement=insert_blank_page` — page 1 is PostGrid's auto envelope
  address page (always uppercase, uneditable from HTML).

### Salutation fallback chain (letter.py `_addressee()`)
1. Official DB record (Companies House officer, Sunbiz, LinkedIn)
2. Any person record in the DB
3. Heuristic: name embedded in company name (e.g. "Jose Fuentes Real Estate")
   — fires when first token has detectable gender, second token is not a
   business/location word, third token IS a business word.
4. "The Owner" / "Sir or Madam"

### Address formatting (`postgrid_send.py`)
- `_title_addr()` converts ALL-CAPS Companies House addresses to title case.
- Postcodes/zip codes always stay uppercase.
- Ordinal suffixes handled: "1ST FLOOR" → "1st Floor".
- Known abbreviations restored: LLP, PLC, NHS, Ltd, LLC.

### Claim URL
`go.antekautomation.com/<slug>` (CNAME → antek-claim.pages.dev).
Set `CLAIM_SITE_URL=https://go.antekautomation.com` in .env.
