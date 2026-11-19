"""Bot de Telegram para actualizar las fechas del tour de DJ Cristian Aguirre.

Lo corre GitHub Actions cada pocos minutos (ver .github/workflows/bot.yml):
lee los mensajes nuevos, actualiza events.json y los flyers, y responde en el chat.

Variables de entorno:
  TG_TOKEN     token del bot (de @BotFather)
  ALLOWED_IDS  IDs de Telegram autorizados, separados por coma
"""
import io, json, os, re, sys, urllib.parse, urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(ROOT, "events.json")
FLYERS = os.path.join(ROOT, "img", "flyers")
OFFSET = os.path.join(ROOT, "bot", "offset.txt")

TOKEN = os.environ.get("TG_TOKEN", "")
ALLOWED = {x.strip() for x in os.environ.get("ALLOWED_IDS", "").split(",") if x.strip()}
API = f"https://api.telegram.org/bot{TOKEN}/"

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

AYUDA = """🎧 *Bot de fechas · DJ Cristian Aguirre*

*Agregar un evento:* manda el flyer (como foto) con este texto:
`21/11 | Jamundí | Los Guayabales | Fiesta Blanca | 3002549856`
fecha | ciudad | lugar | nombre del evento | WhatsApp del organizador
(nombre y WhatsApp son opcionales; también puedes mandar solo el texto sin flyer)

*Otros comandos:*
/lista – ver las próximas fechas
/borrar 21/11 – quitar una fecha
/libre 12/12 – publicar una fecha disponible
/ayuda – ver este mensaje

La página se actualiza unos minutos después de cada cambio."""


def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API + method, data=data, timeout=30) as r:
        return json.load(r)


def reply(chat, text):
    try:
        api("sendMessage", chat_id=chat, text=text, parse_mode="Markdown")
    except Exception:
        api("sendMessage", chat_id=chat, text=text)


def load():
    with open(EVENTS, encoding="utf-8") as f:
        return json.load(f)


def save(ev):
    ev.sort(key=lambda e: e["d"])
    with open(EVENTS, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)


def parse_date(txt, today=None):
    """'21/11', '21-11', '21/11/2026' -> '2026-11-21'. Sin año: la próxima vez que llegue esa fecha."""
    today = today or date.today()
    m = re.match(r"\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?:\s*[/\-.]\s*(\d{2,4}))?\s*$", txt)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if y:
            y = int(y) + (2000 if len(y) == 2 else 0)
            return date(y, mo, d).isoformat()
        cand = date(today.year, mo, d)
        if cand < today:
            cand = date(today.year + 1, mo, d)
        return cand.isoformat()
    except ValueError:
        return None


def parse_event(caption):
    parts = [p.strip() for p in caption.split("|")]
    fecha = parse_date(parts[0]) if parts else None
    if not fecha or len(parts) < 2:
        return None
    tel = None
    rest = []
    for p in parts[1:]:
        digits = re.sub(r"\D", "", p)
        if len(digits) >= 10 and len(digits) == len(re.sub(r"[\s+()\-]", "", p)):
            tel = digits[-10:]
        elif p:
            rest.append(p)
    e = {"d": fecha, "c": rest[0] if rest else ""}
    e["l"] = rest[1] if len(rest) > 1 else ""
    if len(rest) > 2:
        e["n"] = rest[2]
    if tel:
        e["tel"] = tel
    return e


def bonita(iso):
    y, m, d = iso.split("-")
    return f"{int(d)} {MESES[int(m) - 1]}"


def save_flyer(file_id, fecha):
    from PIL import Image
    info = api("getFile", file_id=file_id)["result"]
    url = f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}"
    with urllib.request.urlopen(url, timeout=60) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    img.thumbnail((1080, 1350))
    os.makedirs(FLYERS, exist_ok=True)
    img.save(os.path.join(FLYERS, f"{fecha}.webp"), "WEBP", quality=80)


def handle(msg):
    chat = msg["chat"]["id"]
    uid = str(msg.get("from", {}).get("id", chat))
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if uid not in ALLOWED:
        reply(chat, f"🔒 No estás autorizado para publicar fechas.\nTu ID de Telegram es: `{uid}`\nPásale este número a quien administra la página.")
        return False

    cmd = text.split()[0].lower().split("@")[0] if text.startswith("/") else ""
    arg = text[len(text.split()[0]):].strip() if cmd else ""

    if cmd in ("/start", "/ayuda", "/help"):
        reply(chat, AYUDA)
        return False

    ev = load()
    hoy = date.today().isoformat()

    if cmd == "/lista":
        up = [e for e in ev if e["d"] >= hoy]
        if not up:
            reply(chat, "No hay fechas próximas publicadas.")
        else:
            lines = [f"• {bonita(e['d'])} — " + ("🟢 Fecha disponible" if e.get("free") else f"{e.get('n') or e['c']} · {e['l']}" + (" 🖼" if e.get("f") else "")) for e in up]
            reply(chat, "📅 *Próximas fechas:*\n" + "\n".join(lines))
        return False

    if cmd == "/borrar":
        fecha = parse_date(arg)
        if not fecha:
            reply(chat, "Escribe la fecha así: /borrar 21/11")
            return False
        antes = len(ev)
        ev = [e for e in ev if e["d"] != fecha]
        if len(ev) == antes:
            reply(chat, f"No encontré ninguna fecha el {bonita(fecha)}.")
            return False
        save(ev)
        fl = os.path.join(FLYERS, f"{fecha}.webp")
        if os.path.exists(fl):
            os.remove(fl)
        reply(chat, f"🗑 Listo, quité la fecha del {bonita(fecha)}.")
        return True

    if cmd == "/libre":
        fecha = parse_date(arg)
        if not fecha:
            reply(chat, "Escribe la fecha así: /libre 12/12")
            return False
        ev = [e for e in ev if e["d"] != fecha] + [{"d": fecha, "free": 1}]
        save(ev)
        reply(chat, f"🟢 Publicada la fecha disponible del {bonita(fecha)}.")
        return True

    if cmd:
        reply(chat, "No conozco ese comando. Escribe /ayuda")
        return False

    nuevo = parse_event(text)
    if not nuevo:
        reply(chat, "No entendí el formato 🤔\nEjemplo:\n`21/11 | Jamundí | Los Guayabales | Fiesta Blanca | 3002549856`\nEscribe /ayuda para ver todo.")
        return False

    if msg.get("photo"):
        save_flyer(msg["photo"][-1]["file_id"], nuevo["d"])
        nuevo["f"] = 1
    else:
        prev = next((e for e in ev if e["d"] == nuevo["d"] and e.get("f")), None)
        if prev:
            nuevo["f"] = 1

    existia = any(e["d"] == nuevo["d"] for e in ev)
    ev = [e for e in ev if e["d"] != nuevo["d"]] + [nuevo]
    save(ev)
    titulo = nuevo.get("n") or nuevo["c"]
    reply(chat, ("✏️ Actualicé" if existia else "✅ Publiqué") + f" el {bonita(nuevo['d'])}: *{titulo}*" + (f" · {nuevo['l']}" if nuevo["l"] else "") + (" con flyer 🖼" if nuevo.get("f") else "") + ("\n📲 Boletas: " + nuevo["tel"] if nuevo.get("tel") else "") + "\n\nSe verá en la página en unos minutos.")
    return True


def main():
    if not TOKEN:
        print("Falta TG_TOKEN"); return
    me = api("getMe")["result"]
    wh = api("getWebhookInfo")["result"]
    print(f"Bot: @{me.get('username')} · webhook: {'sí' if wh.get('url') else 'no'} · pendientes: {wh.get('pending_update_count')}")
    if wh.get("url"):
        api("deleteWebhook")
    offset = 0
    if os.path.exists(OFFSET):
        offset = int(open(OFFSET).read().strip() or 0)
    ups = api("getUpdates", offset=offset, timeout=0)["result"]
    changed = False
    for u in ups:
        offset = u["update_id"] + 1
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        try:
            changed |= bool(handle(msg))
        except Exception as e:
            print("Error:", e, file=sys.stderr)
            reply(msg["chat"]["id"], "😕 Hubo un error procesando ese mensaje. Intenta de nuevo.")
    with open(OFFSET, "w") as f:
        f.write(str(offset))
    print(f"{len(ups)} mensajes, cambios: {changed}")


if __name__ == "__main__":
    main()
