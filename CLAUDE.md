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
  `llm.complete(task, ...)` so models swap in one place. Only the ChatGPT
  visibility probe uses the OpenAI key directly.

## PIPELINE STATUSES (companies.status)

`new → checked → in_sequence → replied → check_delivered → audit_proposed →
client → closed_lost`

## CHANNEL ROUTING

One pipeline, two delivery channels, decided by `cli route`:
- A person record with a `linkedin_url` → LinkedIn 3-touch sequence.
- No LinkedIn person → postal letter to a named director (Companies House for
  Ltds, proprietor / "The Owner" for sole traders). Post is the safe channel
  for both. Letters carry the same headline finding plus a QR code and short URL.
