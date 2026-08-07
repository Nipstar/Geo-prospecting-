---
name: ai-visibility-check-manual
description: Run a one-off AI visibility check for a single named company with custom prompts (not the batch prospecting pipeline). Use when the operator gives a specific business + a list of questions to check across AI engines, and wants a claim page + full walk-through report + client PDF.
---

# Manual AI Visibility Check

For a single company, custom prompts, on demand — e.g. "run a visibility check
for flowformtax.co.uk with these 5 questions". Different from
`full-prospect-pipeline`, which is a batch sector/town search with
auto-generated prompts.

## Brand-name queries are auto-excluded from scoring (code-enforced)

`score.score_company()` (`src/visibility/score.py`) auto-detects brand
queries via `is_brand_query(query, company_name)` — true when the company
name's core tokens (legal suffixes stripped) appear inside the query text
itself, e.g. "What is Acme Ltd?", "Acme reviews". These are still probed
and still show up in the report (full `queries` list is stored/rendered),
but excluded from every scoring tally: `platforms_tested`,
`platforms_mentioned`, `prompts_total`, `prompts_mentioned`, per-engine
scores, and the headline's "asked N different ways" count.

**Why this exists**: a self-referential query trivially passes on every
engine that has ever indexed the company's own site — the question already
contains the answer. Before this fix, mixing one into the scored set could
massively inflate the composite. Caught on a real check (FlowFormTax,
2026-08-06): 0/4 on genuine discovery questions, 5/5 on "What is
FlowFormTax?" alone, blended composite came out 76/100 — completely
misrepresenting an actual finding of total invisibility. Fixed first as a
manual workaround (split queries by hand before calling score_company),
then baked into `score_company()` itself so no operator discipline is
required — pass all the queries the operator gave you, brand question
included, and it self-corrects.

No special handling needed at call time: just pass every question the
operator gave you into `queries=`. Only worth a heads-up to the operator if
`is_brand_query()` would classify something unexpectedly (rare — check with
`from src.visibility.score import is_brand_query` if unsure on an edge case).

## Steps

0. **Ask market before running anything, if not obviously implied.** The
   AI Overview engine is geo-parameterized (`config.country_geo()`) — a US
   company checked under the default UK market still runs, still scores,
   and looks completely normal, it's just quietly wrong for one of the five
   engines. This happened for real on xautomatex.net (2026-08-07): ran
   under the silent UK default, had to be redone once the operator asked.
   Don't silently default. Check for a signal first (currency on the site,
   `.com` vs a country-code TLD, an explicit address/phone) and if it's not
   obvious, ask. `--country` accepts any key in `config._COUNTRY_GEO`
   (check that dict for the current list — UK and US at minimum).

1. **Run `cli check custom`** — this is the whole flow (insert-or-reuse
   company, run the check, render the PDF) in one command:
   ```
   uv run cli check custom \
     --name "Company Name" --website "https://example.com/" \
     --sector "..." \
     --query "..." --query "..." --query "..." --query "..." \
     --country US --yes
   ```
   Or `--company-id N` instead of `--name`/`--website` for a company already
   in the DB. Repeat `--query` for each question — typical shape mirrors how
   these are usually briefed: 2 pain/use-case angles, 1 core use-case query,
   1 category term the company could own, optionally 1 brand-check query
   ("What is Company Name?"). Brand queries are auto-detected
   (`score.is_brand_query`) and excluded from scoring but still probed and
   shown in the report — no need to split the list yourself.

   Pick `--sector` text carefully — `competitor_gate.noun_phrase()`
   substring-matches against a fixed vocabulary (`accountanc`, `dental`,
   `estate agent`, `solicitor`, `law`, etc.) to phrase the headline finding.
   A sector string that happens to contain one of those substrings (e.g.
   "pre-accounting software" contains "accounting") gets mislabelled ("an
   accountancy firm" for a SaaS product). Prefer a sector word that won't
   collide, or check `antek_geo_core.competitors.VERTICAL_NOUN_PHRASES`
   first if unsure — the CLI's `--sector` help text points at the same
   check.

   This writes the `visibility_checks` row (including the `queries` JSON
   column — required so the report page renders from the exact queries
   actually run, not a re-guess) and the client PDF in one step. Cost
   ~$0.03-0.05 per query across 5 engines; the command prints an estimate
   before running (skip `--yes` to get a confirm prompt first).

   Only fall back to calling `report.build_full_report(conn, company,
   queries=[...])` directly in Python if you need something the CLI doesn't
   expose yet (e.g. scripted batch of many companies) — don't reimplement
   the PDF-render logic inline; that produced two near-duplicate ad hoc
   scripts (FlowFormTax, XautomateX) before this command existed.

2. **Rebuild claim pages + deploy.**
   ```
   CAL_LINK=antek-automation/30min CLAIM_SITE_URL=https://go.antekautomation.com \
     uv run python claim-site/build.py --limit 2000
   cd claim-site && cp -r functions dist/functions && npx wrangler pages deploy dist --project-name antek-claim --branch main --commit-dirty=true
   ```
   This generates BOTH pages automatically once the check row + `queries`
   column exist:
   - `/​<slug>` — the short claim page (score + headline + CTA)
   - `/report/<slug>` — the full walk-through: every question, every
     engine's actual answer, brand-highlighted. This is the client
     walk-through document the operator asks for by default — always build
     and hand over both links, don't wait to be asked for the report link
     separately.

3. **Push the client PDF to GitHub** (binaries as releases, not Drive —
   standing workaround): `gh release create <slug>-visibility-check-<date> ...`

4. **Hand back both URLs**: claim page + `/report/<slug>` full report link.
   State the score plainly and, if it's low, say so — don't soften a
   genuine 0/100.

## Known gap

`probe_cache` has no `company_id` column — it's keyed only by
`(query, engine, run_date, country)`. The `queries` column on
`visibility_checks` (added 2026-08-06) is what lets the report page recover
which cache rows belong to which company/check. Any code path that reads
probe answers for a report must key off `visibility_checks.queries`, not
`prompts.build_queries(company)` — the latter only matches for standard
pipeline checks, never for manual ones.

`country` was added to that key on 2026-08-07 after a same-day rerun under a
different `CHECK_COUNTRY` silently returned the wrong market's cached AI
Overview answer instead of refetching (see `db._migrate_probe_cache_country`
for the fix and why it needed a full table rebuild, not just an added
column — SQLite can't ALTER a UNIQUE constraint). If you ever bypass
`cli check custom` and call `score.score_company()` directly, remember to
set `config.CHECK_COUNTRY` *before* the call, not after — `probes.py` reads
it lazily per probe, so setting it too late silently leaves earlier probes
on the previous market.
