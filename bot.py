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

import podbor as PB
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
    PB.db_init(con)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS subs(
      user_id INTEGER PRIMARY KEY, query TEXT, active INTEGER DEFAULT 1, created INTEGER,
      day TEXT, sent_today INTEGER DEFAULT 0, ping_at INTEGER);
    CREATE TABLE IF NOT EXISTS sub_queue(user_id INTEGER, message_id INTEGER, ts INTEGER,
      PRIMARY KEY(user_id, message_id));
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


# Дополнительные хэштеги профессии (основной — в texts.py, поле tag)
ALT_TAGS = {"hostess": ["#администратор", "#админ"], "kalyan": ["#км", "#пкм"]}


def fresh_vacancies(con, tag, limit=3, max_age_days=21, alt=()):
    since = int(time.time()) - max_age_days * 86400
    rows = con.execute("SELECT message_id, text, tags FROM posts WHERE closed=0 AND date>=? "
                       "ORDER BY date DESC LIMIT 200", (since,)).fetchall()
    out = []
    for r in rows:
        tags = r["tags"].split()
        if tag.lower() in tags or any(a in tags for a in alt):
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


def vac_title(text, limit=60, title=None):
    """Заголовок вакансии для списков: название заведения (от нейросети) или первая содержательная строка."""
    t = PB.clean_title(title) if title else None
    if t:
        return t
    line = "Вакансия"
    for raw in (text or "").splitlines():
        cand = HASHTAG_RE.sub("", raw).strip(" •-—*_!.,")
        cand = re.split(r"\s+[|•·]\s+|\s+[—–-]\s+(?=\d|от\s|до\s)|,\s+(?=м\.|метро|\d|от\s|до\s)", cand)[0]
        if PB.clean_title(cand):  # пропускаем «Привет!», «Срочно!», пустые и эмодзи
            first = re.split(r"(?<![\s(]м)(?<![\s(]г)(?<!ст)(?<!ул)[.!?](?:\s|$)", " " + cand)[0].strip()
            line = PB.clean_title(first) or cand
            break
    head = re.split(r"\s+[|•·]\s+|\s+[—–-]\s+(?=\d|от\s|до\s)|,\s+(?=м\.|метро|\d|от\s|до\s)", line)[0]
    head = head.strip(" ,.;:—–-")
    if len(head) >= 6:
        line = head
    if len(line) > limit:
        line = line[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:—–-(") + "…"
    return line


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


# Вход в подборку для обычного пользователя — кнопкой, без команд.
# Пока тест: кнопку видят только админы. Открыть всем — PODBOR_FOR_ALL=1 в /etc/ddbot.env.
PODBOR_FOR_ALL = os.environ.get("PODBOR_FOR_ALL", "") == "1"
PODBOR_BTN = ("🔎 Подобрать вакансии", "pb:start")


# Тестировщики: видят подборку и рассылку как обычные пользователи, без команд админа и служебных вопросов.
TESTER_IDS = {int(x) for x in os.environ.get("TESTER_IDS", "").replace(" ", "").split(",") if x}


def podbor_allowed(uid):
    return PODBOR_FOR_ALL or uid in ADMIN_IDS or uid in TESTER_IDS


def professions_kb(uid=None):
    keys = list(T.PROFESSIONS)
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([(f"{T.PROFESSIONS[k]['emoji']} {T.PROFESSIONS[k]['name']}", f"p:{k}")
                     for k in keys[i:i + 2]])
    # подборка — под профессиями: кто пришёл с рекламы «узнай зарплату», сначала видит профессии
    if uid is not None and podbor_allowed(uid):
        rows.append([PODBOR_BTN])
    rows.append([("🏪 Я работодатель", "emp")])
    return kb(rows)


def final_kb(uid=None):
    top = [[PODBOR_BTN]] if uid is not None and podbor_allowed(uid) else []
    return kb(top + [
        [("📢 Все вакансии в канале", CHANNEL_INVITE)],
        [("📝 Оставить анкету", T.ANKETA_URL)],
        [("🛵 Подработка курьером", T.COURIER_URL)],
        [("🔁 Другая профессия", "restart"), ("🏪 Я работодатель", "emp")],
    ])


def result_text(prof_key, region_key, exp_key):
    p = T.PROFESSIONS[prof_key]
    note = p["exp"].get(exp_key) or T.EXP_NOTE[exp_key]
    return T.RESULT.format(emoji=p["emoji"], name=p["name"], region=T.REGIONS[region_key],
                           range=p.get("range_by_exp", {}).get(exp_key) or p["range"],
                           source=p["source"], exp_note=note)


def full_text(con, prof_key):
    p = T.PROFESSIONS[prof_key]
    vac = fresh_vacancies(con, p["tag"], alt=ALT_TAGS.get(prof_key, ()))
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
    send(msg["chat"]["id"], T.WELCOME.format(privacy=T.PRIVACY_URL), professions_kb(msg["from"]["id"]))


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

    if data.startswith("sb:") and podbor_allowed(uid):
        sub_callback(con, uid, chat_id, mid, data)
    elif data == "pb:start" and podbor_allowed(uid):
        st = pb_get(con, uid)
        if st.get("done") and st.get("prof") in T.PROFESSIONS:
            m = send(chat_id, "…")
            pb_show(con, uid, chat_id, m["message_id"], st)
        else:
            pb_set(con, uid, {})
            send(chat_id, "🔎 <b>Подборка вакансий</b>\n\nОтветь на 5 коротких вопросов — покажу все вакансии "
                          "из канала за неделю, которые тебе подходят.\n\nКем хочешь работать?", pb_prof_kb())
    elif data.startswith("pb:") and podbor_allowed(uid):
        pb_callback(con, uid, chat_id, mid, data)
    elif data.startswith("pm:") and uid in ADMIN_IDS:
        _, n, city = (data.split(":") + ["", "", ""])[:3]
        if n.isdigit():
            if city == "skip":
                PB.set_field(con, int(n), asked=2)
                edit(chat_id, mid, f"Пост №{n}: пропущен, в подборку по месту не попадёт.")
            else:
                PB.set_field(con, int(n), manual=True, city=PB.MOSCOW)
                edit(chat_id, mid, vac_card(con, int(n)))
    elif data == "restart":
        edit(chat_id, mid, T.WELCOME.format(privacy=T.PRIVACY_URL), professions_kb(uid))
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
            send(chat_id, full_text(con, prof), final_kb(uid))
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


# ---------- подборка вакансий (тест: только ADMIN_IDS) ----------

PROF_TAGS = {k: v["tag"] for k, v in T.PROFESSIONS.items()}
PROF_NAMES = {k: v["name"] for k, v in T.PROFESSIONS.items()}


def post_tags(text):
    return sorted({t.lower() for t in HASHTAG_RE.findall(text or "")})


def analyze_post(con, message_id, notify=False, use_gpt=True, force=False):
    row = con.execute("SELECT * FROM posts WHERE message_id=?", (message_id,)).fetchone()
    if not row or not (force or PB.needs_parse(con, message_id, row["text"])):
        return None
    if not row["text"].strip():
        return None
    fields, by = PB.parse_post(row["text"], post_tags(row["text"]), PROF_TAGS, use_gpt=use_gpt)
    PB.store(con, message_id, row["date"], row["text"], fields, by)
    r = PB.get_vac(con, message_id)
    if notify and fields.get("prof") and fields.get("prof") != "courier" and r and not r["asked"]:
        missing = [n for f, n in (("city", "место"), ("unit", "ставку")) if not fields.get(f)]
        if missing:
            ask_admins(con, message_id, missing)
    return fields


def vac_card(con, n):
    r = PB.get_vac(con, n)
    if not r:
        return f"Пост №{n} в базе подборки не найден."
    return (f"Пост <a href=\"{post_link(n)}\">№{n}</a> (разобрал: {r['parsed_by']}):\n"
            f"{html.escape(PB.describe(r, PROF_NAMES))}")


def ask_admins(con, n, missing):
    PB.set_field(con, n, asked=1)
    text = (vac_card(con, n) + f"\n\n❓ Не нашёл {' и '.join(missing)}.\n"
            f"Место: <code>/mesto {n} Химки</code>\nСтавка: <code>/stavka {n} 4000-6000 смена</code>")
    for aid in ADMIN_IDS:
        try:
            send(aid, text, kb([[("📍 Москва", f"pm:{n}:msk"), ("Пропустить", f"pm:{n}:skip")]]))
        except TgError as e:
            log.warning("ask_admins: %s", e)


UNIT_WORDS = {"смена": "shift", "смену": "shift", "выход": "shift", "час": "hour", "ч": "hour",
              "мес": "month", "месяц": "month"}


def on_admin(con, msg, text):
    chat = msg["chat"]["id"]
    cmd, *args = text.split()
    cmd = cmd.split("@")[0].lower()
    if cmd == "/podbor":
        uid = msg["from"]["id"]
        st = pb_get(con, uid)
        st.pop("wait", None)
        if st.get("done") and st.get("prof") in T.PROFESSIONS:
            r = send(chat, "…")
            pb_show(con, uid, chat, r["message_id"], st)
        else:
            pb_set(con, uid, {})
            send(chat, "🔎 <b>Подборка вакансий</b> (тест)\n\nКем хочешь работать?", pb_prof_kb())
    elif cmd == "/testrass":
        on_testrass(con, msg)
    elif cmd == "/rass":
        rows = con.execute("SELECT user_id, query, active FROM subs").fetchall()
        q = con.execute("SELECT COUNT(*) c FROM sub_queue").fetchone()["c"]
        lines = [f"🔔 Подписок: {sum(r['active'] for r in rows)} активных из {len(rows)}; в очереди: {q}"]
        lines += [f"• {r['user_id']}: {html.escape(query_title(json.loads(r['query'])))}"
                  f"{'' if r['active'] else ' (выкл.)'}" for r in rows[:20]]
        send(chat, "\n".join(lines))
    elif cmd == "/vac" and args and args[0].isdigit():
        send(chat, vac_card(con, int(args[0])))
    elif cmd == "/mesto" and len(args) >= 2 and args[0].isdigit():
        city = PB.canon_city(" ".join(args[1:]))
        if not city:
            send(chat, "Не знаю такой город. Пиши как на сайте: Химки, Красногорск, Москва.")
            return
        PB.set_field(con, int(args[0]), manual=True, city=city)
        send(chat, vac_card(con, int(args[0])))
    elif cmd == "/stavka" and len(args) >= 2 and args[0].isdigit():
        nums = [int(x) for x in re.findall(r"\d+", args[1])]
        unit = UNIT_WORDS.get((args[2] if len(args) > 2 else "смена").lower().strip("."), "shift")
        if not nums:
            send(chat, "Формат: /stavka 65 4000-6000 смена")
            return
        PB.set_field(con, int(args[0]), sal_min=nums[0], sal_max=nums[1] if len(nums) > 1 else None, unit=unit,
                     manual=True)
        send(chat, vac_card(con, int(args[0])))
    elif cmd == "/reparse":
        days = int(args[0]) if args and args[0].isdigit() else 14
        since = int(time.time()) - days * 86400
        ids = [r["message_id"] for r in con.execute(
            "SELECT message_id FROM posts WHERE date>=? ORDER BY date", (since,)).fetchall()]
        send(chat, f"Разбираю {len(ids)} постов за {days} дн… (ручные правки сохраняю)")
        ok = gpt = media = 0
        no_city, no_sal, not_vac, digests = [], [], [], []
        for n in ids:
            row = con.execute("SELECT text FROM posts WHERE message_id=?", (n,)).fetchone()
            if not row or not (row["text"] or "").strip():
                media += 1  # фото/видео без текста
                continue
            f = analyze_post(con, n, force=True) or {}
            r = PB.get_vac(con, n)
            if r and r["parsed_by"] == "gpt":
                gpt += 1
            if f.get("kind") == "digest":
                digests.append(n)
                continue
            if not f.get("prof"):
                not_vac.append(n)
                continue
            if not f.get("city"):
                no_city.append(n)
            if not f.get("unit"):
                no_sal.append(n)
            if f.get("city") and f.get("unit"):
                ok += 1
        vac_total = len(ids) - media - len(not_vac) - len(digests)
        lines = [f"✅ Готово: вакансий {vac_total}, из них полностью разобрано {ok} "
                 f"(через YandexGPT: {gpt}; фото/видео без текста: {media})."]
        if digests:
            lines.append(f"Подборки (в поиск не идут): {', '.join(map(str, digests))}")
        if not_vac:
            lines.append(f"Не вакансии (промо, анонсы): {', '.join(map(str, not_vac))}")
        if no_city:
            lines.append(f"Нет места: {', '.join(map(str, no_city))}")
        if no_sal:
            lines.append(f"Нет ставки: {', '.join(map(str, no_sal))}")
        lines.append("Посмотреть пост: /vac 65 · поправить: /mesto 65 Химки, /stavka 65 4000-6000 смена")
        send(chat, "\n".join(lines))
    else:
        send(chat, "Команды: /podbor · /testrass · /rass · /reparse · /vac N · /mesto N Город · "
                   "/stavka N 4000-6000 смена")


def pb_get(con, uid):
    r = con.execute("SELECT value FROM state WHERE key=?", (f"pb:{uid}",)).fetchone()
    try:
        return json.loads(r["value"]) if r else {}
    except ValueError:
        return {}


def pb_set(con, uid, d):
    con.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"pb:{uid}", json.dumps(d, ensure_ascii=False)))
    con.commit()


def pb_prof_kb():
    keys = list(T.PROFESSIONS)
    return kb([[(f"{T.PROFESSIONS[k]['emoji']} {T.PROFESSIONS[k]['name']}", f"pb:p:{k}") for k in keys[i:i + 2]]
               for i in range(0, len(keys), 2)])


PB_SALARY = [0, 3000, 4000, 5000, 6000, 7000]
PB_SCHED = [("Сменный 2/2, 3/3", "shift"), ("Пятидневка 5/2", "52"),
            ("Подработка, пара смен", "part"), ("Не важно", "any")]
PB_SCHED_NAMES = {k: n for n, k in PB_SCHED}


def pb_q_place(chat_id, mid):
    edit(chat_id, mid, "📍 Где удобно работать?",
         kb([[("Москва", "pb:w:msk"), ("Подмосковье", "pb:w:mo")], [("Не важно", "pb:w:any")]]))


def pb_q_exp(chat_id, mid, prefix=""):
    edit(chat_id, mid, prefix + "🧑‍🍳 Какой у тебя опыт?",
         kb([[(name, f"pb:e:{k}")] for k, name in T.EXPERIENCE.items()]))


def pb_q_salary(chat_id, mid):
    edit(chat_id, mid, "💰 Сколько хочешь за смену (минимум)?",
         kb([[(f"от {PB.money(v)} ₽" if v else "Не важно", f"pb:s:{v}") for v in PB_SALARY[i:i + 2]]
             for i in range(0, len(PB_SALARY), 2)]))


def pb_q_sched(chat_id, mid):
    edit(chat_id, mid, "🕒 Какой график нужен?",
         kb([[(n, f"pb:g:{k}")] for n, k in PB_SCHED]))


def pb_result_kb(sub_state="none"):
    """sub_state: none — не подписан; same — подписан на этот запрос; other — подписан на другой."""
    bell = {"none": ("🔔 Сообщать о новых под запрос", "sb:on"),
            "same": ("🔕 Отписаться", "sb:off"),
            "other": ("🔔 Сообщать по этому", "sb:on")}[sub_state]
    return kb([
        [bell],
        [("📍 Место", "pb:c:w"), ("💰 Ставка", "pb:c:s")],
        [("🕒 График", "pb:c:g"), ("🧑‍🍳 Опыт", "pb:c:e")],
        [("🆕 Новый поиск", "pb:new")],
        [("📢 Все вакансии", CHANNEL_INVITE)],
    ])


def sub_state(con, uid, st):
    r = con.execute("SELECT query, active FROM subs WHERE user_id=?", (uid,)).fetchone()
    if not r or not r["active"]:
        return "none"
    return "same" if json.loads(r["query"]) == pb_query(st) else "other"


COURIER_TEXT = (
    "🛵 <b>Курьер в Яндекс Еду</b>\n\n"
    "Курьеров подключаем напрямую по партнёрской программе Яндекс Еды — это наше агентство «Делай Дело».\n\n"
    "• работа в любом районе Москвы и МО\n"
    "• пешком, на велосипеде или на авто\n"
    "• график выбираешь сам — подходит и как подработка\n\n"
    "Условия, доход и заявка — по кнопке 👇"
)


def pb_courier(chat_id, mid):
    edit(chat_id, mid, COURIER_TEXT, kb([[("🛵 Стать курьером", T.COURIER_URL.replace("campaign=courier", "campaign=courier_podbor"))],
                                         [("🆕 Другая профессия", "pb:new")]]))


def pb_show(con, uid, chat_id, mid, st):
    if st.get("prof") == "courier":
        pb_courier(chat_id, mid)
        return
    text = pb_result(con, st)
    state = sub_state(con, uid, st)
    if state == "same":
        text += "\n\n🔔 <i>Ты подписан: новые вакансии под этот запрос бот пришлёт сам.</i>"
    edit(chat_id, mid, text, pb_result_kb(state))
    event(con, uid, "podbor", json.dumps(st, ensure_ascii=False))


# порядок вопросов; после первой выдачи (done) меняется только один параметр и сразу новая выдача
PB_NEXT = {"p": pb_q_place, "w": pb_q_exp, "e": pb_q_salary, "s": pb_q_sched}


def pb_after(con, uid, chat_id, mid, st, step):
    if st.get("done") or step == "g":
        st["done"] = True
        st.setdefault("place", "any")
        pb_show(con, uid, chat_id, mid, st)
    else:
        PB_NEXT[step](chat_id, mid)


def pb_callback(con, uid, chat_id, mid, data):
    st = pb_get(con, uid)
    _, step, val = (data.split(":", 2) + ["", "", ""])[:3]
    if step == "c":  # «изменить» с экрана результатов
        st.pop("wait", None)
        ask = {"p": None, "w": pb_q_place, "e": pb_q_exp, "s": pb_q_salary, "g": pb_q_sched}
        if val == "p":
            edit(chat_id, mid, "Кем хочешь работать?", pb_prof_kb())
        elif val in ask:
            ask[val](chat_id, mid)
    elif step == "new":
        st = {}
        edit(chat_id, mid, "🔎 <b>Подборка вакансий</b> (тест)\n\nКем хочешь работать?", pb_prof_kb())
    elif step == "p" and val == "courier":
        st = {"prof": "courier"}
        pb_courier(chat_id, mid)
    elif step == "p" and val in T.PROFESSIONS:
        st["prof"] = val
        pb_after(con, uid, chat_id, mid, st, "p")
    elif "prof" not in st:
        edit(chat_id, mid, "Давай начнём заново 🙂", kb([[PODBOR_BTN]]))
    elif step == "w":
        if val == "mo":
            st["wait"] = "city"
            edit(chat_id, mid, "🏙 Напиши город, например: <b>Химки</b>")
        else:
            st["place"] = val
            pb_after(con, uid, chat_id, mid, st, "w")
    elif step == "e" and val in T.EXPERIENCE:
        st["exp"] = val
        pb_after(con, uid, chat_id, mid, st, "e")
    elif step == "s" and val.isdigit():
        st["salary"] = int(val)
        pb_after(con, uid, chat_id, mid, st, "s")
    elif step == "g" and val in PB_SCHED_NAMES:
        st["schedule"] = val
        pb_after(con, uid, chat_id, mid, st, "g")
    pb_set(con, uid, st)


def pb_city_text(con, msg, text):
    uid = msg["from"]["id"]
    chat = msg["chat"]["id"]
    st = pb_get(con, uid)
    city = PB.canon_city(text)
    if not city:
        send(chat, "Не нашёл такой город в МО 🙈 Напиши как на карте, например: Красногорск")
        return
    st.pop("wait", None)
    st["place"] = "msk" if city == PB.MOSCOW else city
    r = send(chat, "…")
    pb_after(con, uid, chat, r["message_id"], st, "w")
    pb_set(con, uid, st)


PB_MAX = 8    # сколько подходящих показываем
PB_NEAR = 3   # сколько похожих добавляем, если подходящих мало


def plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def pb_result(con, st):
    exact, near = PB.match(con, st)
    p = T.PROFESSIONS[st["prof"]]
    place = {"msk": "Москва", "any": "Москва и МО"}.get(st.get("place"), st.get("place"))
    head = f"🔎 <b>{p['emoji']} {p['name']} · {html.escape(place)}</b>"
    if st.get("salary"):
        head += f" · от {PB.money(st['salary'])} ₽/смена"
    extra = [T.EXPERIENCE.get(st.get("exp"), "")]
    if st.get("schedule") not in (None, "any"):
        extra.append(PB_SCHED_NAMES.get(st["schedule"], ""))
    lines = [head, "<i>" + html.escape(" · ".join(x for x in extra if x)) + "</i>", ""]
    def item(r, diffs=None):
        s = f"• <a href=\"{post_link(r['message_id'])}\">{html.escape(vac_title(r['text'], title=r['title']))}</a>\n" \
            f"   {html.escape(PB.place_str(r))} · {html.escape(PB.salary_str(r))}"
        if diffs:
            s += f"\n   <i>{html.escape('; '.join(diffs))}</i>"
        return s
    show_exact = exact[:PB_MAX]
    show_near = near[:max(PB_NEAR, PB_MAX - len(show_exact))] if len(show_exact) < PB_MAX else []
    if exact:
        lines.append(f"✅ <b>{plural(len(exact), 'Нашлась', 'Нашлись', 'Нашлись')} {len(exact)} "
                     f"{plural(len(exact), 'вакансия', 'вакансии', 'вакансий')}</b> — "
                     f"это {'единственная подходящая' if len(exact) == 1 else 'все подходящие'} за 7 дней"
                     + (f", показаны первые {len(show_exact)}:" if len(exact) > len(show_exact) else ":"))
        lines += [item(r) for r, _ in show_exact]
    else:
        lines.append("😕 За 7 дней вакансий точно под твой запрос нет.")
    if show_near:
        lines += ["", "🔸 <b>Похожие, но не всё совпадает:</b>"]
        lines += [item(r, d) for r, d in show_near]
        if len(near) > len(show_near):
            rest = len(near) - len(show_near)
            lines.append(f"<i>…и ещё {rest} {plural(rest, 'похожая', 'похожие', 'похожих')}.</i>")
    if not exact:
        lines += ["", "Попробуй изменить место, ставку или график кнопками ниже. "
                      f"Новые вакансии выходят в канале каждый день — хэштег {p['tag']}."]
    return "\n".join(lines)


# ---------- рассылка новых вакансий (тест: только ADMIN_IDS) ----------

SUB_DAILY_MAX = 3          # не больше 3 отдельных сообщений в день, остальное — вечерней сводкой
QUIET_FROM, QUIET_TO = 22, 10   # с 22:00 до 10:00 по Москве не пишем — копим до утра
DIGEST_HOURS = (10, 20)    # когда отправляем накопленное: утром и вечером
INACTIVE_DAYS = 30         # не заходил в бота месяц — спрашиваем, нужна ли рассылка
PING_WAIT_DAYS = 3         # не ответил — выключаем


def msk_now():
    return time.gmtime(time.time() + 3 * 3600)


def pb_query(st):
    return {k: st.get(k) for k in ("prof", "place", "exp", "salary", "schedule")}


def query_title(q):
    p = T.PROFESSIONS.get(q.get("prof"), {})
    place = {"msk": "Москва", "any": "Москва и МО"}.get(q.get("place"), q.get("place") or "Москва и МО")
    parts = [f"{p.get('emoji', '')} {p.get('name', '')}".strip(), place]
    if q.get("salary"):
        parts.append(f"от {PB.money(q['salary'])} ₽/смена")
    if q.get("schedule") not in (None, "any"):
        parts.append(PB_SCHED_NAMES.get(q["schedule"], ""))
    return " · ".join(x for x in parts if x)


def vac_line(r):
    s = f"<a href=\"{post_link(r['message_id'])}\">{html.escape(vac_title(r['text'], title=r['title']))}</a>\n" \
        f"📍 {html.escape(PB.place_str(r))} · 💰 {html.escape(PB.salary_str(r))}"
    if r["schedule"]:
        s += f" · 🕒 {html.escape(PB.SCHEDULES.get(r['schedule'], r['schedule']))}"
    if r["noexp"] == 1:
        s += " · без опыта"
    return s


def sub_kb():
    return kb([[("⚙️ Изменить запрос", "sb:edit"), ("🔕 Отписаться", "sb:off")],
               [("📢 Все вакансии", CHANNEL_INVITE)]])


def sub_callback(con, uid, chat_id, mid, data):
    st = pb_get(con, uid)
    if data == "sb:on" and st.get("done"):
        q = pb_query(st)
        con.execute("INSERT INTO subs(user_id, query, active, created, day, sent_today, ping_at) "
                    "VALUES(?,?,1,?,?,0,NULL) ON CONFLICT(user_id) DO UPDATE SET query=excluded.query, "
                    "active=1, ping_at=NULL", (uid, json.dumps(q, ensure_ascii=False), int(time.time()), ""))
        con.commit()
        event(con, uid, "sub_on", json.dumps(q, ensure_ascii=False))
        pb_show(con, uid, chat_id, mid, st)
        send(chat_id, "🔔 <b>Готово!</b> Как только в канале выйдет вакансия под твой запрос — пришлю сюда.\n\n"
                      f"<i>{html.escape(query_title(q))}</i>\n\n"
                      "Не больше 3 сообщений в день, ночью не беспокою. "
                      "Отписаться можно в один клик под любым сообщением рассылки.")
    elif data == "sb:off":
        con.execute("UPDATE subs SET active=0 WHERE user_id=?", (uid,))
        con.execute("DELETE FROM sub_queue WHERE user_id=?", (uid,))
        con.commit()
        event(con, uid, "sub_off")
        send(chat_id, "🔕 Рассылка выключена. Включить снова — кнопкой «🔔» под подборкой.",
             kb([[("🔎 Открыть подборку", "sb:edit")]]))
    elif data == "sb:edit":
        r = con.execute("SELECT query FROM subs WHERE user_id=?", (uid,)).fetchone()
        if r:
            st.update(json.loads(r["query"]))
            st["done"] = True
            pb_set(con, uid, st)
        if st.get("done"):
            m = send(chat_id, "…")
            pb_show(con, uid, chat_id, m["message_id"], st)
        else:
            send(chat_id, "🔎 <b>Подборка вакансий</b>\n\nКем хочешь работать?", pb_prof_kb())
    elif data == "sb:keep":
        con.execute("UPDATE subs SET ping_at=NULL WHERE user_id=?", (uid,))
        con.commit()
        touch_user(con, uid)
        edit(chat_id, mid, "👍 Оставил рассылку. Пришлю, как появятся новые вакансии.", sub_kb())


def notify_subscribers(con, message_id):
    r = con.execute("SELECT v.*, p.text FROM vac v JOIN posts p ON p.message_id=v.message_id "
                    "WHERE v.message_id=? AND p.closed=0", (message_id,)).fetchone()
    if not r or not r["prof"] or (r["kind"] or "vacancy") != "vacancy":
        return
    for sub in con.execute("SELECT * FROM subs WHERE active=1").fetchall():
        if not podbor_allowed(sub["user_id"]):  # тест: рассылка только админам
            continue
        if PB.compare(r, json.loads(sub["query"])):
            continue
        deliver(con, sub, r)


def deliver(con, sub, r, force=False):
    """Сразу, если день и лимит не исчерпан; иначе — в очередь до сводки."""
    now = msk_now()
    today = time.strftime("%Y-%m-%d", now)
    sent = sub["sent_today"] if sub["day"] == today else 0
    quiet = now.tm_hour >= QUIET_FROM or now.tm_hour < QUIET_TO
    if not force and (quiet or sent >= SUB_DAILY_MAX):
        con.execute("INSERT OR IGNORE INTO sub_queue VALUES(?,?,?)",
                    (sub["user_id"], r["message_id"], int(time.time())))
        con.commit()
        return "queued"
    q = json.loads(sub["query"])
    text = (f"🔔 <b>Новая вакансия под твой запрос</b>\n<i>{html.escape(query_title(q))}</i>\n\n" + vac_line(r))
    try:
        send(sub["user_id"], text, sub_kb())
    except TgError as e:
        log.warning("deliver %s: %s", sub["user_id"], e)
        if "blocked" in str(e) or "deactivated" in str(e):
            con.execute("UPDATE subs SET active=0 WHERE user_id=?", (sub["user_id"],))
            con.commit()
        return "error"
    con.execute("UPDATE subs SET day=?, sent_today=? WHERE user_id=?", (today, sent + 1, sub["user_id"]))
    con.commit()
    event(con, sub["user_id"], "sub_sent", str(r["message_id"]))
    return "sent"


def send_digest(con, uid, force=False):
    rows = con.execute(
        "SELECT v.*, p.text FROM sub_queue q JOIN vac v ON v.message_id=q.message_id "
        "JOIN posts p ON p.message_id=q.message_id WHERE q.user_id=? AND p.closed=0 ORDER BY v.date DESC",
        (uid,)).fetchall()
    sub = con.execute("SELECT * FROM subs WHERE user_id=?", (uid,)).fetchone()
    con.execute("DELETE FROM sub_queue WHERE user_id=?", (uid,))
    con.commit()
    if not rows or not sub or not (sub["active"] or force):
        return
    q = json.loads(sub["query"])
    n = len(rows)
    lines = [f"🔔 <b>{n} {plural(n, 'новая вакансия', 'новые вакансии', 'новых вакансий')} под твой запрос</b>",
             f"<i>{html.escape(query_title(q))}</i>", ""]
    lines += [vac_line(r) + "\n" for r in rows[:10]]
    if n > 10:
        lines.append(f"<i>…и ещё {n - 10} — все в подборке.</i>")
    send(uid, "\n".join(lines), sub_kb())
    event(con, uid, "sub_digest", str(n))


def ping_inactive(con, uid):
    send(uid, "👋 Ты давно не заглядывал. <b>Рассылка вакансий ещё нужна?</b>\n\n"
              "Если не ответишь за 3 дня — выключу, чтобы не беспокоить.",
         kb([[("✅ Да, оставить", "sb:keep"), ("🔕 Выключить", "sb:off")]]))
    con.execute("UPDATE subs SET ping_at=? WHERE user_id=?", (int(time.time()), uid))
    con.commit()


def subs_tick(con):
    """Раз в цикл: сводки утром и вечером, проверка неактивных."""
    now = msk_now()
    slot = time.strftime("%Y-%m-%d ", now) + str(now.tm_hour)
    if now.tm_hour in DIGEST_HOURS:
        done = con.execute("SELECT value FROM state WHERE key='digest_slot'").fetchone()
        if not done or done["value"] != slot:
            con.execute("INSERT INTO state(key, value) VALUES('digest_slot', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (slot,))
            con.commit()
            for r in con.execute("SELECT DISTINCT user_id FROM sub_queue").fetchall():
                try:
                    send_digest(con, r["user_id"])
                except Exception:
                    log.exception("digest %s", r["user_id"])
    if now.tm_hour == 12:
        old = int(time.time()) - INACTIVE_DAYS * 86400
        for r in con.execute("SELECT s.user_id, s.ping_at FROM subs s JOIN users u ON u.id=s.user_id "
                             "WHERE s.active=1 AND u.last_seen<?", (old,)).fetchall():
            if r["ping_at"] is None:
                ping_inactive(con, r["user_id"])
            elif r["ping_at"] < time.time() - PING_WAIT_DAYS * 86400:
                con.execute("UPDATE subs SET active=0 WHERE user_id=?", (r["user_id"],))
                con.commit()
                send(r["user_id"], "🔕 Выключил рассылку вакансий. Вернуть — кнопкой «🔔» под подборкой.",
                     kb([[PODBOR_BTN]]))


def on_testrass(con, msg):
    """Показать админу все сообщения рассылки так, как их увидит соискатель."""
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    sub = con.execute("SELECT * FROM subs WHERE user_id=? AND active=1", (uid,)).fetchone()
    if not sub:
        send(chat, "Сначала подпишись: /start → «🔎 Подобрать вакансии» → «🔔 Сообщать о новых под запрос».")
        return
    q = json.loads(sub["query"])
    exact, near = PB.match(con, q, days=30)
    rows = [r for r, _ in exact] or [r for r, _ in near]
    if not rows:
        send(chat, "За 30 дней нет вакансий даже похожих на запрос — показать нечего. Поменяй запрос в /podbor.")
        return
    note = "" if exact else " (точных нет — для показа беру похожие)"
    send(chat, f"🧪 <b>Тест рассылки</b>{note}. Ниже 3 сообщения — ровно как их увидит соискатель.")
    send(chat, "<b>1. Новая вакансия</b> — приходит сразу после выхода поста (днём, до 3 в день):")
    deliver(con, sub, rows[0], force=True)
    send(chat, "<b>2. Сводка</b> — если вакансий больше 3 в день или они вышли ночью, "
               "приходят одним сообщением в 10:00 и 20:00:")
    for r in rows[:4]:
        con.execute("INSERT OR IGNORE INTO sub_queue VALUES(?,?,?)", (uid, r["message_id"], int(time.time())))
    con.commit()
    send_digest(con, uid, force=True)
    send(chat, "<b>3. Проверка активности</b> — если человек месяц не открывал бота:")
    ping_inactive(con, uid)
    con.execute("UPDATE subs SET ping_at=NULL WHERE user_id=?", (uid,))
    con.commit()


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
            try:
                fields = analyze_post(con, post["message_id"], notify=True)
                if fields and "channel_post" in upd:  # только новые посты, не правки
                    notify_subscribers(con, post["message_id"])
            except Exception:
                log.exception("analyze_post %s failed", post.get("message_id"))
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
    elif msg["from"]["id"] in ADMIN_IDS and text.startswith("/"):
        on_admin(con, msg, text)
    elif podbor_allowed(msg["from"]["id"]) and pb_get(con, msg["from"]["id"]).get("wait") == "city":
        pb_city_text(con, msg, text)
    else:
        touch_user(con, msg["from"]["id"])
        send(msg["chat"]["id"], T.WELCOME.format(privacy=T.PRIVACY_URL), professions_kb(msg["from"]["id"]))


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
        try:
            subs_tick(con)
        except Exception:
            log.exception("subs_tick failed")
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
