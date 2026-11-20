// Recibe los mensajes de Telegram al instante, los guarda en una cola (KV)
// y despierta a GitHub Actions para que el bot (bot/bot.py) los procese.
const pad = id => 'u:' + String(id).padStart(12, '0');

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TG_TOKEN}/${method}`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body)
  });
  return r.json();
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const ok = url.searchParams.get('key') === env.WEBHOOK_SECRET;

    if (url.pathname === '/telegram' && req.method === 'POST') {
      if (req.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.WEBHOOK_SECRET)
        return new Response('forbidden', { status: 403 });
      const up = await req.json();
      await env.Q.put(pad(up.update_id), JSON.stringify(up), { expirationTtl: 3 * 86400 });
      const m = up.message || up.edited_message;
      ctx.waitUntil((async () => {
        if (m && m.photo && !(m.caption || '').includes('|'))
          await tg(env, 'sendMessage', { chat_id: m.chat.id, text: '📸 Recibido, estoy leyendo el flyer…' });
        else if (m) await tg(env, 'sendChatAction', { chat_id: m.chat.id, action: 'typing' });
        await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
          method: 'POST',
          headers: { Authorization: 'Bearer ' + env.GH_TOKEN, Accept: 'application/vnd.github+json', 'User-Agent': 'bot-fechas' },
          body: JSON.stringify({ event_type: 'telegram', client_payload: { update: up } })
        });
      })());
      return new Response('ok');
    }

    if (url.pathname === '/pending' && ok) {
      const list = await env.Q.list({ prefix: 'u:' });
      const out = [];
      for (const k of list.keys) { const v = await env.Q.get(k.name); if (v) out.push(JSON.parse(v)); }
      return Response.json(out);
    }

    if (url.pathname === '/ack' && ok && req.method === 'POST') {
      const ids = await req.json();
      await Promise.all(ids.map(i => env.Q.delete(pad(i))));
      return new Response('ok');
    }

    if (url.pathname === '/setup' && ok) {
      return Response.json(await tg(env, 'setWebhook', {
        url: url.origin + '/telegram', secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ['message', 'edited_message'], drop_pending_updates: false
      }));
    }

    return new Response('Bot de fechas · DJ Cristian Aguirre');
  }
};
