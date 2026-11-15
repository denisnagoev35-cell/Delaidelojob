"""Анкеты соискателей для чата @delaijobchat.

Соискатель заполняет анкету в личке с ботом, бот публикует её в чат БЕЗ контактов.
Под анкетой кнопка «📩 Связаться»: работодатель открывает бота, пишет название заведения
и сразу получает контакт, а кандидату приходит уведомление.

Режим задаётся в /etc/ddbot.env:
  ANKETA_MODE=test — анкеты доступны только ADMIN_IDS и TESTER_IDS, «публикация» уходит админам в личку;
  ANKETA_MODE=on   — для всех, публикация в ANKETA_CHAT;
  пусто            — выключено.
"""
import html
import json
import os
import re
import time

B = None   # модуль bot.py, задаётся в init()

MODE = os.environ.get("ANKETA_MODE", "").strip().lower()
CHAT = os.environ.get("ANKETA_CHAT", "@delaijobchat")
CONSENT_URL = os.environ.get("ANKETA_CONSENT_URL", "https://job.24delaidelo.ru/soglasie-anketa.html")
CONSENT_VERSION = "2026-11-16"
CONTACT_DAILY_MAX = 5          # запросов «Связаться» в день на одного работодателя

LINK_RE = re.compile(r"@\w{3,}|t\.me/|https?://|www\.|\+?\d[\d\s\-()]{8,}\d", re.I)
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё\- ]{2,30}$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS anketa(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, data TEXT, created INTEGER,
  chat_id TEXT, message_id INTEGER, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS anketa_req(
  ts INTEGER, anketa_id INTEGER, requester INTEGER, venue TEXT);
"""


def init(bot_module):
    global B
    B = bot_module


def enabled():
    return MODE in ("test", "on")


def allowed(uid):
    if MODE == "on":
        return True
    return MODE == "test" and (uid in B.ADMIN_IDS or uid in B.TESTER_IDS)


def test_mode():
    return MODE == "test"


# ---------- состояние ----------

def st_get(con, key):
    r = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(r["value"]) if r else {}
    except ValueError:
        return {}


def st_set(con, key, d):
    if d is None:
        con.execute("DELETE FROM state WHERE key=?", (key,))
    else:
        con.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(d, ensure_ascii=False)))
    con.commit()


def waiting(con, uid):
    """Ждём ли от человека текст/фото/контакт для анкеты или для запроса контакта."""
    return bool(st_get(con, f"an:{uid}").get("wait") or st_get(con, f"ac:{uid}").get("wait")
                or st_get(con, f"ae:{uid}").get("wait"))


# ---------- тексты ----------

PROF_KEYS = [k for k in ("kalyan", "bar", "barista", "waiter", "cook", "hostess", "staff")]
SALARY = [0, 3000, 4000, 5000, 6000, 7000]
SCHED = [("Сменный 2/2, 3/3", "shift"), ("Пятидневка 5/2", "52"), ("Подработка, пара смен", "part"), ("Любой", "any")]
SCHED_NAMES = {k: n for n, k in SCHED}

INTRO = (
    "📝 <b>Анкета соискателя</b>\n\n"
    "Пара минут — и анкета появится в чате @delaijobchat, где её смотрят заведения.\n\n"
    "🔒 <b>Твои контакты в чате не видны.</b> Под анкетой будет кнопка «Связаться»: заведение получит "
    "твой контакт только через бота, а тебе сразу придёт уведомление, кто интересуется.\n\n"
    "Анкету можно поправить или удалить в любой момент: /anketa\n\n"
    "Чтобы продолжить, нужно согласие на обработку персональных данных: "
    "<a href=\"{url}\">текст согласия</a>. Коротко: храним анкету на своём сервере в России, публикуем в чате "
    "без имени и контактов, контакт передаём только заведениям, которые нажали «Связаться». "
    "Удалить всё — одной кнопкой."
)
ASK_NAME = "👤 Как тебя зовут? Только имя — его увидит лишь заведение, которое захочет связаться."
ASK_PROF = "Кем хочешь работать?"
ASK_EXP = "🧑‍🍳 Какой у тебя опыт?"
ASK_PLACE = "📍 Где удобно работать?"
ASK_CITY = "🏙 Напиши город, например: <b>Химки</b>"
ASK_SAL = "💰 Сколько хочешь за смену (минимум)?"
ASK_SCHED = "🕒 Какой график нужен?"
ASK_ABOUT = ("💬 Пара слов о себе: где работал, что умеешь. До 300 знаков.\n\n"
             "<i>Без телефонов, ников и ссылок — контакты передаём только через кнопку «Связаться».</i>")
ASK_PHOTO = ("📸 Можешь прислать одно фото <b>своих работ</b> — латте-арт, коктейль, кальян, подача блюда. "
             "Не селфи: анкета публичная.")
ASK_CONTACT = "📞 Как заведению с тобой связаться?"


OTHER_EMOJI = "✨"


def prof_name(k, d=None):
    if k == "other":
        return f"{OTHER_EMOJI} {(d or {}).get('prof_text') or 'Другое'}"
    p = B.T.PROFESSIONS.get(k, {})
    return f"{p.get('emoji', '')} {p.get('name', k)}".strip()


def place_name(d):
    pl = d.get("place")
    return {"msk": "Москва", "any": "Москва и МО"}.get(pl, pl or "Москва и МО")


def public_text(d, test=False):
    p = B.T.PROFESSIONS.get(d.get("prof"), {})
    lines = []
    if test:
        lines += ["🧪 <i>Тест: так анкета будет выглядеть в чате @delaijobchat</i>", ""]
    lines.append(f"📝 <b>Анкета · {html.escape(prof_name(d.get('prof'), d))}</b>")
    lines.append("")
    lines.append(f"🧑‍🍳 Опыт: {html.escape(B.T.EXPERIENCE.get(d.get('exp'), '—'))}")
    lines.append(f"📍 {html.escape(place_name(d))}")
    sal = d.get("salary") or 0
    lines.append(f"💰 {'от ' + B.PB.money(sal) + ' ₽ за смену' if sal else 'ставка — договоримся'}")
    sched = d.get("sched")
    lines.append(f"🕒 {html.escape('Любой график' if sched in (None, 'any') else SCHED_NAMES.get(sched, 'Любой график'))}")
    if d.get("about"):
        lines += ["", f"💬 {html.escape(d['about'])}"]
    tag = p.get("tag", "") if d.get("prof") != "other" else "#другое"
    lines += ["", f"#анкета {tag}".strip()]
    return "\n".join(lines)


def contact_str(d):
    if d.get("username"):
        return f"@{d['username']}"
    if d.get("phone"):
        return d["phone"]
    return f"<a href=\"tg://user?id={d.get('uid')}\">написать в Telegram</a>"


# ---------- клавиатуры ----------

def kb_prof():
    rows = []
    for i in range(0, len(PROF_KEYS), 2):
        rows.append([(prof_name(k), f"an:p:{k}") for k in PROF_KEYS[i:i + 2]])
    rows.append([(f"{OTHER_EMOJI} Другое", "an:p:other")])
    rows.append([("Отмена", "an:cancel")])
    return B.kb(rows)


def kb_exp():
    return B.kb([[(n, f"an:e:{k}")] for k, n in B.T.EXPERIENCE.items()])


def kb_place():
    return B.kb([[("Москва", "an:w:msk"), ("Подмосковье", "an:w:mo")], [("Не важно", "an:w:any")]])


def kb_sal():
    return B.kb([[(f"от {B.PB.money(v)} ₽" if v else "Договоримся", f"an:s:{v}") for v in SALARY[i:i + 2]]
                 for i in range(0, len(SALARY), 2)])


def kb_sched():
    return B.kb([[(n, f"an:g:{k}")] for n, k in SCHED])


def kb_skip(step):
    return B.kb([[("Пропустить ➡️", f"an:skip:{step}")]])


def kb_contact(username):
    rows = []
    if username:
        rows.append([(f"💬 Пусть пишут в Telegram (@{username})", "an:c:tg")])
    rows.append([("📱 Оставить номер телефона", "an:c:phone")])
    return B.kb(rows)


def kb_preview():
    return B.kb([[("✅ Опубликовать", "an:pub")], [("✏️ Заполнить заново", "an:restart"), ("Отмена", "an:cancel")]])


def kb_mine():
    return B.kb([[("✏️ Заполнить заново", "an:restart")], [("🗑 Удалить анкету и данные", "an:del")]])


# ---------- соискатель ----------

def start(con, uid, chat_id):
    if not allowed(uid):
        B.send(chat_id, "📝 Анкеты скоро появятся. Пока можно посмотреть вакансии 👇", B.professions_kb(uid))
        return
    mine = my_anketa(con, uid)
    if mine:
        d = json.loads(mine["data"])
        B.send(chat_id, "У тебя уже есть анкета:\n\n" + public_text(d), kb_mine())
        return
    st_set(con, f"an:{uid}", {})
    B.send(chat_id, INTRO.format(url=CONSENT_URL),
           B.kb([[("✅ Согласен, заполнить анкету", "an:ok")], [("Отмена", "an:cancel")]]))


def my_anketa(con, uid):
    return con.execute("SELECT * FROM anketa WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 1",
                       (uid,)).fetchone()


def callback(con, cq, uid, chat_id, mid, data):
    if data.startswith("an:req:"):
        return
    d = st_get(con, f"an:{uid}")
    parts = data.split(":", 2)
    step = parts[1] if len(parts) > 1 else ""
    val = parts[2] if len(parts) > 2 else ""
    user = cq.get("from", {})
    if step == "cancel":
        st_set(con, f"an:{uid}", None)
        B.edit(chat_id, mid, "Ок, анкету не сохраняю. Вернуться — /anketa")
        return
    if step == "ok":
        d = {"consent": int(time.time()), "consent_v": CONSENT_VERSION, "uid": uid,
             "username": user.get("username"), "wait": "name"}
        B.event(con, uid, "anketa_consent", CONSENT_VERSION)
        st_set(con, f"an:{uid}", d)
        B.edit(chat_id, mid, ASK_NAME)
        return
    if step == "restart":
        d = {"consent": int(time.time()), "consent_v": CONSENT_VERSION, "uid": uid,
             "username": user.get("username"), "wait": "name"}
        st_set(con, f"an:{uid}", d)
        B.send(chat_id, ASK_NAME)
        return
    if step == "del":
        delete_all(con, uid)
        B.edit(chat_id, mid, "🗑 Анкета удалена из чата, данные стёрты. Заполнить снова — /anketa")
        return
    if not d.get("consent"):
        B.edit(chat_id, mid, "Давай начнём сначала: /anketa")
        return
    if step == "p" and val in PROF_KEYS:
        d["prof"] = val
        d.pop("prof_text", None)
        B.edit(chat_id, mid, ASK_EXP, kb_exp())
    elif step == "p" and val == "other":
        d["prof"] = "other"
        d["wait"] = "prof"
        B.edit(chat_id, mid, "✍️ Напиши, кем хочешь работать, например: <b>сушист</b>, <b>кассир</b>, <b>мойщик посуды</b>")
    elif step == "e" and val in B.T.EXPERIENCE:
        d["exp"] = val
        B.edit(chat_id, mid, ASK_PLACE, kb_place())
    elif step == "w":
        if val == "mo":
            d["wait"] = "city"
            B.edit(chat_id, mid, ASK_CITY)
        else:
            d["place"] = val
            B.edit(chat_id, mid, ASK_SAL, kb_sal())
    elif step == "s" and val.isdigit():
        d["salary"] = int(val)
        B.edit(chat_id, mid, ASK_SCHED, kb_sched())
    elif step == "g" and val in SCHED_NAMES:
        d["sched"] = val
        d["wait"] = "about"
        B.edit(chat_id, mid, ASK_ABOUT, kb_skip("about"))
    elif step == "skip" and val == "about":
        d.pop("about", None)
        d["wait"] = "photo"
        B.edit(chat_id, mid, ASK_PHOTO, kb_skip("photo"))
    elif step == "skip" and val == "photo":
        d.pop("photo", None)
        d.pop("wait", None)
        B.edit(chat_id, mid, ASK_CONTACT, kb_contact(d.get("username")))
    elif step == "c" and val == "tg" and d.get("username"):
        d.pop("phone", None)
        d.pop("wait", None)
        preview(con, uid, chat_id, d)
    elif step == "c" and val == "phone":
        d["wait"] = "phone"
        B.tg("sendMessage", chat_id=chat_id, text="Нажми кнопку внизу, чтобы отправить номер 👇",
             reply_markup={"keyboard": [[{"text": "📱 Отправить номер", "request_contact": True}]],
                           "resize_keyboard": True, "one_time_keyboard": True})
    elif step == "pub" and d.get("prof"):
        publish(con, uid, chat_id, d)
        d = None
    if d is not None:
        st_set(con, f"an:{uid}", d)


def on_message(con, msg):
    """Текст, фото или контакт, пока идёт анкета или запрос контакта. True — сообщение обработано."""
    uid = msg["from"]["id"]
    chat = msg["chat"]["id"]
    if st_get(con, f"ac:{uid}").get("wait"):
        return req_message(con, msg)
    if st_get(con, f"ae:{uid}").get("wait") == "city":
        return emp_city(con, msg)
    d = st_get(con, f"an:{uid}")
    w = d.get("wait")
    if not w:
        return False
    text = (msg.get("text") or "").strip()
    if w == "name":
        if not NAME_RE.match(text):
            B.send(chat, "Напиши просто имя буквами, например: <b>Аня</b>")
            return True
        d["name"] = text.title()
        d.pop("wait", None)
        B.send(chat, ASK_PROF, kb_prof())
    elif w == "prof":
        if not (2 <= len(text) <= 30) or LINK_RE.search(text) or not re.search(r"[A-Za-zА-Яа-яЁё]", text):
            B.send(chat, "Напиши профессию коротко, до 30 знаков, например: <b>сушист</b>")
            return True
        d["prof_text"] = text[:1].upper() + text[1:]
        d.pop("wait", None)
        B.send(chat, ASK_EXP, kb_exp())
    elif w == "city":
        city = B.PB.canon_city(text)
        if not city:
            B.send(chat, "Не нашёл такой город в МО 🙈 Напиши как на карте, например: Красногорск")
            return True
        d["place"] = "msk" if city == B.PB.MOSCOW else city
        d.pop("wait", None)
        B.send(chat, ASK_SAL, kb_sal())
    elif w == "about":
        if not text:
            B.send(chat, "Напиши текстом или нажми «Пропустить».", kb_skip("about"))
            return True
        if LINK_RE.search(text):
            B.send(chat, "Убери, пожалуйста, телефоны, ники и ссылки — контакты передаём только через "
                         "кнопку «Связаться». Напиши ещё раз:", kb_skip("about"))
            return True
        d["about"] = text[:300]
        d["wait"] = "photo"
        B.send(chat, ASK_PHOTO, kb_skip("photo"))
    elif w == "photo":
        if not msg.get("photo"):
            B.send(chat, "Пришли одно фото или нажми «Пропустить».", kb_skip("photo"))
            return True
        d["photo"] = msg["photo"][-1]["file_id"]
        d.pop("wait", None)
        B.send(chat, ASK_CONTACT, kb_contact(d.get("username")))
    elif w == "phone":
        c = msg.get("contact")
        if not c or c.get("user_id") != uid:
            B.send(chat, "Нажми кнопку «📱 Отправить номер» внизу — так номер придёт точно твой.")
            return True
        d["phone"] = "+" + c["phone_number"].lstrip("+")
        d.pop("wait", None)
        B.tg("sendMessage", chat_id=chat, text="✅ Номер получил.", reply_markup={"remove_keyboard": True})
        preview(con, uid, chat, d)
    st_set(con, f"an:{uid}", d)
    return True


def preview(con, uid, chat_id, d):
    txt = ("👀 <b>Проверь анкету.</b> Так её увидят в чате — без имени и контактов:\n\n"
           + public_text(d) + "\n\n"
           + f"🔒 Видно только заведению после «Связаться»: {html.escape(d.get('name', ''))}, "
           + (f"@{d['username']}" if not d.get("phone") and d.get("username") else html.escape(d.get("phone") or "")))
    if d.get("photo"):
        B.tg("sendPhoto", chat_id=chat_id, photo=d["photo"], caption="📸 Фото к анкете")
    B.send(chat_id, txt, kb_preview())


def contact_button(aid):
    return B.kb([[("📩 Связаться", f"https://t.me/{B.BOT_USERNAME}?start=c_{aid}")]])


def post(target, d, aid, test):
    """Анкета в чат — без звука, чтобы не будить участников."""
    text = public_text(d, test=test)
    if d.get("photo"):
        return B.tg("sendPhoto", chat_id=target, photo=d["photo"], caption=text, parse_mode="HTML",
                    reply_markup=contact_button(aid), disable_notification=True)
    return B.tg("sendMessage", chat_id=target, text=text, parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=contact_button(aid), disable_notification=True)


def unpublish(con, row):
    if row["chat_id"] and row["message_id"]:
        try:
            B.tg("deleteMessage", chat_id=row["chat_id"], message_id=row["message_id"])
        except B.TgError as e:
            B.log.warning("anketa delete %s: %s", row["id"], e)


def publish(con, uid, chat_id, d):
    old = my_anketa(con, uid)
    if old:
        unpublish(con, old)
        con.execute("UPDATE anketa SET active=0 WHERE id=?", (old["id"],))
    d = {k: v for k, v in d.items() if k != "wait"}
    cur = con.execute("INSERT INTO anketa(user_id, data, created, active) VALUES(?,?,?,1)",
                      (uid, json.dumps(d, ensure_ascii=False), int(time.time())))
    aid = cur.lastrowid
    con.commit()
    targets = list(B.ADMIN_IDS) if test_mode() else [CHAT]
    first = None
    for t in targets:
        try:
            m = post(t, d, aid, test_mode())
            first = first or (t, m["message_id"])
        except B.TgError as e:
            B.log.warning("anketa publish to %s: %s", t, e)
    if first:
        con.execute("UPDATE anketa SET chat_id=?, message_id=? WHERE id=?", (str(first[0]), first[1], aid))
        con.commit()
    st_set(con, f"an:{uid}", None)
    B.event(con, uid, "anketa_pub", str(aid))
    try:
        emp_notify(con, aid)
    except Exception:
        B.log.exception("emp_notify %s", aid)
    where = "тебе в личку (тестовый режим)" if test_mode() else "в чате @delaijobchat"
    B.send(chat_id, f"✅ <b>Анкета опубликована</b> {where}.\n\n"
                    "Когда заведение нажмёт «Связаться», я сразу сообщу, кто интересуется.\n\n"
                    "Поправить или удалить — /anketa",
           B.kb([[PODBOR_BTN()]]) if B.podbor_allowed(uid) else None)
    if not test_mode():
        for a in B.ADMIN_IDS:
            try:
                B.send(a, f"📝 Новая анкета №{aid}: {html.escape(prof_name(d.get('prof'), d))}, "
                          f"{html.escape(place_name(d))} · {html.escape(d.get('name', ''))} {contact_str(d)}")
            except B.TgError:
                pass


def PODBOR_BTN():
    return ("🔎 Подобрать вакансии под меня", "pb:start")


def delete_all(con, uid):
    for r in con.execute("SELECT * FROM anketa WHERE user_id=? AND active=1", (uid,)).fetchall():
        unpublish(con, r)
    con.execute("DELETE FROM anketa WHERE user_id=?", (uid,))
    con.commit()
    st_set(con, f"an:{uid}", None)
    B.event(con, uid, "anketa_deleted")


# ---------- работодатель: «Связаться» ----------

def req_start(con, uid, chat_id, aid):
    row = con.execute("SELECT * FROM anketa WHERE id=?", (aid,)).fetchone()
    if not row or not row["active"]:
        B.send(chat_id, "😕 Эта анкета уже снята — кандидат нашёл работу или удалил её.\n\n"
                        "Свежие анкеты — в чате @delaijobchat")
        return
    if row["user_id"] == uid and not test_mode():
        B.send(chat_id, "Это твоя анкета 🙂 Поправить или удалить — /anketa")
        return
    day = int(time.time()) - 86400
    n = con.execute("SELECT COUNT(*) c FROM anketa_req WHERE requester=? AND ts>=?", (uid, day)).fetchone()["c"]
    if n >= CONTACT_DAILY_MAX and uid not in B.ADMIN_IDS:
        B.send(chat_id, f"На сегодня лимит — {CONTACT_DAILY_MAX} контактов. Если ищете много людей, напишите нам: "
                        f"{B.T.CONTACT}")
        return
    st_set(con, f"ac:{uid}", {"wait": "venue", "aid": aid})
    d = json.loads(row["data"])
    B.send(chat_id, f"📩 <b>Контакт кандидата</b>\n<i>{html.escape(prof_name(d.get('prof'), d))} · "
                    f"{html.escape(place_name(d))}</i>\n\n"
                    "Напишите название заведения — кандидат увидит, кто им интересуется, и будет ждать сообщения.",
           B.kb([[("Отмена", "an:req:cancel")]]))


def req_message(con, msg):
    uid = msg["from"]["id"]
    chat = msg["chat"]["id"]
    s = st_get(con, f"ac:{uid}")
    venue = (msg.get("text") or "").strip()
    if not (2 <= len(venue) <= 60) or LINK_RE.search(venue):
        B.send(chat, "Напишите просто название заведения, например: <b>Кофейня «Зерно»</b>")
        return True
    row = con.execute("SELECT * FROM anketa WHERE id=?", (s.get("aid"),)).fetchone()
    st_set(con, f"ac:{uid}", None)
    if not row or not row["active"]:
        B.send(chat, "😕 Анкета уже снята.")
        return True
    d = json.loads(row["data"])
    con.execute("INSERT INTO anketa_req VALUES(?,?,?,?)", (int(time.time()), row["id"], uid, venue))
    con.commit()
    B.event(con, uid, "anketa_contact", f"{row['id']}:{venue}")
    B.send(chat, f"✅ <b>Контакт кандидата</b>\n\n"
                 f"👤 {html.escape(d.get('name', ''))} — {html.escape(prof_name(d.get('prof'), d))}, "
                 f"{html.escape(place_name(d))}\n📞 {contact_str(d)}\n\n"
                 f"Напишите кандидату и представьтесь: «{html.escape(venue)}, по анкете в чате Делай Дело». "
                 f"Мы уже предупредили, что вы напишете.")
    try:
        B.send(row["user_id"], f"👀 <b>Твоей анкетой заинтересовались!</b>\n\n"
                               f"Заведение: <b>{html.escape(venue)}</b>. Скоро напишут"
                               f"{' в Telegram' if not d.get('phone') else ' или позвонят'} — проверь сообщения "
                               f"(в том числе папку «Запросы на переписку»).")
    except B.TgError as e:
        B.log.warning("anketa notify %s: %s", row["user_id"], e)
    frm = msg.get("from", {})
    who = f"@{frm['username']}" if frm.get("username") else f"id {uid}"
    for a in B.ADMIN_IDS:
        if a == uid:
            continue
        try:
            B.send(a, f"📩 Запрос контакта: «{html.escape(venue)}» ({who}) → анкета №{row['id']}")
        except B.TgError:
            pass
    return True


def req_callback(con, uid, chat_id, mid, data):
    if data == "an:req:cancel":
        st_set(con, f"ac:{uid}", None)
        B.edit(chat_id, mid, "Ок, отменил.")


# ---------- админ ----------

def admin_pin(con, chat_id):
    """Сообщение с кнопкой для закрепа в чате. В тесте — превью админу."""
    text = ("📝 <b>Анкеты соискателей · HoReCa Москвы и МО</b>\n\n"
            "Здесь заведения ищут сотрудников: кальянщиков, барменов, бариста, официантов, поваров и не только.\n\n"
            "<b>Ищешь работу?</b>\n"
            "1. Нажми «📝 Заполнить анкету» — откроется наш бот.\n"
            "2. Ответь на несколько вопросов, это 2 минуты.\n"
            "3. Анкета появится в этом чате, и заведения напишут тебе сами.\n\n"
            "🔒 Контакты в анкете не видны. Заведение получает их только через кнопку «Связаться», а тебе сразу "
            "приходит уведомление, кто интересуется. Поправить или удалить анкету — /anketa в боте.\n\n"
            "<b>Ищете сотрудников?</b>\n"
            "Листайте анкеты и нажимайте «📩 Связаться» под подходящей. Или ищите в боте: выберите профессию и город — "
            "бот покажет подходящие анкеты и будет присылать новые, как только они появятся.\n"
            f"Разместить вакансию в канале @delaidelojob — {B.T.CONTACT}")
    markup = B.kb([[("📝 Заполнить анкету", f"https://t.me/{B.BOT_USERNAME}?start=anketa")],
                   [("🏪 Найти сотрудника в боте", f"https://t.me/{B.BOT_USERNAME}?start=kandidaty")],
                   [("📢 Вакансии каждый день", B.CHANNEL_INVITE)]])
    if test_mode():
        B.send(chat_id, "🧪 Тест: этот пост бот опубликует в чате после запуска (ANKETA_MODE=on):")
        B.send(chat_id, text, markup)
    else:
        m = B.tg("sendMessage", chat_id=CHAT, text=text, parse_mode="HTML", disable_web_page_preview=True,
                 reply_markup=markup, disable_notification=True)
        try:
            B.tg("pinChatMessage", chat_id=CHAT, message_id=m["message_id"], disable_notification=True)
            B.send(chat_id, "✅ Опубликовал и закрепил в чате.")
        except B.TgError as e:
            B.send(chat_id, f"Опубликовал, но закрепить не смог: {html.escape(str(e))}. Закрепи вручную.")


def admin_list(con, chat_id):
    rows = con.execute("SELECT * FROM anketa WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall()
    reqs = con.execute("SELECT COUNT(*) c FROM anketa_req").fetchone()["c"]
    lines = [f"📝 Активных анкет: {len(rows)} (показаны последние 20) · запросов контакта всего: {reqs}"]
    for r in rows:
        d = json.loads(r["data"])
        lines.append(f"№{r['id']} · {html.escape(prof_name(d.get('prof'), d))} · {html.escape(place_name(d))} · "
                     f"{html.escape(d.get('name', ''))} {contact_str(d)}")
    B.send(chat_id, "\n".join(lines))


# ---------- работодатель: подборка анкет и рассылка новых ----------
# EMP_MODE=test — только ADMIN_IDS/TESTER_IDS; on — все; пусто — выключено.

EMP_MODE = os.environ.get("EMP_MODE", "").strip().lower()
EMP_DAYS = 30            # анкеты не старше
EMP_LIST_MAX = 8         # сколько анкет в одной выдаче
EMP_DAILY_MAX = 10       # новых анкет в день отдельными сообщениями, остальное — сводкой
EMP_QUIET = (22, 10)     # с 22:00 до 10:00 МСК копим
EMP_DIGEST_HOURS = (10, 20)

EMP_SCHEMA = """
CREATE TABLE IF NOT EXISTS emp_subs(
  user_id INTEGER PRIMARY KEY, query TEXT, active INTEGER DEFAULT 1, created INTEGER,
  day TEXT, sent_today INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS emp_queue(user_id INTEGER, anketa_id INTEGER, ts INTEGER,
  PRIMARY KEY(user_id, anketa_id));
"""
SCHEMA += EMP_SCHEMA


def emp_allowed(uid):
    if EMP_MODE == "on":
        return True
    return EMP_MODE == "test" and (uid in B.ADMIN_IDS or uid in B.TESTER_IDS)


def emp_q_title(q):
    prof = q.get("prof") or "any"
    p = "Все профессии" if prof == "any" else prof_name(prof, {"prof_text": "Другие профессии"})
    pl = {"msk": "Москва", "any": "Москва и МО"}.get(q.get("place"), q.get("place") or "Москва и МО")
    return f"{p} · {pl}"


def emp_kb_prof():
    rows = [[(prof_name(k), f"ae:p:{k}") for k in PROF_KEYS[i:i + 2]] for i in range(0, len(PROF_KEYS), 2)]
    rows.append([(f"{OTHER_EMOJI} Другие", "ae:p:other"), ("Все профессии", "ae:p:any")])
    return B.kb(rows)


def emp_kb_place():
    return B.kb([[("Москва", "ae:w:msk"), ("Подмосковье", "ae:w:mo")], [("Москва и МО", "ae:w:any")]])


def emp_matches(row_data, q):
    if q.get("prof") not in (None, "any") and row_data.get("prof") != q["prof"]:
        return False
    want, have = q.get("place") or "any", row_data.get("place") or "any"
    if want == "any" or have == "any":
        return True
    return want == have


def emp_find(con, q):
    since = int(time.time()) - EMP_DAYS * 86400
    out = []
    for r in con.execute("SELECT * FROM anketa WHERE active=1 AND created>=? ORDER BY id DESC", (since,)).fetchall():
        d = json.loads(r["data"])
        if emp_matches(d, q):
            out.append((r, d))
    return out


def emp_line(r, d):
    sal = d.get("salary") or 0
    sched = d.get("sched")
    parts = [B.T.EXPERIENCE.get(d.get("exp"), ""), "от " + B.PB.money(sal) + " ₽/смена" if sal else "ставка — договоримся",
             "любой график" if sched in (None, "any") else SCHED_NAMES.get(sched, "")]
    s = (f"<b>№{r['id']} · {html.escape(prof_name(d.get('prof'), d))}</b> · {html.escape(place_name(d))}"
         f"{' · 📸' if d.get('photo') else ''}\n   " + html.escape(" · ".join(x for x in parts if x)))
    if d.get("about"):
        a = d["about"] if len(d["about"]) <= 120 else d["about"][:117].rsplit(" ", 1)[0] + "…"
        s += f"\n   <i>{html.escape(a)}</i>"
    return s


def emp_sub_state(con, uid, q):
    r = con.execute("SELECT query, active FROM emp_subs WHERE user_id=?", (uid,)).fetchone()
    if not r or not r["active"]:
        return "none"
    return "same" if json.loads(r["query"]) == q else "other"


def emp_show(con, uid, chat_id, mid, q):
    found = emp_find(con, q)
    lines = [f"📋 <b>Анкеты: {html.escape(emp_q_title(q))}</b>", f"<i>за {EMP_DAYS} дней, свежие сверху</i>", ""]
    shown = found[:EMP_LIST_MAX]
    if shown:
        lines += [emp_line(r, d) + "\n" for r, d in shown]
        if len(found) > len(shown):
            lines.append(f"<i>…и ещё {len(found) - len(shown)} в чате @delaijobchat</i>")
    else:
        lines.append("😕 Пока анкет под этот запрос нет. Включите уведомления — пришлём, как только появится.")
    state = emp_sub_state(con, uid, q)
    if state == "same":
        lines.append("🔔 <i>Уведомления включены: новые анкеты под этот запрос бот пришлёт сам.</i>")
    rows = []
    btns = [(f"📩 Связаться №{r['id']}", f"ae:c:{r['id']}") for r, _ in shown]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([{"none": ("🔔 Присылать новые анкеты", "ae:on"), "same": ("🔕 Выключить уведомления", "ae:off"),
                  "other": ("🔔 Присылать по этому запросу", "ae:on")}[state]])
    rows.append([("⚙️ Изменить запрос", "ae:new")])
    B.edit(chat_id, mid, "\n".join(lines), B.kb(rows))
    B.event(con, uid, "emp_list", json.dumps(q, ensure_ascii=False))


def emp_start(con, uid, chat_id):
    st_set(con, f"ae:{uid}", {})
    B.send(chat_id, "📋 <b>Анкеты кандидатов</b>\n\nСоискатели оставляют анкеты в чате @delaijobchat. "
                    "Выберите, кого ищете, — покажу свежие анкеты, а контакт кандидата отдам по кнопке «Связаться».\n\n"
                    "Кого ищете?", emp_kb_prof())


def emp_callback(con, uid, chat_id, mid, data):
    st = st_get(con, f"ae:{uid}")
    parts = data.split(":", 2)
    step = parts[1] if len(parts) > 1 else ""
    val = parts[2] if len(parts) > 2 else ""
    if step == "start" or step == "new":
        st_set(con, f"ae:{uid}", {})
        B.edit(chat_id, mid, "📋 <b>Анкеты кандидатов</b>\n\nКого ищете?", emp_kb_prof())
        return
    if step == "p" and (val in PROF_KEYS or val in ("other", "any")):
        st = {"prof": val}
        B.edit(chat_id, mid, "📍 Где заведение?", emp_kb_place())
    elif step == "w" and "prof" in st:
        if val == "mo":
            st["wait"] = "city"
            B.edit(chat_id, mid, "🏙 Напишите город, например: <b>Химки</b>")
        else:
            st["place"] = val
            st["done"] = True
            emp_show(con, uid, chat_id, mid, {"prof": st["prof"], "place": val})
    elif step == "c" and val.isdigit():
        req_start(con, uid, chat_id, int(val))
    elif step == "on" and st.get("done"):
        q = {"prof": st["prof"], "place": st["place"]}
        con.execute("INSERT INTO emp_subs(user_id, query, active, created, day, sent_today) VALUES(?,?,1,?,'',0) "
                    "ON CONFLICT(user_id) DO UPDATE SET query=excluded.query, active=1",
                    (uid, json.dumps(q, ensure_ascii=False), int(time.time())))
        con.commit()
        B.event(con, uid, "emp_sub_on", json.dumps(q, ensure_ascii=False))
        emp_show(con, uid, chat_id, mid, q)
        B.send(chat_id, f"🔔 <b>Готово!</b> Новые анкеты «{html.escape(emp_q_title(q))}» пришлю сюда, как только появятся.\n\n"
                        f"Не больше {EMP_DAILY_MAX} в день, ночью не беспокою — утром пришлю сводку.")
    elif step == "off":
        con.execute("UPDATE emp_subs SET active=0 WHERE user_id=?", (uid,))
        con.execute("DELETE FROM emp_queue WHERE user_id=?", (uid,))
        con.commit()
        B.event(con, uid, "emp_sub_off")
        if st.get("done"):
            emp_show(con, uid, chat_id, mid, {"prof": st["prof"], "place": st["place"]})
        else:
            B.edit(chat_id, mid, "🔕 Уведомления об анкетах выключены.", B.kb([[("📋 Анкеты кандидатов", "ae:start")]]))
    else:
        B.edit(chat_id, mid, "Давайте начнём заново 🙂", B.kb([[("📋 Анкеты кандидатов", "ae:start")]]))
        return
    st_set(con, f"ae:{uid}", st)


def emp_city(con, msg):
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    st = st_get(con, f"ae:{uid}")
    city = B.PB.canon_city((msg.get("text") or "").strip())
    if not city:
        B.send(chat, "Не нашёл такой город в МО 🙈 Напишите как на карте, например: Красногорск")
        return True
    st.pop("wait", None)
    st["place"] = "msk" if city == B.PB.MOSCOW else city
    st["done"] = True
    st_set(con, f"ae:{uid}", st)
    m = B.send(chat, "…")
    emp_show(con, uid, chat, m["message_id"], {"prof": st["prof"], "place": st["place"]})
    return True


def emp_card_kb(aid):
    return B.kb([[("📩 Связаться", f"ae:c:{aid}")],
                 [("⚙️ Изменить запрос", "ae:new"), ("🔕 Выключить", "ae:off")]])


def emp_deliver(con, sub, row, d, force=False):
    now = B.msk_now()
    today = time.strftime("%Y-%m-%d", now)
    sent = sub["sent_today"] if sub["day"] == today else 0
    quiet = (now.tm_hour >= EMP_QUIET[0] or now.tm_hour < EMP_QUIET[1]) and EMP_MODE != "test"
    if not force and (quiet or sent >= EMP_DAILY_MAX):
        con.execute("INSERT OR IGNORE INTO emp_queue VALUES(?,?,?)", (sub["user_id"], row["id"], int(time.time())))
        con.commit()
        return "queued"
    q = json.loads(sub["query"])
    text = f"🆕 <b>Новая анкета</b> · <i>{html.escape(emp_q_title(q))}</i>\n\n" + public_text(d)
    try:
        if d.get("photo"):
            B.tg("sendPhoto", chat_id=sub["user_id"], photo=d["photo"], caption=text, parse_mode="HTML",
                 reply_markup=emp_card_kb(row["id"]))
        else:
            B.send(sub["user_id"], text, emp_card_kb(row["id"]))
    except B.TgError as e:
        B.log.warning("emp_deliver %s: %s", sub["user_id"], e)
        if "blocked" in str(e) or "deactivated" in str(e):
            con.execute("UPDATE emp_subs SET active=0 WHERE user_id=?", (sub["user_id"],))
            con.commit()
        return "error"
    con.execute("UPDATE emp_subs SET day=?, sent_today=? WHERE user_id=?", (today, sent + 1, sub["user_id"]))
    con.commit()
    B.event(con, sub["user_id"], "emp_sent", str(row["id"]))
    return "sent"


def emp_notify(con, aid):
    if EMP_MODE not in ("test", "on"):
        return
    row = con.execute("SELECT * FROM anketa WHERE id=? AND active=1", (aid,)).fetchone()
    if not row:
        return
    d = json.loads(row["data"])
    for sub in con.execute("SELECT * FROM emp_subs WHERE active=1").fetchall():
        if sub["user_id"] == row["user_id"] or not emp_allowed(sub["user_id"]):
            continue
        if emp_matches(d, json.loads(sub["query"])):
            emp_deliver(con, sub, row, d)


def emp_digest(con, uid, force=False):
    rows = con.execute("SELECT a.* FROM emp_queue q JOIN anketa a ON a.id=q.anketa_id "
                       "WHERE q.user_id=? AND a.active=1 ORDER BY a.id DESC", (uid,)).fetchall()
    sub = con.execute("SELECT * FROM emp_subs WHERE user_id=?", (uid,)).fetchone()
    con.execute("DELETE FROM emp_queue WHERE user_id=?", (uid,))
    con.commit()
    if not rows or not sub or not (sub["active"] or force):
        return
    q = json.loads(sub["query"])
    n = len(rows)
    lines = [f"🆕 <b>{n} {B.plural(n, 'новая анкета', 'новые анкеты', 'новых анкет')}</b> · "
             f"<i>{html.escape(emp_q_title(q))}</i>", ""]
    shown = rows[:10]
    lines += [emp_line(r, json.loads(r["data"])) + "\n" for r in shown]
    btns = [(f"📩 Связаться №{r['id']}", f"ae:c:{r['id']}") for r in shown]
    kbrows = [btns[i:i + 2] for i in range(0, len(btns), 2)] + [[("⚙️ Изменить запрос", "ae:new"), ("🔕 Выключить", "ae:off")]]
    B.send(uid, "\n".join(lines), B.kb(kbrows))
    B.event(con, uid, "emp_digest", str(n))


def emp_tick(con):
    if EMP_MODE not in ("test", "on"):
        return
    now = B.msk_now()
    if now.tm_hour not in EMP_DIGEST_HOURS:
        return
    slot = time.strftime("%Y-%m-%d ", now) + str(now.tm_hour)
    done = con.execute("SELECT value FROM state WHERE key='emp_digest_slot'").fetchone()
    if done and done["value"] == slot:
        return
    con.execute("INSERT INTO state(key, value) VALUES('emp_digest_slot', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (slot,))
    con.commit()
    for r in con.execute("SELECT DISTINCT user_id FROM emp_queue").fetchall():
        try:
            emp_digest(con, r["user_id"])
        except Exception:
            B.log.exception("emp_digest %s", r["user_id"])


def admin_empsubs(con, chat_id):
    rows = con.execute("SELECT * FROM emp_subs ORDER BY created DESC LIMIT 30").fetchall()
    q = con.execute("SELECT COUNT(*) c FROM emp_queue").fetchone()["c"]
    lines = [f"🏪 Подписок работодателей: {sum(r['active'] for r in rows)} активных из {len(rows)}; в очереди: {q}"]
    lines += [f"• {r['user_id']}: {html.escape(emp_q_title(json.loads(r['query'])))}{'' if r['active'] else ' (выкл.)'}"
              for r in rows]
    B.send(chat_id, "\n".join(lines))
