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
PHONE_RE = re.compile(r"(?:(?:\+7|8)[\s\-(]*)?\(?9\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
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


# Дополнительные хэштеги канала по должностям (после хэштега профессии)
EXTRA_TAGS = [
    (r"помо[щш]ни\w*\s+(?:кал[ьл]ян\w*|км\b)|#пкм\b|\bпкм\b", "#пкм"),
    (r"универсал", "#универсал"),
]


def hashtags(text, tags=()):
    """Наши хэштеги: профессии (по хэштегам исходника и словам) + #км, #пкм, #универсал по должностям."""
    profs = PB.find_profs(text, list(tags), B.PROF_TAGS) or []
    out = [B.PROF_TAGS[p] for p in profs if p in B.PROF_TAGS]
    # #км — кальянный мастер (не помощник): убираем «помощник кальянного мастера» и ищем мастера/кальянщика
    rest = re.sub(r"помо[щш]ни\w*\s+(?:кал[ьл]ян\w*(?:\s+мастер\w*)?|км\b)", " ", text or "", flags=re.I)
    if "#км" not in out and re.search(r"кал[ьл]ян\w*\s+мастер|кальянщик|(?<!\w)#?км\b", rest, re.I):
        out.append("#км")
    for rx, tag in EXTRA_TAGS:
        if tag not in out and re.search(rx, text or "", re.I):
            out.append(tag)
    # администратор: #администратор вместо #хостес (подборка понимает оба), если хостес не упомянута отдельно
    if "#хостес" in out and re.search(r"администратор|\bадмин\b", text or "", re.I) \
            and not re.search(r"хост[еэ]с", text or "", re.I):
        out[out.index("#хостес")] = "#администратор"
    # #кальянщик в канале не ставим: кальянные вакансии — #км / #пкм (подборка понимает их как кальянщика)
    if "#кальянщик" in out:
        out.remove("#кальянщик")
        if "#км" not in out and "#пкм" not in out:
            out.insert(0, "#км")
    return out


# ---------- переписывание ----------

GPT_SYSTEM = (
    "Ты редактор Telegram-канала вакансий HoReCa (Москва и МО). Разбери вакансию и перескажи СВОИМИ словами, "
    "коротко. Факты не меняй и не выдумывай, ничего не теряй из важного: ставка, проценты, чаевые, такси, "
    "средний доход, график и часы, когда выплаты, требования, обязанности, условия, скидки, обучение.\n"
    "Ответь ТОЛЬКО JSON:\n"
    '{"venue": название заведения как в тексте или null,\n'
    ' "place": "м. Станция, улица, дом" или город — коротко, или null,\n'
    ' "positions": [ — каждая должность ОТДЕЛЬНЫМ объектом, даже если их в посте несколько\n'
    '   {"title": "Кальянный мастер" — должность с большой буквы, без «требуется»,\n'
    '    "pay": "2 000 ₽ за смену + 10% + чай" — коротко, только деньги, или null,\n'
    '    "schedule": "2/2, 3/3 · 13:00–02:00, пт–сб до 04:00" или null,\n'
    '    "details": ["обязанности и условия этой должности: до 6 пунктов, каждый — одна конкретная мысль до 70 '
    'знаков; средний доход — только если в тексте есть цифра; без ярлыков и общих фраз; график НЕ повторяй"],\n'
    '    "requirements": ["ВСЕ требования к кандидату из текста, коротко: «18+», «опыт от года», '
    '«пунктуальность» — до 6 штук, до 40 знаков каждое; если опыт НЕ нужен или обучают — ПЕРВЫМ пунктом '
    '«опыт не обязателен» / «научим с нуля»"]}],\n'
    ' "perks": ["до 4 общих плюсов, только те, что есть в тексте: такси, выплаты раз в 2 недели, скидки, обучение '
    '— до 60 знаков"],\n'
    ' "contact_note": "как откликаться БЕЗ номеров и ников: что прислать (о себе, опыт, фото), мессенджер, имя, '
    'пометка — если в тексте это есть" или null}\n'
    "Никогда не пиши «не указано», «нет данных» и т. п. — чего нет в тексте, того просто нет. "

    "Пиши числа с пробелом в тысячах (2 000), «₽» вместо «рублей». Без эмодзи, без markdown, без контактов, "
    "ссылок, хэштегов и названий других каналов."
)

PROF_EMOJI = {"kalyan": "💨", "bar": "🍸", "barista": "☕", "waiter": "🍽", "cook": "👨‍🍳", "hostess": "🛎",
              "staff": "🧹", "courier": "🛵"}


def _gpt(messages, max_tokens="1200", temperature=0.3):
    if not (PB.YC_KEY and PB.YC_FOLDER):
        return None
    body = {"modelUri": f"gpt://{PB.YC_FOLDER}/{MODEL}",
            "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": max_tokens},
            "messages": messages}
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
        return json.loads(raw)["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        log.warning("YandexGPT (пост) failed: %s", e)
        return None


def gpt_rewrite(text):
    """Вакансия → поля (dict) или None."""
    out = _gpt([{"role": "system", "text": GPT_SYSTEM},
                {"role": "user", "text": PHONE_RE.sub("[контакт]", PB.strip_contacts(text))[:3500]}])
    g = PB.extract_json(out) if out else None
    if not g or not isinstance(g.get("positions"), list) or not g["positions"]:
        return None
    return g


def _s(v, limit=120):
    """Строка из поля нейросети: без контактов, хэштегов, markdown; пустое — ''."""
    if not isinstance(v, str):
        return ""
    v = PHONE_RE.sub("", PB.CONTACT_RE.sub("", v)).replace("[контакт]", "")
    v = B.HASHTAG_RE.sub("", re.sub(r"\*\*|__|`", "", v))
    v = re.sub(r"\s+", " ", v).strip(" .;,—-")
    return v[:limit].rstrip() if v.lower() not in ("null", "none", "нет") else ""


EMPTY_RE = re.compile(r"не\s+(?:указан|уточн|сказан|оговор)|нет\s+данных|неизвестн", re.I)


def _words(x):
    return {w for w in re.findall(r"[а-яёa-z0-9]{3,}", (x or "").lower())}


def _dup(a, b):
    """Пункт почти повторяет другой (график и «График плавающий…»)."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def _cap(x):
    return x[:1].upper() + x[1:] if x else x


LABEL_RE = re.compile(r"^(?:(?:главн\w*|основн\w*)\s+)?(?:обязанност\w*|требовани\w*|что делать|кого ждём|кого ждем|условия|задачи|"
                      r"мы предлагаем|что даём|что даем|важно)\s*[:—-]\s*", re.I)


# общие фразы без фактов («доход зависит от количества смен»)
VAGUE_RE = re.compile(r"^доход\w*\s+(?:в месяц\s+)?зависит|^зарплата\s+зависит|^оплата\s+зависит", re.I)


def _cut(x, limit):
    """Обрезка по целому слову с «…»."""
    if len(x) <= limit:
        return x
    x = x[:limit].rsplit(" ", 1)[0].rstrip(" ,;—-")
    return x + "…"


def _list(v, n, limit):
    """Пункты: без контактов и «не указано»; «обязанности: а, б, в» → отдельные пункты; с заглавной; по слову."""
    out = []
    for i in (v if isinstance(v, list) else []):
        if not isinstance(i, str):
            continue
        y = _s(re.sub(r"(?<![\w.])@\w+", "", i), 400)
        x = LABEL_RE.sub("", y)
        if not x or _call_only(x) or EMPTY_RE.search(x) or VAGUE_RE.search(x):
            continue
        parts = [x]
        if len(x) > limit or x != y:   # склеенный список или «обязанности: а, б» — режем по запятым (не в числах)
            parts = [p.strip() for p in re.split(r"[;,](?!\d)\s+", x) if p.strip()]
        out += [_cut(_cap(p), limit) for p in parts]
    return out[:n]


def render(g, tags, conts, cut=0):
    """Поля → пост в HTML. cut — сколько пунктов подробностей срезать с конца (чтобы влезть под обложку)."""
    e = html.escape
    pos = [p for p in g.get("positions", []) if isinstance(p, dict) and _s(p.get("title"))][:4]
    lines = [" ".join(tags), ""] if tags else []
    venue, place = _cut(_s(g.get("venue"), 400), 50), _cut(_s(g.get("place"), 400), 110)
    for p in pos:
        title = _s(p.get("title"), 60)
        prof = PB.find_prof(title, [], B.PROF_TAGS)
        emo = PROF_EMOJI.get(prof, "💼")
        pay = _cut(_s(p.get("pay"), 400), 90)
        pay = "" if EMPTY_RE.search(pay) else pay
        lines.append(f"<b>{emo} {e(title)}{' — ' + e(pay) if pay else ''}</b>")
    where = " · ".join(x for x in (f"«{venue}»" if venue and not venue.startswith("«") else venue, place) if x)
    if where:
        lines.append(f"📍 {e(where)}")
    # подробности — в свёрнутом блоке
    blocks = []
    for p in pos:
        sch = _cap(_cut(_s(p.get("schedule"), 400), 150))
        if EMPTY_RE.search(sch):
            sch = ""
        det = [x for x in _list(p.get("details"), 6 if len(pos) == 1 else 4, 90) if not _dup(x, sch)]
        req = [x if re.match(r"[A-ZА-ЯЁ]{2}", x) else x[:1].lower() + x[1:] for x in _list(p.get("requirements"), 6, 70)]
        head = (f"<b>{e(_s(p.get('title'), 60))}</b>" if len(pos) > 1 else "") + \
               (f"{' · ' if len(pos) > 1 else ''}🗓 {e(sch)}" if sch else "")
        blocks.append([head] if head else [])
        blocks[-1] += [f"• {e(x)}" for x in det]
        req = [x for x in req if not any(_dup(x, y) for y in det)]   # «работа с кальянами» и там и там
        if req:   # требования одной строкой; при нехватке места срезаются пункты «•», а не эта строка
            blocks[-1].append("👤 " + e(_cap(", ".join(req))))
    perks = _list(g.get("perks"), 4, 70)
    flat = [(bi, li) for bi, b in enumerate(blocks) for li in range(len(b)) if b[li].startswith("• ")]
    for bi, li in reversed(flat[len(flat) - cut:] if cut else []):
        blocks[bi][li] = None
    perks = perks[:max(0, len(perks) - max(0, cut - len(flat)))]
    body = "\n\n".join("\n".join(x for x in b if x) for b in blocks if any(b))
    if perks:
        pk = "; ".join(x if re.match(r"[A-ZА-ЯЁ]{2}", x) else x[:1].lower() + x[1:] for x in perks)
        body += ("\n\n" if body else "") + "✅ " + e(pk[:1].upper() + pk[1:])
    if body:
        lines += ["", f"<blockquote expandable>{body}</blockquote>"]
    note = _s(g.get("contact_note"), 140)
    note = "" if EMPTY_RE.search(note) else note
    if conts and note and not re.match(r"[A-ZА-ЯЁ]{2}", note):   # «В телеграм» → «в телеграм», «HR» не трогаем
        note = note[:1].lower() + note[1:]
    if conts or note:
        lines += ["", "📩 <b>Отклик:</b> " + e(", ".join(conts)) + (f" ({e(note)})" if conts and note else e(note))]
    lines += ["", e(FOOTER)]
    return "\n".join(lines)


def plain_len(h):
    """Длина так, как её считает Telegram (без тегов)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", h or "")))


def render_fit(g, tags, conts, limit=1000):
    for cut in range(0, 13):
        h = render(g, tags, conts, cut)
        if plain_len(h) <= limit:
            return h
    return h


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
    """Запасной пост без нейросети (HTML): хэштеги, текст, отклик, подвал."""
    e = html.escape
    parts = []
    if tags:
        parts += [" ".join(tags), ""]
    parts.append(e(body.strip()))
    if conts:
        parts.append("\n📩 <b>Отклик:</b> " + e(", ".join(conts)))
    parts += ["", e(FOOTER)]
    return "\n".join(parts)


def entities_html(text, entities):
    """Текст с форматированием Telegram (entities, смещения в UTF-16) → HTML для parse_mode=HTML."""
    tags = {"bold": ("<b>", "</b>"), "italic": ("<i>", "</i>"), "underline": ("<u>", "</u>"),
            "strikethrough": ("<s>", "</s>"), "code": ("<code>", "</code>"), "spoiler": ("<tg-spoiler>", "</tg-spoiler>"),
            "blockquote": ("<blockquote>", "</blockquote>"),
            "expandable_blockquote": ("<blockquote expandable>", "</blockquote>")}
    u = text.encode("utf-16-le")
    n = len(u) // 2
    opens, closes = {}, {}
    for en in entities or []:
        t = en.get("type")
        if t == "text_link":
            o, c = f'<a href="{html.escape(en.get("url", ""), quote=True)}">', "</a>"
        elif t in tags:
            o, c = tags[t]
        else:
            continue
        a, b = en["offset"], en["offset"] + en["length"]
        opens.setdefault(a, []).append((b, o))
        closes.setdefault(b, []).append((a, c))
    out, pos = [], 0
    for k in sorted(set(opens) | set(closes) | {n}):
        out.append(html.escape(u[pos * 2:k * 2].decode("utf-16-le")))
        out += [c for a, c in sorted(closes.get(k, []), key=lambda x: -x[0])]
        out += [o for b, o in sorted(opens.get(k, []), key=lambda x: -x[0])]
        pos = k
    return "".join(out)




# ---------- доступ ----------

def allowed(uid):
    return uid in B.ADMIN_IDS or uid in POST_IDS


POST_IDS = {int(x) for x in os.environ.get("POST_IDS", "703473452").replace(" ", "").split(",") if x}


# ---------- обложки ----------
# Обложки — только наши: папка covers/ и список BUILTIN ниже. Новая: файл в covers/ + строка в BUILTIN.

SCHEMA = """
CREATE TABLE IF NOT EXISTS post_covers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, prof TEXT, file_id TEXT, added INTEGER);
"""


COVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "covers")
# Встроенные обложки (папка covers/): файл, название, профессия, слово в начале вакансии (важнее профессии)
BUILTIN = [
    ("01_kalyanshik.jpg", "Кальянщик", "kalyan", None),
    ("02_barista.jpg", "Бариста", "barista", None),
    ("03_barmen.jpg", "Бармен", "bar", None),
    ("04_oficiant.jpg", "Официант", "waiter", None),
    ("05_povar.jpg", "Повар", "cook", None),
    ("06_kurier.jpg", "Курьер", "courier", None),
    ("07_hostes.jpg", "Хостес", "hostess", None),
    ("08_administrator.jpg", "Администратор", None, r"администратор|\bадмин\b"),
    ("09_menedzher.jpg", "Менеджер", None, r"\bменеджер(?!у)|управляющ"),
    ("10_vakansiya.jpg", "Вакансия (общая)", None, None),
]
DEFAULT_COVER = "10_vakansiya.jpg"
_seeded = set()


def seed(con):
    """Колонки для встроенных обложек и сами обложки из covers/ (file_id появится после первой загрузки)."""
    if id(con) in _seeded:
        return
    cols = {r[1] for r in con.execute("PRAGMA table_info(post_covers)")}
    for c, typ in (("path", "TEXT"), ("kw", "TEXT"), ("hidden", "INTEGER DEFAULT 0")):
        if c not in cols:
            con.execute(f"ALTER TABLE post_covers ADD COLUMN {c} {typ}")
    for f, name, prof, kw in BUILTIN:
        if not os.path.exists(os.path.join(COVER_DIR, f)):
            continue
        if not con.execute("SELECT 1 FROM post_covers WHERE path=?", (f,)).fetchone():
            con.execute("INSERT INTO post_covers(name, prof, path, kw, added) VALUES(?,?,?,?,?)",
                        (name, prof, f, kw, int(time.time())))
    con.commit()
    _seeded.add(id(con))


def pick_cover(con, text, prof):
    """Автовыбор: слово в начале вакансии (администратор, менеджер) → профессия → общая «Вакансия»."""
    seed(con)
    rows = [r for r in con.execute("SELECT * FROM post_covers WHERE hidden=0 AND path IS NOT NULL ORDER BY id")]
    head = "\n".join((text or "").splitlines()[:3]).lower()
    for r in rows:
        if r["kw"] and re.search(r["kw"], head, re.I):
            return r["id"]
    if prof:
        own = [r for r in rows if r["prof"] == prof]
        if own:
            return own[0]["id"]
    gen = [r for r in rows if r["path"] == DEFAULT_COVER]
    return gen[0]["id"] if gen else None


def _prof_of(name):
    p = PB.find_profs(name or "", [], B.PROF_TAGS) or []
    return p[0] if p else None


def covers(con, prof=None):
    seed(con)
    rows = con.execute("SELECT * FROM post_covers WHERE hidden=0 AND path IS NOT NULL ORDER BY id").fetchall()
    return sorted(rows, key=lambda r: (r["prof"] != prof, r["id"])) if prof else list(rows)


def _cover_label(con, d):
    c = d.get("cover")
    if c == "src":
        return "фото из исходника"
    if c:
        r = con.execute("SELECT name FROM post_covers WHERE id=?", (c,)).fetchone()
        return r["name"] if r else "нет"
    return "нет"


def _cover_file(con, d):
    """Что ставить фото: file_id фото из исходника, «cover:<id>» из списка или None."""
    c = d.get("cover")
    if c == "src":
        return d.get("photo")
    if c and con.execute("SELECT 1 FROM post_covers WHERE id=?", (c,)).fetchone():
        return f"cover:{c}"
    return None


def _upload(chat, path, caption, markup):
    """sendPhoto файлом с диска (multipart), с подписью и кнопками."""
    import uuid
    with open(path, "rb") as f:
        data = f.read()
    bd = "----ddpost" + uuid.uuid4().hex
    fields = {"chat_id": str(chat), "caption": caption, "parse_mode": "HTML"}
    if markup:
        fields["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    body = b"".join(f"--{bd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                    for k, v in fields.items())
    body += (f"--{bd}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"cover.jpg\"\r\n"
             f"Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{bd}--\r\n".encode()
    c = B.FastHTTPS("api.telegram.org", timeout=60)
    try:
        c.request("POST", f"/bot{B.TOKEN}/sendPhoto", body=body,
                  headers={"Content-Type": f"multipart/form-data; boundary={bd}"})
        res = json.loads(c.getresponse().read().decode())
    except Exception as e:
        raise B.TgError(f"sendPhoto(upload): {e.__class__.__name__}: {e}")
    finally:
        c.close()
    if not res.get("ok"):
        raise B.TgError(f"sendPhoto(upload): {res.get('description')}")
    return res["result"]


def send_photo(con, chat, ref, caption, markup=None):
    """Фото по ссылке _cover_file(): встроенная обложка грузится с диска один раз, дальше — по file_id."""
    if not str(ref).startswith("cover:"):
        return B.tg("sendPhoto", chat_id=chat, photo=ref, caption=caption, parse_mode="HTML", reply_markup=markup)
    r = con.execute("SELECT * FROM post_covers WHERE id=?", (int(ref[6:]),)).fetchone()
    if not r:
        raise B.TgError("обложка удалена")
    if r["file_id"]:
        try:
            return B.tg("sendPhoto", chat_id=chat, photo=r["file_id"], caption=caption, parse_mode="HTML",
                        reply_markup=markup)
        except B.TgError:
            if not r["path"]:
                raise
    m = _upload(chat, os.path.join(COVER_DIR, r["path"]), caption, markup)
    con.execute("UPDATE post_covers SET file_id=? WHERE id=?", (m["photo"][-1]["file_id"], r["id"]))
    con.commit()
    return m


def cover_kb(con, d):
    rows = []
    sug = d.get("suggest")
    for r in sorted(covers(con), key=lambda r: r["id"] != sug)[:24]:
        mark = "✅ " if d.get("cover") == r["id"] else ("⭐ " if r["id"] == sug else "")
        rows.append((mark + r["name"], f"po:cset:{r['id']}"))
    out = [rows[i:i + 2] for i in range(0, len(rows), 2)]
    out.append([(("✅ " if not d.get("cover") else "") + "🚫 Без обложки", "po:cset:none")])
    out.append([("↩️ К превью", "po:back")])
    return B.kb(out)


# ---------- превью и кнопки ----------

def _kb(con, d):
    return B.kb([[("✅ Опубликовать", "po:pub"), ("🕒 Запланировать", "po:sched")],
                 [("🔁 Переписать ещё", "po:redo"), ("✏️ Исправить", "po:edit")],
                 [(("🖼 Обложки" if not d.get("cover") else f"🖼 Обложка: {_cover_label(con, d)}")[:60], "po:cover")],
                 [("❌ Отмена", "po:cancel")]])


def _limit(con, d):
    return CAPTION_MAX if _cover_file(con, d) else TEXT_MAX


def _nums(t):
    """Числа от 100: «4 500», «4.500», «4500» → 4500; «110-120 тыс» → 110, 120."""
    t = re.sub(r"(?<=\d)[\s\u00a0.,](?=\d{3}\b)", "", html.unescape(re.sub(r"<[^>]+>", "", t or "")))
    return {int(x) for x in re.findall(r"\d+", t) if 100 <= int(x) < 10_000_000}


def checks(d):
    """Предупреждения к превью: выдуманные цифры, нет контакта."""
    out = []
    head = d.get("text", "").split("<blockquote", 1)[0]
    src = _nums(d.get("src", "")) | {n * 1000 for n in _nums(d.get("src", "")) if n < 1000}
    bad = sorted(n for n in _nums(head) - src if not re.search(rf"#\w*{n}", head))
    if bad and d.get("by") == "gpt":
        out.append("⚠️ В заголовке есть числа, которых нет в исходнике: " + ", ".join(f"{n:,}".replace(",", " ")
                                                                                 for n in bad) + " — проверь ставку.")
    if not d.get("contacts"):
        out.append("⚠️ Контакт для отклика не нашёл — добавь через «✏️ Исправить».")
    if d.get("by") == "gpt" and d.get("no_place"):
        out.append("⚠️ Нет адреса или метро — если в исходнике есть, добавь через «✏️ Исправить».")
    first = re.sub(r"<[^>]+>", "", head).split("\n")
    titles = [l for l in first if l.startswith(tuple(PROF_EMOJI.values()) + ("💼",))]
    if d.get("by") == "gpt" and titles and not any(re.search(r"\d", l) for l in titles):
        out.append("⚠️ В заголовке нет суммы ставки — проверь исходник.")
    return out


def preview(con, uid, chat):
    """Пост так, как он уйдёт в канал (с обложкой — фото с подписью), и кнопки под ним."""
    d = _get(con, uid)
    text = d.get("text", "")
    notes = checks(d)
    if d.get("by") == "raw":
        notes.append("⚠️ Нейросеть не ответила — текст только очищен. «🔁 Переписать ещё» или «✏️ Исправить».")
    if plain_len(text) > _limit(con, d):
        notes.append(f"⚠️ {plain_len(text)} знаков, лимит {_limit(con, d)}"
                     + (" для поста с обложкой" if _cover_file(con, d) else "") + " — сократи («✏️ Исправить»).")
    if notes:   # отдельной строкой только предупреждения; сам пост — следующим сообщением
        B.send(chat, "\n".join(notes))
    fid = _cover_file(con, d)
    if fid and plain_len(text) <= CAPTION_MAX:
        try:
            send_photo(con, chat, fid, text, _kb(con, d))
            return
        except B.TgError as e:
            log.warning("posting preview photo: %s", e)
    B.send(chat, text, _kb(con, d))


def start(con, msg):
    """Новое сообщение (переслано или длинный текст) — делаем черновик. True, если взяли в работу."""
    if not enabled():
        return False
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    raw = msg.get("text") or msg.get("caption") or ""
    forwarded = bool(msg.get("forward_origin") or msg.get("forward_from_chat") or msg.get("forward_from"))
    if len(raw.strip()) < MIN_LEN and not forwarded:
        return False
    if not raw.strip() and msg.get("media_group_id"):
        return True   # остальные фото альбома: подпись приходит только с одним из них
    if not raw.strip():
        B.send(chat, "В пересланном посте нет текста (длинные подписи с Premium Telegram боту не отдаёт). "
                     "Скопируй текст и пришли сообщением.")
        return True
    src_user, src_title = source_of(msg)
    skip = [src_user, B.channel_username() or "", B.BOT_USERNAME, "delaijobchat"]
    tags = hashtags(raw, B.post_tags(raw))
    prof = (PB.find_profs(raw, tags + B.post_tags(raw), B.PROF_TAGS) or [None])[0]
    d = {"src": raw, "src_user": src_user, "src_title": src_title,
         "photo": (msg.get("photo") or [{}])[-1].get("file_id"), "prof": prof, "tags": tags,
         "contacts": contacts(clean(raw, skip), skip),
         # обложку выбирают кнопкой; подходящая (администратор/менеджер по тексту → профессия → общая) — ⭐ первой
         "cover": None, "suggest": pick_cover(con, raw, prof)}
    B.send(chat, "✍️ Переписываю…")
    _rewrite(con, uid, d)
    preview(con, uid, chat)
    return True


NOEXP_RE = re.compile(r"опыт\w*\s+(?:работы\s+)?не\s+(?:обязател|нуж|требу|важ)|без\s+опыта|#безопыта", re.I)
TEACH_RE = re.compile(r"научим|обучим|всему науч|обучение\s+с\s+нуля|с\s+нуля", re.I)
HAS_NOEXP_RE = re.compile(r"опыт\w*\s+не|без\s+опыта|науч|обуч|с\s+нуля", re.I)


def ensure_noexp(g, src):
    """Если в исходнике «опыт не нужен / научим», а нейросеть это потеряла — ставим первым требованием."""
    note = "научим с нуля" if TEACH_RE.search(src) else ("опыт не обязателен" if NOEXP_RE.search(src) else None)
    pos = [p for p in g.get("positions", []) if isinstance(p, dict)]
    if not note or not pos:
        return g
    for p in pos:
        req = p.get("requirements") if isinstance(p.get("requirements"), list) else []
        have = any(isinstance(x, str) and HAS_NOEXP_RE.search(x) for x in req + (p.get("details") or []))
        if not have and len(pos) == 1:   # несколько должностей — не знаем, к какой относится
            p["requirements"] = [note] + [x for x in req if isinstance(x, str)]
    return g


def _rewrite(con, uid, d):
    skip = [d.get("src_user"), B.channel_username() or ""]
    base = clean(d["src"], skip)
    g = gpt_rewrite(base)
    if g:
        g = ensure_noexp(g, base)
        d["no_place"] = not _s(g.get("place"))
    d["by"] = "gpt" if g else "raw"
    if g:   # с запасом под обложку (1024): лишние подробности срезаются с конца
        titles = "\n".join(_s(p.get("title")) for p in g.get("positions", []) if isinstance(p, dict))
        # хэштеги — по названиям должностей (в тексте уборщицы есть «чистка кальянов» — это не #км);
        # #пкм/#универсал из исходника добавляем, только если такая должность есть
        own = hashtags(titles)
        d["tags"] = own or d["tags"]
        d["text"] = render_fit(g, d["tags"], d["contacts"], CAPTION_MAX - 24)
    else:   # без нейросети — очищенный исходник без контактов (они уйдут строкой «Отклик»)
        d["text"] = build(tidy(PB.CONTACT_RE.sub("", base)), d["tags"], d["contacts"])
    d.pop("wait", None)
    _set(con, uid, d)


def auto_format(plain):
    """Правка пришла без форматирования (Android копирует голый текст) — восстанавливаем наш формат:
    жирные «эмодзи Должность — ставка», подробности (🗓 … ✅) в свёрнутом блоке, жирное «Отклик:»."""
    e = html.escape
    lines = plain.strip("\n").splitlines()
    heads = tuple(PROF_EMOJI.values()) + ("💼",)
    q_start = next((i for i, l in enumerate(lines) if l.startswith(("🗓", "• ", "👤"))), None)
    q_end = next((i for i, l in enumerate(lines) if l.startswith(("📩", "📝"))), len(lines))
    out = []
    for i, l in enumerate(lines):
        x = e(l)
        if l.startswith(heads) and " — " in l and (q_start is None or i < q_start):
            x = f"<b>{x}</b>"
        elif l.startswith("📩 Отклик:"):
            x = "📩 <b>Отклик:</b>" + e(l[len("📩 Отклик:"):])
        out.append(x)
    if q_start is not None and q_start < q_end:
        body = out[q_start:q_end]
        while body and not body[-1].strip():   # пустые строки перед «Отклик» — за блоком
            body.pop()
        n = q_start + len(body)
        out = out[:q_start] + [f"<blockquote expandable>{chr(10).join(body)}</blockquote>"] + out[n:]
    return "\n".join(out)


def on_edit_text(con, msg):
    """Прислали исправленный текст поста целиком."""
    uid = msg["from"]["id"]
    d = _get(con, uid)
    ents = [x for x in (msg.get("entities") or []) if x.get("type") not in ("url", "mention", "hashtag",
                                                                           "phone_number", "email")]
    # с форматированием — сохраняем как есть; без (Android копирует голый текст) — восстанавливаем наш формат
    d["text"] = (entities_html(msg["text"], msg.get("entities")) if ents else auto_format(msg.get("text") or "")).strip()
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
    if act == "qlist":
        _markup(chat, mid, None)
        queue_list(con, chat)
        return
    if act in ("qone", "qtime", "qnow", "qdel"):
        qid = int(parts[2])
        if act == "qone":
            queue_one(con, chat, qid)
        elif act == "qtime":
            ask_time(con, uid, chat, mid, f"q{qid}")
        elif act == "qdel":
            cur = con.execute("UPDATE post_queue SET status='deleted' WHERE id=? AND status='wait'", (qid,))
            con.commit()
            _markup(chat, mid, None)
            B.send(chat, "🗑 Убрал из очереди." if cur.rowcount else "Этот пост уже вышел или удалён.")
        elif act == "qnow":
            r = con.execute("SELECT * FROM post_queue WHERE id=? AND status='wait'", (qid,)).fetchone()
            _markup(chat, mid, None)
            if not r:
                B.send(chat, "Этот пост уже вышел или удалён.")
            else:
                _publish_q(con, r)
        return
    if act == "day":
        _markup(chat, mid, time_kb(con, parts[2], int(parts[3])))
        return
    if act == "at":
        _markup(chat, mid, None)
        set_time(con, uid, chat, parts[2], int(parts[3]))
        return
    d = _get(con, uid)
    if not d:
        B.send(chat, "Черновик не найден — перешли пост ещё раз.")
        return
    if act == "sched":
        if _check_len(con, chat, d):
            ask_time(con, uid, chat, mid, "d")
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
        if d.get("wait") == "time":
            d.pop("wait")
            _set(con, uid, d)
        _markup(chat, mid, _kb(con, d))
    elif act == "cset":
        v = parts[2] if len(parts) > 2 else "none"
        d["cover"] = None if v == "none" or not v.isdigit() else int(v)
        _set(con, uid, d)
        _markup(chat, mid, None)
        preview(con, uid, chat)
    elif act == "pub":
        publish(con, uid, chat, mid, d)


def _to_channel(con, text, fid):
    if fid:
        return send_photo(con, B.CHANNEL, fid, text)
    return B.tg("sendMessage", chat_id=B.CHANNEL, text=text, parse_mode="HTML",
                disable_web_page_preview=True)


def _after(con, uid, m, note):
    """Опубликовали: сохраняем, разбираем, рассылаем подписчикам, сообщаем обоим."""
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
    link = f"<a href=\"{B.post_link(n)}\">пост №{n}</a>"
    for aid in {uid} | B.ADMIN_IDS | POST_IDS:
        try:
            B.send(aid, (f"✅ {note}: {link}" if aid == uid else f"📣 {note} через бота: {link}"))
        except B.TgError:
            pass
    return n


def _check_len(con, chat, d):
    if plain_len(d.get("text", "")) > _limit(con, d):
        B.send(chat, f"⚠️ {plain_len(d['text'])} знаков, лимит {_limit(con, d)}. Сократи через «✏️ Исправить» "
                     "или убери обложку.")
        return False
    return True


def publish(con, uid, chat, mid, d):
    if not _check_len(con, chat, d):
        return
    try:
        m = _to_channel(con, d.get("text", ""), _cover_file(con, d))
    except B.TgError as e:
        B.send(chat, f"❌ Не опубликовал: {html.escape(str(e))}")
        return
    _set(con, uid, {})
    _markup(chat, mid, None)
    _after(con, uid, m, "Опубликовано")


# ---------- отложенная публикация ----------
# Telegram не даёт боту ставить посты в «отложенные» канала, поэтому очередь у бота: в нужную минуту он публикует сам.

MSK = 3 * 3600
SLOT_FROM, SLOT_TO = 9, 23        # кнопки времени: с 9:00 до 22:00 по часам
SLOT_GAP = 20 * 60                # другой пост ближе 20 минут — слот помечаем занятым

QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS post_queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, at INTEGER, text TEXT, file_id TEXT,
  status TEXT DEFAULT 'wait', message_id INTEGER, created INTEGER, err TEXT);
"""
SCHEMA += QUEUE_SCHEMA


def msk_ts(y, mo, d, h, mi):
    """Время по Москве → unix."""
    import calendar
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0)) - MSK


def fmt_at(ts):
    t, now = time.gmtime(ts + MSK), time.gmtime(time.time() + MSK)
    day = ("сегодня" if t.tm_yday == now.tm_yday and t.tm_year == now.tm_year else
           "завтра" if (ts + MSK) // 86400 - (time.time() + MSK) // 86400 == 1 else time.strftime("%d.%m", t))
    return f"{day} в {time.strftime('%H:%M', t)}"


def busy(con, ts, skip_id=None):
    return con.execute("SELECT 1 FROM post_queue WHERE status='wait' AND ABS(at-?)<? AND id IS NOT ?",
                       (ts, SLOT_GAP, skip_id)).fetchone() is not None


def time_kb(con, target, day=0):
    """target: 'd' — черновик, 'q<id>' — перенос поста из очереди. day: 0 сегодня, 1 завтра."""
    now = time.time()
    base = time.gmtime(now + MSK + day * 86400)
    btns = []
    for h in range(SLOT_FROM, SLOT_TO):
        ts = msk_ts(base.tm_year, base.tm_mon, base.tm_mday, h, 0)
        if ts < now + 120:
            continue
        btns.append((("🔸" if busy(con, ts, _qid(target)) else "") + f"{h}:00", f"po:at:{target}:{ts}"))
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([("📅 Завтра ▸", f"po:day:{target}:1") if day == 0 else ("◂ Сегодня", f"po:day:{target}:0")])
    rows.append([("↩️ Назад", "po:back" if target == "d" else "po:qlist")])
    return B.kb(rows)


def _qid(target):
    return int(target[1:]) if target.startswith("q") else None


TIME_RE = re.compile(r"^\s*(?:(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s+)?(\d{1,2})[:.](\d{2})\s*$")


def parse_time(text):
    """«15:30» (сегодня, если прошло — завтра), «28.11 15:30», «28.11.2026 9:05». → unix или None."""
    m = TIME_RE.match(text or "")
    if not m:
        return None
    dd, mm, yy, hh, mi = m.groups()
    hh, mi = int(hh), int(mi)
    if hh > 23 or mi > 59:
        return None
    now = time.gmtime(time.time() + MSK)
    try:
        if dd:
            y = int(yy) + (2000 if yy and len(yy) == 2 else 0) if yy else now.tm_year
            ts = msk_ts(y, int(mm), int(dd), hh, mi)
            if not yy and ts < time.time() - 86400:
                ts = msk_ts(y + 1, int(mm), int(dd), hh, mi)   # «05.01» в декабре — следующий год
        else:
            ts = msk_ts(now.tm_year, now.tm_mon, now.tm_mday, hh, mi)
            if ts <= time.time():
                ts += 86400
    except (ValueError, OverflowError):
        return None
    return ts


def ask_time(con, uid, chat, mid, target):
    if target == "d":
        d = _get(con, uid)
        d["wait"] = "time"
        _set(con, uid, d)
    else:
        _set_wait_q(con, uid, _qid(target))
    _markup(chat, mid, time_kb(con, target))
    B.send(chat, "🕒 Выбери время кнопкой (🔸 — рядом уже есть пост) или напиши: <code>15:30</code> "
                 "или <code>28.11 15:30</code> (по Москве).")


def _set_wait_q(con, uid, qid):
    con.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"postq:{uid}", str(qid)))
    con.commit()


def _pop_wait_q(con, uid):
    r = con.execute("SELECT value FROM state WHERE key=?", (f"postq:{uid}",)).fetchone()
    con.execute("DELETE FROM state WHERE key=?", (f"postq:{uid}",))
    con.commit()
    return int(r["value"]) if r else None


def set_time(con, uid, chat, target, ts):
    if ts < time.time() + 60:
        B.send(chat, "Это время уже прошло — выбери другое.")
        return
    if ts > time.time() + 30 * 86400:
        B.send(chat, "Дальше 30 дней не планирую — выбери время поближе.")
        return
    warn = "\n⚠️ Рядом (±20 мин) уже стоит пост — проверь /ochered." if busy(con, ts, _qid(target)) else ""
    if target == "d":
        d = _get(con, uid)
        if not d:
            B.send(chat, "Черновик не найден — перешли пост ещё раз.")
            return
        if not _check_len(con, chat, d):
            return
        con.execute("INSERT INTO post_queue(uid, at, text, file_id, created) VALUES(?,?,?,?,?)",
                    (uid, ts, d["text"], _cover_file(con, d), int(time.time())))
        _set(con, uid, {})
        B.event(con, uid, "post_sched", str(ts))
        B.send(chat, f"🕒 Запланировано на <b>{fmt_at(ts)}</b>.{warn}\nВся очередь: /ochered")
    else:
        qid = _qid(target)
        cur = con.execute("UPDATE post_queue SET at=? WHERE id=? AND status='wait'", (ts, qid))
        con.commit()
        B.send(chat, (f"🕒 Перенёс на <b>{fmt_at(ts)}</b>.{warn}" if cur.rowcount
                      else "Этот пост уже вышел или удалён."))
        _pop_wait_q(con, uid)


def queue_list(con, chat):
    rows = con.execute("SELECT * FROM post_queue WHERE status='wait' ORDER BY at").fetchall()
    if not rows:
        B.send(chat, "🕒 Очередь пуста.")
        return
    lines = [f"🕒 <b>Запланировано: {len(rows)}</b>"]
    btns = []
    for r in rows[:30]:
        plain = html.unescape(re.sub(r"<[^>]+>", "", r["text"]))
        first = next((l for l in plain.splitlines() if l.strip() and not l.startswith("#")), "")[:45]
        lines.append(f"• <b>{fmt_at(r['at'])}</b>{' 🖼' if r['file_id'] else ''} — {html.escape(first)}")
        btns.append([(f"{time.strftime('%d.%m %H:%M', time.gmtime(r['at'] + MSK))} ⚙️", f"po:qone:{r['id']}")])
    B.send(chat, "\n".join(lines), B.kb(btns))


def queue_one(con, chat, qid):
    r = con.execute("SELECT * FROM post_queue WHERE id=?", (qid,)).fetchone()
    if not r or r["status"] != "wait":
        B.send(chat, "Этот пост уже вышел или удалён.")
        return
    kbd = B.kb([[("🕒 Другое время", f"po:qtime:{qid}"), ("▶️ Сейчас", f"po:qnow:{qid}")],
                [("🗑 Убрать из очереди", f"po:qdel:{qid}")], [("↩️ К очереди", "po:qlist")]])
    head = f"🕒 <b>{fmt_at(r['at'])}</b>\n\n"
    if r["file_id"] and plain_len(head) + plain_len(r["text"]) <= CAPTION_MAX:
        try:
            send_photo(con, chat, r["file_id"], head + r["text"], kbd)
            return
        except B.TgError:
            pass
    B.send(chat, head + r["text"], kbd)


def _publish_q(con, r):
    try:
        m = _to_channel(con, r["text"], r["file_id"])
    except B.TgError as e:
        con.execute("UPDATE post_queue SET status='error', err=? WHERE id=?", (str(e)[:300], r["id"]))
        con.commit()
        for aid in {r["uid"]} | B.ADMIN_IDS:
            try:
                B.send(aid, f"❌ Запланированный пост ({fmt_at(r['at'])}) не вышел: {html.escape(str(e))}\n"
                            f"Текст:\n{html.escape(html.unescape(re.sub(r'<[^>]+>', '', r['text']))[:1500])}")
            except B.TgError:
                pass
        return None
    con.execute("UPDATE post_queue SET status='done', message_id=? WHERE id=?", (m["message_id"], r["id"]))
    con.commit()
    return _after(con, r["uid"], m, "Вышел запланированный")


def tick(con):
    """Каждый проход цикла: публикуем всё, чему пришло время (по одному, чтобы не залпом)."""
    if not enabled():
        return
    rows = con.execute("SELECT * FROM post_queue WHERE status='wait' AND at<=? ORDER BY at LIMIT 3",
                       (int(time.time()),)).fetchall()
    for r in rows:
        if time.time() - r["at"] > 6 * 3600:   # бот лежал больше 6 часов — не публикуем вслепую
            con.execute("UPDATE post_queue SET status='late' WHERE id=?", (r["id"],))
            con.commit()
            for aid in {r["uid"]} | B.ADMIN_IDS:
                try:
                    B.send(aid, f"⚠️ Пост на {fmt_at(r['at'])} не вышел вовремя (бот был недоступен). "
                                f"Текст:\n{html.escape(html.unescape(re.sub(r'<[^>]+>', '', r['text']))[:1500])}")
                except B.TgError:
                    pass
            continue
        _publish_q(con, r)


def _has_wait_q(con, uid):
    return con.execute("SELECT 1 FROM state WHERE key=?", (f"postq:{uid}",)).fetchone() is not None


def is_own(con, message_id):
    """Пост опубликован ботом и уже обработан — повторно подписчикам не рассылаем."""
    return con.execute("SELECT 1 FROM state WHERE key=?", (f"own_post:{message_id}",)).fetchone() is not None


HELP = ("📝 <b>Посты через бота</b>\n\n"
        "Перешли сюда вакансию (или вставь текст) — бот перепишет её своими словами в формате канала: "
        "хэштеги, текст, контакты для отклика, подвал. Дальше кнопки: опубликовать, переписать ещё, "
        "исправить вручную, выбрать обложку. В канал уходит только по кнопке «✅ Опубликовать» "
        "или в назначенное время через «🕒 Запланировать».\n\n"
        "🕒 <code>/ochered</code> — запланированные посты: посмотреть, перенести, выпустить сейчас, убрать.\n\n"
        "🖼 «Обложки» — выбрать одну из наших (подходящая отмечена ⭐) или без обложки.")


def on_message(con, msg):
    """Сообщения в личке от админа или помощника. True — обработали."""
    uid, chat = msg["from"]["id"], msg["chat"]["id"]
    if not (enabled() and allowed(uid)):
        return False
    text = (msg.get("text") or msg.get("caption") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd == "/post":
        B.send(chat, HELP)
    elif cmd == "/ochered":
        queue_list(con, chat)
    elif cmd:
        return False
    elif waiting_edit(con, uid) and msg.get("text"):
        on_edit_text(con, msg)
    elif msg.get("text") and parse_time(msg["text"]) and (_get(con, uid).get("wait") == "time" or
                                                          _has_wait_q(con, uid)):
        q = _pop_wait_q(con, uid)
        set_time(con, uid, chat, f"q{q}" if q else "d", parse_time(msg["text"]))
    else:
        return start(con, msg)
    return True
