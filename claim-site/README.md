# Claim site — personalised AI Visibility Check pages

One static page per prospect (`antek.link/<slug>`) showing their score, the real
AI quotes, a self-verify challenge, a Cal.com booking embed, and an email-capture
form. Form posts to a Cloudflare Pages Function → Airtable + Telegram.

```
claim-site/
  template.html          page template (Antek brand)
  build.py               DB -> dist/<slug>/index.html
  functions/api/lead.js  Cloudflare Pages Function (form capture)
  dist/                  generated output (gitignored)
```

## 1. Generate pages

```bash
CAL_LINK="andy-norman/15min" uv run python claim-site/build.py --status checked
```
`CAL_LINK` = your Cal.com event path (the bit after cal.com/). One folder per
prospect lands in `claim-site/dist/`.

## 2. Deploy to Cloudflare Pages

First time — create the project (one-off):
```bash
npm i -g wrangler
wrangler login
# copy the Pages Functions in next to the static output, then deploy:
cp -r claim-site/functions claim-site/dist/functions
wrangler pages deploy claim-site/dist --project-name antek-claim
```
Re-deploys after a rebuild = repeat the `cp` + `wrangler pages deploy` line.

Then in the Cloudflare dashboard → Pages → antek-claim → **Custom domains**, add
`antek.link` (or `go.antek.link`). Pages issues SSL automatically.

## 3. Secrets (Cloudflare dashboard → Pages → Settings → Environment variables)

| Var | Purpose |
|-----|---------|
| `AIRTABLE_API_KEY` | write leads to Airtable |
| `AIRTABLE_BASE_ID` | target base (`app…`) |
| `AIRTABLE_TABLE`   | table name (default `Leads`) — needs fields: Name, Email, Firm, Slug, Source, Received |
| `TELEGRAM_BOT_TOKEN` | ping you on each lead |
| `TELEGRAM_CHAT_ID`   | your chat id |

All integrations are optional — a missing one is skipped, the form still succeeds.

## Flow

letter / LinkedIn → `antek.link/<slug>` → book a call (Cal.com) **or** submit
email → Worker logs to Airtable + Telegrams you → you deliver the full report.
