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
    return bool(st_get(con, f"an:{uid}").get("wait") or st_get(con, f"ac:{uid}").get("wait"))


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


def prof_name(k):
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
    lines.append(f"📝 <b>Анкета · {html.escape(prof_name(d.get('prof')))}</b>")
    lines.append("")
    lines.append(f"🧑‍🍳 Опыт: {html.escape(B.T.EXPERIENCE.get(d.get('exp'), '—'))}")
    lines.append(f"📍 {html.escape(place_name(d))}")
    sal = d.get("salary") or 0
    lines.append(f"💰 {'от ' + B.PB.money(sal) + ' ₽ за смену' if sal else 'ставка — договоримся'}")
    lines.append(f"🕒 {html.escape(SCHED_NAMES.get(d.get('sched'), 'Любой график'))}")
    if d.get("about"):
        lines += ["", f"💬 {html.escape(d['about'])}"]
    tag = p.get("tag", "")
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
        B.edit(chat_id, mid, ASK_EXP, kb_exp())
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
    text = public_text(d, test=test)
    if d.get("photo"):
        return B.tg("sendPhoto", chat_id=target, photo=d["photo"], caption=text, parse_mode="HTML",
                    reply_markup=contact_button(aid))
    return B.send(target, text, contact_button(aid))


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
    where = "тебе в личку (тестовый режим)" if test_mode() else "в чате @delaijobchat"
    B.send(chat_id, f"✅ <b>Анкета опубликована</b> {where}.\n\n"
                    "Когда заведение нажмёт «Связаться», я сразу сообщу, кто интересуется.\n\n"
                    "Поправить или удалить — /anketa",
           B.kb([[PODBOR_BTN()]]) if B.podbor_allowed(uid) else None)
    if not test_mode():
        for a in B.ADMIN_IDS:
            try:
                B.send(a, f"📝 Новая анкета №{aid}: {html.escape(prof_name(d.get('prof')))}, "
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
    B.send(chat_id, f"📩 <b>Контакт кандидата</b>\n<i>{html.escape(prof_name(d.get('prof')))} · "
                    f"{html.escape(place_name(d))}</i>\n\n"
                    "Напиши название заведения — кандидат увидит, кто им интересуется, и будет ждать сообщения.",
           B.kb([[("Отмена", "an:req:cancel")]]))


def req_message(con, msg):
    uid = msg["from"]["id"]
    chat = msg["chat"]["id"]
    s = st_get(con, f"ac:{uid}")
    venue = (msg.get("text") or "").strip()
    if not (2 <= len(venue) <= 60) or LINK_RE.search(venue):
        B.send(chat, "Напиши просто название заведения, например: <b>Кофейня «Зерно»</b>")
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
                 f"👤 {html.escape(d.get('name', ''))} — {html.escape(prof_name(d.get('prof')))}, "
                 f"{html.escape(place_name(d))}\n📞 {contact_str(d)}\n\n"
                 f"Напиши кандидату и представься: «{html.escape(venue)}, по анкете в чате Делай Дело». "
                 f"Мы уже предупредили, что ты напишешь.")
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
    text = ("📝 <b>Ищешь работу в барах, кафе и кальянных?</b>\n\n"
            "Заполни анкету в нашем боте за 2 минуты — она появится здесь, и заведения напишут тебе сами.\n\n"
            "🔒 Контакты в чате не видны: заведение получит их только через бота, а ты сразу узнаешь, кто интересуется.")
    markup = B.kb([[("📝 Заполнить анкету", f"https://t.me/{B.BOT_USERNAME}?start=anketa")]])
    if test_mode():
        B.send(chat_id, "🧪 Тест: этот пост бот опубликует в чате после запуска (ANKETA_MODE=on):")
        B.send(chat_id, text, markup)
    else:
        m = B.send(CHAT, text, markup)
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
        lines.append(f"№{r['id']} · {html.escape(prof_name(d.get('prof')))} · {html.escape(place_name(d))} · "
                     f"{html.escape(d.get('name', ''))} {contact_str(d)}")
    B.send(chat_id, "\n".join(lines))
