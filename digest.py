"""Дайджест «ТОП-10 вакансий недели».

Каждый понедельник в 15:00 МСК бот берёт разобранные вакансии канала за 7 дней, выбирает до 10 лучших
(с указанной ставкой и местом, открытые, без повторов и одинаковых строк, профессии вперемешку, внутри профессии — где платят
больше) и отправляет пост с обложкой digest_cover.png. Название профессии — ссылка на пост вакансии.

Режим в /etc/ddbot.env:
  DIGEST_MODE=test (или пусто) — дайджест приходит только ADMIN_IDS в личку;
  DIGEST_MODE=on               — публикуется в канал CHANNEL и закрепляется без уведомления, прошлый дайджест
                                 открепляется (боту нужны права «Публикация» и «Закрепление сообщений»);
  DIGEST_MODE=off              — выключено.
Команды админа: /digest — прислать дайджест себе прямо сейчас (в канал не уходит никогда);
/digest_pin N — записать вручную опубликованный дайджест (пост №N), чтобы бот открепил его при следующем.
"""
import html
import json
import os
import time
import uuid

B = None   # модуль bot.py, задаётся в init()

MODE = os.environ.get("DIGEST_MODE", "test").strip().lower() or "test"
COVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digest_cover.png")
DAYS = 7
TOP = 10
WEEKDAY, HOUR_FROM, HOUR_TO = 0, 15, 21     # понедельник, с 15:00; если бот лежал — догонит до 21:00
CAPTION_MAX = 1000                          # у подписи к фото лимит Telegram — 1024 знака
UNIT = {"shift": "за смену", "hour": "в час", "month": "в месяц"}


def init(bot_module):
    global B
    B = bot_module


def enabled():
    return MODE in ("test", "on")


# ---------- отбор ----------

def salary_text(r):
    lo, hi, u = r["sal_min"], r["sal_max"], r["unit"]
    m = B.PB.money
    if lo and hi and lo != hi:
        s = f"{m(lo)}–{m(hi)} ₽"
    elif lo:
        s = f"{m(lo)} ₽" if lo == hi else f"от {m(lo)} ₽"
    else:
        s = f"до {m(hi)} ₽"
    s += " " + UNIT.get(u, "")
    if r["bonus"]:
        s += " + % или чаевые"
    return s.strip()


def pick(con, days=DAYS, top=TOP):
    """До top вакансий за days дней: со ставкой и местом, открытые, без повторов, профессии вперемешку."""
    since = int(time.time()) - days * 86400
    rows = con.execute(
        "SELECT v.*, p.text FROM vac v JOIN posts p ON p.message_id=v.message_id "
        "WHERE p.closed=0 AND v.date>=? AND COALESCE(v.kind, 'vacancy')='vacancy' "
        "AND v.shift_eq IS NOT NULL ORDER BY v.date DESC", (since,)).fetchall()
    groups, seen = {}, set()
    for r in rows:
        profs = [p for p in B.PB.row_profs(r) if p in B.T.PROFESSIONS and p != "courier"]
        if not profs or not B.PB.row_cities(r):
            continue
        key = B.PB.dup_key(r)
        if key in seen:
            continue        # повтор той же вакансии — оставляем самый свежий пост
        seen.add(key)
        same = (profs[0], B.PB.place_str(r), r["sal_min"], r["sal_max"], r["unit"])
        if same in seen:
            continue        # другое заведение, но в списке строка выглядела бы повтором — берём свежую
        seen.add(same)
        groups.setdefault(profs[0], []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: -(r["shift_eq"] or 0))
    order = sorted(groups, key=lambda p: -(groups[p][0]["shift_eq"] or 0))
    out = []
    while len(out) < top and any(groups[p] for p in order):
        for p in order:     # по одной из каждой профессии по кругу
            if groups[p] and len(out) < top:
                out.append(groups[p].pop(0))
    return out


def line(r):
    profs = [p for p in B.PB.row_profs(r) if p in B.T.PROFESSIONS][:2]
    names = [B.T.PROFESSIONS[p]["name"] for p in profs]
    title = names[0] if len(names) == 1 else f"{names[0]} / {names[1].lower()}"
    emoji = B.T.PROFESSIONS[profs[0]]["emoji"]
    s = (f"{emoji} <a href=\"{B.post_link(r['message_id'])}\">{html.escape(title)}</a> — "
         f"{html.escape(B.PB.place_str(r))} — {html.escape(salary_text(r))}")
    if r["noexp"] == 1:
        s += ", можно без опыта"
    return s


def build(con):
    rows = pick(con)
    if not rows:
        return None, 0
    head = "ТОП-10 вакансий недели" if len(rows) == TOP else "Вакансии недели"
    lines = [f"🔥 <b>{head} в HoReCa Москвы и МО</b>", ""]
    lines.append("\n\n".join(line(r) for r in rows))   # пустая строка между вакансиями
    lines += ["", f"👉 Отклик и новые вакансии каждый день — {html.escape(B.CHANNEL)}"]
    return "\n".join(lines), len(rows)


# ---------- отправка ----------

def plain_len(text):
    import re
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def upload_photo(chat_id, path, caption=None):
    """sendPhoto с файлом (multipart). Возвращает сообщение."""
    with open(path, "rb") as f:
        data = f.read()
    boundary = "----ddbot" + uuid.uuid4().hex
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields.update(caption=caption, parse_mode="HTML")
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"cover.png\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    c = B.FastHTTPS("api.telegram.org", timeout=60)
    try:
        c.request("POST", f"/bot{B.TOKEN}/sendPhoto", body=body,
                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        res = json.loads(c.getresponse().read().decode())
    except Exception as e:
        raise B.TgError(f"sendPhoto(upload): {e.__class__.__name__}: {e}")
    finally:
        c.close()
    if not res.get("ok"):
        raise B.TgError(f"sendPhoto(upload): {res.get('description')}")
    return res["result"]


def cover_id(con):
    """file_id обложки, если этот же файл уже загружали."""
    try:
        st = os.stat(COVER)
    except OSError:
        return None, None
    stamp = f"{st.st_size}:{int(st.st_mtime)}"
    r = con.execute("SELECT value FROM state WHERE key='digest_cover'").fetchone()
    if r:
        try:
            d = json.loads(r["value"])
            if d.get("stamp") == stamp:
                return d.get("file_id"), stamp
        except ValueError:
            pass
    return None, stamp


def send(con, chat_id, text):
    """Пост дайджеста: обложка с подписью; если текст длиннее лимита подписи — обложка и текст отдельно."""
    fid, stamp = cover_id(con)
    if stamp is None:                       # обложки нет — просто текст
        return B.send(chat_id, text)
    caption = text if plain_len(text) <= CAPTION_MAX else None
    m = None
    if fid:
        try:
            m = B.tg("sendPhoto", chat_id=chat_id, photo=fid, caption=caption,
                     parse_mode="HTML" if caption else None)
        except B.TgError:
            fid = None
    if not fid:
        m = upload_photo(chat_id, COVER, caption)
        new = m["photo"][-1]["file_id"]
        con.execute("INSERT INTO state(key, value) VALUES('digest_cover', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps({"stamp": stamp, "file_id": new}),))
        con.commit()
    if not caption:
        m = B.send(chat_id, text)
    return m            # сообщение с текстом дайджеста — его и закрепляем


def state_get(con, key):
    r = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def state_set(con, key, value):
    con.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
    con.commit()


def repin(con, message_id):
    """Закрепить новый дайджест без уведомления и открепить прошлый. Возвращает текст ошибки или None."""
    old = state_get(con, "digest_pin")
    try:
        B.tg("pinChatMessage", chat_id=B.CHANNEL, message_id=message_id, disable_notification=True)
    except B.TgError as e:
        return str(e)
    state_set(con, "digest_pin", message_id)
    if old and old.isdigit() and int(old) != message_id:
        try:
            B.tg("unpinChatMessage", chat_id=B.CHANNEL, message_id=int(old))
        except B.TgError as e:
            B.log.warning("digest unpin %s: %s", old, e)   # пост могли удалить или открепить руками
    return None


def set_pin(con, chat_id, arg):
    """/digest_pin N — запомнить вручную опубликованный дайджест, чтобы бот открепил его при выходе следующего."""
    if not arg.isdigit():
        cur = state_get(con, "digest_pin")
        B.send(chat_id, f"Сейчас как закреплённый дайджест записан пост: {cur or 'нет'}.\n"
                        "Записать другой: <code>/digest_pin 205</code> (номер поста в канале).")
        return
    state_set(con, "digest_pin", int(arg))
    B.send(chat_id, f"Запомнил: пост №{arg} — текущий дайджест. Откреплю его, когда выйдет следующий.")


def to_admins(con, text, n, note):
    for a in B.ADMIN_IDS:
        try:
            B.send(a, note)
            send(con, a, text)
        except B.TgError as e:
            B.log.warning("digest to %s: %s", a, e)


def run(con, manual_chat=None):
    """manual_chat — команда /digest: прислать только этому админу. Иначе — по расписанию."""
    text, n = build(con)
    if manual_chat is not None:
        if not text:
            B.send(manual_chat, f"За {DAYS} дней нет вакансий со ставкой и местом — дайджест собрать не из чего.")
            return
        B.send(manual_chat, f"🧪 <b>Дайджест сейчас</b>: {n} из {TOP}. Так он выйдет в понедельник в 15:00. "
                            "Ссылки сохранятся, если переслать пост или скопировать его внутри Telegram.")
        send(con, manual_chat, text)
        return
    if not text:
        for a in B.ADMIN_IDS:
            try:
                B.send(a, f"📰 Дайджест не вышел: за {DAYS} дней нет вакансий со ставкой и местом.")
            except B.TgError:
                pass
        return
    if MODE == "on":
        try:
            m = send(con, B.CHANNEL, text)
            B.event(con, 0, "digest_pub", str(n))
            err = repin(con, m["message_id"])
            if err:
                for a in B.ADMIN_IDS:
                    try:
                        B.send(a, f"📌 Дайджест опубликован, но закрепить не смог: {html.escape(err)}. "
                                  "Дай боту в канале право «Закрепление сообщений» или закрепи вручную.")
                    except B.TgError:
                        pass
            return
        except B.TgError as e:
            B.log.warning("digest publish: %s", e)
            to_admins(con, text, n, f"⚠️ Не смог опубликовать дайджест в канал ({html.escape(str(e))}). "
                                    "Вот он — опубликуй вручную:")
            return
    to_admins(con, text, n, f"🧪 <b>Тест дайджеста</b> ({n} из {TOP}). В канал не опубликован — режим test. "
                            "Перешли или скопируй пост в канал сам; включить автопубликацию — DIGEST_MODE=on.")
    B.event(con, 0, "digest_test", str(n))


def week_id(now):
    return time.strftime("%G-W%V", now)


def tick(con):
    """Раз в цикл. Первый запуск после установки текущую неделю пропускает — старт со следующего понедельника."""
    if not enabled():
        return
    now = B.msk_now()
    wk = week_id(now)
    r = con.execute("SELECT value FROM state WHERE key='digest_week'").fetchone()
    if r is None:
        con.execute("INSERT INTO state(key, value) VALUES('digest_week', ?)", (wk,))
        con.commit()
        return
    if r["value"] == wk or now.tm_wday != WEEKDAY or not (HOUR_FROM <= now.tm_hour < HOUR_TO):
        return
    con.execute("UPDATE state SET value=? WHERE key='digest_week'", (wk,))
    con.commit()
    run(con)
