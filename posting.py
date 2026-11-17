"""Посты-вакансии через бота: присылаешь текст (можно с фото) — бот собирает пост в формате канала.

Формат поста:
    #хэштеги
    (пустая строка)
    текст вакансии
    (пустая строка)
    📝 Ищешь работу? Оставь анкету: t.me/delaijobchat
    💼 Разместить вакансию: t.me/delaidelojob?direct

Кто может: ADMIN_IDS и POST_EDITORS (через запятую в /etc/ddbot.env; по умолчанию — помощник).
Что умеет:
  • любой текст или фото с подписью → готовый пост с кнопками: опубликовать сейчас, на время, заменить текст,
    удалить. Пост можно и скопировать руками (зажать сообщение → «Копировать»);
  • /pachka — пачка: присылаешь несколько вакансий подряд, потом «Готово» и время
    («18.11 10:00 каждые 30 мин» или список времён) — бот выложит сам;
  • /ochered — что запланировано, с кнопками «сейчас» и «удалить».
Посты, которые выкладывает бот, он сразу разбирает для подборки, рассылки и дайджеста.
Текст вакансии бот не переписывает. Подпись к фото — не больше 1024 знаков: если длиннее, бот предупредит.
"""
import html
import json
import os
import re
import time

B = None   # модуль bot.py, задаётся в init()

EDITORS = {int(x) for x in os.environ.get("POST_EDITORS", "703473452").replace(" ", "").split(",") if x}
FOOTER = ("📝 Ищешь работу? Оставь анкету: t.me/delaijobchat\n"
          "💼 Разместить вакансию: t.me/delaidelojob?direct")
CAPTION_MAX, TEXT_MAX = 1024, 4096
COMMANDS = ("/pachka", "/ochered", "/post")
LATE_MIN = 10        # вышел позже на столько минут — сообщим
MSK = 3 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, created INTEGER, body TEXT, tags TEXT,
  media TEXT, file_id TEXT, status TEXT DEFAULT 'draft', run_at INTEGER, posted_id INTEGER, err TEXT);
"""
# status: draft → queued → posted | failed | deleted

HELP = ("📰 <b>Посты через бота</b>\n\n"
        "• Пришли текст вакансии (можно фото с подписью) — соберу пост: хэштеги сверху, текст, подвал со ссылками. "
        "Дальше: опубликовать сейчас, на время, заменить текст или скопировать и выложить руками.\n"
        "• Фото без подписи — тоже можно: потом пришли текст, приклею фото к нему.\n"
        "• /pachka — загрузить пачку вакансий и разложить по времени.\n"
        "• /ochered — что запланировано.\n\n"
        "Время пиши так: <code>18:30</code>, <code>завтра 10:00</code>, <code>19.11 12:00</code>.")


def init(bot_module):
    global B
    B = bot_module


def allowed(uid):
    return uid in B.ADMIN_IDS or uid in EDITORS


def db_init(con):
    con.executescript(SCHEMA)
    con.commit()


# ---------- состояние диалога ----------

def st_get(con, uid):
    r = con.execute("SELECT value FROM state WHERE key=?", (f"ps:{uid}",)).fetchone()
    try:
        return json.loads(r["value"]) if r else {}
    except ValueError:
        return {}


def st_set(con, uid, d):
    con.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"ps:{uid}", json.dumps(d, ensure_ascii=False)))
    con.commit()


# ---------- сборка поста ----------

FOOTER_LINE_RE = re.compile(r"delaijobchat|delaidelojob\?direct|ищешь работу\?\s*оставь анкету|разместить вакансию",
                            re.I)


def clean_body(raw):
    """Текст вакансии без хэштегов и без нашего подвала (если вставили пост целиком)."""
    lines = []
    for line in (raw or "").replace("\r", "").split("\n"):
        if FOOTER_LINE_RE.search(line):
            continue
        had_tags = bool(B.HASHTAG_RE.search(line))
        line = B.HASHTAG_RE.sub("", line).rstrip()
        if had_tags and not re.search(r"[0-9A-Za-zА-Яа-яЁё]", line):
            continue        # строка была из хэштегов (и, может, эмодзи) — убираем целиком
        if line.strip() or (lines and lines[-1] != ""):
            lines.append(line if line.strip() else "")
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def make_tags(raw):
    """Хэштеги профессий (по тексту и YandexGPT) + те, что уже были в тексте, без повторов."""
    own = []
    for t in B.HASHTAG_RE.findall(raw or ""):
        if t.lower() not in [x.lower() for x in own]:
            own.append(t.lower())
    try:
        fields, _ = B.PB.parse_post(raw, [t.lower() for t in own], B.PROF_TAGS)
        profs = fields.get("profs") or []
    except Exception:
        B.log.exception("make_tags: parse_post")
        profs = []
    tags = [B.T.PROFESSIONS[p]["tag"] for p in profs if p in B.T.PROFESSIONS]
    for t in own:
        if t not in tags:
            tags.append(t)
    return " ".join(tags)


def render(body, tags):
    parts = [tags.strip()] if tags.strip() else []
    parts += [body.strip(), FOOTER]
    return "\n\n".join(p for p in parts if p)


def tlen(s):
    """Длина так, как считает Telegram (UTF-16): эмодзи — 2 знака."""
    return len(s.encode("utf-16-le")) // 2


def limit_for(d):
    return CAPTION_MAX if d["file_id"] else TEXT_MAX


def fit(d):
    """Текст не меняем. Если с фото не влезает в 1024 знака — только предупреждаем. Возвращает (d, заметка)."""
    lim = limit_for(d)
    if tlen(render(d["body"], d["tags"])) <= lim:
        return d, ""
    return d, (f"⚠️ Пост длиннее {lim} знаков ({tlen(render(d['body'], d['tags']))}) — с фото так нельзя. "
               f"Сократи текст (кнопка «Заменить текст») или публикуй без фото.")


def first_line(body, n=50):
    for line in (body or "").split("\n"):
        s = line.strip()
        if s:
            return s if len(s) <= n else s[:n - 1] + "…"
    return "—"


# ---------- черновики ----------

def media_of(msg):
    if msg.get("photo"):
        return "photo", msg["photo"][-1]["file_id"]
    if msg.get("video"):
        return "video", msg["video"]["file_id"]
    return None, None


def new_draft(con, uid, raw, media=None, file_id=None):
    d = {"body": clean_body(raw), "tags": make_tags(raw), "file_id": file_id, "media": media}
    d, note = fit(d)
    cur = con.execute("INSERT INTO drafts(uid, created, body, tags, media, file_id) VALUES(?,?,?,?,?,?)",
                      (uid, int(time.time()), d["body"], d["tags"], media, file_id))
    con.commit()
    return cur.lastrowid, note


def get(con, did):
    return con.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()


def draft_kb(d, too_long=False):
    rows = [[("✅ Опубликовать сейчас", f"ps:pub:{d['id']}"), ("⏰ На время", f"ps:time:{d['id']}")],
            [("✏️ Заменить текст", f"ps:repl:{d['id']}"), ("🗑 Удалить", f"ps:del:{d['id']}")]]
    if d["file_id"] and too_long:
        rows.insert(0, [("📄 Опубликовать без фото", f"ps:nophoto:{d['id']}")])
    return B.kb(rows)


def send_post(chat_id, d, markup=None):
    """Пост как он выйдет в канале: фото/видео с подписью или текст. Без разметки — чтобы копировался как есть."""
    text = render(d["body"], d["tags"])
    if d["file_id"] and tlen(text) <= CAPTION_MAX:
        method, key = ("sendVideo", "video") if d["media"] == "video" else ("sendPhoto", "photo")
        return B.tg(method, chat_id=chat_id, caption=text, reply_markup=markup, **{key: d["file_id"]})
    return B.tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True, reply_markup=markup)


def preview(con, chat_id, did, note=""):
    d = get(con, did)
    too_long = tlen(render(d["body"], d["tags"])) > limit_for(d)
    if note:
        B.send(chat_id, note)
    send_post(chat_id, d, draft_kb(d, too_long))


# ---------- время ----------

WHEN_RE = re.compile(r"(?:(сегодня|завтра|послезавтра)\s*)?(?:(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*)?"
                     r"(?:в\s*)?(\d{1,2})[:.](\d{2})", re.I)


def msk_ts(y, mo, d, h, mi):
    return int(time.mktime((y, mo, d, h, mi, 0, 0, 0, 0)) - time.mktime(time.gmtime(0))) - MSK


def parse_times(s, now=None):
    """Все моменты из строки, по порядку. «10:00» без даты — сегодня, а если уже прошло — завтра.
    Дата у одного времени переносится на следующие («19.11 10:00, 12:00»)."""
    now = now or time.time()
    out, last_day = [], None
    for m in WHEN_RE.finditer(s or ""):
        word, dd, mm, yy, hh, mi = m.groups()
        h, mn = int(hh), int(mi)
        if h > 23 or mn > 59:
            continue
        t = time.gmtime(now + MSK)
        if dd:
            y = int(yy) + (2000 if yy and len(yy) == 2 else 0) if yy else t.tm_year
            day = (y, int(mm), int(dd))
            if not yy and msk_ts(*day, 23, 59) < now - 86400:
                day = (y + 1, int(mm), int(dd))     # «05.01» в декабре — это следующий год
        elif word:
            k = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[word.lower()]
            tt = time.gmtime(now + MSK + k * 86400)
            day = (tt.tm_year, tt.tm_mon, tt.tm_mday)
        elif last_day:
            day = last_day
        else:
            day = (t.tm_year, t.tm_mon, t.tm_mday)
            if msk_ts(*day, h, mn) <= now:
                tt = time.gmtime(now + MSK + 86400)
                day = (tt.tm_year, tt.tm_mon, tt.tm_mday)
        try:
            ts = msk_ts(*day, h, mn)
        except (OverflowError, ValueError):
            continue
        last_day = day
        out.append(ts)
    return out


STEP_RE = re.compile(r"кажд\w*\s*(\d+)?\s*(мин|час|ч)\w*", re.I)


def plan_times(s, n, now=None):
    """n моментов для пачки: список времён или «старт + каждые N мин/ч». None — не понял."""
    ts = parse_times(s, now)
    m = STEP_RE.search(s or "")
    if ts and m:
        k = int(m.group(1) or 1)
        step = k * (60 if m.group(2).lower().startswith("мин") else 3600)
        if step < 60:
            return None
        return [ts[0] + i * step for i in range(n)]
    if len(ts) >= n:
        return ts[:n]
    return None


def fmt_ts(ts):
    t = time.gmtime(ts + MSK)
    return f"{t.tm_mday:02d}.{t.tm_mon:02d} {t.tm_hour:02d}:{t.tm_min:02d}"


# ---------- публикация ----------

def publish(con, d):
    m = send_post(B.CHANNEL, d)
    mid = m["message_id"]
    con.execute("UPDATE drafts SET status='posted', posted_id=?, err=NULL WHERE id=?", (mid, d["id"]))
    con.commit()
    # свои посты бот из канала не получает — разбираем сразу, чтобы попали в подборку, рассылку и дайджест
    try:
        B.save_post(con, m)
        if B.analyze_post(con, mid, notify=True):
            B.notify_subscribers(con, mid)
    except Exception:
        B.log.exception("posting: разбор поста %s", mid)
    return mid


def tick(con):
    now = int(time.time())
    for d in con.execute("SELECT * FROM drafts WHERE status='queued' AND run_at<=? ORDER BY run_at", (now,)).fetchall():
        try:
            mid = publish(con, d)
        except B.TgError as e:
            con.execute("UPDATE drafts SET status='failed', err=? WHERE id=?", (str(e)[:300], d["id"]))
            con.commit()
            B.log.warning("posting: пост %s не вышел: %s", d["id"], e)
            _tell(d["uid"], f"❌ Запланированный пост ({fmt_ts(d['run_at'])}) не вышел: {html.escape(str(e))}\n"
                            f"«{html.escape(first_line(d['body']))}». Пришли его заново.")
            continue
        late = (now - d["run_at"]) // 60
        note = f" — с опозданием на {late} мин (бот был недоступен)" if late >= LATE_MIN else ""
        _tell(d["uid"], f"✅ Вышел пост <a href=\"{B.post_link(mid)}\">№{mid}</a>{note}: "
                        f"«{html.escape(first_line(d['body']))}»")


def _tell(uid, text):
    try:
        B.send(uid, text)
    except B.TgError as e:
        B.log.warning("posting: не смог написать %s: %s", uid, e)


def queue_text(con):
    rows = con.execute("SELECT * FROM drafts WHERE status='queued' ORDER BY run_at").fetchall()
    if not rows:
        return "⏰ Запланированных постов нет.", None
    lines = [f"⏰ <b>Запланировано: {len(rows)}</b>"]
    btns = []
    for i, d in enumerate(rows[:15], 1):
        lines.append(f"{i}. {fmt_ts(d['run_at'])} — {html.escape(first_line(d['body']))}"
                     f"{' 🖼' if d['file_id'] else ''}")
        btns.append([(f"{i}. 👁", f"ps:show:{d['id']}"), ("🚀 Сейчас", f"ps:qnow:{d['id']}"),
                     ("🗑", f"ps:qdel:{d['id']}")])
    if len(rows) > 15:
        lines.append(f"…и ещё {len(rows) - 15}")
    return "\n".join(lines), B.kb(btns)


# ---------- входящие ----------

def on_command(con, msg, text):
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    cmd = text.split()[0].split("@")[0].lower()
    if cmd == "/pachka":
        st_set(con, uid, {"mode": "batch", "ids": []})
        B.send(chat, "📦 <b>Пачка</b>: присылай вакансии по одной (текст или фото с подписью). "
                     "Когда закончишь — «Готово».", B.kb([[("✅ Готово", "ps:bdone"), ("❌ Отмена", "ps:bcancel")]]))
    elif cmd == "/ochered":
        t, k = queue_text(con)
        B.send(chat, t, k)
    else:
        st_set(con, uid, {})
        B.send(chat, HELP)


def on_message(con, msg):
    """Сообщение от редактора. True — обработали."""
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""
    media, file_id = media_of(msg)
    st = st_get(con, uid)
    mode = st.get("mode")

    if mode == "time" and text and not media:
        ts = parse_times(text)
        d = get(con, st.get("id") or 0)
        if not ts or not d:
            B.send(chat, "Не понял время. Пример: <code>18:30</code>, <code>завтра 10:00</code>, <code>19.11 12:00</code>")
            return True
        if ts[0] <= time.time():
            B.send(chat, "Это время уже прошло. Напиши другое.")
            return True
        con.execute("UPDATE drafts SET status='queued', run_at=? WHERE id=?", (ts[0], d["id"]))
        con.commit()
        st_set(con, uid, {})
        B.send(chat, f"⏰ Запланировал на {fmt_ts(ts[0])}: «{html.escape(first_line(d['body']))}».\n/ochered — очередь.")
        return True

    if mode == "repl" and text:
        d = get(con, st.get("id") or 0)
        st_set(con, uid, {})
        if d:
            nd = {"body": clean_body(text), "tags": make_tags(text), "file_id": d["file_id"], "media": d["media"]}
            if media:
                nd["media"], nd["file_id"] = media, file_id
            nd, note = fit(nd)
            con.execute("UPDATE drafts SET body=?, tags=?, media=?, file_id=? WHERE id=?",
                        (nd["body"], nd["tags"], nd["media"], nd["file_id"], d["id"]))
            con.commit()
            preview(con, chat, d["id"], note)
            return True

    if mode == "batch_time" and text and not media:
        ids = st.get("ids", [])
        ts = plan_times(text, len(ids))
        if not ts:
            B.send(chat, f"Не понял. Нужно {len(ids)} времён или старт и шаг, например:\n"
                         "<code>завтра 10:00 каждые 30 мин</code>\n<code>19.11 10:00, 12:00, 15:30</code>")
            return True
        if ts[0] <= time.time():
            B.send(chat, "Первое время уже прошло. Напиши другое.")
            return True
        st["mode"], st["plan"] = "batch_ok", list(zip(ids, ts))
        st_set(con, uid, st)
        lines = ["📦 <b>План</b>:"] + [f"{fmt_ts(t)} — {html.escape(first_line(get(con, i)['body']))}"
                                       for i, t in st["plan"]]
        B.send(chat, "\n".join(lines), B.kb([[("✅ Запланировать", "ps:bok"), ("✏️ Другое время", "ps:bdone")],
                                             [("❌ Отмена", "ps:bcancel")]]))
        return True

    # фото без подписи — ждём текст
    if media and not text:
        if not (msg.get("media_group_id") and st.get("photo")):
            st["photo"] = [media, file_id]
            st_set(con, uid, st)
            B.send(chat, "🖼 Фото есть. Теперь пришли текст вакансии — приклею к нему."
                   + (" (из альбома беру только первое фото)" if msg.get("media_group_id") else ""))
        return True
    if not text.strip():
        return False

    if not media and st.get("photo"):
        media, file_id = st.pop("photo")
    did, note = new_draft(con, uid, text, media, file_id)

    if mode == "batch":
        st.setdefault("ids", []).append(did)
        st_set(con, uid, st)
        d = get(con, did)
        warn = f"\n{note}" if note else ""
        B.send(chat, f"✅ {len(st['ids'])}. {html.escape(first_line(d['body']))}{' 🖼' if d['file_id'] else ''}{warn}\n"
                     "Ещё вакансия или «Готово».", B.kb([[("✅ Готово", "ps:bdone"), ("❌ Отмена", "ps:bcancel")]]))
        return True

    st_set(con, uid, {k: v for k, v in st.items() if k == "photo"})
    preview(con, chat, did, note)
    return True


def callback(con, cq, uid, chat_id, mid, data):
    parts = data.split(":")
    act = parts[1] if len(parts) > 1 else ""
    did = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    d = get(con, did) if did else None
    st = st_get(con, uid)

    def drop_kb():
        try:
            B.tg("editMessageReplyMarkup", chat_id=chat_id, message_id=mid, reply_markup={"inline_keyboard": []})
        except B.TgError:
            pass

    if act in ("pub", "qnow", "nophoto") and d:
        if d["status"] in ("posted", "deleted"):
            B.send(chat_id, "Этот пост уже опубликован или удалён.")
            return
        if act == "nophoto":
            con.execute("UPDATE drafts SET file_id=NULL, media=NULL WHERE id=?", (did,))
            con.commit()
            d = get(con, did)
        if tlen(render(d["body"], d["tags"])) > limit_for(d):
            B.send(chat_id, f"Пост длиннее {limit_for(d)} знаков — не выложу. Замени текст на покороче.")
            return
        try:
            n = publish(con, d)
        except B.TgError as e:
            B.send(chat_id, f"❌ Не вышло: {html.escape(str(e))}")
            return
        if act != "qnow":
            drop_kb()
        B.send(chat_id, f"✅ Опубликовано: <a href=\"{B.post_link(n)}\">пост №{n}</a>")
        if act == "qnow":
            t, k = queue_text(con)
            B.edit(chat_id, mid, t, k)
    elif act == "time" and d:
        st_set(con, uid, {"mode": "time", "id": did})
        B.send(chat_id, "Когда выложить? Например: <code>18:30</code>, <code>завтра 10:00</code>, <code>19.11 12:00</code>")
    elif act == "repl" and d:
        st_set(con, uid, {"mode": "repl", "id": did})
        B.send(chat_id, "Пришли новый текст вакансии целиком (можно с новым фото). Хэштеги и подвал добавлю сам.")
    elif act in ("del", "qdel") and d:
        con.execute("UPDATE drafts SET status='deleted' WHERE id=? AND status IN ('draft','queued','failed')", (did,))
        con.commit()
        if act == "qdel":
            t, k = queue_text(con)
            B.edit(chat_id, mid, t, k)
        else:
            drop_kb()
            B.send(chat_id, "🗑 Удалил черновик.")
    elif act == "show" and d:
        send_post(chat_id, d)
        if d["run_at"] and d["status"] == "queued":
            B.send(chat_id, f"⏰ Выйдет {fmt_ts(d['run_at'])}.")
    elif act == "bdone":
        ids = st.get("ids", [])
        if not ids:
            B.send(chat_id, "В пачке пусто — пришли хотя бы одну вакансию.")
            return
        st["mode"] = "batch_time"
        st_set(con, uid, st)
        B.send(chat_id, f"В пачке {len(ids)}. Когда выкладывать? Например:\n"
                        "<code>завтра 10:00 каждые 30 мин</code>\n<code>19.11 10:00, 12:00, 15:30</code>")
    elif act == "bok" and st.get("plan"):
        for i, t in st["plan"]:
            con.execute("UPDATE drafts SET status='queued', run_at=? WHERE id=? AND status='draft'", (t, i))
        con.commit()
        n = len(st["plan"])
        st_set(con, uid, {})
        drop_kb()
        B.send(chat_id, f"⏰ Запланировал {n} пост(ов). /ochered — очередь.")
    elif act == "bcancel":
        for i in st.get("ids", []):
            con.execute("UPDATE drafts SET status='deleted' WHERE id=? AND status='draft'", (i,))
        con.commit()
        st_set(con, uid, {})
        drop_kb()
        B.send(chat_id, "Пачку отменил.")
