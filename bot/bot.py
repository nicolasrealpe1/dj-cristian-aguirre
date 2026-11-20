"""Bot de Telegram para actualizar las fechas del tour de DJ Cristian Aguirre.

Lo corre GitHub Actions cada pocos minutos (ver .github/workflows/bot.yml):
lee los mensajes nuevos, actualiza events.json y los flyers, y responde en el chat.

Variables de entorno:
  TG_TOKEN     token del bot (de @BotFather)
  ALLOWED_IDS  IDs de Telegram autorizados, separados por coma
"""
import base64, io, json, os, re, sys, urllib.parse, urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(ROOT, "events.json")
FLYERS = os.path.join(ROOT, "img", "flyers")
OFFSET = os.path.join(ROOT, "bot", "offset.txt")
PENDING = os.path.join(ROOT, "bot", "pending.json")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

TOKEN = os.environ.get("TG_TOKEN", "")
ALLOWED = {x.strip() for x in os.environ.get("ALLOWED_IDS", "").split(",") if x.strip()}
API = f"https://api.telegram.org/bot{TOKEN}/"

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

AYUDA = """🎧 *Bot de fechas · DJ Cristian Aguirre*

*Agregar un evento:* manda solo el flyer (como foto). Lo leo, te muestro lo que entendí y respondes *sí* para publicar, o me escribes la corrección (ej: "el lugar es Bar La 33").

*También a mano:* flyer o mensaje con este texto:
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


def download(file_id):
    info = api("getFile", file_id=file_id)["result"]
    url = f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


PROMPT = """Eres asistente del DJ de salsa Cristian Aguirre (Colombia). Hoy es {hoy}.
{tarea}
Responde SOLO un JSON con estas claves:
 "dia" (número), "mes" (número 1-12), "anio" (número o null si no aparece),
 "ciudad" (municipio/ciudad, ej. "Jamundí"), "lugar" (nombre del sitio/discoteca/finca),
 "nombre" (nombre de la fiesta o evento, corto; null si no hay),
 "tel" (WhatsApp/celular de boletas o reservas, solo dígitos, 10 dígitos colombianos; null si no hay).
Si algo no aparece, usa null. No inventes."""


def gemini(parts):
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}).encode()
    err = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.load(r)
            txt = out["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(txt)
            return data[0] if isinstance(data, list) else data
        except Exception as e:
            err = e
            print(f"Gemini {model}: {e}", file=sys.stderr)
    raise RuntimeError(err)


def to_event(j):
    """JSON de Gemini -> evento del sitio (o None si no hay fecha)."""
    try:
        d, m = int(j.get("dia")), int(j.get("mes"))
    except (TypeError, ValueError):
        return None
    y = j.get("anio")
    fecha = parse_date(f"{d}/{m}" + (f"/{int(y)}" if y else ""))
    if not fecha:
        return None
    e = {"d": fecha, "c": (j.get("ciudad") or "").strip(), "l": (j.get("lugar") or "").strip()}
    if j.get("nombre"):
        e["n"] = str(j["nombre"]).strip()
    tel = re.sub(r"\D", "", str(j.get("tel") or ""))
    if len(tel) >= 10:
        e["tel"] = tel[-10:]
    return e


def read_flyer(raw):
    return gemini([{"text": PROMPT.format(hoy=date.today().isoformat(), tarea="Lee este flyer de un evento y extrae los datos.")},
                   {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(raw).decode()}}])


def correct(ev, txt):
    j = {"dia": int(ev["d"][8:]), "mes": int(ev["d"][5:7]), "anio": int(ev["d"][:4]),
         "ciudad": ev["c"], "lugar": ev["l"], "nombre": ev.get("n"), "tel": ev.get("tel")}
    return gemini([{"text": PROMPT.format(hoy=date.today().isoformat(),
                    tarea=f"Estos son los datos actuales de un evento: {json.dumps(j, ensure_ascii=False)}\nAplica esta corrección del usuario y devuelve los datos completos: {txt}")}])


def resumen(e):
    return (f"📅 *{bonita(e['d'])}* · {e.get('n') or 'Sin nombre'}\n📍 {e['l'] or '¿lugar?'}, {e['c'] or '¿ciudad?'}"
            + (f"\n📲 Boletas: {e['tel']}" if e.get("tel") else "\n📲 Sin WhatsApp de boletas"))


def load_pending():
    try:
        with open(PENDING, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_pending(p):
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def publish(ev_list, nuevo, photo_id=None):
    if photo_id:
        save_flyer(photo_id, nuevo["d"])
        nuevo["f"] = 1
    elif any(e["d"] == nuevo["d"] and e.get("f") for e in ev_list):
        nuevo["f"] = 1
    existia = any(e["d"] == nuevo["d"] for e in ev_list)
    save([e for e in ev_list if e["d"] != nuevo["d"]] + [nuevo])
    return existia


def save_flyer(file_id, fecha):
    from PIL import Image
    img = Image.open(io.BytesIO(download(file_id))).convert("RGB")
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

    pend = load_pending()
    key = str(chat)
    p = pend.get(key)
    low = text.lower().strip(" .!¡¿?")

    # Respuesta a una confirmación pendiente
    if p and not msg.get("photo"):
        if low in ("si", "sí", "sii", "dale", "ok", "listo", "publicar", "publica", "de una", "👍"):
            existia = publish(ev, p["e"], p.get("photo"))
            pend.pop(key); save_pending(pend)
            reply(chat, ("✏️ Actualicé" if existia else "✅ Publiqué") + " el evento:\n" + resumen(p["e"]) + "\n\nSe verá en la página en unos minutos.")
            return True
        if low in ("no", "cancelar", "cancela", "borrar"):
            pend.pop(key); save_pending(pend)
            reply(chat, "👌 Cancelado, no publiqué nada.")
            return True
        manual = parse_event(text)
        fixed = manual or (to_event(correct(p["e"], text)) if GEMINI_KEY else None)
        if not fixed:
            reply(chat, "No entendí la corrección 🤔 Escríbela de otra forma, o responde *sí* para publicar / *no* para cancelar.")
            return False
        p["e"] = fixed; save_pending(pend)
        reply(chat, "Corregido:\n" + resumen(fixed) + "\n\n¿Publico? Responde *sí*, o corrige lo que esté mal.")
        return True

    nuevo = parse_event(text)
    # Flyer sin texto: lo lee la IA y pide confirmación
    if not nuevo and msg.get("photo") and GEMINI_KEY:
        fid = msg["photo"][-1]["file_id"]
        e = to_event(read_flyer(download(fid)))
        if not e:
            reply(chat, "No pude encontrar la fecha en ese flyer 🤔\nMándalo de nuevo con el texto:\n`21/11 | Jamundí | Los Guayabales | Fiesta Blanca | 3002549856`")
            return False
        pend[key] = {"e": e, "photo": fid}; save_pending(pend)
        reply(chat, "Esto es lo que leí en el flyer:\n\n" + resumen(e) + "\n\n¿Publico? Responde *sí*, o escríbeme lo que haya que corregir.")
        return True
    if not nuevo:
        reply(chat, "No entendí el formato 🤔\nEjemplo:\n`21/11 | Jamundí | Los Guayabales | Fiesta Blanca | 3002549856`\nEscribe /ayuda para ver todo.")
        return False

    existia = publish(ev, nuevo, msg["photo"][-1]["file_id"] if msg.get("photo") else None)
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
