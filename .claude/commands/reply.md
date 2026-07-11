---
description: Classify an inbound reply and draft a response.
---
Log an inbound LinkedIn reply. Ask for the person id and the exact reply text.
```
uv run cli log-reply --person-id N --text "THEIR REPLY"
```
Show the intent, urgency and the drafted response. Remind the user to reply the
same day, ideally within the hour. After they send it:
```
uv run cli sent-reply --reply-id N
```
