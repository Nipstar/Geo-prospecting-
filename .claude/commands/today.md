---
description: The daily 30-minute queue, in order.
---
Show today's work list:
```
uv run cli queue
```
Walk the user through it in order: replies first (respond within the hour), then
touch 2s and 3s due, then up to 15 new connection notes, then promised checks,
then the POST section. Each item prints the exact command to log it as sent.
