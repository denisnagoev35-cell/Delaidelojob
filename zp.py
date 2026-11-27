"""Зарплатные вилки калькулятора — по живым вакансиям канала.

Берём вакансии профессии за 30 дней (без повторов), в одной единице (смена / час / месяц — где больше
вакансий), и показываем среднюю половину ставок: от 25-го до 75-го процентиля. Мало данных (< ZP_MIN)
или ZP_MODE=off — показываем прежнюю вилку из texts.py.
ZP_MODE: on — всем; test (по умолчанию) — живые цифры видят только админы; off — выключено.
Админу: /zp — таблица по всем профессиям.
"""
import os, re, time

MODE = os.environ.get("ZP_MODE", "test").strip().lower()
DAYS = 30
MIN = int(os.environ.get("ZP_MIN", "5"))
UNIT_TXT = {"shift": "за смену", "hour": "в час", "month": "в месяц"}
STEP = {"shift": 500, "hour": 10, "month": 5000}
# хвост, который не вытащить из ставки: проценты и чаевые
EXTRA = {"kalyan": " + % и чаевые", "bar": " + % и чаевые", "barista": " + чаевые", "waiter": " + % и чаевые",
         "cook": "", "hostess": " + чаевые", "staff": ""}

# вакансии начального уровня: для опытных их не берём, для новичков — берём
ENTRY_RE = re.compile(r"помощник|стаж[её]р|стажировк|ученик|без опыта|обучим с нуля|научим с нуля|#пкм|\bпкм\b", re.I)

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


def is_entry(r):
    head = (r["title"] if "title" in r.keys() and r["title"] else "") + "\n" + "\n".join((r["text"] or "").splitlines()[:3])
    return bool(r["noexp"]) or bool(ENTRY_RE.search(head))


def rows(con, prof, region=None, noexp=None, days=DAYS, level=None):
    """level: None — все; 'entry' — начальный уровень (помощник, стажёр, без опыта); 'senior' — без них."""
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
        if level == "entry" and not is_entry(r): continue
        if level == "senior" and is_entry(r): continue
        key = B.PB.dup_key(r)
        if key in seen: continue
        seen.add(key); out.append(r)
    return out


# какую часть ставок показываем по опыту: доли от 0 (самая низкая) до 1 (самая высокая)
QUANT = {"none": (0.10, 0.50), "lt1": (0.25, 0.50), "1to3": (0.25, 0.75), "gt3": (0.50, 0.85)}
ORDER = ["none", "lt1", "1to3", "gt3"]


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


def _tier(con, prof, region, exp):
    """→ (res, region, level) для одной ступени опыта, без поправок."""
    q = QUANT.get(exp, (0.25, 0.75))
    if exp == "none":     # вакансии начального уровня (помощник, стажёр, без опыта), иначе нижняя часть всех
        tries = [(region, "entry", (0.25, 0.75)), (None, "entry", (0.25, 0.75)), (region, None, q), (None, None, q)]
    elif exp in ("1to3", "gt3"):   # опытным — без вакансий помощников и стажёров
        tries = [(region, "senior", q), (None, "senior", q), (region, None, q), (None, None, q)]
    else:
        tries = [(region, None, q), (None, None, q)]
    for reg, level, qs in tries:
        res = calc(rows(con, prof, reg, level=level), qs)
        if res: return res, reg, level
    return None


def tier(con, prof, region, exp):
    """Ступень опыта с поправкой: не ниже предыдущей ступени (опытный не получает меньше новичка)."""
    t = _tier(con, prof, region, exp)
    if not t or exp not in ORDER: return t
    (unit, lo, hi, n), reg, level = t
    i = ORDER.index(exp)
    if i > 0:
        prev = tier(con, prof, region, ORDER[i - 1])
        if prev and prev[0][0] == unit:
            lo, hi = max(lo, prev[0][1]), max(hi, prev[0][2])
    return (unit, lo, hi, n), reg, level


def pick(con, prof, region, exp):
    """Живая вилка: регион и опыт, если данных хватает, иначе шире. → (текст, подпись) или None."""
    if prof not in EXTRA: return None
    t = tier(con, prof, region, exp)
    if not t: return None
    res, reg, level = t
    where = {"msk": " в Москве", "mo": " в Подмосковье"}.get(reg, "")
    what = " для начинающих" if level == "entry" else ""
    return fmt(prof, res), f"по {res[3]} вакансиям канала{what}{where} за {DAYS} дней"


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
        tiers = []
        for e in ("lt1", "1to3", "gt3"):
            t = tier(con, k, None, e)
            tiers.append(short(t[0]).split(" ₽")[0] if t else "—")
        parts.append("опыт: " + " / ".join(tiers) + " (до года / 1–3 / 3+)")
        res0 = calc(rows(con, k, level="entry"))
        n0 = len(rows(con, k, level="entry"))
        parts.append(f"начинающие: {short(res0)} ({res0[3]})" if res0 else f"начинающие: мало ({n0})")
        lines.append(f"<b>{p['name']}</b> — сейчас в боте: {p['range']}")
        lines.append("   " + " · ".join(parts))
    return "\n".join(lines)


# ---------- «Индекс зарплат HoReCa» (/zp_post) ----------
INDEX_PROFS = [("kalyan", "Кальянщик", "кальянщикам", "кальянщиков"), ("bar", "Бармен", "барменам", "барменов"),
               ("waiter", "Официант", "официантам", "официантов"), ("cook", "Повар", "поварам", "поваров"),
               ("hostess", "Хостес", "хостес", "хостес"), ("barista", "Бариста", "бариста", "бариста"),
               ("staff", "Хозперсонал", "хозперсоналу", "хозперсонала")]
UNIT_HEAD = {"shift": "₽ за смену", "hour": "₽ за час", "month": "₽ в месяц"}


def _cell(lo, hi):
    return f"{money(lo)}–{money(hi)}" if lo != hi else f"≈ {money(lo)}"


def index_data(con, region=None):
    """Строки индекса: профессия, единица, вилки до года / 1–3 / 3+; профессии без данных — пропускаем."""
    out, ids = [], set()
    for k, name, dat, gen in INDEX_PROFS:
        ts = [tier(con, k, region, e) for e in ("lt1", "1to3", "gt3")]
        if not all(ts):
            continue
        units = {t[0][0] for t in ts}
        if len(units) != 1:
            continue
        unit = units.pop()
        ids |= {r["message_id"] for r in rows(con, k, region)} | {r["message_id"] for r in rows(con, k)}
        out.append({"prof": k, "name": name, "dat": dat, "gen": gen, "unit": unit,
                    "t": [(t[0][1], t[0][2]) for t in ts], "cells": [_cell(t[0][1], t[0][2]) for t in ts]})
    return out, len(ids)


def index_facts(rows_, prev=None):
    """2–3 вывода по цифрам (без нейросети) + динамика к прошлому месяцу."""
    sh = [r for r in rows_ if r["unit"] == "shift"]
    out = []
    if sh:
        top = max(sh, key=lambda r: r["t"][2][1])
        out.append(f"Больше всего платят опытным: {top['dat']} 3+ года — до {money(top['t'][2][1])} ₽ за смену")
        st = max(sh, key=lambda r: r["t"][0][0])
        if st is not top:
            out.append(f"Самый высокий старт — {st['dat']}: от {money(st['t'][0][0])} ₽ за смену в первый год")
        rest = [r for r in sh if r is not top and r is not st]
        nar = min(rest, key=lambda r: r["t"][2][1] - r["t"][0][0]) if rest else None
        if nar:
            out.append(f"Меньше всего разброс у {nar['gen']}: {money(nar['t'][0][0])}–{money(nar['t'][2][1])} ₽")
    if prev:
        for r in rows_:
            p = prev.get(r["prof"])
            if not p or p.get("unit") != r["unit"]:
                continue
            mid, pmid = sum(r["t"][1]) / 2, sum(p["t"][1]) / 2
            dlt = _round(mid - pmid, r["unit"])
            if abs(dlt) >= STEP[r["unit"]]:
                out.append(f"{r['name']} (1–3 года): {'+' if dlt > 0 else '−'}{money(abs(dlt))} ₽ к прошлому месяцу")
    return out


def why(con, prof, exp="1to3", region=None):
    """Какие вакансии попали в ступень: № поста, ставка, единица, «новичок?» — для проверки цифр (/zp_why)."""
    t = _tier(con, prof, region, exp)
    if not t:
        return "Данных нет."
    res, reg, level = t
    rs = rows(con, prof, reg, level=level)
    vals = sorted(((_value(r), r) for r in rs if _value(r) and r["unit"] == res[0]), key=lambda x: x[0])
    head = (f"{prof} · {exp} · {'все' if not level else level} · {reg or 'Москва и МО'}: "
            f"{money(res[1])}–{money(res[2])} ({UNIT_TXT[res[0]]}), {res[3]} вакансий")
    return head + "\n" + "\n".join(f"№{r['message_id']} — {money(int(v))} ₽" + (" · новичок" if is_entry(r) else "")
                                    for v, r in vals[:40])
