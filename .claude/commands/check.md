---
description: Run the mini AI visibility check on new companies (or one company).
---
Run visibility checks. Ask whether the user wants one company or a batch.

Single company:
```
uv run cli check mini --company-id N --yes
```
Batch of new companies:
```
uv run cli check mini --status new --limit 10
```
For a full PDF report and delivery:
```
uv run cli check full --company-id N --yes
```
Always show the estimated API cost the command prints before a batch run.
