---
description: Draft a 3-touch LinkedIn sequence in the Antek voice.
---
Draft outbound. First read .claude/skills/antek-outreach-voice/SKILL.md so the
voice rules are loaded.

One person:
```
uv run cli draft --person-id N
```
Batch (checked companies on the linkedin channel):
```
uv run cli draft --batch --status checked --limit 10
```
Drafts are printed and written to output/queue/ for copy on iPad. Do not send
anything: all LinkedIn sends are manual.
