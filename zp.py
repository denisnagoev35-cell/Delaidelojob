"""Зарплатные вилки калькулятора — по живым вакансиям канала.

Берём вакансии профессии за 30 дней (без повторов), в одной единице (смена / час / месяц — где больше
вакансий), и показываем среднюю половину ставок: от 25-го до 75-го процентиля. Мало данных (< ZP_MIN)
или ZP_MODE=off — показываем прежнюю вилку из texts.py.
ZP_MODE: on — всем; test (по умолчанию) — живые цифры видят только админы; off — выключено.
Админу: /zp — таблица по всем профессиям.
"""
import os, time

MODE = os.environ.get("ZP_MODE", "test").strip().lower()
DAYS = 30
MIN = int(os.environ.get("ZP_MIN", "5"))
UNIT_TXT = {"shift": "за смену", "hour": "в час", "month": "в месяц"}
STEP = {"shift": 500, "hour": 10, "month": 5000}
# хвост, который не вытащить из ставки: проценты и чаевые
EXTRA = {"kalyan": " + % и чаевые", "bar": " + % и чаевые", "barista": " + чаевые", "waiter": " + % и чаевые",
         "cook": "", "hostess": " + чаевые", "staff": ""}

B = None


def init(bot_module):
    global B
    B = bot_module


def live_for(uid):
    return MODE == "on" or (MODE == "test" and uid in B.ADMIN_IDS)


def _value(r):
    lo, hi = r["sal_min"], r["sal_max"]
    if lo and hi: return (lo + hi) / 2
    return lo or hi


def _q(xs, p):
    xs = sorted(xs); k = (len(xs) - 1) * p; f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _round(v, unit):
    s = STEP[unit]; return int(round(v / s) * s)


def rows(con, prof, region=None, noexp=None, days=DAYS):
    since = int(time.time()) - days * 86400
    out, seen = [], set()
    for r in con.execute(
            "SELECT v.*, p.text FROM vac v JOIN posts p ON p.message_id=v.message_id "
            "WHERE v.date>=? AND COALESCE(v.kind, 'vacancy')='vacancy' AND v.unit IN ('shift','hour','month') "
            "AND (v.sal_min IS NOT NULL OR v.sal_max IS NOT NULL) ORDER BY v.date DESC", (since,)):
        if prof not in B.PB.row_profs(r): continue
        cities = B.PB.row_cities(r)
        if region == "msk" and B.PB.MOSCOW not in cities: continue
        if region == "mo" and not [c for c in cities if c != B.PB.MOSCOW]: continue
        if noexp and not r["noexp"]: continue
        key = B.PB.dup_key(r)
        if key in seen: continue
        seen.add(key); out.append(r)
    return out


# какую часть ставок показываем по опыту: доли от 0 (самая низкая) до 1 (самая высокая)
QUANT = {"none": (0.10, 0.50), "lt1": (0.25, 0.50), "1to3": (0.25, 0.75), "gt3": (0.50, 0.90)}


def calc(rs, qs=(0.25, 0.75)):
    """→ (unit, lo, hi, n) по самой частой единице или None."""
    by = {}
    for r in rs:
        v = _value(r)
        if v: by.setdefault(r["unit"], []).append(v)
    if not by: return None
    unit = max(by, key=lambda u: len(by[u])); xs = by[unit]
    if len(xs) < MIN: return None
    return unit, _round(_q(xs, qs[0]), unit), _round(_q(xs, qs[1]), unit), len(xs)


def money(v): return f"{v:,}".replace(",", " ")


def fmt(prof, res):
    unit, lo, hi, n = res
    s = f"{money(lo)}–{money(hi)} ₽" if lo != hi else f"≈{money(lo)} ₽"
    return f"{s} {UNIT_TXT[unit]}{EXTRA.get(prof, '')}"


def pick(con, prof, region, exp):
    """Живая вилка: регион и опыт, если данных хватает, иначе шире. → (текст, подпись) или None."""
    if prof not in EXTRA: return None
    tries = []
    if exp == "none":   # сначала вакансии, где опыт не нужен, — их середина
        tries += [(region, True, (0.25, 0.75)), (None, True, (0.25, 0.75))]
    q = QUANT.get(exp, (0.25, 0.75))   # иначе — нужная по опыту часть всех ставок
    tries += [(region, None, q), (None, None, q)]
    for reg, noexp, qs in tries:
        res = calc(rows(con, prof, reg, noexp), qs)
        if res:
            where = {"msk": " в Москве", "mo": " в Подмосковье"}.get(reg, "")
            what = " без опыта" if noexp else ""
            return fmt(prof, res), f"по {res[3]} вакансиям канала{what}{where} за {DAYS} дней"
    return None


def short(res):
    unit, lo, hi, n = res
    u = {"shift": "/смена", "hour": "/час", "month": "/мес"}[unit]
    return (f"{money(lo)}–{money(hi)}" if lo != hi else money(lo)) + f" ₽{u}"


def table(con):
    lines = ["💰 <b>Зарплаты по вакансиям канала</b> (30 дней, середина ставок)",
             f"Режим: {MODE} · порог: {MIN} вакансий", ""]
    for k, p in B.T.PROFESSIONS.items():
        if k not in EXTRA: continue
        parts = []
        for reg, title in ((None, "всё"), ("msk", "Мск"), ("mo", "МО")):
            res = calc(rows(con, k, reg))
            n = len(rows(con, k, reg))
            parts.append(f"{title}: {short(res)} ({res[3]})" if res else f"{title}: мало ({n})")
        all_rows = rows(con, k)
        if calc(all_rows):
            parts.append("опыт: " + " / ".join(
                short(calc(all_rows, QUANT[e])).split(" ₽")[0] for e in ("lt1", "1to3", "gt3")) + " (до года / 1–3 / 3+)")
        res0 = calc(rows(con, k, None, True))
        parts.append(f"без опыта: {short(res0)} ({res0[3]})" if res0 else "без опыта: мало")
        lines.append(f"<b>{p['name']}</b> — сейчас в боте: {p['range']}")
        lines.append("   " + " · ".join(parts))
    return "\n".join(lines)
