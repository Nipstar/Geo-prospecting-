---
description: Run the full prospecting pipeline (ingest -> route -> enrich -> check -> draft -> bundle -> send) for a new industry + area, staged with mandatory scope questions and a mandatory send confirmation.
---
Invoke the `.claude/skills/full-prospect-pipeline/SKILL.md` skill and follow
it exactly, start to finish, for whatever industry/area the user gives (ask
if not given).

Do not hand-chain the individual `cli` commands yourself instead of using the
skill — the skill exists specifically so Stage 0's scope questions and Stage
7's final send confirmation are never skipped under time pressure. That
includes the token-cost discipline notes in Stage 3 (continuous resumable
loop per dispatch, cheap-model/haiku for mechanical runs, Sonnet-tier worker
reserved for judgement calls only).

If the user just says `/pipeline` with no industry/area, ask for both before
doing anything else.
