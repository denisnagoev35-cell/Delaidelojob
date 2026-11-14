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
                           range=p.get("range_by_exp", {}).get(exp_key) or p["range"],
                           source=p["source"], exp_note=note)


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

    if data.startswith("pb:") and uid in ADMIN_IDS:
        pb_callback(con, uid, chat_id, mid, data)
    elif data.startswith("pm:") and uid in ADMIN_IDS:
        _, n, city = (data.split(":") + ["", "", ""])[:3]
        if n.isdigit():
            if city == "skip":
                PB.set_field(con, int(n), asked=2)
                edit(chat_id, mid, f"Пост №{n}: пропущен, в подборку по месту не попадёт.")
            else:
                PB.set_field(con, int(n), city=PB.MOSCOW, parsed_by="manual")
                edit(chat_id, mid, vac_card(con, int(n)))
    elif data == "restart":
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


# ---------- подборка вакансий (тест: только ADMIN_IDS) ----------

PROF_TAGS = {k: v["tag"] for k, v in T.PROFESSIONS.items()}
PROF_NAMES = {k: v["name"] for k, v in T.PROFESSIONS.items()}


def post_tags(text):
    return sorted({t.lower() for t in HASHTAG_RE.findall(text or "")})


def analyze_post(con, message_id, notify=False, use_gpt=True):
    row = con.execute("SELECT * FROM posts WHERE message_id=?", (message_id,)).fetchone()
    if not row or not PB.needs_parse(con, message_id, row["text"]):
        return None
    if not row["text"].strip():
        return None
    fields, by = PB.parse_post(row["text"], post_tags(row["text"]), PROF_TAGS, use_gpt=use_gpt)
    PB.store(con, message_id, row["date"], row["text"], fields, by)
    r = PB.get_vac(con, message_id)
    if notify and fields.get("prof") and r and not r["asked"]:
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
        pb_set(con, msg["from"]["id"], {})
        send(chat, "🔎 <b>Подборка вакансий</b> (тест)\n\nКем хочешь работать?", pb_prof_kb())
    elif cmd == "/vac" and args and args[0].isdigit():
        send(chat, vac_card(con, int(args[0])))
    elif cmd == "/mesto" and len(args) >= 2 and args[0].isdigit():
        city = PB.canon_city(" ".join(args[1:]))
        if not city:
            send(chat, "Не знаю такой город. Пиши как на сайте: Химки, Красногорск, Москва.")
            return
        PB.set_field(con, int(args[0]), city=city, parsed_by="manual")
        send(chat, vac_card(con, int(args[0])))
    elif cmd == "/stavka" and len(args) >= 2 and args[0].isdigit():
        nums = [int(x) for x in re.findall(r"\d+", args[1])]
        unit = UNIT_WORDS.get((args[2] if len(args) > 2 else "смена").lower().strip("."), "shift")
        if not nums:
            send(chat, "Формат: /stavka 65 4000-6000 смена")
            return
        PB.set_field(con, int(args[0]), sal_min=nums[0], sal_max=nums[1] if len(nums) > 1 else None, unit=unit,
                     parsed_by="manual")
        send(chat, vac_card(con, int(args[0])))
    elif cmd == "/reparse":
        days = int(args[0]) if args and args[0].isdigit() else 14
        since = int(time.time()) - days * 86400
        # посты, поправленные вручную (/mesto, /stavka), не трогаем
        ids = [r["message_id"] for r in con.execute(
            "SELECT p.message_id FROM posts p LEFT JOIN vac v ON v.message_id=p.message_id "
            "WHERE p.date>=? AND (v.parsed_by IS NULL OR v.parsed_by!='manual') ORDER BY p.date",
            (since,)).fetchall()]
        send(chat, f"Разбираю {len(ids)} постов за {days} дн… (поправленные вручную не трогаю)")
        con.execute("DELETE FROM vac WHERE message_id IN (%s)" % ",".join("?" * len(ids)), ids)
        con.commit()
        ok = gpt = media = 0
        no_city, no_sal, no_prof = [], [], []
        for n in ids:
            row = con.execute("SELECT text FROM posts WHERE message_id=?", (n,)).fetchone()
            if not row or not (row["text"] or "").strip():
                media += 1  # фото/видео без текста
                continue
            f = analyze_post(con, n) or {}
            r = PB.get_vac(con, n)
            if r and r["parsed_by"] == "gpt":
                gpt += 1
            if not f.get("prof"):
                no_prof.append(n)  # промо, анонсы, дайджесты
                continue
            if not f.get("city"):
                no_city.append(n)
            if not f.get("unit"):
                no_sal.append(n)
            if f.get("city") and f.get("unit"):
                ok += 1
        lines = [f"✅ Готово: полностью разобрано {ok} из {len(ids) - media} постов с текстом "
                 f"(через YandexGPT: {gpt}; без текста пропущено: {media})."]
        if no_prof:
            lines.append(f"Не вакансии (промо, анонсы): {', '.join(map(str, no_prof))}")
        if no_city:
            lines.append(f"Нет места: {', '.join(map(str, no_city))}")
        if no_sal:
            lines.append(f"Нет ставки: {', '.join(map(str, no_sal))}")
        lines.append("Посмотреть пост: /vac 65 · поправить: /mesto 65 Химки, /stavka 65 4000-6000 смена")
        send(chat, "\n".join(lines))
    else:
        send(chat, "Команды подборки: /podbor · /reparse · /vac N · /mesto N Город · /stavka N 4000-6000 смена")


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
PB_SCHED = [("2/2", "2/2"), ("5/2", "5/2"), ("Подработка", "parttime"), ("Не важно", "any")]


def pb_ask_exp(chat_id, mid):
    edit(chat_id, mid, "🧑‍🍳 Какой у тебя опыт?",
         kb([[(name, f"pb:e:{k}")] for k, name in T.EXPERIENCE.items()]))


def pb_callback(con, uid, chat_id, mid, data):
    st = pb_get(con, uid)
    _, step, val = (data.split(":", 2) + ["", "", ""])[:3]
    if step == "p" and val in T.PROFESSIONS:
        st = {"prof": val}
        edit(chat_id, mid, "📍 Где удобно работать?",
             kb([[("Москва", "pb:w:msk"), ("Подмосковье", "pb:w:mo")], [("Не важно", "pb:w:any")]]))
    elif step == "w" and "prof" in st:
        if val == "mo":
            st["wait"] = "city"
            edit(chat_id, mid, "🏙 Напиши город, например: <b>Химки</b>")
        else:
            st["place"] = val
            pb_ask_exp(chat_id, mid)
    elif step == "e" and val in T.EXPERIENCE and "prof" in st:
        st["exp"] = val
        edit(chat_id, mid, "💰 Сколько хочешь за смену (минимум)?",
             kb([[(f"от {PB.money(v)} ₽" if v else "Не важно", f"pb:s:{v}") for v in PB_SALARY[i:i + 2]]
                 for i in range(0, len(PB_SALARY), 2)]))
    elif step == "s" and val.isdigit() and "prof" in st:
        st["salary"] = int(val)
        edit(chat_id, mid, "🕒 Какой график нужен?",
             kb([[(n, f"pb:g:{k}") for n, k in PB_SCHED[:2]], [(n, f"pb:g:{k}") for n, k in PB_SCHED[2:]]]))
    elif step == "g" and "prof" in st:
        st["schedule"] = val
        st.setdefault("place", "any")
        edit(chat_id, mid, pb_result(con, st),
             kb([[("🔁 Ещё раз", "pb:p:" + st["prof"])], [("📢 Все вакансии", CHANNEL_INVITE)]]))
        event(con, uid, "podbor", json.dumps(st, ensure_ascii=False))
    pb_set(con, uid, st)


def pb_city_text(con, msg, text):
    uid = msg["from"]["id"]
    st = pb_get(con, uid)
    city = PB.canon_city(text)
    if not city:
        send(msg["chat"]["id"], "Не нашёл такой город в МО 🙈 Напиши как на карте, например: Красногорск")
        return
    st.pop("wait", None)
    st["place"] = "msk" if city == PB.MOSCOW else city
    pb_set(con, uid, st)
    send(msg["chat"]["id"], f"📍 {html.escape(city)}\n\n🧑‍🍳 Какой у тебя опыт?",
         kb([[(name, f"pb:e:{k}")] for k, name in T.EXPERIENCE.items()]))


def pb_result(con, st):
    exact, near = PB.match(con, st)
    p = T.PROFESSIONS[st["prof"]]
    place = {"msk": "Москва", "any": "Москва и МО"}.get(st.get("place"), st.get("place"))
    head = f"🔎 <b>{p['emoji']} {p['name']} · {html.escape(place)}</b>"
    if st.get("salary"):
        head += f" · от {PB.money(st['salary'])} ₽/смена"
    lines = [head, ""]
    def item(r, diffs=None):
        s = f"• <a href=\"{post_link(r['message_id'])}\">{html.escape(first_line(r['text'], 50))}</a>\n" \
            f"   {html.escape(PB.place_str(r))} · {html.escape(PB.salary_str(r))}"
        if diffs:
            s += f"\n   <i>{html.escape('; '.join(diffs))}</i>"
        return s
    if exact:
        lines.append(f"✅ <b>Подходят ({len(exact)})</b> — за последние 7 дней:")
        lines += [item(r) for r, _ in exact]
    else:
        lines.append("Точно под тебя за неделю вакансий нет 😕")
    if near:
        lines += ["", "🔸 <b>Близко, но не всё совпадает:</b>"]
        lines += [item(r, d) for r, d in near]
    if not exact and not near:
        lines.append(f"Загляни в канал по хэштегу {p['tag']} — новые вакансии выходят каждый день.")
    return "\n".join(lines)


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
                analyze_post(con, post["message_id"], notify=True)
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
    elif msg["from"]["id"] in ADMIN_IDS and pb_get(con, msg["from"]["id"]).get("wait") == "city":
        pb_city_text(con, msg, text)
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
