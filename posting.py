"""Посты через бота: админ пересылает (или вставляет) вакансию → бот переписывает её своими словами
в формате канала → превью с кнопками → публикация в канал только по кнопке.

- Текст переписывает YandexGPT (ключи те же, что у подборки). Контакты в нейросеть не уходят:
  бот вынимает их сам и добавляет в конце строкой «📩 Отклик: …».
- Без ключа YandexGPT — текст остаётся как есть, но без чужих хэштегов, ссылок на каналы и подписей.
- Фото из исходного поста по умолчанию НЕ берём (кнопка «🖼 С фото» включает).
- Режим POST_MODE в /etc/ddbot.env: on (по умолчанию) / off.
"""
import html
import json
import logging
import os
import re
import time

import podbor as PB
import texts as T

log = logging.getLogger("ddbot")
B = None   # модуль bot, задаётся в init()

MODE = os.environ.get("POST_MODE", "on").strip().lower()
MODEL = os.environ.get("YANDEX_POST_MODEL", "").strip() or PB.YC_MODEL
FOOTER = ("📝 Ищешь работу? Оставь анкету: t.me/delaijobchat\n"
          "💼 Разместить вакансию: t.me/delaidelojob?direct")
TEXT_MAX, CAPTION_MAX = 4096, 1024
MIN_LEN = 60     # короче — это не вакансия, а обычное сообщение боту


def init(bot):
    global B
    B = bot


def enabled():
    return MODE != "off"


# ---------- черновик ----------

def _get(con, uid):
    r = con.execute("SELECT value FROM state WHERE key=?", (f"post:{uid}",)).fetchone()
    try:
        return json.loads(r["value"]) if r else {}
    except ValueError:
        return {}


def _set(con, uid, d):
    if d:
        con.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f"post:{uid}", json.dumps(d, ensure_ascii=False)))
    else:
        con.execute("DELETE FROM state WHERE key=?", (f"post:{uid}",))
    con.commit()


def waiting_edit(con, uid):
    return _get(con, uid).get("wait") == "edit"


# ---------- разбор исходника ----------

TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/(\+?[A-Za-z0-9_]+)", re.I)
NICK_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9_]{4,32})")
PHONE_RE = re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
MAIL_RE = re.compile(r"[\w.\-]+@[\w\-]+\.[A-Za-z.]{2,}")
WA_RE = re.compile(r"(?:https?://)?wa\.me/\+?\d+", re.I)
# строки-«подвалы» чужих каналов
PROMO_LINE_RE = re.compile(r"подпис|наш канал|ещё вакансии|еще вакансии|больше вакансий|разместить ваканс|"
                           r"реклам|предложк|по вопросам размещ|чат соискател|оставь анкету|оставить анкету",
                           re.I)


def source_of(msg):
    """Откуда переслано: (username канала/чата в нижнем регистре или '', название)."""
    o = msg.get("forward_origin") or {}
    chat = o.get("chat") or o.get("sender_chat") or msg.get("forward_from_chat") or {}
    return (chat.get("username") or "").lower(), chat.get("title") or o.get("sender_user_name") or ""


def contacts(text, skip=()):
    """Телефоны, почта, WhatsApp и @ники/ссылки на людей. Ник канала-источника и наш — пропускаем."""
    skip = {s.lower() for s in skip if s}
    out = []

    def add(x):
        if x and x not in out:
            out.append(x)

    for m in PHONE_RE.finditer(text):
        add(re.sub(r"\s+", " ", m.group(0)).strip())
    for m in WA_RE.finditer(text):
        add(m.group(0))
    for m in MAIL_RE.finditer(text):
        add(m.group(0))
    for m in list(NICK_RE.finditer(text)) + list(TG_LINK_RE.finditer(text)):
        nick = m.group(1)
        if nick.startswith("+") or nick.lower() in skip or nick.lower().endswith("bot"):
            continue   # инвайт-ссылки, источник, боты
        add("@" + nick)
    return out


def clean(text, skip=()):
    """Убираем чужие хэштеги, ссылки на каналы и строки-подвалы. Контакты оставляем — их вынет contacts()."""
    skip = {s.lower() for s in skip if s}
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if PROMO_LINE_RE.search(line) or any(s and s in low for s in skip):
            continue
        line = B.HASHTAG_RE.sub("", line)
        line = TG_LINK_RE.sub(lambda m: "" if m.group(1).startswith("+") or m.group(1).lower() in skip
                              else m.group(0), line)
        lines.append(line.rstrip())
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def hashtags(text, tags=()):
    """Наши хэштеги профессий: по хэштегам исходника и словам в тексте (правилами, без нейросети)."""
    profs = PB.find_profs(text, list(tags), B.PROF_TAGS) or []
    return [B.PROF_TAGS[p] for p in profs if p in B.PROF_TAGS]


# ---------- переписывание ----------

GPT_SYSTEM = (
    "Ты редактор Telegram-канала вакансий HoReCa (Москва и МО). Перепиши вакансию СВОИМИ словами: "
    "другой порядок, другие формулировки, без копирования фраз оригинала. Факты не меняй и не выдумывай: "
    "должность, заведение, место (город, метро, адрес), ставка и проценты, график, обязанности, требования, "
    "условия — всё, что есть в тексте, и ничего сверх. Ставки пиши цифрами как в оригинале.\n"
    "Формат (обычный текст, без markdown, без звёздочек и решёток):\n"
    "1-я строка: эмодзи + должность + «в» + заведение (если названо), например «💨 Кальянный мастер в «Название»».\n"
    "Пустая строка, затем строки с эмодзи: 📍 место, 💰 оплата, 🗓 график, и при наличии: "
    "✅ что даём (условия), 📋 что делать, 👤 кого ждём.\n"
    "Каждый пункт — коротко, через запятую или с новой строки с «— ».\n"
    "Не пиши контакты, ссылки, хэштеги, призывы подписаться, названия других каналов, слово [контакт]. "
    "Ответь только текстом поста, до 700 знаков."
)


def gpt_rewrite(text):
    if not (PB.YC_KEY and PB.YC_FOLDER):
        return None
    body = {
        "modelUri": f"gpt://{PB.YC_FOLDER}/{MODEL}",
        "completionOptions": {"stream": False, "temperature": 0.4, "maxTokens": "900"},
        "messages": [{"role": "system", "text": GPT_SYSTEM},
                     {"role": "user", "text": PB.strip_contacts(text)[:3500]}],
    }
    try:
        import http.client
        c = http.client.HTTPSConnection("llm.api.cloud.yandex.net", timeout=40)
        c.request("POST", "/foundationModels/v1/completion", body=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json", "Authorization": f"Api-Key {PB.YC_KEY}",
                           "x-folder-id": PB.YC_FOLDER})
        r = c.getresponse()
        raw = r.read().decode()
        c.close()
        if r.status != 200:
            log.warning("YandexGPT (пост) %s: %s", r.status, raw[:300])
            return None
        out = json.loads(raw)["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        log.warning("YandexGPT (пост) failed: %s", e)
        return None
    return tidy(out)


CALL_WORDS = {"пиши", "пишите", "писать", "звони", "звоните", "звонить", "контакт", "контакты", "контакты:",
              "связь", "тел", "телефон", "номер", "номеру", "по", "или", "и", "в", "на", "whatsapp", "ватсап",
              "вотсап", "тг", "telegram", "телеграм", "обращаться", "вопросам", "всем", "резюме", "отклик",
              "откликнуться", "сюда", "лс", "личку", "менеджеру", "hr", "за", "подробностями", "подробности"}


def _call_only(line):
    """Строка, от которой после выноса контактов остался только призыв: «Звоните», «Пиши  или»."""
    words = re.findall(r"[a-zа-яё]+", line.lower())
    return bool(words) and not re.search(r"\d", line) and all(w in CALL_WORDS for w in words)


def tidy(s):
    s = (s or "").strip().strip("`").strip()
    s = re.sub(r"\*\*|__|^#+\s*", "", s, flags=re.M)        # markdown
    s = PB.CONTACT_RE.sub("", s).replace("[контакт]", "")
    s = B.HASHTAG_RE.sub("", s)
    s = "\n".join(re.sub(r"\s{2,}", " ", l).rstrip() for l in s.splitlines() if not _call_only(l))
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def build(body, tags, conts):
    """Пост целиком (обычный текст): хэштеги, пустая строка, текст, отклик, пустая строка, подвал."""
    parts = []
    if tags:
        parts += [" ".join(tags), ""]
    parts.append(body.strip())
    if conts:
        parts.append("\n📩 Отклик: " + ", ".join(conts))
    parts += ["", FOOTER]
    return "\n".join(parts)




# ---------- доступ ----------

def allowed(uid):
    return uid in B.ADMIN_IDS or uid in POST_IDS


POST_IDS = {int(x) for x in os.environ.get("POST_IDS", "703473452").replace(" ", "").split(",") if x}


# ---------- обложки ----------
# Обложки хранятся в базе как file_id Telegram: добавить — прислать боту картинку с подписью
# «/oblozhka Кальянщик тёмная». Профессия по названию определяется сама — такую обложку бот предложит первой.

SCHEMA = """
CREATE TABLE IF NOT EXISTS post_covers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, prof TEXT, file_id TEXT, added INTEGER);
"""


def _prof_of(name):
    p = PB.find_profs(name or "", [], B.PROF_TAGS) or []
    return p[0] if p else None


def covers(con, prof=None):
    rows = con.execute("SELECT * FROM post_covers ORDER BY id").fetchall()
    return sorted(rows, key=lambda r: (r["prof"] != prof, r["id"])) if prof else list(rows)


def add_cover(con, msg):
    chat = msg["chat"]["id"]
    photo = msg.get("photo") or []
    name = re.sub(r"^/oblozhka(?:@\w+)?\s*", "", msg.get("caption") or msg.get("text") or "").strip()
    if not photo:
        B.send(chat, "Пришли картинку с подписью: <code>/oblozhka Кальянщик тёмная</code>")
        return
    if not name:
        name = f"Обложка {int(time.time()) % 100000}"
    con.execute("INSERT INTO post_covers(name, prof, file_id, added) VALUES(?,?,?,?)",
                (name[:40], _prof_of(name), photo[-1]["file_id"], int(time.time())))
    con.commit()
    prof = _prof_of(name)
    B.send(chat, f"🖼 Обложка «{html.escape(name[:40])}» добавлена"
                 + (f" (профессия: {html.escape(B.PROF_NAMES[prof])})." if prof else ". Профессию по названию не понял — "
                    "будет в общем списке."))


def list_covers(con, chat):
    rows = covers(con)
    if not rows:
        B.send(chat, "Обложек пока нет. Пришли картинку с подписью <code>/oblozhka Кальянщик тёмная</code>.")
        return
    B.send(chat, f"🖼 Обложек: {len(rows)}. Удалить — кнопкой.",
           B.kb([[(f"🗑 {r['name']}", f"po:cdel:{r['id']}")] for r in rows[:40]]))


def _cover_label(con, d):
    c = d.get("cover")
    if c == "src":
        return "фото из исходника"
    if c:
        r = con.execute("SELECT name FROM post_covers WHERE id=?", (c,)).fetchone()
        return r["name"] if r else "нет"
    return "нет"


def _cover_file(con, d):
    c = d.get("cover")
    if c == "src":
        return d.get("photo")
    if c:
        r = con.execute("SELECT file_id FROM post_covers WHERE id=?", (c,)).fetchone()
        return r["file_id"] if r else None
    return None


def cover_kb(con, d):
    rows = []
    for r in covers(con, d.get("prof"))[:24]:
        mark = "✅ " if d.get("cover") == r["id"] else ("⭐ " if r["prof"] and r["prof"] == d.get("prof") else "")
        rows.append((mark + r["name"], f"po:cset:{r['id']}"))
    out = [rows[i:i + 2] for i in range(0, len(rows), 2)]
    if d.get("photo"):
        out.append([(("✅ " if d.get("cover") == "src" else "") + "📷 Фото из исходника", "po:cset:src")])
    out.append([(("✅ " if not d.get("cover") else "") + "🚫 Без обложки", "po:cset:none")])
    out.append([("↩️ К превью", "po:back")])
    return B.kb(out)


# ---------- превью и кнопки ----------

def _kb(con, d):
    return B.kb([[("✅ Опубликовать", "po:pub")],
                 [("🔁 Переписать ещё", "po:redo"), ("✏️ Исправить", "po:edit")],
                 [(f"🖼 Обложка: {_cover_label(con, d)}"[:60], "po:cover")],
                 [("❌ Отмена", "po:cancel")]])


def _limit(con, d):
    return CAPTION_MAX if _cover_file(con, d) else TEXT_MAX


def preview(con, uid, chat):
    """Пост так, как он уйдёт в канал (с обложкой — фото с подписью), и кнопки под ним."""
    d = _get(con, uid)
    text = d.get("text", "")
    notes = []
    if d.get("by") == "raw":
        notes.append("⚠️ Нейросеть не ответила — текст только очищен. «🔁 Переписать ещё» или «✏️ Исправить».")
    if len(text) > _limit(con, d):
        notes.append(f"⚠️ {len(text)} знаков, лимит {_limit(con, d)}"
                     + (" для поста с обложкой" if _cover_file(con, d) else "") + " — сократи («✏️ Исправить»).")
    head = "👀 <b>Превью</b>" + (f" (из «{html.escape(d['src_title'])}»)" if d.get("src_title") else "")
    B.send(chat, "\n".join([head] + notes))
    fid = _cover_file(con, d)
    if fid and len(text) <= CAPTION_MAX:
        try:
            B.tg("sendPhoto", chat_id=chat, photo=fid, caption=html.escape(text), parse_mode="HTML",
                 reply_markup=_kb(con, d))
            return
        except B.TgError as e:
            log.warning("posting preview photo: %s", e)
    B.send(chat, html.escape(text), _kb(con, d))


def start(con, msg):
    """Новое сообщение (переслано или длинный текст) — делаем черновик. True, если взяли в работу."""
    if not enabled():
        return False
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    raw = msg.get("text") or msg.get("caption") or ""
    forwarded = bool(msg.get("forward_origin") or msg.get("forward_from_chat") or msg.get("forward_from"))
    if len(raw.strip()) < MIN_LEN and not forwarded:
        return False
    if not raw.strip():
        B.send(chat, "В пересланном посте нет текста (длинные подписи с Premium Telegram боту не отдаёт). "
                     "Скопируй текст и пришли сообщением.")
        return True
    src_user, src_title = source_of(msg)
    skip = [src_user, B.channel_username() or "", B.BOT_USERNAME, "delaijobchat"]
    tags = hashtags(raw, B.post_tags(raw))
    prof = next((k for k, t in B.PROF_TAGS.items() if t in tags), None)
    first = covers(con, prof)
    d = {"src": raw, "src_user": src_user, "src_title": src_title,
         "photo": (msg.get("photo") or [{}])[-1].get("file_id"), "prof": prof, "tags": tags,
         "contacts": contacts(raw, skip),
         # по умолчанию — обложка этой профессии, если есть
         "cover": first[0]["id"] if first and prof and first[0]["prof"] == prof else None}
    B.send(chat, "✍️ Переписываю…")
    _rewrite(con, uid, d)
    preview(con, uid, chat)
    return True


def _rewrite(con, uid, d):
    skip = [d.get("src_user"), B.channel_username() or ""]
    base = clean(d["src"], skip)
    body = gpt_rewrite(base)
    d["by"] = "gpt" if body else "raw"
    if not body:   # без нейросети — очищенный исходник без контактов (они уйдут строкой «Отклик»)
        body = tidy(PB.CONTACT_RE.sub("", base))
    d["text"] = build(body, d["tags"], d["contacts"])
    d.pop("wait", None)
    _set(con, uid, d)


def on_edit_text(con, msg):
    """Прислали исправленный текст поста целиком."""
    uid = msg["from"]["id"]
    d = _get(con, uid)
    d["text"] = (msg.get("text") or "").strip()
    d["by"] = "manual"
    d.pop("wait", None)
    _set(con, uid, d)
    preview(con, uid, msg["chat"]["id"])


def _markup(chat, mid, markup):
    try:
        B.tg("editMessageReplyMarkup", chat_id=chat, message_id=mid, reply_markup=markup)
    except B.TgError as e:
        if "not modified" not in str(e):
            log.warning("posting markup: %s", e)


def callback(con, uid, chat, mid, data):
    if not allowed(uid):
        return
    parts = data.split(":")
    act = parts[1] if len(parts) > 1 else ""
    if act == "cdel":
        con.execute("DELETE FROM post_covers WHERE id=?", (int(parts[2]),))
        con.commit()
        B.send(chat, "🗑 Обложка удалена.")
        list_covers(con, chat)
        return
    d = _get(con, uid)
    if not d:
        B.send(chat, "Черновик не найден — перешли пост ещё раз.")
        return
    if act == "cancel":
        _set(con, uid, {})
        _markup(chat, mid, None)
        B.send(chat, "❌ Черновик удалён.")
    elif act == "redo":
        _markup(chat, mid, None)
        B.send(chat, "✍️ Переписываю ещё раз…")
        _rewrite(con, uid, d)
        preview(con, uid, chat)
    elif act == "edit":
        d["wait"] = "edit"
        _set(con, uid, d)
        B.send(chat, "Пришли исправленный текст поста целиком (скопируй из превью и поправь). "
                     "Он уйдёт в канал как есть.")
    elif act == "cover":
        _markup(chat, mid, cover_kb(con, d))
    elif act == "back":
        _markup(chat, mid, _kb(con, d))
    elif act == "cset":
        v = parts[2] if len(parts) > 2 else "none"
        d["cover"] = None if v == "none" else ("src" if v == "src" else int(v))
        _set(con, uid, d)
        _markup(chat, mid, None)
        preview(con, uid, chat)
    elif act == "pub":
        publish(con, uid, chat, mid, d)


def publish(con, uid, chat, mid, d):
    text = d.get("text", "")
    fid = _cover_file(con, d)
    if len(text) > _limit(con, d):
        B.send(chat, f"⚠️ {len(text)} знаков, лимит {_limit(con, d)}. Сократи через «✏️ Исправить» "
                     "или убери обложку.")
        return
    try:
        if fid:
            m = B.tg("sendPhoto", chat_id=B.CHANNEL, photo=fid, caption=html.escape(text), parse_mode="HTML")
        else:
            m = B.tg("sendMessage", chat_id=B.CHANNEL, text=html.escape(text), parse_mode="HTML",
                     disable_web_page_preview=True)
    except B.TgError as e:
        B.send(chat, f"❌ Не опубликовал: {html.escape(str(e))}")
        return
    _set(con, uid, {})
    _markup(chat, mid, None)
    n = m["message_id"]
    B.event(con, uid, "post_pub", str(n))
    # свои посты бот апдейтом обычно не получает — сохраняем, разбираем и рассылаем сами
    m.setdefault("chat", {"username": B.channel_username()})
    B.save_post(con, m)
    con.execute("INSERT OR REPLACE INTO state(key, value) VALUES(?, '1')", (f"own_post:{n}",))
    con.commit()
    try:
        if B.analyze_post(con, n, notify=True):
            B.notify_subscribers(con, n)
    except Exception:
        log.exception("posting: analyze %s", n)
    B.send(chat, f"✅ Опубликовано: <a href=\"{B.post_link(n)}\">пост №{n}</a>")
    for aid in B.ADMIN_IDS | POST_IDS:   # второму человеку — что пост вышел
        if aid != uid:
            try:
                B.send(aid, f"📣 Опубликован <a href=\"{B.post_link(n)}\">пост №{n}</a> через бота.")
            except B.TgError:
                pass


def is_own(con, message_id):
    """Пост опубликован ботом и уже обработан — повторно подписчикам не рассылаем."""
    return con.execute("SELECT 1 FROM state WHERE key=?", (f"own_post:{message_id}",)).fetchone() is not None


HELP = ("📝 <b>Посты через бота</b>\n\n"
        "Перешли сюда вакансию (или вставь текст) — бот перепишет её своими словами в формате канала: "
        "хэштеги, текст, контакты для отклика, подвал. Дальше кнопки: опубликовать, переписать ещё, "
        "исправить вручную, выбрать обложку. В канал уходит только по кнопке «✅ Опубликовать».\n\n"
        "🖼 Обложки: пришли картинку с подписью <code>/oblozhka Кальянщик тёмная</code> — добавится в список "
        "(если в названии есть профессия, для её вакансий будет предлагаться первой). "
        "<code>/oblozhki</code> — список и удаление.")


def on_message(con, msg):
    """Сообщения в личке от админа или помощника. True — обработали."""
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    if not (enabled() and allowed(uid)):
        return False
    text = (msg.get("text") or msg.get("caption") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd == "/oblozhka":
        add_cover(con, msg)
    elif cmd == "/oblozhki":
        list_covers(con, chat)
    elif cmd == "/post":
        B.send(chat, HELP)
    elif cmd:
        return False
    elif waiting_edit(con, uid) and msg.get("text"):
        on_edit_text(con, msg)
    else:
        return start(con, msg)
    return True
