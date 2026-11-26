"""Напоминание тем, кто посмотрел зарплату в боте, но не подписался на канал.

Один раз на человека, через сутки после последнего действия, только 12:00–20:00 МСК.
Перед отправкой — живая проверка подписки через Telegram: кто уже в канале, тому не пишем,
а молча отмечаем подписчиком. Выключить: REMIND_MODE=off в /etc/ddbot.env
(test — напоминания уходят только админам, для проверки текста).
"""
import os, time, html, logging

log = logging.getLogger("remind")
MODE = os.environ.get("REMIND_MODE", "on").strip().lower()
AFTER_SEC = 24 * 3600        # сколько ждать после последнего действия
WINDOW_MSK = (12, 20)        # с 12:00 до 20:00 по Москве
PER_TICK = 10                # не больше стольких за один заход
EVERY_SEC = 600              # как часто проверяем очередь

SCHEMA = """
CREATE TABLE IF NOT EXISTS remind(user_id INTEGER PRIMARY KEY, status TEXT, ts INTEGER);
"""
# status: sent — напомнили; optout — «не напоминать»; subscribed — оказался в канале; blocked — бот заблокирован

WHO = {"kalyan": "кальянщиков", "bar": "барменов", "barista": "бариста", "waiter": "официантов",
       "cook": "поваров", "hostess": "хостес и администраторов", "staff": "хозперсонала"}

B = None
_next = 0


def init(bot_module):
    global B
    B = bot_module


def enabled():
    return MODE in ("on", "test")


def _member(uid):
    """True/False — в канале или нет; None — Telegram не ответил (тогда не шлём)."""
    try:
        m = B.tg("getChatMember", chat_id=B.CHANNEL, user_id=uid)
    except B.TgError as e:
        log.warning("getChatMember %s: %s", uid, e)
        return None
    st = m.get("status")
    return st in ("creator", "administrator", "member") or (st == "restricted" and bool(m.get("is_member")))


def _count_week(con, prof):
    p = B.T.PROFESSIONS[prof]
    return len(B.fresh_vacancies(con, p["tag"], limit=1000, max_age_days=7, alt=B.ALT_TAGS.get(prof, ())))


def text(con, u):
    prof = u["profession"]; p = B.T.PROFESSIONS[prof]
    reg = B.T.REGIONS.get(u["region"] or "", "")
    head = f"{p['name']} · {reg}" if reg else p["name"]
    n = _count_week(con, prof)
    if n:
        word = "вакансия" if n % 10 == 1 and n % 100 != 11 else \
               "вакансии" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "вакансий"
        line = f"За неделю в канале вышло <b>{n} {word} для {WHO[prof]}</b> — со ставками и адресами."
    else:
        line = "Новые вакансии выходят в канале каждый день — со ставками и адресами."
    return (f"👋 Привет! Ты смотрел зарплату: <b>{html.escape(head)}</b>.\n\n{line}\n"
            "Полный разбор зарплаты тоже ждёт тебя: подпишись на канал и нажми «Я подписался».")


def markup(prof):
    return B.kb([[("📢 Открыть канал", B.CHANNEL_INVITE)],
                 [("✅ Я подписался", f"f:{prof}")],
                 [("🔕 Не напоминать", "rm:off")]])


def _mark(con, uid, status):
    con.execute("INSERT INTO remind(user_id, status, ts) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET status=excluded.status, ts=excluded.ts",
                (uid, status, int(time.time())))
    con.commit()


def due(con, now=None):
    now = now or int(time.time())
    rows = con.execute(
        "SELECT u.* FROM users u LEFT JOIN remind r ON r.user_id=u.id "
        "WHERE r.user_id IS NULL AND u.subscribed=0 AND u.profession IS NOT NULL AND u.profession!='courier' "
        "AND u.last_seen<=? ORDER BY u.last_seen DESC LIMIT ?", (now - AFTER_SEC, PER_TICK)).fetchall()
    out = []
    for u in rows:
        if u["profession"] not in B.T.PROFESSIONS or u["profession"] not in WHO: continue
        if MODE == "test" and u["id"] not in B.ADMIN_IDS: continue
        out.append(u)
    return out


def tick(con):
    global _next
    if not enabled() or time.time() < _next:
        return
    _next = time.time() + EVERY_SEC
    h = B.msk_now().tm_hour
    if not (WINDOW_MSK[0] <= h < WINDOW_MSK[1]):
        return
    for u in due(con):
        uid = u["id"]
        m = _member(uid)
        if m is None:
            continue                       # Telegram не ответил — попробуем в следующий раз
        if m:
            B.set_user(con, uid, subscribed=1, sub_at=int(time.time()))
            B.event(con, uid, "subscribed", "remind_check")
            _mark(con, uid, "subscribed")
            continue
        try:
            B.send(uid, text(con, u), markup(u["profession"]))
        except B.TgError as e:
            if "blocked" in str(e) or "deactivated" in str(e) or "chat not found" in str(e):
                _mark(con, uid, "blocked")
            else:
                log.warning("remind %s: %s", uid, e)
            continue
        _mark(con, uid, "sent")
        B.event(con, uid, "remind", u["profession"])


def callback(con, uid, chat_id, mid, data):
    if data == "rm:off":
        _mark(con, uid, "optout")
        B.event(con, uid, "remind_off")
        try:
            B.edit(chat_id, mid, "Хорошо, больше не напоминаю 👌\nКанал с вакансиями всегда тут: @delaidelojob")
        except B.TgError:
            pass


def stats_line(con):
    r = {row["status"]: row["c"] for row in con.execute("SELECT status, COUNT(*) c FROM remind GROUP BY status")}
    back = con.execute("SELECT COUNT(*) c FROM users u JOIN remind r ON r.user_id=u.id "
                       "WHERE r.status='sent' AND u.subscribed=1").fetchone()["c"]
    if not r:
        return "Напоминания: пока не отправлялись"
    total = r.get("sent", 0) + r.get("optout", 0)
    return (f"Напоминания: отправлено {total} → подписались {back} · "
            f"уже были в канале {r.get('subscribed', 0)} · отказались {r.get('optout', 0)}")
