# AI Visibility Check funnel — deploy runbook

Landing page (Antek Rebrand) → Vercel `/api/visibility-check` → **WF9** (n8n) →
**geo-scan** `/scan` (this repo, Coolify/Contabo) → Brevo → Telegram.

Secrets live in the local `coolify.md` / `.env` only. **Never commit them.** Every
file in this folder uses `$env` placeholders.

---

## 1. Brevo (DONE)

`deploy/brevo_setup.py` created the folder + list + attributes. Re-run any time —
it is idempotent.

- Folder: **GEO Funnel** (id 9)
- List: **GEO Funnel - Checked** → **`BREVO_LIST_ID = 10`**
- Attributes: BUSINESS_NAME, DOMAIN, TOWN, TRADE, TOP_COMPETITOR, PHONE (text);
  VISIBILITY_SCORE, MENTION_RATE (float); SCAN_DATE (date).

Authorise the **Contabo IP `161.97.92.229`** at
https://app.brevo.com/security/authorised_ips (prod Brevo calls come from n8n).

## 2. geo-scan `/scan` service → Coolify on Contabo

Coolify API is not reachable off-box (port 8000 firewalled, `claw.cerberos.app`
Cloudflare-1010-blocks non-browsers), so create it in the **Coolify UI**:

1. **New Resource → Application → Public/Private Git**
   - Repo: `https://github.com/Nipstar/Geo-prospecting-`
   - Branch: `claude/geo-outreach-engine-uj04pi`
   - Build pack: **Dockerfile** (repo root `Dockerfile`)
   - Port: **8000**
2. **Domain**: `geo-scan.cerberos.app` (wildcard Cloudflare origin cert already
   covers `*.cerberos.app`). Keep it behind Cloudflare.
3. **Persistent storage**: mount a volume at **`/app/data`** (keeps `pipeline.db`
   across redeploys).
4. **Environment variables**:
   | Var | Value |
   |---|---|
   | `OPENROUTER_API_KEY` | from `.env` |
   | `SERPAPI_KEY` | from `.env` (root key is `SERPAPI_API_KEY`) |
   | `APIFY_TOKEN` | from `.env` (optional) |
   | `COMPANIES_HOUSE_API_KEY` | from `.env` (optional) |
   | `SCAN_TOKEN` | generate a random secret; also set in n8n |
5. **Deploy**, then smoke-test (from an authorised host):
   ```
   POST https://geo-scan.cerberos.app/scan
   Header: X-Scan-Token: <SCAN_TOKEN>
   Body:   {"business_name":"Test Co","website_url":"example.com","location":"Basingstoke","trade":"lawyer","email":"you@x.com"}
   ```
   Expect JSON with `status:"done"`, `visibility_score`, `pdf_b64`.
   Form fields sent by the page: `name, email, phone, business_name, website_url,
   location, trade, consent, company_size` (honeypot). `trade` (e.g. lawyer,
   accountant) drives the probe prompts; if omitted, `/scan` classifies from the site.

## 3. WF9 in n8n

Import `deploy/wf9-visibility-check.json` (**verify on import — untested against
your n8n version; node typeVersions target n8n 1.x**). It is secret-free; set
these as **n8n environment variables** (Settings → Environment, or the n8n host
env):

| Var | Value |
|---|---|
| `SCAN_URL` | `https://geo-scan.cerberos.app` |
| `SCAN_TOKEN` | same secret as Coolify |
| `BREVO_API_KEY` | from `.env` |
| `BREVO_LIST_ID` | `10` |
| `BREVO_FROM_EMAIL` | `hello@antekautomation.com` |
| `BREVO_FROM_NAME` | `Antek Automation` |
| `TELEGRAM_BOT_TOKEN` | from `coolify.md` (`telegram=`) |
| `TELEGRAM_CHAT_ID` | from `coolify.md` (`telegram_chat_id=`) |

Flow: Webhook → Respond OK → Validate (honeypot + consent + normalise) → Scan →
Scan OK? → (Brevo Upsert → Brevo Email w/ PDF → Telegram) / (Brevo Delay Email).
Activate it, then copy the **Production** webhook URL.

## 4. Vercel (Antek Rebrand)

Set `VISIBILITY_WEBHOOK_URL` = the WF9 production webhook URL. (`RECAPTCHA_SECRET_KEY`
and `VITE_RECAPTCHA_SITE_KEY` are already set.) Until then the form falls back to
`CONTACT_WEBHOOK_URL`. Deploy the `free-ai-visibility-check` branch.

## 5. End-to-end test (before promoting the page)

1. `curl` the WF9 test webhook with a sample lead → within ~1 min: Brevo contact
   in list 10 with attributes, email with **PDF attached**, Telegram ping.
2. Submit the real form (Vercel preview) → same result via the full chain.
3. Honeypot filled or consent false → dropped silently, no email.
4. Re-submit same domain within 7 days → cached PDF, no fresh probe spend.

Only then promote `antekautomation.com/free-ai-visibility-check` (paid ads,
LinkedIn CTAs). Until the pipeline is green, keep it unlinked.
