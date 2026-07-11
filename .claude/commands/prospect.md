---
description: Prospect a sector + town into the pipeline (Apify Places).
---
Ingest prospects for a sector and town, then enrich and route them.

Run, substituting the user's sector/town (ask if not given):
```
uv run cli ingest places --sector "$SECTOR" --town "$TOWN" --max 50
uv run cli ingest enrich --limit 50
uv run cli ingest ch --status new --limit 50
uv run cli route
```
Report how many companies were inserted, enriched, matched at Companies House,
and how they routed (linkedin vs post).
