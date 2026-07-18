// Cloudflare Pages Function: POST /api/lead
// Captures a claim-page lead -> Airtable + Telegram ping.
// Configure these as Pages env vars / secrets:
//   AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE (default "Leads")
//   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
// Any missing integration is skipped gracefully (never blocks the visitor).

export async function onRequestPost({ request, env }) {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
  };
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "bad json" }), { status: 400, headers: cors });
  }

  const email = (body.email || "").trim();
  const name = (body.name || "").trim();
  const firm = (body.firm || "").trim();
  const slug = (body.slug || "").trim();

  const emailOk = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);
  if (!emailOk) {
    return new Response(JSON.stringify({ error: "invalid email" }), { status: 422, headers: cors });
  }

  const ts = new Date().toISOString();
  const results = { airtable: false, telegram: false };

  // --- Airtable ---
  if (env.AIRTABLE_API_KEY && env.AIRTABLE_BASE_ID) {
    const table = env.AIRTABLE_TABLE || "Leads";
    try {
      const r = await fetch(
        `https://api.airtable.com/v0/${env.AIRTABLE_BASE_ID}/${encodeURIComponent(table)}`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${env.AIRTABLE_API_KEY}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            records: [{ fields: { Name: name, Email: email, Firm: firm, Slug: slug, Source: "claim-page", Received: ts } }],
            typecast: true,
          }),
        }
      );
      results.airtable = r.ok;
    } catch (_) {}
  }

  // --- Telegram ---
  if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
    try {
      const text =
        `New claim-page lead\nFirm: ${firm || "?"}\nName: ${name || "?"}\nEmail: ${email}\nPage: ${slug}`;
      const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text }),
      });
      results.telegram = r.ok;
    } catch (_) {}
  }

  return new Response(JSON.stringify({ ok: true, ...results }), { status: 200, headers: cors });
}

export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    },
  });
}
