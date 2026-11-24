"""Офлайн-конверсии в Яндекс Метрику — чтобы Директ учился на запуске бота и подписке, а не на клике.

Сайт /zarplata/ (utm_source=yandex) ведёт в бота с меткой ya_<ClientID Метрики>.
Бот отправляет в Метрику две цели (тип «JavaScript-событие»):
  tg_start     — человек запустил бота;
  tg_subscribe — человек подписался на канал (бот проверяет сам 3 дня после запуска).
В статистике бота такие люди считаются как ya_direct.
Нужно в /etc/ddbot.env: YM_COUNTER=113889494, YM_TOKEN=<OAuth-токен с доступом к Метрике>.
Без токена конверсии копятся в очереди и уйдут, когда токен появится.
"""
import http.client
import logging
import os
import re
import time
import uuid

log = logging.getLogger("ddbot.ym")

COUNTER = os.environ.get("YM_COUNTER", "").strip()
TOKEN = os.environ.get("YM_TOKEN", "").strip()
SRC_RE = re.compile(r"^ya_(\d{6,30})$")
T_START, T_SUB = "tg_start", "tg_subscribe"
CHECK_DAYS = 3          # сколько дней после запуска проверять подписку
CHECK_EVERY = 3600      # раз в час
UPLOAD_EVERY = 600      # раз в 10 минут
_next = {"check": 0, "upload": 0}
B = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS ym_users(
  user_id INTEGER PRIMARY KEY, cid TEXT, started INTEGER, subscribed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ym_queue(
  cid TEXT, target TEXT, ts INTEGER, sent INTEGER DEFAULT 0, PRIMARY KEY(cid, target));
"""


def init(bot):
    global B
    B = bot


def enabled():
    return bool(COUNTER and TOKEN)


def parse(payload):
    """'ya_<ClientID>' → ('ya_direct', ClientID); остальное — (payload, None)."""
    m = SRC_RE.match(payload or "")
    return ("ya_direct", m.group(1)) if m else (payload, None)


def _queue(con, cid, target):
    con.execute("INSERT OR IGNORE INTO ym_queue(cid, target, ts) VALUES(?,?,?)", (cid, target, int(time.time())))
    con.commit()


def on_start(con, uid, cid):
    """Запуск бота по ссылке с ClientID. Кто уже был в канале — подпиской не считаем."""
    already = 1 if B.is_subscribed(uid) else 0
    con.execute("INSERT INTO ym_users(user_id, cid, started, subscribed) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET cid=excluded.cid, started=excluded.started, "
                "subscribed=MAX(subscribed, excluded.subscribed)", (uid, cid, int(time.time()), already))
    con.commit()
    _queue(con, cid, T_START)


def on_subscribed(con, uid):
    row = con.execute("SELECT cid FROM ym_users WHERE user_id=? AND subscribed=0", (uid,)).fetchone()
    if not row:
        return
    con.execute("UPDATE ym_users SET subscribed=1 WHERE user_id=?", (uid,))
    con.commit()
    _queue(con, row["cid"], T_SUB)


def _check(con):
    """Подписался на канал, но не вернулся в бота — всё равно засчитываем."""
    since = int(time.time()) - CHECK_DAYS * 86400
    rows = con.execute("SELECT user_id FROM ym_users WHERE subscribed=0 AND started>=? LIMIT 50", (since,)).fetchall()
    for r in rows:
        uid = r["user_id"]
        if B.is_subscribed(uid):
            u = B.get_user(con, uid)
            if u and not u["subscribed"]:
                B.set_user(con, uid, subscribed=1, sub_at=int(time.time()))
                B.event(con, uid, "subscribed", "ym_check")
            on_subscribed(con, uid)


def _upload(rows):
    csv = "ClientId,Target,DateTime\n" + "".join(f"{r['cid']},{r['target']},{r['ts']}\n" for r in rows)
    bd = uuid.uuid4().hex
    body = (f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="conv.csv"\r\n'
            f"Content-Type: text/csv\r\n\r\n{csv}\r\n--{bd}--\r\n").encode()
    c = http.client.HTTPSConnection("api-metrika.yandex.net", timeout=30)
    try:
        c.request("POST", f"/management/v1/counter/{COUNTER}/offline_conversions/upload?client_id_type=CLIENT_ID",
                  body=body, headers={"Authorization": f"OAuth {TOKEN}",
                                      "Content-Type": f"multipart/form-data; boundary={bd}"})
        r = c.getresponse()
        raw = r.read()
    finally:
        c.close()
    if r.status != 200:
        raise RuntimeError(f"HTTP {r.status}: {raw[:300].decode(errors='replace')}")


def tick(con):
    now = time.time()
    if now >= _next["check"]:
        _next["check"] = now + CHECK_EVERY
        _check(con)
    if enabled() and now >= _next["upload"]:
        _next["upload"] = now + UPLOAD_EVERY
        rows = con.execute("SELECT cid, target, ts FROM ym_queue WHERE sent=0 ORDER BY ts LIMIT 500").fetchall()
        if rows:
            try:
                _upload(rows)
            except Exception as e:
                log.warning("Метрика: не отправил %s конверсий: %s", len(rows), e)
                _next["upload"] = now + 1800
                return
            con.executemany("UPDATE ym_queue SET sent=1 WHERE cid=? AND target=?",
                            [(r["cid"], r["target"]) for r in rows])
            con.commit()
            log.info("Метрика: отправлено %s конверсий", len(rows))


def stats_line(con):
    n = lambda where, *a: con.execute("SELECT COUNT(*) FROM ym_queue WHERE " + where, a).fetchone()[0]
    starts, subs, wait = n("target=?", T_START), n("target=?", T_SUB), n("sent=0")
    return (f"Метрика (Директ): запуски {starts}, подписки {subs}, ждут отправки {wait}"
            + ("" if enabled() else " (нет YM_TOKEN)"))
