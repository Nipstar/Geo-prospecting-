// Cloudflare Pages Function: POST /api/lead
// Validates the claim-page lead and forwards it to an n8n webhook, which
// (running on the Contabo box's authorised IP) adds the contact to a Brevo
// list -> triggers the email sequence, and pings Telegram.
//
// Pages env var:
//   N8N_LEAD_WEBHOOK   full https URL of the n8n Webhook node
//   LEAD_SHARED_SECRET (optional) sent as X-Lead-Token for the n8n side to verify

export async function onRequestPost({ request, env }) {
  const cors = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json" };
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

  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return new Response(JSON.stringify({ error: "invalid email" }), { status: 422, headers: cors });
  }

  if (!env.N8N_LEAD_WEBHOOK) {
    // Not wired yet — accept but report so the visitor still gets a thank-you.
    return new Response(JSON.stringify({ ok: true, forwarded: false }), { status: 200, headers: cors });
  }

  const parts = name.split(" ");
  const payload = {
    email,
    name,
    firstName: parts[0] || "",
    lastName: parts.slice(1).join(" "),
    firm,
    slug,
    source: "claim-page",
    received: new Date().toISOString(),
  };

  try {
    const headers = { "Content-Type": "application/json" };
    if (env.LEAD_SHARED_SECRET) headers["X-Lead-Token"] = env.LEAD_SHARED_SECRET;
    const r = await fetch(env.N8N_LEAD_WEBHOOK, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    return new Response(JSON.stringify({ ok: true, forwarded: r.ok }), { status: 200, headers: cors });
  } catch (_) {
    // Never fail the visitor — log-and-forget.
    return new Response(JSON.stringify({ ok: true, forwarded: false }), { status: 200, headers: cors });
  }
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
