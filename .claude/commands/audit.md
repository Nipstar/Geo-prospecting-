---
description: Pre-send audit of a drafted touch against the voice rules.
---
Audit a drafted message before it is sent.
```
uv run cli audit --touch-id N
```
Read the flags (hype words, automated tells, oversized ask, about-me lines) and
the tightened rewrite. If the verdict is "start over", draft a fresh one rather
than polishing.
