#!/usr/bin/env python3
"""Бот-приманка «Зарплаты HoReCa» для канала @delaidelojob.

Работает на чистом Python 3.10+ без сторонних библиотек: long polling Bot API,
данные в SQLite. Настройки берутся из файла окружения (см. ddbot.env.example).
"""
import html
import http.client
import json
import logging
import os
import re
import socket
import sqlite3
import time

import texts as T

log = logging.getLogger("ddbot")

TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL = os.environ.get("CHANNEL", "@delaidelojob")
CHANNEL_INVITE = os.environ.get("CHANNEL_INVITE", T.CHANNEL_URL_FALLBACK)
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.environ.get("DB_PATH", "ddbot.sqlite3")

HASHTAG_RE = re.compile(r"#[0-9A-Za-zА-Яа-яЁё_]+")
CLOSED_RE = re.compile(r"закрыт", re.IGNORECASE)
SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------- Telegram API ----------

class TgError(Exception):
    pass


# Подключение к api.telegram.org: перебираем адреса (IPv6 и IPv4) с коротким таймаутом
# и запоминаем тот, что ответил. Так один «висящий» адрес не тормозит бота.
CONNECT_TIMEOUT = float(os.environ.get("CONNECT_TIMEOUT", "4"))
PREFER = os.environ.get("PREFER_IP", "")  # "4" или "6" — если нужно жёстко
_good = {"family": None}


class FastHTTPS(http.client.HTTPSConnection):
    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        fam_pref = {"4": socket.AF_INET, "6": socket.AF_INET6}.get(PREFER) or _good["family"]
        if fam_pref:
            infos.sort(key=lambda i: 0 if i[0] == fam_pref else 1)
        last = None
        for fam, typ, proto, _, sa in infos:
            sock = socket.socket(fam, typ, proto)
            sock.settimeout(CONNECT_TIMEOUT)
            try:
                sock.connect(sa)
            except OSError as e:
                last = e
                sock.close()
                continue
            if _good["family"] != fam:
                log.info("connected via %s", "IPv6" if fam == socket.AF_INET6 else "IPv4")
                _good["family"] = fam
            sock.settimeout(self.timeout)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
            return
        raise last or OSError("no address")


_conns = {}


def _conn(kind, timeout):
    c = _conns.get(kind)
    if c is None:
        c = FastHTTPS("api.telegram.org", timeout=timeout)
        _conns[kind] = c
    c.timeout = timeout
    if c.sock is not None:
        c.sock.settimeout(timeout)
    return c


def tg(method, **params):
    """Вызов Bot API по постоянному соединению (без нового TLS на каждый запрос)."""
    body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
    kind = "poll" if method == "getUpdates" else "api"
    timeout = params.get("timeout", 0) + 15
    path = f"/bot{TOKEN}/{method}"
    attempts = 4
    for attempt in range(1, attempts + 1):
        c = _conn(kind, timeout)
        try:
            c.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            r = c.getresponse()
            raw = r.read()
            break
        except (OSError, http.client.HTTPException) as e:
            c.close()
            _conns.pop(kind, None)
            if attempt == attempts:
                raise TgError(f"{method}: {e.__class__.__name__}: {e}")
            log.warning("%s: %s, повтор %s", method, e.__class__.__name__, attempt)
            time.sleep(min(attempt, 3))
    try:
        res = json.loads(raw.decode())
    except ValueError:
        raise TgError(f"{method}: bad response")
    if not res.get("ok"):
        raise TgError(f"{method}: {res.get('description')}")
    return res["result"]


def kb(rows):
    """rows: [[(text, 'cb:...') | (text, 'https://...')], ...]"""
    out = []
    for row in rows:
        line = []
        for text, target in row:
            if target.startswith("http"):
                line.append({"text": text, "url": target})
            else:
                line.append({"text": text, "callback_data": target})
        out.append(line)
    return {"inline_keyboard": out}


def send(chat_id, text, markup=None):
    return tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
              disable_web_page_preview=True, reply_markup=markup)


def edit(chat_id, message_id, text, markup=None):
    try:
        return tg("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                  parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
    except TgError as e:
        if "message is not modified" in str(e):
            return None
        # сообщение слишком старое или удалено — пришлём новое
        return send(chat_id, text, markup)


# ---------- база ----------

def db_connect(path=None):
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY, source TEXT, first_seen INTEGER, last_seen INTEGER,
      profession TEXT, region TEXT, exp TEXT, subscribed INTEGER DEFAULT 0, sub_at INTEGER);
    CREATE TABLE IF NOT EXISTS events(
      ts INTEGER, user_id INTEGER, type TEXT, data TEXT);
    CREATE TABLE IF NOT EXISTS posts(
      message_id INTEGER PRIMARY KEY, date INTEGER, text TEXT, tags TEXT, closed INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
    """)
    con.commit()
    return con


def touch_user(con, uid, source=None):
    now = int(time.time())
    row = con.execute("SELECT id, source FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        con.execute("INSERT INTO users(id, source, first_seen, last_seen) VALUES(?,?,?,?)",
                    (uid, source or "direct", now, now))
    else:
        # источник запоминаем по первому заходу, чтобы не перебивать статистику
        if source and row["source"] == "direct":
            con.execute("UPDATE users SET source=? WHERE id=?", (source, uid))
        con.execute("UPDATE users SET last_seen=? WHERE id=?", (now, uid))
    con.commit()


def event(con, uid, kind, data=""):
    con.execute("INSERT INTO events VALUES(?,?,?,?)", (int(time.time()), uid, kind, data))
    con.commit()


def set_user(con, uid, **fields):
    for k, v in fields.items():
        con.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, uid))
    con.commit()


def get_user(con, uid):
    return con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def save_post(con, post):
    text = post.get("text") or post.get("caption") or ""
    tags = " ".join(sorted({t.lower() for t in HASHTAG_RE.findall(text)}))
    closed = 1 if CLOSED_RE.search(text[:200]) or "❌" in text[:50] else 0
    con.execute("INSERT INTO posts(message_id, date, text, tags, closed) VALUES(?,?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET text=excluded.text, tags=excluded.tags, "
                "closed=excluded.closed",
                (post["message_id"], post.get("date", int(time.time())), text, tags, closed))
    con.commit()


def fresh_vacancies(con, tag, limit=3, max_age_days=21):
    since = int(time.time()) - max_age_days * 86400
    rows = con.execute("SELECT message_id, text, tags FROM posts WHERE closed=0 AND date>=? "
                       "ORDER BY date DESC LIMIT 200", (since,)).fetchall()
    out = []
    for r in rows:
        if tag.lower() in r["tags"].split():
            out.append(r)
        if len(out) >= limit:
            break
    return out


# ---------- логика ----------

def channel_username():
    return CHANNEL[1:] if CHANNEL.startswith("@") else None


def post_link(message_id):
    u = channel_username()
    return f"https://t.me/{u}/{message_id}" if u else CHANNEL_INVITE


def first_line(text, limit=70):
    for line in text.splitlines():
        line = HASHTAG_RE.sub("", line).strip(" •-—*_")
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return "Вакансия"


def is_subscribed(uid):
    try:
        m = tg("getChatMember", chat_id=CHANNEL, user_id=uid)
    except TgError as e:
        log.warning("getChatMember failed: %s", e)
        return False
    st = m.get("status")
    return st in ("creator", "administrator", "member") or (st == "restricted" and m.get("is_member"))


def professions_kb():
    keys = list(T.PROFESSIONS)
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([(f"{T.PROFESSIONS[k]['emoji']} {T.PROFESSIONS[k]['name']}", f"p:{k}")
                     for k in keys[i:i + 2]])
    rows.append([("🏪 Я работодатель", "emp")])
    return kb(rows)


def final_kb():
    return kb([
        [("📢 Все вакансии в канале", CHANNEL_INVITE)],
        [("📝 Оставить анкету", T.ANKETA_URL)],
        [("🛵 Подработка курьером", T.COURIER_URL)],
        [("🔁 Другая профессия", "restart"), ("🏪 Я работодатель", "emp")],
    ])


def result_text(prof_key, region_key, exp_key):
    p = T.PROFESSIONS[prof_key]
    note = p["exp"].get(exp_key) or T.EXP_NOTE[exp_key]
    return T.RESULT.format(emoji=p["emoji"], name=p["name"], region=T.REGIONS[region_key],
                           range=p["range"], source=p["source"], exp_note=note)


def full_text(con, prof_key):
    p = T.PROFESSIONS[prof_key]
    vac = fresh_vacancies(con, p["tag"])
    if vac:
        lines = [T.VACANCIES_HEAD]
        for r in vac:
            lines.append(f"• <a href=\"{post_link(r['message_id'])}\">{html.escape(first_line(r['text']))}</a>")
        vac_block = "\n".join(lines)
    else:
        vac_block = T.NO_VACANCIES.format(tag=p["tag"])
    return T.FULL.format(emoji=p["emoji"], name=p["name"],
                         breakdown="\n".join("• " + b for b in p["breakdown"]),
                         tips="\n".join("• " + t for t in p["tips"]),
                         vacancies=vac_block)


def on_start(con, msg):
    uid = msg["from"]["id"]
    parts = (msg.get("text") or "").split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else ""
    source = payload if SOURCE_RE.match(payload) else None
    touch_user(con, uid, source)
    event(con, uid, "start", source or "")
    send(msg["chat"]["id"], T.WELCOME.format(privacy=T.PRIVACY_URL), professions_kb())


def on_stats(con, msg):
    if msg["from"]["id"] not in ADMIN_IDS:
        return
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    week = int(time.time()) - 7 * 86400
    total = q("SELECT COUNT(*) c FROM users")[0]["c"]
    subs = q("SELECT COUNT(*) c FROM users WHERE subscribed=1")[0]["c"]
    new7 = q("SELECT COUNT(*) c FROM users WHERE first_seen>=?", week)[0]["c"]
    by_src = q("SELECT source, COUNT(*) c, SUM(subscribed) s FROM users GROUP BY source ORDER BY c DESC")
    by_prof = q("SELECT profession, COUNT(*) c FROM users WHERE profession IS NOT NULL "
                "GROUP BY profession ORDER BY c DESC")
    lines = [f"📊 <b>Статистика бота</b>",
             f"Всего: {total} · за 7 дней: {new7} · подписались через бота: {subs}", "",
             "<b>Источники</b> (пришли / подписались):"]
    lines += [f"• {html.escape(r['source'] or '—')}: {r['c']} / {r['s'] or 0}" for r in by_src] or ["—"]
    lines += ["", "<b>Профессии</b>:"]
    lines += [f"• {T.PROFESSIONS.get(r['profession'], {}).get('name', r['profession'])}: {r['c']}"
              for r in by_prof] or ["—"]
    posts = q("SELECT COUNT(*) c FROM posts WHERE closed=0")[0]["c"]
    lines += ["", f"Вакансий в базе бота: {posts}"]
    send(msg["chat"]["id"], "\n".join(lines))


def on_callback(con, cq):
    uid = cq["from"]["id"]
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id", uid)
    mid = msg.get("message_id")
    touch_user(con, uid)
    answer_text = None
    answered = False
    if not data.startswith("f:"):
        # сразу гасим «часики» на кнопке, а потом уже меняем сообщение
        _answer(cq["id"])
        answered = True

    if data == "restart":
        edit(chat_id, mid, T.WELCOME.format(privacy=T.PRIVACY_URL), professions_kb())
    elif data == "emp":
        event(con, uid, "employer")
        send(chat_id, T.EMPLOYER.format(contact=T.CONTACT, oferta=T.OFERTA_URL),
             kb([[("✍️ Написать", f"https://t.me/{T.CONTACT.lstrip('@')}")]]))
    elif data.startswith("p:") and data[2:] in T.PROFESSIONS:
        set_user(con, uid, profession=data[2:])
        event(con, uid, "profession", data[2:])
        edit(chat_id, mid, T.ASK_REGION,
             kb([[(name, f"r:{data[2:]}:{k}") for k, name in T.REGIONS.items()]]))
    elif data.startswith("r:"):
        _, prof, reg = (data.split(":") + ["", "", ""])[:3]
        if prof in T.PROFESSIONS and reg in T.REGIONS:
            set_user(con, uid, region=reg)
            rows = [[(name, f"e:{prof}:{reg}:{k}")] for k, name in T.EXPERIENCE.items()]
            edit(chat_id, mid, T.ASK_EXP, kb(rows))
    elif data.startswith("e:"):
        _, prof, reg, exp = (data.split(":") + ["", "", "", ""])[:4]
        if prof in T.PROFESSIONS and reg in T.REGIONS and exp in T.EXPERIENCE:
            set_user(con, uid, exp=exp)
            event(con, uid, "result", f"{prof}:{reg}:{exp}")
            edit(chat_id, mid, result_text(prof, reg, exp),
                 kb([[("🔓 Открыть полный разбор", f"f:{prof}")],
                     [("🔁 Другая профессия", "restart")]]))
    elif data.startswith("f:") and data[2:] in T.PROFESSIONS:
        prof = data[2:]
        if is_subscribed(uid):
            u = get_user(con, uid)
            if u and not u["subscribed"]:
                set_user(con, uid, subscribed=1, sub_at=int(time.time()))
                event(con, uid, "subscribed")
            event(con, uid, "full", prof)
            send(chat_id, full_text(con, prof), final_kb())
        else:
            event(con, uid, "need_sub", prof)
            if mid and (msg.get("text") or "").startswith("🔒"):
                answer_text = T.NOT_SUBSCRIBED_YET
            else:
                send(chat_id, T.NEED_SUB,
                     kb([[("📢 Подписаться на канал", CHANNEL_INVITE)],
                         [("✅ Я подписался", f"f:{prof}")]]))
    if not answered:
        _answer(cq["id"], answer_text)


def _answer(cq_id, text=None):
    try:
        tg("answerCallbackQuery", callback_query_id=cq_id, text=text, show_alert=bool(text))
    except TgError as e:
        log.warning("answerCallbackQuery: %s", e)


def handle(con, upd):
    kind = next((k for k in upd if k != "update_id"), "?")
    if kind == "callback_query":
        log.info("update %s callback %s", upd.get("update_id"), upd["callback_query"].get("data"))
    elif kind == "message":
        log.info("update %s message %r", upd.get("update_id"), (upd["message"].get("text") or "")[:30])
    else:
        log.info("update %s %s", upd.get("update_id"), kind)
    if "channel_post" in upd or "edited_channel_post" in upd:
        post = upd.get("channel_post") or upd.get("edited_channel_post")
        uname = (post.get("chat", {}).get("username") or "").lower()
        if channel_username() and uname == channel_username().lower():
            save_post(con, post)
        return
    if "callback_query" in upd:
        on_callback(con, upd["callback_query"])
        return
    msg = upd.get("message")
    if not msg or msg.get("chat", {}).get("type") != "private":
        return
    text = msg.get("text") or ""
    if text.startswith("/start"):
        on_start(con, msg)
    elif text.startswith("/stats"):
        on_stats(con, msg)
    else:
        touch_user(con, msg["from"]["id"])
        send(msg["chat"]["id"], T.WELCOME.format(privacy=T.PRIVACY_URL), professions_kb())


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not TOKEN:
        raise SystemExit("BOT_TOKEN не задан — проверь файл окружения")
    con = db_connect()
    me = tg("getMe")
    log.info("started as @%s", me.get("username"))
    row = con.execute("SELECT value FROM state WHERE key='offset'").fetchone()
    offset = int(row["value"]) if row else None
    allowed = ["message", "callback_query", "channel_post", "edited_channel_post"]
    while True:
        try:
            updates = tg("getUpdates", offset=offset, timeout=50, allowed_updates=allowed)
        except TgError as e:
            log.warning("getUpdates: %s", e)
            time.sleep(5)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                handle(con, upd)
            except Exception:
                log.exception("update %s failed", upd.get("update_id"))
            con.execute("INSERT INTO state(key, value) VALUES('offset', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),))
            con.commit()


if __name__ == "__main__":
    main()
