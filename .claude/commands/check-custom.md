---
description: One-off AI visibility check for a named company with custom prompts.
---
Run a manual visibility check for a specific business the operator names,
with their own questions — not the batch pipeline, not auto-generated
prompts. See the `ai-visibility-check-manual` skill for the full rationale.

**Ask before running, if not already given:**
1. Company name + website.
2. The questions to check (typical shape: 2 pain/use-case angles, 1 core
   use-case query, 1 category term they could own, optionally 1 brand-check
   query — "What is <Company>?"). Don't invent these — ask, or work from
   whatever the operator already gave you.
3. **Market** (UK or US, or another `config._COUNTRY_GEO` key) — check for a
   signal first (currency on the site, TLD, stated address/phone); if not
   obvious, ask rather than default. Silently defaulting to UK for a US
   company scored one of the five engines (AI Overview) against the wrong
   market without erroring — caught on xautomatex.net (2026-08-07), had to
   be rerun.
4. Sector/service line, in their own words — avoid vocabulary that collides
   with `antek_geo_core.competitors.VERTICAL_NOUN_PHRASES` (e.g. a sector
   string containing "accounting" mislabels a SaaS product "an accountancy
   firm" in the headline).

Then run:
```
uv run cli check custom \
  --name "Company Name" --website "https://example.com/" \
  --sector "..." \
  --query "..." --query "..." --query "..." --query "..." \
  --country <UK|US> --yes
```
(`--company-id N` instead of `--name`/`--website` if the company already
exists.) This inserts-or-reuses the company, runs the check, and renders the
client PDF in one step. Confirm the estimated cost it prints unless already
told to skip.

After it completes:
```
CAL_LINK=antek-automation/30min CLAIM_SITE_URL=https://go.antekautomation.com \
  uv run python claim-site/build.py --limit 2000
cd claim-site && cp -r functions dist/functions && npx wrangler pages deploy dist --project-name antek-claim --branch main --commit-dirty=true
```
Then push the PDF to a GitHub release (binaries as releases, not Drive —
standing workaround): `gh release create <slug>-visibility-check-<date> ...`

Hand back both URLs: the claim page (`/<slug>`) and the full report
(`/report/<slug>`, every question + every engine's actual answer). State the
score plainly, including a low one — don't soften a genuine 0/100.
