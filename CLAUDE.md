# CLAUDE.md — geo-outreach

PROJECT: geo-outreach. LinkedIn outbound system for Antek Automation's GEO
(Generative Engine Optimisation) services. Lead magnet is a free AI Visibility
Check. Conversion path: free check, paid GEO audit, monthly retainer.

OPERATOR: Andy Norman, founder of Antek Automation, Andover, Hampshire.
Certified Retell AI Partner. 30+ years in field service and managed print.
Antek Automation is NOT a limited company. Never write "Antek Automation Ltd"
or "Limited" anywhere.

## FULL PIPELINE SKILL

For "run prospecting for [industry] in [area]" style requests, use the
`.claude/skills/full-prospect-pipeline/SKILL.md` skill — it stages
ingest -> route -> enrich -> check -> draft -> bundle -> **mandatory stop and
ask** -> PostGrid send, with the scope questions and final send confirmation
built in as hard gates. Do not hand-chain the individual `cli` commands for a
new batch without it; the skill exists specifically to stop the guardrails
below (franchise/client exclusion, scrape-before-Apollo, worker cross-check,
name cleaning) from being skipped under time pressure.

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

## COMPANY NAME CLEANING (`clean_display_name()` in `src/post/letter.py`)

Company names in the DB are raw Google Places listing titles — they routinely
stack the real business name with SEO taglines, brokerage affiliations, and
credential suffixes. Anywhere a company name is shown to a recipient (letter
body, address block, claim page) MUST go through `clean_display_name()`
first. **Never write `company["name"]` / `co["name"]` directly into
recipient-facing output** — that was the exact bug found twice this session
(see "Known gap" below).

### What it strips

1. **Tagline/affiliation separators** — truncates at the EARLIEST occurrence
   of any of: `" | "`, `" / "`, `" - "`, `" – "`, `" — "`, `" with "`,
   `" at "`, `" @ "`, `" powered by "`, `" brokered by "`, `" in "` (any
   casing). All require surrounding spaces, so a brand's own unspaced text
   is never touched (`RE/MAX`, `LLC/KW St Pete` survive intact).
   - `"Alena Nicole Kolyadchik, LLC / English - Russian speaking Realtor® in Orlando"`
     → `"Alena Nicole Kolyadchik, LLC"`
   - `"Jac Smith Group with Keller Williams Realty St. Pete"` → `"Jac Smith Group"`
   - `"Chris Rogers Realtor - Home Dream Team Clearwater"` → `"Chris Rogers Realtor"`
2. **Trailing Realtor(s)/trademark credential** (`_strip_realtor_suffix()`)
   — `"Chris Rogers Realtor"` → `"Chris Rogers"`, `"Yvette Fuertes REALTOR®"`
   → `"Yvette Fuertes"`. Redundant: the letter body already says "...ask for
   a real estate agent...".
3. **Dangling punctuation** left over from either strip (comma, dash,
   ampersand, slash) is trimmed — but a trailing **period is never touched**,
   it legitimately ends abbreviations (`Inc.`, `Co.`, `P.A.`).

### Safety guard

`_strip_realtor_suffix()` reverts to the original if stripping would leave a
dangling preposition/article as the last word (`of`, `for`, `in`, `the`, `a`,
`an`, `with`, `by`, `at`, `and`, `to`) — catches cases like `"Marco Island Area
Association of Realtors"`, where "Realtors" is part of the org's real name,
not a decorative suffix. Any change to the separator/suffix logic must be
re-tested against the full FL name set before shipping (see workflow below) —
new separators can create new dangling-word edge cases.

### Franchise / client exclusion (separate from name cleaning, same theme)

- **Franchises never get a letter, full stop** — `franchises.is_franchise()`
  is checked at THREE points so no single miss lets one through: `places.py`
  ingest, `router.py` routing (→ `channel='excluded_franchise'`, never
  `post`/`linkedin`), and defensively again at draft time in both
  `letter.py draft_letters_for_post()` and `messages/generate.py
  draft_batch()`. Found 61 franchise letters had leaked through via the
  routing gap alone before this was fixed — always keep all three checks.
- **Clients are permanently excluded** by setting `companies.status='client'`
  (the router/draft queries already filter `status NOT IN
  ('closed_lost','client')`). Pursuit Real Estate (id 604, Jacksonville) was
  set this way — a live voice-agent client, conflict of interest to
  cold-letter. When onboarding a new client that also exists as a prospect
  row: `UPDATE companies SET status='client', channel=NULL WHERE id=?`, then
  delete its `letters` row + PDF file, and remove its claim page from
  `claim-site/dist/<slug>/` before the next deploy.

### Standing workflow: name-cleaning fixes MUST regenerate everything downstream

`clean_display_name()` runs at RENDER time, not ingest time — so a fix to the
function does nothing to already-drafted PDFs/pages/CSVs until you re-run
every consumer. After any change to this function:

1. Regenerate every FL letter PDF in place (same `pdf_path`, same
   `claim_code`/slug — only the body text changes):
   ```python
   # for each row in `letters` joined to FL companies:
   html, meta = letter_mod.render_letter_html(conn, company, letter_no, stamped_address=False)
   letter_mod._render_pdf(html, Path(pdf_path))
   ```
2. Rebuild claim pages: `CAL_LINK=... CLAIM_SITE_URL=... uv run python claim-site/build.py --status checked --limit 2000`
3. Redeploy: `cd claim-site && cp -r functions dist/functions && npx wrangler pages deploy dist --project-name antek-claim --branch main`
4. Rebuild every mailing CSV — **apply `clean_display_name()` to the
   business-name column explicitly**; the CSV builder does NOT get the fix
   for free just because the PDFs were regenerated (this was the second bug
   found this session: the CSV script wrote `r['name']` raw, never calling
   the cleaner, even after the PDFs were already correct).
5. Re-zip the (already-regenerated) PDFs and `gh release upload --clobber`
   both the combined bundle and the region-split batches.

Skipping any of steps 1–5 leaves a stale artifact with the old dirty name
sitting somewhere a human will eventually open.
