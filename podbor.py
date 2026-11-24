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

MOSCOW = "Москва"

# Города МО — те же, что на сайте (/rabota/...).
CITIES = [
    "Апрелевка", "Балашиха", "Бронницы", "Верея", "Видное", "Волоколамск", "Воскресенск",
    "Высоковск", "Голицыно", "Дедовск", "Дзержинский", "Дмитров", "Долгопрудный", "Домодедово",
    "Дрезна", "Дубна", "Егорьевск", "Жуковский", "Зарайск", "Звенигород",
    "Ивантеевка", "Истра", "Кашира", "Клин", "Коломна", "Королёв", "Котельники", "Красноармейск",
    "Красногорск", "Краснозаводск", "Краснознаменск", "Кубинка", "Куровское", "Ликино-Дулёво",
    "Лобня", "Лосино-Петровский", "Луховицы", "Лыткарино", "Люберцы", "Можайск", "Мытищи",
    "Наро-Фоминск", "Ногинск", "Одинцово", "Озёры", "Орехово-Зуево", "Павловский Посад",
    "Пересвет", "Подольск", "Протвино", "Пушкино", "Пущино", "Раменское", "Реутов", "Рошаль",
    "Руза", "Сергиев Посад", "Серпухов", "Солнечногорск", "Старая Купавна", "Ступино", "Фрязино",
    "Химки", "Хотьково", "Чехов", "Черноголовка", "Шатура", "Щёлково", "Электрогорск",
    "Электросталь", "Электроугли", "Яхрома",
]

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


# районы и бывшие города, которые входят в другие
ALIASES = [(re.compile(r"\bжелезнодорожн", re.I), "Балашиха"),
           (re.compile(r"\bсколков|\bновая москва|\bкоммунарк|\bзеленоград", re.I), MOSCOW)]


# «Химкинский бульвар», «ул. Подольских курсантов» — это улицы, а не города
STREET_BEFORE = re.compile(r"(?:ул\.?|улиц\w*|бульвар\w*|б-р|проспект\w*|пр-т|пр\.|шоссе|переул\w*|пер\.|"
                           r"площад\w*|пл\.|проезд\w*|набережн\w*|наб\.|тупик\w*)\s*$", re.I)
STREET_AFTER = re.compile(r"^\s*(?:улиц\w*|ул\.|бульвар\w*|шоссе|проспект\w*|переул\w*|проезд\w*|курсант\w*|"
                          r"набережн\w*|площад\w*|тупик\w*)", re.I)
# «м. Каширская», «метро Подольская» — это станция метро, а не город Кашира или Подольск
METRO_BEFORE = re.compile(r"(?:\bм\.?|\bметро|\bмцк|\bмцд|\bст\.|\bстанци\w*)\s*[«\"]?\s*$", re.I)


def find_cities(text):
    """Все города МО из текста, по порядку; названия улиц пропускаем."""
    t = _norm(text)
    found = []
    for rx, name in ALIASES:
        if rx.search(t) and name not in found:
            found.append(name)
    hits = []
    for name, rx in CITY_RES:
        for m in rx.finditer(t):
            before = t[max(0, m.start() - 15):m.start()]
            if STREET_BEFORE.search(before) or METRO_BEFORE.search(before) or \
                    STREET_AFTER.match(t[m.end():m.end() + 15]):
                continue
            hits.append((m.start(), name))
            break
    for _, name in sorted(hits):
        if name not in found:
            found.append(name)
    return found


def find_city(text):
    c = find_cities(text)
    return c[0] if c else None


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
                      r"переулок|улица|сокол|горы|холмы|мост|вал|стадион|бульвар|набережная|пруд|роща|сад))?)")
MOSCOW_RE = re.compile(r"\bмоскв|\bмск\b|\b(?:цао|сао|свао|вао|ювао|юао|юзао|зао|сзао|зелао|нао|тао)\b", re.I)

# ---------- профессия ----------

PROF_WORDS = [
    ("kalyan", r"кальян|#км\b"),
    ("barista", r"бариста|кофейн"),
    ("bar", r"бармен"),
    ("waiter", r"официант|раннер"),
    ("cook", r"повар|кухн|шеф|пицца[йи]ол|сушист"),
    ("hostess", r"хостес|администратор|\bадмин"),
    ("staff", r"посудомо|уборщ|клининг|хозперсонал|мойщ"),
    ("courier", r"курьер|доставк"),
]


EXTRA_TAGS = {"#км": "kalyan", "#пкм": "kalyan", "#кальянный": "kalyan", "#админ": "hostess",
              "#администратор": "hostess", "#хостесс": "hostess"}


def find_profs(text, tags, prof_tags, use_words=True):
    """Все профессии поста: по хэштегам; если хэштегов нет — по словам в тексте."""
    tag_map = {v: k for k, v in prof_tags.items()}
    tag_map.update({k: v for k, v in EXTRA_TAGS.items() if v in prof_tags})
    out = []
    for t in tags:
        k = tag_map.get(t)
        if k and k not in out:
            out.append(k)
    if out or not use_words:
        return out
    p = find_prof(text, [], prof_tags)
    return [p] if p else []


def find_prof(text, tags, prof_tags):
    """prof_tags: {"kalyan": "#кальянщик", ...}. Сначала хэштег, потом слова в тексте."""
    tag_map = {v: k for k, v in prof_tags.items()}
    for t in tags:
        if t in tag_map:
            return tag_map[t]
    if "#км" in tags or "#пкм" in tags:
        return "kalyan"
    if any(t in tags for t in ("#админ", "#администратор", "#хостесс")):
        return "hostess"
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
    s = re.sub(r"[\s ]", "", s or "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)        # «7.000», «120,000» — точка/запятая как разделитель тысяч
    s = s.replace(",", ".")
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
SHIFT_HI_MAX = 12000   # смена дороже — значит, нейросеть склеила ставку с другой выплатой (см. gpt-разбор)


def parse_salary(text):
    best = None
    for m in SAL_RE.finditer(text):
        a, b = _num(m.group(2)), _num(m.group(4))
        if a is None:
            continue
        k1, k2, cur, tail = m.group("k1"), m.group("k2"), m.group("cur"), m.group("tail") or ""
        thousands = bool(k1 or k2)
        before = _norm(text[max(0, m.start() - 20):m.start()])
        if not (cur or thousands or re.search(r"смен|час|мес|выход|₽|руб", _norm(tail))
                or re.search(r"(?:оплат\w*|ставк\w*|выход|оклад|\bзп\b|зарплат\w*|фикс\w*)\s*[:\-–—=]?\s*$", before)
                and not re.match(r"\s*(?:г\.|год)", tail)):
            continue  # просто число (дата, адрес, возраст); «Оплата: 2000 + 10%» — ставка
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
    if re.search(r"\b3\s*/\s*3\b|\b3\s*через\s*3\b|\b4\s*/\s*4\b", t):
        return "3/3"
    if re.search(r"\b5\s*/\s*2\b", t):
        return "5/2"
    # «трёхразовое питание», «частичная оплата такси» — не про график
    if re.search(r"подработ|\bразов(?:ая|ые|ую|ой)\s+(?:работ|смен|выход|подработ)|на выходные|"
                 r"частичн\w*\s+занят", t):
        return "parttime"
    if re.search(r"гибкий|свободный график|выбираешь смены|сам выбираешь|удобный график", t):
        return "flexible"
    if re.search(r"сменн|посменн|сменами|гибкий график|свободный график", t):
        return "shifts"
    return None


def parse_noexp(text):
    t = _norm(re.sub(r"(?i)опыт\w*\s+(?:работы\s+)?(?:приветствуется|будет плюсом|желател\w*)", "", text))
    if re.search(r"без опыта|#безопыта|обуча|обучим|научим|с нуля|стажир", t):
        return 1
    if re.search(r"опыт (?:работы )?от|опыт обязател|только с опытом|требуется опыт|опыт работы (?:не менее|от)", t):
        return 0
    return None


SCHEDULES = {"2/2": "2/2", "3/3": "3/3", "5/2": "5/2", "shifts": "сменный", "flexible": "гибкий график",
             "parttime": "подработка"}
# что выбирает соискатель → какие графики вакансий ему подходят
WANT_SCHEDULE = {
    "shift": {"2/2", "3/3", "shifts", "flexible"},   # сменный, полная занятость
    "52": {"5/2"},                                   # пятидневка
    "part": {"parttime", "flexible"},                # подработка, пара смен в неделю
}


BONUS_RE = re.compile(r"%|процент|чаев|\bчай\b|\bчай\s*\d|бонус|премии|премия", re.I)
PCT_RE = re.compile(r"%|процент", re.I)
NOT_PAY_RE = re.compile(r"скидк|налог|ндфл|предоплат|аванс|кешб|кэшб|оформлен|официальн|белая", re.I)
TIPS_RE = re.compile(r"чаев|\bчай\b|\bчаи\b", re.I)
PREM_RE = re.compile(r"бонус|\bпреми(?:и|я|ю|ей|ями?)\b", re.I)


def parse_bonus_kind(text):
    """Какие доплаты прямо названы в тексте: pct (% с кассы/бара), tips (чаевые), prem (бонусы, премии).
    «Скидка 20% на меню», «100% официальное оформление» — не доплата."""
    t = text or ""
    kinds = []
    for m in PCT_RE.finditer(t):
        win = t[max(0, m.start() - 35):m.end() + 25]
        if NOT_PAY_RE.search(win) or re.search(r"\b100\s*%", t[max(0, m.start() - 5):m.end()]):
            continue
        kinds.append("pct")
        break
    if TIPS_RE.search(t):
        kinds.append("tips")
    if PREM_RE.search(t):
        kinds.append("prem")
    return ",".join(kinds) or None


BONUS_WORDS = {"pct": "%", "tips": "чаевые", "prem": "бонусы"}


def bonus_str(r):
    """« + % и чаевые» — только то, что есть в тексте вакансии."""
    keys = r.keys()
    kind = r["bonus_kind"] if "bonus_kind" in keys else None
    if not kind:
        return ""
    words = [BONUS_WORDS[k] for k in kind.split(",") if k in BONUS_WORDS]
    if not words:
        return ""
    return " + " + (words[0] if len(words) == 1 else ", ".join(words[:-1]) + " и " + words[-1])


def parse_kind(text, tags, prof_keys):
    """vacancy | digest (подборка нескольких вакансий) | other (промо, анонс)."""
    t = _norm(text)
    if re.search(r"ваканси[йия] недели|топ[- ]?\d+ ваканс|подборк\w* ваканс|дайджест", t):
        return "digest"
    if len(find_profs("", tags, prof_keys, use_words=False)) >= 4:
        return "digest"
    return "vacancy"


def parse_rules(text, tags, prof_keys):
    profs = find_profs(text, tags, prof_keys)
    out = {"profs": profs, "prof": profs[0] if profs else None}
    cities = find_cities(text)
    metro = METRO_RE.search(text)
    if (metro or MOSCOW_RE.search(text)) and MOSCOW not in cities:
        cities.insert(0, MOSCOW) if metro else cities.append(MOSCOW)
    out["cities"] = cities
    out["city"] = cities[0] if cities else None
    out["bonus_kind"] = parse_bonus_kind(text)
    out["bonus"] = 1 if out["bonus_kind"] else 0
    out["kind"] = parse_kind(text, tags, prof_keys)
    out["metro"] = clean_metro(metro.group(1)) if metro else None
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
    '{"kind": "vacancy" (одна вакансия, даже на несколько должностей) | "digest" (подборка разных вакансий) '
    '| "other" (реклама, анонс, не вакансия),\n'
    ' "professions": список из [%s] — все должности, которые ищут в этом посте,\n'
    ' "cities": список: "Москва" и/или города Московской области в именительном падеже — все места работы,\n'
    ' "metro": станция метро/МЦК/МЦД или null,\n'
    ' "salary_min": число в рублях или null, "salary_max": число в рублях или null,\n'
    ' "salary_unit": "shift" (за смену/выход) | "hour" | "month" | null,\n'
    ' "bonus": true если кроме ставки есть процент, чаевые или бонусы, иначе false,\n'
    ' "schedule": "2/2" | "3/3" | "5/2" | "shifts" (другой сменный) | "flexible" (гибкий, смены выбираешь сам) '
    '| "parttime" (подработка, разовые смены) | null,\n'
    ' "no_experience": true если берут без опыта / обучают, false если опыт строго обязателен, '
    'иначе null («опыт приветствуется» — null),\n'
    ' "title": название заведения или сети как в тексте (без «требуется», «ищем», приветствий, эмодзи, '
    'обычными буквами, не капсом), до 40 символов; если названия нет — null}\n'
    "Правила: «6-8 тыс.» = 6000 и 8000; «4500 т.р» = 4500; «7.000 рублей» = 7000 (точка — разделитель тысяч); ставка на испытательном сроке — не основная, бери основную; «от 130 тысяч в месяц» = salary_min 130000, month. "
    "hostess — это и хостес, и администратор зала/заведения. "
    "Ставка — фиксированная часть, чаевые и проценты к ней не прибавляй (для них поле bonus). "
    "salary_min и salary_max — вилка ОДНОЙ и той же выплаты в одной единице; доплаты с другой единицей "
    "(«30 000 в месяц за шефские обязанности», «премия 10 000 в месяц») в вилку не включай. "
    "«частичная оплата такси», «трёхразовое питание», «договорной график» — не подработка. "
    "В полях кроме списков — ОДНО значение (если графиков несколько — первый). "
    "Город определяй по любым подсказкам: адрес, улица, район, метро, МЦК, МЦД, торговый центр, известное место "
    "(улицы, районы и станции Москвы → «Москва»; Сколково, Новая Москва → «Москва»; Железнодорожный → «Балашиха»). "
    "Если в тексте только название заведения без адреса — cities пустой. Ничего не выдумывай: нет данных — null."
)


def gpt_parse(text, prof_keys):
    if not (YC_KEY and YC_FOLDER):
        return None
    body = {
        "modelUri": f"gpt://{YC_FOLDER}/{YC_MODEL}",
        "completionOptions": {"stream": False, "temperature": 0, "maxTokens": "400"},
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
        # «"schedule": "2/2", "3/3", "5/2"» — несколько значений вместо одного: оставляем первое
        multi = lambda x: re.sub(r'(:\s*"[^"]*")(?:\s*,\s*"[^"]*")+(?=\s*(?:,\s*"?[a-z_]+"?\s*:|\}))', r"\1", x)
        for fix in (lambda x: x, comma, quote, multi, lambda x: comma(quote(multi(x)))):
            try:
                obj, _ = dec.raw_decode(fix(chunk))
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    log.warning("YandexGPT: не JSON: %r", (answer or "")[:200])
    return None


GREETING_RE = re.compile(r"^(?:всем\s+)?(?:привет\w*|здравствуй\w*|добр\w+\s+(?:день|утро|вечер)|хай|внимание|"
                         r"срочно|вакансия|ищем|требу\w+|друзья|коллеги)[!.,\s]*$", re.I)


def clean_title(t):
    t = re.sub(r"[^\w\s«»\"'&.,:+()\-–—/№]", "", str(t or "")).strip(" .,:;-–—")
    if not t or t.lower() in ("null", "none") or GREETING_RE.match(t) or len(t) < 2:
        return None
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7 and len(letters) > 4:
        t = t.capitalize()  # КАПС → обычный текст
    if len(t) > 45:
        t = t[:45].rsplit(" ", 1)[0].rstrip(" ,.;:—–-(") + "…"
    return t


def clean_metro(m):
    """«м. Академическая или м. Ленинский проспект» → «Академическая, Ленинский проспект»."""
    m = re.sub(r"[\[\]'\"]", "", m or "")  # нейросеть иногда отдаёт список: ['Трубная', 'Цветной бульвар']
    m = re.sub(r"(?i)\b(?:ст\.\s*)?(?:м\.|метро|мцк|мцд)\s*", "", m)
    parts = [x.strip(" .,;") for x in re.split(r"\s+или\s+|[,/;]", m) if x.strip(" .,;")]
    return ", ".join(parts[:2])[:60] or None


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
    if g.get("kind") in ("vacancy", "digest", "other"):
        out["kind"] = g["kind"]
    profs = g.get("professions")
    if not isinstance(profs, list):
        profs = [g.get("profession")] if g.get("profession") else []
    profs = [p for p in dict.fromkeys(str(x) for x in profs) if p in prof_keys]
    if profs:
        out["profs"], out["prof"] = profs, profs[0]
    cities = g.get("cities")
    if not isinstance(cities, list):
        cities = [g.get("city")] if g.get("city") else []
    cities = [c for c in dict.fromkeys(canon_city(str(x)) for x in cities) if c]
    if g.get("metro"):
        m = g["metro"]
        out["metro"] = clean_metro(", ".join(map(str, m)) if isinstance(m, list) else str(m))
        if MOSCOW not in cities:
            cities.insert(0, MOSCOW)
    if cities:
        out["cities"], out["city"] = cities, cities[0]
    if isinstance(g.get("bonus"), bool):
        out["bonus"] = 1 if g["bonus"] else 0
    unit = g.get("salary_unit") if g.get("salary_unit") in LIMITS else None
    lo, hi = _int(g.get("salary_min")), _int(g.get("salary_max"))
    if unit and (lo or hi):
        mn, mx = LIMITS[unit]
        if unit == "shift" and lo and hi and hi > lo * 3 and hi > SHIFT_HI_MAX:
            hi = None   # «6 000 за смену» + «30 000 в месяц за шефские» — не вилка, а разные выплаты
        if all(v is None or mn <= v <= mx for v in (lo, hi)):
            out.update(sal_min=lo, sal_max=hi, unit=unit)
    if g.get("schedule") in SCHEDULES:
        out["schedule"] = g["schedule"]
    t = clean_title(g.get("title"))
    if t:
        out["title"] = t
    if isinstance(g.get("no_experience"), bool):
        out["noexp"] = 1 if g["no_experience"] else 0
    return out


# ---------- база ----------

FIELDS = ("prof", "profs", "city", "cities", "metro", "sal_min", "sal_max", "unit", "schedule", "noexp",
          "title", "bonus", "kind", "bonus_kind")
NEW_COLS = {"title": "TEXT", "profs": "TEXT", "cities": "TEXT", "bonus": "INTEGER", "kind": "TEXT", "manual": "TEXT",
            "bonus_kind": "TEXT"}


def db_init(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS vac(
      message_id INTEGER PRIMARY KEY, date INTEGER, hash TEXT,
      prof TEXT, city TEXT, metro TEXT, sal_min INTEGER, sal_max INTEGER, unit TEXT,
      shift_eq INTEGER, schedule TEXT, noexp INTEGER, parsed_by TEXT, asked INTEGER DEFAULT 0);
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(vac)")}
    for c, typ in NEW_COLS.items():
        if c not in cols:
            con.execute(f"ALTER TABLE vac ADD COLUMN {c} {typ}")
    con.commit()


def parse_post(text, tags, prof_keys, use_gpt=True):
    """Разбор поста: YandexGPT + правила. Возвращает (поля, кто разобрал)."""
    rules = parse_rules(text, tags, prof_keys)
    raw = gpt_parse(text, prof_keys) if use_gpt else None
    g = from_gpt(raw, prof_keys)
    out = {}
    for f in FIELDS:
        out[f] = g.get(f) if g.get(f) not in (None, []) else rules.get(f)
    tag_profs = find_profs("", tags, prof_keys, use_words=False)
    if tag_profs:
        out["profs"] = tag_profs          # хэштеги надёжнее нейросети
    elif raw is not None and not g.get("profs"):
        out["profs"] = []                 # нет хэштега, нейросеть вакансии не видит
    if rules["kind"] == "digest" and out.get("kind") != "other":
        out["kind"] = "digest"
    if out.get("kind") == "other" and tag_profs and rules.get("sal_min") or \
            out.get("kind") == "other" and tag_profs and rules.get("sal_max"):
        out["kind"] = "vacancy"           # хэштег профессии + ставка в тексте — это вакансия, нейросеть ошиблась
    if out.get("kind") in ("digest", "other"):
        out["profs"] = []                 # подборки и промо в поиск не берём
    out["prof"] = out["profs"][0] if out.get("profs") else None
    out["city"] = out["cities"][0] if out.get("cities") else None
    out["bonus_kind"] = rules["bonus_kind"]   # вид доплаты — только по тексту, нейросети не доверяем
    out["bonus"] = rules["bonus"]
    by = "gpt" if g else "rules"
    return out, by


def _join(v, sep):
    if isinstance(v, (list, tuple)):
        return sep + sep.join(v) + sep if v else None
    return v


def store(con, message_id, date, text, fields, by):
    old = get_vac(con, message_id)
    manual = json.loads(old["manual"]) if old and old["manual"] else {}
    f = dict(fields)
    f.update(manual)  # ручные правки (/mesto, /stavka) переживают правку поста и /reparse
    if "city" in manual:
        f["cities"] = [manual["city"]]
    eq = shift_equiv(f.get("sal_min"), f.get("sal_max"), f.get("unit"))
    vals = {
        "date": date, "hash": text_hash(text), "prof": (f.get("profs") or [None])[0] if f.get("profs") else f.get("prof"),
        "profs": _join(f.get("profs") or [], " "), "city": (f.get("cities") or [None])[0] if f.get("cities") else f.get("city"),
        "cities": _join(f.get("cities") or [], "|"), "metro": f.get("metro"), "sal_min": f.get("sal_min"),
        "sal_max": f.get("sal_max"), "unit": f.get("unit"), "shift_eq": eq, "schedule": f.get("schedule"),
        "noexp": f.get("noexp"), "parsed_by": "manual" if manual else by, "title": f.get("title"),
        "bonus": f.get("bonus"), "kind": f.get("kind"), "bonus_kind": f.get("bonus_kind"), "manual": json.dumps(manual, ensure_ascii=False) if manual else None,
    }
    cols = ", ".join(vals)
    con.execute(f"INSERT INTO vac(message_id, {cols}) VALUES(?{', ?' * len(vals)}) "
                f"ON CONFLICT(message_id) DO UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in vals),
                (message_id, *vals.values()))
    con.commit()


def text_hash(text):
    return hashlib.sha1((text or "").encode()).hexdigest()[:16]


def needs_parse(con, message_id, text):
    r = con.execute("SELECT hash FROM vac WHERE message_id=?", (message_id,)).fetchone()
    return r is None or r["hash"] != text_hash(text)


def get_vac(con, message_id):
    return con.execute("SELECT * FROM vac WHERE message_id=?", (message_id,)).fetchone()


def set_field(con, message_id, manual=False, **kw):
    """Поправить поля вакансии. manual=True — запомнить как ручную правку (не затрётся при переразборе)."""
    if "city" in kw:
        kw["cities"] = _join([kw["city"]], "|") if kw["city"] else None
    for k, v in kw.items():
        con.execute(f"UPDATE vac SET {k}=? WHERE message_id=?", (v, message_id))
    r = get_vac(con, message_id)
    if r:
        con.execute("UPDATE vac SET shift_eq=? WHERE message_id=?",
                    (shift_equiv(r["sal_min"], r["sal_max"], r["unit"]), message_id))
        if manual:
            m = json.loads(r["manual"]) if r["manual"] else {}
            m.update({k: v for k, v in kw.items() if k in ("city", "sal_min", "sal_max", "unit", "metro")})
            con.execute("UPDATE vac SET manual=?, parsed_by='manual' WHERE message_id=?",
                        (json.dumps(m, ensure_ascii=False), message_id))
    con.commit()


def row_profs(r):
    p = (r["profs"] or "").split() if "profs" in r.keys() else []
    return p or ([r["prof"]] if r["prof"] else [])


def row_cities(r):
    c = [x for x in (r["cities"] or "").split("|") if x] if "cities" in r.keys() else []
    return c or ([r["city"]] if r["city"] else [])


# ---------- показ ----------

UNIT_TXT = {"shift": "₽/смена", "hour": "₽/час", "month": "₽/мес"}


def money(v):
    return f"{v:,}".replace(",", " ")


def salary_str(r):
    lo, hi, u = r["sal_min"], r["sal_max"], r["unit"]
    if not (lo or hi):
        return "ставка не указана"
    if lo and hi and lo != hi:
        s = f"{money(lo)}–{money(hi)}"
    elif lo or hi:
        s = money(lo or hi) if lo == hi else (f"от {money(lo)}" if lo else f"до {money(hi)}")
    s = f"{s} {UNIT_TXT.get(u, '₽')}"
    return s + bonus_str(r)


def place_str(r):
    parts = []
    for c in row_cities(r):
        if c == MOSCOW and r["metro"]:
            parts.append(f"м. {clean_metro(r['metro']) or r['metro']}")
        else:
            parts.append(c)
    return ", ".join(parts[:3]) or "место не указано"


def describe(r, prof_names):
    profs = row_profs(r)
    kind = r["kind"] if "kind" in r.keys() else None
    if kind in ("digest", "other"):
        return {"digest": "подборка вакансий — в поиск не идёт", "other": "не вакансия — в поиск не идёт"}[kind]
    parts = [" + ".join(prof_names.get(p, p) for p in profs) or "профессия ?", place_str(r), salary_str(r)]
    if r["schedule"]:
        parts.append(SCHEDULES.get(r["schedule"], r["schedule"]))
    if r["noexp"] == 1:
        parts.append("без опыта")
    elif r["noexp"] == 0:
        parts.append("нужен опыт")
    return " · ".join(parts)


# ---------- подбор ----------

def compare(r, q):
    """Чем вакансия r не совпадает с запросом q (пустой список — подходит точно)."""
    diffs = []
    if q.get("prof") not in row_profs(r):
        return ["другая профессия"]
    place = q.get("place", "any")
    cities = row_cities(r)
    if place != "any" and not cities:
        diffs.append("место не указано")
    elif place == "msk" and MOSCOW not in cities:
        diffs.append("не Москва")
    elif place not in ("any", "msk") and place not in cities:
        diffs.append("другое место")
    want = q.get("salary") or 0
    bonus = "bonus" in r.keys() and r["bonus"]
    if want and r["shift_eq"] is None:
        diffs.append("ставка не указана")
    elif want and r["shift_eq"] < want and not (bonus and r["shift_eq"] >= want * 0.5):
        # со ставкой + % и чаевыми фикс бывает вдвое ниже итога — такие не отсекаем
        diffs.append("ставка ниже")
    if q.get("exp") == "none" and r["noexp"] == 0:
        diffs.append("нужен опыт")
    sch = q.get("schedule", "any")
    if sch in WANT_SCHEDULE and r["schedule"] and r["schedule"] not in WANT_SCHEDULE[sch]:
        diffs.append(f"график {SCHEDULES.get(r['schedule'], r['schedule'])}")  # графика в строке нет
    return diffs


def dup_key(r):
    title = _norm(r["title"] or "") if "title" in r.keys() else ""
    if not title:  # первая содержательная строка поста
        for line in re.sub(r"#\S+", "", r["text"] or "").splitlines():
            line = re.split(r"[,|•·—–]", line)[0]
            if clean_title(line):
                title = _norm(line)
                break
    return (re.sub(r"[^а-яa-z0-9]", "", title), r["sal_min"], r["sal_max"], "|".join(row_cities(r)))


def match(con, q, days=7):
    """q: prof, place ('msk' | 'any' | имя города), exp, salary (за смену или 0), schedule ('any' или код).
    Возвращает (точные, ближайшие с пометкой «чем отличается»)."""
    since = int(time.time()) - days * 86400
    rows = con.execute(
        "SELECT v.*, p.text FROM vac v JOIN posts p ON p.message_id=v.message_id "
        "WHERE p.closed=0 AND v.date>=? AND (v.prof=? OR v.profs LIKE ?) "
        "AND COALESCE(v.kind, 'vacancy')='vacancy' ORDER BY v.date DESC",
        (since, q["prof"], f"% {q['prof']} %")).fetchall()
    exact, near, seen = [], [], set()
    for r in rows:
        key = dup_key(r)
        if key in seen:
            continue  # повтор той же вакансии — оставляем самый свежий пост
        seen.add(key)
        diffs = compare(r, q)
        (exact if not diffs else near).append((r, diffs))
    # новичкам первыми — те, где явно берут без опыта
    if q.get("exp") == "none":
        exact.sort(key=lambda x: 0 if x[0]["noexp"] == 1 else 1)
    # среди «близких» первыми — те, что в нужном месте
    near.sort(key=lambda x: (any(d in ("не Москва", "другое место", "место не указано") for d in x[1]), len(x[1])))
    return exact, near
