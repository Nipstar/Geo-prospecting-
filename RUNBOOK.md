# Geo-prospecting Runbook

Operational commands + standing rules for the prospecting → enrichment → mail
pipeline. Any agent/operator can run these. **All DB-touching steps run serially
on the main box** (one shared `data/pipeline.db`, single writer) — do NOT run two
DB-mutating commands at once.

Run everything with the repo venv: `.venv/bin/python -m src.cli <cmd>`
Env keys live in `.env` (never commit): `OPENROUTER_API_KEY`, `SERPAPI_KEY`,
`GOOGLE_PLACES_API_KEY`, `PROSPEO_API_KEY`, `POSTGRID_API_KEY`, `APIFY_TOKEN`.

## Standing rules
- **Skip franchises** (Keller Williams, RE/MAX, Berkshire Hathaway, Compass, eXp,
  Coldwell Banker, Century 21, Douglas Elliman, Sotheby's…) in enrichment AND
  ingest — `src/franchises.is_franchise()`. Applied automatically.
- **US owner enrichment = "minimal owner address"**: website About-page scrape +
  `gpt-4o-mini` extraction (~£0, server-side, off-context). Falls back to
  "The Owner". B2B DBs (Prospeo/Apollo) only for verified-email reveal.
- **Emails**: owner-matched public emails, **US-only** (cold-email channel).
  UK stays postal/LinkedIn — no cold email.
- **Return address** (POSTGRID_FROM): 4 Highlands Road, Andover, Hampshire, SP10 2PX.
- **US letters** drop UK positioning in body copy (template switches on `market`).

## Pipeline steps

### 1. Ingest prospects (Google Places)
    cli ingest places --sector "real estate" --town "Tampa" --country us
Skips national chains + franchises automatically.

### 2. AI-visibility check (needed before a letter can be built)
    cli check mini --limit 50            # per-company 5-engine check

### 3. Owner enrichment (US) — name + optional email
    cli ingest web-owner --state Florida --limit 50           # fast (requests)
    cli ingest web-owner --state Florida --limit 50 --render-js  # phase-2: JS/Cloudflare sites (Playwright)
    cli ingest web-owner --limit 50 --no-email                # names only
Franchises skipped; report includes franchise_skipped.

### 4. Send letters (PostGrid — server-side render, no local PDF)
    cli postgrid-send --limit 25 --campaign hampshire-solicitors-1 --dry-run
    cli postgrid-send --limit 25 --campaign hampshire-solicitors-1          # test key = sandbox
    cli postgrid-send --limit 25 --campaign hampshire-solicitors-1 --live   # live key required
Records postgrid_id/status on `letters`; dedups by (company, campaign);
skips missing postcode; idempotent; 429/503 backoff. Test key sandboxes (nothing
mailed) but still returns a PDF preview URL.

## Useful SQL (data/pipeline.db)
    -- US mailable inventory
    SELECT COUNT(*) FROM companies c
    WHERE c.registered_address GLOB '*, [A-Z][A-Z] [0-9][0-9][0-9][0-9][0-9]*';
    -- US with named owner
    ... AND EXISTS(SELECT 1 FROM people p WHERE p.company_id=c.id AND p.name IS NOT NULL);
    -- US owner + email (cold-email list)
    SELECT c.name, p.name, p.email FROM companies c JOIN people p ON p.company_id=c.id
    WHERE p.email IS NOT NULL AND p.email <> '';

## Delegation reality
- `worker` cannot run code (no repo/python). `clawdineresearch` = research,
  `clawdine_content` = copy/sequences. Code + DB work runs here (main box), serially.
- Good hand-offs: clawdine_content drafts letter/email copy; clawdineresearch
  finds new towns/sectors + data sources. Execution (ingest/enrich/send) stays here.
