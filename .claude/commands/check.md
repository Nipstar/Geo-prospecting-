---
description: Run the mini AI visibility check on new companies (or one company).
---
Run visibility checks. Ask whether the user wants one company or a batch.

For a company not already in the DB, or with custom questions, use
`/check-custom` instead — this command is for existing pipeline companies
with the standard auto-generated prompts.

Ask which market before running anything if it's not obvious from context
(company already has a `town`/UK address in the DB = UK; otherwise ask).
The AI Overview engine is geo-parameterized — running under the wrong market
default doesn't error, it just quietly scores one of the five engines
against the wrong country.

Single company:
```
uv run cli check mini --company-id N --country <UK|US> --yes
```
Batch of new companies:
```
uv run cli check mini --status new --limit 10 --country <UK|US>
```
For a full PDF report and delivery:
```
uv run cli check full --company-id N --yes
```
Always show the estimated API cost the command prints before a batch run.
