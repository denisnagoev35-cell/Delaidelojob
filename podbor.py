# Подборка вакансий под соискателя (тестовый режим — только для ADMIN_IDS).
#
# 1) Каждый новый пост канала разбираем: профессия, город/метро, ставка, график, опыт.
#    Сначала YandexGPT (если в окружении есть YANDEX_API_KEY и YANDEX_FOLDER_ID),
#    что не нашлось — правилами. Контакты в YandexGPT не отправляем.
# 2) Результат пишем в таблицу vac. Если не нашли город или ставку — спрашиваем админа.
# 3) /podbor: опрос → вакансии за 7 дней, подходящие по профессии, месту, ставке и графику.
import hashlib
import http.client
import json
import logging
import os
import re
import time

log = logging.getLogger("ddbot.podbor")

YC_KEY = os.environ.get("YANDEX_API_KEY", "").strip()
YC_FOLDER = os.environ.get("YANDEX_FOLDER_ID", "").strip()
YC_MODEL = os.environ.get("YANDEX_MODEL", "yandexgpt-lite/latest")

# Города МО — те же, что на сайте (/rabota/...).
CITIES = [
    "Апрелевка", "Балашиха", "Бронницы", "Верея", "Видное", "Волоколамск", "Воскресенск",
    "Высоковск", "Голицыно", "Дедовск", "Дзержинский", "Дмитров", "Долгопрудный", "Домодедово",
    "Дрезна", "Дубна", "Егорьевск", "Железнодорожный", "Жуковский", "Зарайск", "Звенигород",
    "Ивантеевка", "Истра", "Кашира", "Клин", "Коломна", "Королёв", "Котельники", "Красноармейск",
    "Красногорск", "Краснозаводск", "Краснознаменск", "Кубинка", "Куровское", "Ликино-Дулёво",
    "Лобня", "Лосино-Петровский", "Луховицы", "Лыткарино", "Люберцы", "Можайск", "Мытищи",
    "Наро-Фоминск", "Ногинск", "Одинцово", "Озёры", "Орехово-Зуево", "Павловский Посад",
    "Пересвет", "Подольск", "Протвино", "Пушкино", "Пущино", "Раменское", "Реутов", "Рошаль",
    "Руза", "Сергиев Посад", "Серпухов", "Солнечногорск", "Старая Купавна", "Ступино", "Фрязино",
    "Химки", "Хотьково", "Чехов", "Черноголовка", "Шатура", "Щёлково", "Электрогорск",
    "Электросталь", "Электроугли", "Яхрома",
]
MOSCOW = "Москва"

# Особые формы, где «корень + окончание» даёт ложные совпадения (Клин → клиника).
_SPECIAL = {
    "Клин": r"\bклин(?:е|у|а|ом)?\b",
    "Руза": r"\bруз(?:а|е|у|ы|ой)\b",
    "Озёры": r"\bозер(?:ы|ах|ам)\b",
    "Чехов": r"\bчехов(?:е|а|у|ом)?\b",
    "Королёв": r"\bкорол[её]в(?:е|а|у|ом)?\b",
    "Истра": r"\bистр(?:а|е|у|ы|ой)\b",
    "Дубна": r"\bдубн(?:а|е|у|ы|ой)\b",
    "Видное": r"\bвидн(?:ое|ом|ого|ому)\b",
}


def _norm(s):
    return (s or "").lower().replace("ё", "е")


def _city_re(name):
    if name in _SPECIAL:
        return re.compile(_SPECIAL[name], re.I)
    words = []
    for w in re.split(r"([ -])", _norm(name)):
        if w in (" ", "-"):
            words.append(r"[\s-]")
            continue
        stem = re.sub(r"(ий|ый|ое|ая|ск|ки|цы|ны|ва|на|ка|ло|во|но|ки|ы|и|а|о|е|й)$", "", w)
        if len(stem) < 4:
            stem = w[:-1] if len(w) > 4 else w
        words.append(re.escape(stem) + r"[а-я]{0,4}")
    return re.compile(r"\b" + "".join(words) + r"\b", re.I)


CITY_RES = [(c, _city_re(c)) for c in CITIES]
CITY_BY_NORM = {_norm(c): c for c in CITIES}


def find_city(text):
    t = _norm(text)
    for name, rx in CITY_RES:
        if rx.search(t):
            return name
    return None


def canon_city(name):
    """Имя города от пользователя или YandexGPT → наше написание (или None)."""
    n = _norm(name).strip(" .,")
    if not n:
        return None
    if n in ("москва", "мск", "moscow") or n.startswith("москва"):
        return MOSCOW
    if n in CITY_BY_NORM:
        return CITY_BY_NORM[n]
    return find_city(n)


METRO_RE = re.compile(r"(?:\bм\.|\bметро|\bмцк|\bмцд|\bст\.\s*м\.)\s*[«\"]?([А-ЯЁ][А-Яа-яё\-]+"
                      r"(?: (?:[А-ЯЁ][а-яё\-]+|пруды|бульвар|проспект|парк|поле|ворота|площадь|шоссе|"
                      r"переулок|улица|сокол|горы|холмы|мост|вал))?)")
MOSCOW_RE = re.compile(r"\bмоскв|\bмск\b|\b(?:цао|сао|свао|вао|ювао|юао|юзао|зао|сзао|зелао|нао|тао)\b", re.I)

# ---------- профессия ----------

PROF_WORDS = [
    ("kalyan", r"кальян|#км\b"),
    ("barista", r"бариста|кофейн"),
    ("bar", r"бармен"),
    ("waiter", r"официант|раннер"),
    ("cook", r"повар|кухн|шеф|пицца[йи]ол|сушист"),
    ("hostess", r"хостес|администратор зала"),
    ("staff", r"посудомо|уборщ|клининг|хозперсонал|мойщ"),
    ("courier", r"курьер|доставк"),
]


def find_prof(text, tags, prof_tags):
    """prof_tags: {"kalyan": "#кальянщик", ...}. Сначала хэштег, потом слова в тексте."""
    tag_map = {v: k for k, v in prof_tags.items()}
    for t in tags:
        if t in tag_map:
            return tag_map[t]
    if "#км" in tags or "#пкм" in tags:
        return "kalyan"
    low = _norm(text)
    for key, rx in PROF_WORDS:
        if key in prof_tags and re.search(rx, low):
            return key
    return None


# ---------- ставка ----------

NUM = r"(\d[\d\s ]*(?:[.,]\d+)?)"
SAL_RE = re.compile(
    r"(?P<from>от\s*|до\s*)?" + NUM + r"\s*(?P<k1>тыс\.?|тысяч[аи]?|т\.?\s?р\.?|к\b|k\b)?"
    r"(?:\s*(?:[-–—]|до)\s*" + NUM + r"\s*(?P<k2>тыс\.?|тысяч[аи]?|т\.?\s?р\.?|к\b|k\b)?)?"
    r"\s*(?P<cur>₽|руб\.?|р\.?\b|рублей)?"
    r"(?P<tail>[^\n\d]{0,25})", re.I)
UPTO_RE = re.compile(r"до\s*" + NUM + r"\s*(тыс\.?|тысяч[аи]?|т\.?\s?р\.?|к\b)?", re.I)


def _num(s):
    s = re.sub(r"[\s ]", "", s or "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _unit_of(tail, value):
    t = _norm(tail)
    if re.search(r"час|/ч\b|в ч\b", t):
        return "hour"
    if re.search(r"смен|выход|день|/с\b", t):
        return "shift"
    if re.search(r"мес|оклад", t):
        return "month"
    # без единицы угадываем по размеру числа
    if value < 1000:
        return "hour"
    if value >= 30000:
        return "month"
    return "shift"


LIMITS = {"hour": (150, 3000), "shift": (1000, 40000), "month": (20000, 500000)}


def parse_salary(text):
    best = None
    for m in SAL_RE.finditer(text):
        a, b = _num(m.group(2)), _num(m.group(4))
        if a is None:
            continue
        k1, k2, cur, tail = m.group("k1"), m.group("k2"), m.group("cur"), m.group("tail") or ""
        thousands = bool(k1 or k2)
        if not (cur or thousands or re.search(r"смен|час|мес|выход|₽|руб", _norm(tail))):
            continue  # просто число (дата, адрес, возраст)
        lo, hi = a, b
        upto = (m.group("from") or "").lower().startswith("до") and b is None
        if thousands:
            if lo < 1000:
                lo *= 1000
            if hi is not None and hi < 1000:
                hi *= 1000
        if hi is not None and hi < lo:
            hi = None
        unit = _unit_of(tail, lo)
        mn, mx = LIMITS[unit]
        if not (mn <= lo <= mx):
            continue
        if upto:
            cand = {"sal_min": None, "sal_max": int(lo), "unit": unit}
        else:
            cand = {"sal_min": int(lo), "sal_max": int(hi) if hi else None, "unit": unit}
        if best is None:
            best = cand
    if best is None:
        m = UPTO_RE.search(text)
        if m:
            v = _num(m.group(1))
            if v and m.group(2) and v < 1000:
                v *= 1000
            if v:
                unit = _unit_of(text[m.end():m.end() + 25], v)
                mn, mx = LIMITS[unit]
                if mn <= v <= mx:
                    best = {"sal_min": None, "sal_max": int(v), "unit": unit}
    return best


def shift_equiv(sal_min, sal_max, unit):
    """Ставка в пересчёте на смену — чтобы сравнивать с желаемой (≈11 ч, ≈15 смен в месяц)."""
    v = sal_max or sal_min
    if not v:
        return None
    return int({"hour": v * 11, "shift": v, "month": v / 15}.get(unit, v))


# ---------- график и опыт ----------

def parse_schedule(text):
    t = _norm(text)
    if re.search(r"\b2\s*/\s*2\b|\b2\s*через\s*2\b", t):
        return "2/2"
    if re.search(r"\b5\s*/\s*2\b", t):
        return "5/2"
    if re.search(r"подработ|разов|на выходные|частичн", t):
        return "parttime"
    if re.search(r"сменн|посменн|сменами|гибкий график|свободный график", t):
        return "shifts"
    return None


def parse_noexp(text):
    t = _norm(text)
    if re.search(r"без опыта|#безопыта|обуча|обучим|научим|с нуля|стажир", t):
        return 1
    if re.search(r"опыт (?:работы )?от|опыт обязател|только с опытом|требуется опыт|опыт работы (?:не менее|от)", t):
        return 0
    return None


SCHEDULES = {"2/2": "2/2", "5/2": "5/2", "shifts": "сменный", "parttime": "подработка"}


def parse_rules(text, tags, prof_keys):
    out = {"prof": find_prof(text, tags, prof_keys)}
    city = find_city(text)
    metro = METRO_RE.search(text)
    if not city and (metro or MOSCOW_RE.search(text)):
        city = MOSCOW
    out["city"] = city
    out["metro"] = metro.group(1).strip() if metro else None
    out.update(parse_salary(text) or {"sal_min": None, "sal_max": None, "unit": None})
    out["schedule"] = parse_schedule(text)
    out["noexp"] = parse_noexp(text)
    return out


# ---------- YandexGPT ----------

CONTACT_RE = re.compile(
    r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}"   # телефоны
    r"|@[A-Za-z0-9_]{4,}"                                              # @ники
    r"|https?://\S+|t\.me/\S+|wa\.me/\S+"                              # ссылки
    r"|[\w.\-]+@[\w\-]+\.[\w.]+")                                      # почта


def strip_contacts(text):
    return CONTACT_RE.sub("[контакт]", text)


GPT_SYSTEM = (
    "Ты разбираешь вакансии из HoReCa (Москва и Московская область). Ответь ТОЛЬКО JSON без пояснений:\n"
    '{"profession": один из [%s] или null,\n'
    ' "city": "Москва" или название города Московской области в именительном падеже, или null,\n'
    ' "metro": станция метро/МЦК/МЦД или null,\n'
    ' "salary_min": число в рублях или null, "salary_max": число в рублях или null,\n'
    ' "salary_unit": "shift" (за смену/выход) | "hour" | "month" | null,\n'
    ' "schedule": "2/2" | "5/2" | "shifts" | "parttime" | null,\n'
    ' "no_experience": true если берут без опыта / обучают, false если опыт обязателен, иначе null}\n'
    "Правила: «6-8 тыс.» = 6000 и 8000; «4500 т.р» = 4500; «от 130 тысяч в месяц» = salary_min 130000, month. "
    "Чаевые и проценты не прибавляй. Ничего не выдумывай: нет данных — null."
)


def gpt_parse(text, prof_keys):
    if not (YC_KEY and YC_FOLDER):
        return None
    body = {
        "modelUri": f"gpt://{YC_FOLDER}/{YC_MODEL}",
        "completionOptions": {"stream": False, "temperature": 0, "maxTokens": "300"},
        "messages": [
            {"role": "system", "text": GPT_SYSTEM % ", ".join(f'"{k}"' for k in prof_keys)},
            {"role": "user", "text": strip_contacts(text)[:3500]},
        ],
    }
    try:
        c = http.client.HTTPSConnection("llm.api.cloud.yandex.net", timeout=25)
        c.request("POST", "/foundationModels/v1/completion", body=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json", "Authorization": f"Api-Key {YC_KEY}",
                           "x-folder-id": YC_FOLDER})
        r = c.getresponse()
        raw = r.read().decode()
        c.close()
        if r.status != 200:
            log.warning("YandexGPT %s: %s", r.status, raw[:300])
            return None
        answer = json.loads(raw)["result"]["alternatives"][0]["message"]["text"]
        return extract_json(answer)
    except Exception as e:  # сеть, JSON — не роняем бота, работаем на правилах
        log.warning("YandexGPT failed: %s", e)
        return None


def extract_json(answer):
    """Первый JSON-объект из ответа: YandexGPT иногда добавляет ```, пояснения или второй объект."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", answer or ""):
        chunk = answer[m.start():]
        comma = lambda x: re.sub(r",\s*([}\]])", r"\1", x)                       # лишняя запятая
        quote = lambda x: re.sub(r"(?<=[{,])\s*([a-z_]+)\s*:", r'"\1":', x)    # ключ без кавычек
        for fix in (lambda x: x, comma, quote, lambda x: comma(quote(x))):
            try:
                obj, _ = dec.raw_decode(fix(chunk))
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    log.warning("YandexGPT: не JSON: %r", (answer or "")[:200])
    return None


def _int(v):
    try:
        v = int(float(str(v).replace(" ", "").replace(",", ".")))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def from_gpt(g, prof_keys):
    """Ответ YandexGPT → наши поля; всё сомнительное отбрасываем."""
    if not isinstance(g, dict):
        return {}
    out = {}
    if g.get("profession") in prof_keys:
        out["prof"] = g["profession"]
    city = canon_city(g.get("city") or "")
    if city:
        out["city"] = city
    if g.get("metro"):
        out["metro"] = str(g["metro"])[:40]
        out.setdefault("city", MOSCOW)
    unit = g.get("salary_unit") if g.get("salary_unit") in LIMITS else None
    lo, hi = _int(g.get("salary_min")), _int(g.get("salary_max"))
    if unit and (lo or hi):
        mn, mx = LIMITS[unit]
        if all(v is None or mn <= v <= mx for v in (lo, hi)):
            out.update(sal_min=lo, sal_max=hi, unit=unit)
    if g.get("schedule") in SCHEDULES:
        out["schedule"] = g["schedule"]
    if isinstance(g.get("no_experience"), bool):
        out["noexp"] = 1 if g["no_experience"] else 0
    return out


# ---------- база ----------

FIELDS = ("prof", "city", "metro", "sal_min", "sal_max", "unit", "schedule", "noexp")


def db_init(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS vac(
      message_id INTEGER PRIMARY KEY, date INTEGER, hash TEXT,
      prof TEXT, city TEXT, metro TEXT, sal_min INTEGER, sal_max INTEGER, unit TEXT,
      shift_eq INTEGER, schedule TEXT, noexp INTEGER, parsed_by TEXT, asked INTEGER DEFAULT 0);
    """)
    con.commit()


def parse_post(text, tags, prof_keys, use_gpt=True):
    """Разбор поста: YandexGPT + правила. Возвращает (поля, кто разобрал)."""
    rules = parse_rules(text, tags, prof_keys)
    raw = gpt_parse(text, prof_keys) if use_gpt else None
    g = from_gpt(raw, prof_keys)
    tag_prof = find_prof("", tags, prof_keys)  # профессия только по хэштегу
    if raw is not None and not tag_prof and not g.get("prof"):
        # нет хэштега профессии и нейросеть не видит вакансию — это не вакансия (промо, анонс)
        rules["prof"] = None
    out = {}
    for f in FIELDS:
        out[f] = g.get(f) if g.get(f) is not None else rules.get(f)
    # профессию по хэштегу считаем надёжнее нейросети
    if rules.get("prof") and any(t.startswith("#") for t in tags):
        out["prof"] = rules["prof"]
    by = "gpt" if g else "rules"
    return out, by


def store(con, message_id, date, text, fields, by):
    eq = shift_equiv(fields.get("sal_min"), fields.get("sal_max"), fields.get("unit"))
    con.execute(
        "INSERT INTO vac(message_id, date, hash, prof, city, metro, sal_min, sal_max, unit, shift_eq, "
        "schedule, noexp, parsed_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(message_id) DO UPDATE SET hash=excluded.hash, prof=excluded.prof, city=excluded.city, "
        "metro=excluded.metro, sal_min=excluded.sal_min, sal_max=excluded.sal_max, unit=excluded.unit, "
        "shift_eq=excluded.shift_eq, schedule=excluded.schedule, noexp=excluded.noexp, "
        "parsed_by=excluded.parsed_by",
        (message_id, date, text_hash(text), fields.get("prof"), fields.get("city"), fields.get("metro"),
         fields.get("sal_min"), fields.get("sal_max"), fields.get("unit"), eq, fields.get("schedule"),
         fields.get("noexp"), by))
    con.commit()


def text_hash(text):
    return hashlib.sha1((text or "").encode()).hexdigest()[:16]


def needs_parse(con, message_id, text):
    r = con.execute("SELECT hash FROM vac WHERE message_id=?", (message_id,)).fetchone()
    return r is None or r["hash"] != text_hash(text)


def get_vac(con, message_id):
    return con.execute("SELECT * FROM vac WHERE message_id=?", (message_id,)).fetchone()


def set_field(con, message_id, **kw):
    for k, v in kw.items():
        con.execute(f"UPDATE vac SET {k}=? WHERE message_id=?", (v, message_id))
    r = get_vac(con, message_id)
    if r:
        con.execute("UPDATE vac SET shift_eq=? WHERE message_id=?",
                    (shift_equiv(r["sal_min"], r["sal_max"], r["unit"]), message_id))
    con.commit()


# ---------- показ ----------

UNIT_TXT = {"shift": "₽/смена", "hour": "₽/час", "month": "₽/мес"}


def money(v):
    return f"{v:,}".replace(",", " ")


def salary_str(r):
    lo, hi, u = r["sal_min"], r["sal_max"], r["unit"]
    if not (lo or hi):
        return "ставка не указана"
    if lo and hi:
        s = f"{money(lo)}–{money(hi)}"
    elif lo:
        s = f"от {money(lo)}"
    else:
        s = f"до {money(hi)}"
    return f"{s} {UNIT_TXT.get(u, '₽')}"


def place_str(r):
    if r["city"] == MOSCOW and r["metro"]:
        return f"м. {r['metro']}"
    return r["city"] or "место не указано"


def describe(r, prof_names):
    parts = [prof_names.get(r["prof"], r["prof"] or "профессия ?"), place_str(r), salary_str(r)]
    if r["schedule"]:
        parts.append(SCHEDULES.get(r["schedule"], r["schedule"]))
    if r["noexp"] == 1:
        parts.append("без опыта")
    elif r["noexp"] == 0:
        parts.append("нужен опыт")
    return " · ".join(parts)


# ---------- подбор ----------

def match(con, q, days=7, limit=8):
    """q: prof, place ('msk' | 'any' | имя города), exp, salary (за смену или 0), schedule ('any' или код).
    Возвращает (точные, ближайшие с пометкой «чем отличается»)."""
    since = int(time.time()) - days * 86400
    rows = con.execute(
        "SELECT v.*, p.text FROM vac v JOIN posts p ON p.message_id=v.message_id "
        "WHERE p.closed=0 AND v.date>=? AND v.prof=? ORDER BY v.date DESC", (since, q["prof"])).fetchall()
    exact, near = [], []
    for r in rows:
        diffs = []
        place = q.get("place", "any")
        if place != "any" and not r["city"]:
            diffs.append("место не указано")
        elif place == "msk" and r["city"] != MOSCOW:
            diffs.append(f"не Москва — {r['city']}")
        elif place not in ("any", "msk") and r["city"] != place:
            diffs.append(f"в другом месте — {place_str(r)}")
        want = q.get("salary") or 0
        if want and r["shift_eq"] is None:
            diffs.append("ставка не указана")
        elif want and r["shift_eq"] < want:
            diffs.append(f"ставка ниже — {salary_str(r)}")
        if q.get("exp") == "none" and r["noexp"] == 0:
            diffs.append("нужен опыт")
        sch = q.get("schedule", "any")
        if sch != "any" and r["schedule"] and r["schedule"] != sch:
            diffs.append(f"график {SCHEDULES.get(r['schedule'], r['schedule'])}")
        (exact if not diffs else near).append((r, diffs))
    # новичкам первыми — те, где явно берут без опыта
    if q.get("exp") == "none":
        exact.sort(key=lambda x: 0 if x[0]["noexp"] == 1 else 1)
    near.sort(key=lambda x: len(x[1]))
    return exact[:limit], near[:max(0, limit - len(exact))][:3]
