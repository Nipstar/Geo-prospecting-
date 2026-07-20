// Cloudflare Pages Function: POST /api/view
// A claim/report page was opened -> forward to an n8n webhook that pings Telegram
// ("someone viewed their page"). Fire-and-forget; never blocks the visitor.
//
// Pages env var: N8N_VIEW_WEBHOOK (full https URL of the n8n Webhook node)

export async function onRequestPost({ request, env }) {
  const cors = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json" };
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ ok: false }), { status: 200, headers: cors });
  }
  const slug = (body.slug || "").trim();
  if (!slug || !env.N8N_VIEW_WEBHOOK) {
    return new Response(JSON.stringify({ ok: true, forwarded: false }), { status: 200, headers: cors });
  }
  const payload = {
    slug,
    firm: (body.firm || "").trim(),
    page: (body.page || "page").trim(),
    ts: new Date().toISOString(),
  };
  try {
    await fetch(env.N8N_VIEW_WEBHOOK, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (_) {}
  return new Response(JSON.stringify({ ok: true, forwarded: true }), { status: 200, headers: cors });
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
