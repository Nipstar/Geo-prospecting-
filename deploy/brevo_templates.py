#!/usr/bin/env python3
"""Create the 4 nurture email templates in Brevo (idempotent by name).

Reads BREVO_API_KEY from env or ../../../.env. Prints each template id. Run once;
re-running skips templates that already exist.

    python3 deploy/brevo_templates.py
"""
from __future__ import annotations

import json
import os
import re
import pathlib
import urllib.error
import urllib.request

SENDER = {"name": "Antek Automation", "email": "hello@antekautomation.com"}

# (name, subject, html) — copy mirrors NURTURE-SEQUENCE.md. Merge tags are Brevo
# contact attributes. Brevo appends the unsubscribe footer on send.
EMAILS = [
    (
        "GEO Nurture 1 - Day 2 - Did the report make sense",
        "Did your AI visibility report make sense?",
        """<p>Hi {{contact.FIRSTNAME}},</p>
<p>A couple of days ago we sent your free AI Visibility Check for {{contact.BUSINESS_NAME}}. You scored {{contact.VISIBILITY_SCORE}} out of 100.</p>
<p>One question. Did it make sense, or is there a part you want me to explain?</p>
<p>Reply to this email and I will. No pitch.</p>
<p>Andy<br>Antek Automation, Andover</p>""",
    ),
    (
        "GEO Nurture 2 - Day 5 - Why AI recommends other firms",
        "Why AI recommends other firms, not you",
        """<p>Hi {{contact.FIRSTNAME}},</p>
<p>When someone asks ChatGPT or Google's AI for {{contact.TRADE}} near {{contact.TOWN}}, it names businesses it has seen described clearly and consistently across the web.</p>
<p>The firms that get recommended are not always the best in town. They are the most legible to the model: clear pages, consistent details, the right signals in the right places.</p>
<p>That part is fixable, and most of it is technical.</p>
<p>Andy</p>""",
    ),
    (
        "GEO Nurture 3 - Day 9 - The fix list",
        "The fix list for {{contact.BUSINESS_NAME}}",
        """<p>Hi {{contact.FIRSTNAME}},</p>
<p>The free check showed where {{contact.BUSINESS_NAME}} stands. The GEO Audit shows exactly what to change.</p>
<p>It is &pound;247, one-off. You get:</p>
<ul>
<li>Your citability scored across ChatGPT, Claude, Perplexity, Gemini and Google AI Overviews</li>
<li>The schema, llms.txt and crawler gaps holding you back</li>
<li>Which competitors are being cited, and why</li>
<li>A prioritised fix list any developer can action</li>
</ul>
<p>No retainer. Reply "audit" or book at <a href="https://antekautomation.com/services/geo-audit">antekautomation.com/services/geo-audit</a>.</p>
<p>Andy</p>""",
    ),
    (
        "GEO Nurture 4 - Day 16 - Leaving this here",
        "Leaving this here",
        """<p>Hi {{contact.FIRSTNAME}},</p>
<p>Not chasing. If getting {{contact.BUSINESS_NAME}} named in AI answers matters this quarter, the &pound;247 GEO Audit is the quickest way to see what is holding you back.</p>
<p>If not, no problem. The free report is yours to keep.</p>
<p>Either way, I am one reply away.</p>
<p>Andy<br>Antek Automation, Andover, 0333 038 9960</p>""",
    ),
]

BASE = "https://api.brevo.com/v3"


def _key() -> str:
    k = os.getenv("BREVO_API_KEY", "").strip()
    if k:
        return k
    for f in (pathlib.Path(__file__).resolve().parents[3] / ".env",
              pathlib.Path(__file__).resolve().parents[1] / ".env"):
        if f.exists():
            for line in f.read_text().splitlines():
                m = re.match(r"^BREVO_API_KEY=(.*)$", line.strip())
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("Set BREVO_API_KEY.")


def _req(key, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"api-key": key, "accept": "application/json",
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def main() -> None:
    key = _key()
    s, acct = _req(key, "GET", "/account")
    if s != 200:
        raise SystemExit(f"Brevo auth failed ({s}): {acct.get('message', acct)}")
    print(f"Account OK: {acct.get('email')}")

    _, existing = _req(key, "GET", "/smtp/templates?limit=100&sort=desc")
    by_name = {t["name"]: t["id"] for t in existing.get("templates", [])}

    for name, subject, html in EMAILS:
        if name in by_name:
            print(f"  exists (id {by_name[name]}): {name}")
            continue
        s, r = _req(key, "POST", "/smtp/templates", {
            "sender": SENDER, "templateName": name, "subject": subject,
            "htmlContent": html, "isActive": True,
        })
        if s in (200, 201):
            print(f"  created (id {r.get('id')}): {name}")
        else:
            print(f"  ! {name}: {s} {r.get('message', r)}")


if __name__ == "__main__":
    main()
