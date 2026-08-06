---
name: ai-visibility-check-manual
description: Run a one-off AI visibility check for a single named company with custom prompts (not the batch prospecting pipeline). Use when the operator gives a specific business + a list of questions to check across AI engines, and wants a claim page + full walk-through report + client PDF.
---

# Manual AI Visibility Check

For a single company, custom prompts, on demand — e.g. "run a visibility check
for flowformtax.co.uk with these 5 questions". Different from
`full-prospect-pipeline`, which is a batch sector/town search with
auto-generated prompts.

## Hard rule: never let a brand-name query count toward the score

**The score formula's 70-point "platforms_mentioned" component is binary per
engine: mentioned in ANY scored query = full credit for that engine.** A
self-referential query like "What is Acme Ltd?" trivially passes on every
engine that has ever indexed the company's own site — it always "mentions"
them because the question IS the brand name. Mixing a brand query into the
scored set inflates the composite even when the company is invisible on
every real discovery question.

This bit a real check (FlowFormTax, 2026-08-06): 0/4 on genuine discovery
questions, 5/5 on "What is FlowFormTax?" alone, and the blended composite
came out 76/100 — completely misrepresenting the actual finding, which was
total invisibility. Corrected to 0/100 once the brand query was pulled out
of scoring.

**Rule**: if the operator wants a brand-recognition question included
alongside discovery questions, keep it — it's a genuinely useful data point
("does the AI know who you are at all") — but exclude it from the query list
passed into `score.score_company()`. Score only on questions a real
prospective customer would actually ask. Probe the brand query separately
and fold it into the displayed report as an extra, unscored row.

How to tell which queries are "brand" vs "discovery": a query is a brand
query if the company name (or an obvious short form of it) appears in the
query text itself ("What is X?", "Tell me about X", "X reviews"). Everything
else — pain points, category terms, "best X for Y" — is a discovery query
and belongs in scoring.

## Steps

1. **Find or insert the company.**
   ```python
   from src import db
   conn = db.get_connection()
   existing = conn.execute("SELECT id FROM companies WHERE website LIKE ?", (f"%{domain}%",)).fetchone()
   if not existing:
       cid = db.insert_company(conn, name="...", website="https://...",
           town=None, county=None,   # None/None for a non-local/SaaS target — no "near me" framing
           sector="...", primary_service="...", source="manual", status="new")
       conn.commit()
   ```
   Pick `sector`/`primary_service` text carefully — `competitor_gate.noun_phrase()`
   substring-matches against a fixed vocabulary (`accountanc`, `dental`,
   `estate agent`, `solicitor`, `law`, etc.) to phrase the headline finding.
   A sector string that happens to contain one of those substrings
   (e.g. "pre-accounting software" contains "accounting") gets mislabelled
   ("an accountancy firm" for a SaaS product). Prefer a sector word that
   won't collide, or check `antek_geo_core.competitors.VERTICAL_NOUN_PHRASES`
   first if unsure.

2. **Split the operator's questions into scored (discovery) vs unscored (brand).**
   Typical shape (mirrors how these are usually briefed): 2 pain/use-case
   angles, 1 core use-case query, 1 category term the company could own —
   all scored — plus optionally 1 brand-check query, unscored.

3. **Run the scored check.**
   ```python
   from src import config
   from src.visibility import score
   result = score.score_company(conn, company, queries=discovery_queries,
                                  engines=config.CHECK_ENGINES, check_type="full")
   ```
   This writes the `visibility_checks` row, including the `queries` column
   (JSON list) — required so the report page later renders from the exact
   queries actually run, not a re-guess. Cost ~$0.03-0.05 per query across
   5 engines; confirm with the operator first if running many companies.

4. **If a brand query was requested, probe it separately and append for display only.**
   ```python
   from src.visibility import probes
   check = db.latest_check(conn, company["id"])
   for engine in config.CHECK_ENGINES:
       probes.run_probe(conn, engine, brand_query)   # writes to probe_cache, same run_date
   import json
   all_queries = discovery_queries + [brand_query]
   db.update_check(conn, check["id"], queries=json.dumps(all_queries))
   ```
   Composite/engine scores stay as computed in step 3 — do not recompute
   them including the brand query.

5. **Build the client PDF** (reuses `report.py`'s render path — see
   `build_full_report` for the full pattern; construct it inline with the
   corrected `queries` list so the report shows all questions including the
   brand one).

6. **Rebuild claim pages + deploy.**
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

7. **Push the client PDF to GitHub** (binaries as releases, not Drive —
   standing workaround): `gh release create <slug>-visibility-check-<date> ...`

8. **Hand back both URLs**: claim page + `/report/<slug>` full report link.
   State the score plainly and, if it's low, say so — don't soften a
   genuine 0/100.

## Known gap

`probe_cache` has no `company_id` column — it's keyed only by
`(query, engine, run_date)`. The `queries` column on `visibility_checks`
(added 2026-08-06) is what lets the report page recover which cache rows
belong to which company/check. Any code path that reads probe answers for a
report must key off `visibility_checks.queries`, not
`prompts.build_queries(company)` — the latter only matches for standard
pipeline checks, never for manual ones.
