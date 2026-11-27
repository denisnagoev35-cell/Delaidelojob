"""Обложка вакансии: фото заведения на фоне + должность, ставка, место, фишка — в стиле «Делай Дело JOB».
Данные берутся из уже собранного поста (HTML), фишка — из строки ⭐."""
import os, re, html, io
from PIL import Image, ImageDraw, ImageFont, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
Y, BK, TX, T2 = (255, 198, 3), (11, 11, 11), (245, 245, 245), (225, 225, 225)
W = H = 1080


def font(size, wght, italic=True):
    f = ImageFont.truetype(os.path.join(HERE, "fonts", "Montserrat-Italic[wght].ttf" if italic else "Montserrat[wght].ttf"), size)
    f.set_variation_by_axes([wght])
    return f


MONEY = re.compile(r"(?:от\s+|до\s+|≈\s*)?\d[\d\s]*(?:[–-]\s*\d[\d\s]*)?\s*₽")


def from_post(h):
    """Поля для обложки из HTML поста."""
    t = html.unescape(re.sub(r"<[^>]+>", "", h.split("<blockquote")[0]))
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    head = next((l for l in lines if " — " in l and not l.startswith(("📍", "⭐", "#"))), "")
    title, _, pay = head.partition(" — ")
    title = re.sub(r"^\W+", "", title).strip()
    place = next((l[2:].strip() for l in lines if l.startswith("📍")), "")
    star = next((l[1:].strip() for l in lines if l.startswith("⭐")), "")
    m = MONEY.search(pay)
    big = m.group(0).strip() if m else ""
    rest = (pay[:m.start()] + pay[m.end():]).strip(" ,·") if m else pay
    sub = ("+ " + rest.lstrip("+ ")) if m and rest.startswith("+") else rest
    return {"title": title, "place": place, "big": big, "sub": sub,
            "chips": [c.strip() for c in star.split("·") if c.strip()][:2]}


def fit(d, text, wght, size, maxw, minsize=34):
    while size > minsize and d.textlength(text, font=font(size, wght)) > maxw:
        size -= 2
    return font(size, wght)


def logo(s):
    L = Image.open(os.path.join(HERE, "brand", "logo.png")).convert("RGBA").resize((s * 4, s * 4), Image.LANCZOS)
    m = Image.new("L", L.size, 0); k = int(s * 4 * 0.022)
    ImageDraw.Draw(m).ellipse((k, k, s * 4 - k, s * 4 - k), fill=255)
    L.putalpha(ImageChops.multiply(L.getchannel("A"), m))
    return L.resize((s, s), Image.LANCZOS)


PH_BOTTOM = 600   # фото — сверху до этой линии; ниже тёмный низ под текст


def _flat_border(ph):
    """Однотонная рамка (логотип/картинка на сплошном фоне) → цвет фона, иначе None."""
    sm = ph.convert("RGB").resize((64, 64))
    px = [sm.getpixel((x, y)) for x in range(64) for y in (0, 1, 62, 63)] + \
         [sm.getpixel((x, y)) for y in range(64) for x in (0, 1, 62, 63)]
    avg = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
    dev = sum(max(abs(c[i] - avg[i]) for i in range(3)) for c in px) / len(px)
    return avg if dev < 14 else None


def background(ph):
    """Фото в верхней части: обычное — заполняет верх (кадрируем), логотип на однотонном фоне — целиком, по центру.
    Ниже PH_BOTTOM — плавный переход в чёрный, текст всегда на тёмном."""
    flat = _flat_border(ph)
    im = Image.new("RGBA", (W, H), (flat or BK) + (255,))
    if flat:
        box_w, box_h, top = W - 260, PH_BOTTOM - 210, 160
        s = min(box_w / ph.width, box_h / ph.height)
        p2 = ph.resize((max(1, int(ph.width * s)), max(1, int(ph.height * s))), Image.LANCZOS)
        im.alpha_composite(p2, ((W - p2.width) // 2, top + (box_h - p2.height) // 2))
    else:
        area = PH_BOTTOM + 120
        s = max(W / ph.width, area / ph.height)
        p2 = ph.resize((int(ph.width * s + 1), int(ph.height * s + 1)), Image.LANCZOS)
        y0 = max(0, int((p2.height - area) * 0.4))   # чуть выше центра — лица и вывески обычно там
        x0 = (p2.width - W) // 2
        im.alpha_composite(p2.crop((x0, y0, x0 + W, y0 + area)), (0, 0))
    g = Image.new("RGBA", (W, H)); gd = ImageDraw.Draw(g)
    for y in range(H):
        top_a = int(150 * (1 - y / 200)) if y < 200 else 0
        low = 255 if y >= PH_BOTTOM + 120 else int(255 * max(0.0, (y - (PH_BOTTOM - 170)) / 290) ** 1.2)
        gd.line((0, y, W, y), fill=BK + (max(top_a, min(255, low)),))
    return Image.alpha_composite(im, g)


def render(photo_bytes, f):
    ph = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    im = background(ph)
    d = ImageDraw.Draw(im)
    L = 70
    # шапка
    jx = L + 78 + d.textlength("ДЕЛАЙ ДЕЛО", font=font(32, 900)) + 12
    pill = Image.new("RGBA", (W, H)); ImageDraw.Draw(pill).rounded_rectangle(
        (L - 14, 50, jx + 72 + 18, 132), radius=41, fill=BK + (200,))
    im.alpha_composite(pill); d = ImageDraw.Draw(im)
    im.alpha_composite(logo(66), (L - 6, 58))
    d.text((L + 78, 91), "ДЕЛАЙ ДЕЛО", font=font(32, 900), fill=TX, anchor="lm")
    d.rounded_rectangle((jx, 75, jx + 72, 107), radius=8, fill=Y)
    d.text((jx + 36, 91), "JOB", font=font(21, 900), fill=BK, anchor="mm")
    # низ — снизу вверх
    y = 1022
    d.text((W - L, y), "@delaidelojob", font=font(34, 900), fill=Y, anchor="rs")
    d.text((L, y), "МОСКВА И МО", font=font(30, 800), fill=TX, anchor="ls")
    y -= 96
    if f["chips"]:
        x, cy = L, y
        cf = font(28, 800)
        for c in f["chips"]:
            w = d.textlength(c, font=cf) + 44
            if x + w > W - L and x > L:
                break
            d.rounded_rectangle((x, cy - 36, x + w, cy + 22), radius=29, outline=Y, width=3, fill=BK + (150,))
            d.text((x + w / 2, cy - 7), c, font=cf, fill=TX, anchor="mm")
            x += w + 16
        y = cy - 70
    if f["sub"]:
        d.text((L, y), f["sub"].upper(), font=fit(d, f["sub"].upper(), 800, 30, W - 2 * L, 22), fill=T2, anchor="ls")
        y -= 52
    if f["big"]:
        d.text((L - 8, y), f["big"], font=fit(d, f["big"], 900, 112, W - 2 * L, 70), fill=Y, anchor="ls")
        y -= 132
    if f["place"]:
        d.text((L, y), f["place"], font=fit(d, f["place"], 700, 30, W - 2 * L, 22), fill=T2, anchor="ls")
        y -= 50
    d.text((L, y), f["title"].upper(), font=fit(d, f["title"].upper(), 900, 58, W - 2 * L, 36), fill=TX, anchor="ls")
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def render_index(rows, subtitle, note):
    """Картинка «Индекс зарплат»: rows — [{"name", "unit", "cells": [до года, 1–3, 3+]}]."""
    Sx = 2
    G, ROW = (140, 140, 140), (28, 28, 28)
    im = Image.new("RGB", (W * Sx, 1350 * Sx), BK); d = ImageDraw.Draw(im)
    def T(xy, t, f, c, a="la"): d.text((xy[0] * Sx, xy[1] * Sx), t, font=f, fill=c, anchor=a)
    def f(sz, w=800, it=False): return font(sz * Sx, w, it)
    L, R = 56, 1024
    d.rounded_rectangle([L * Sx, 56 * Sx, R * Sx, 256 * Sx], radius=28 * Sx, fill=Y)
    T((540, 128), "ИНДЕКС ЗАРПЛАТ", f(74, 900, True), BK, "mm")
    T((540, 205), subtitle, f(34, 700, False), BK, "mm")
    name_x, cols = L + 28, [465, 690, 905]
    shift_all = all(r["unit"] == "shift" for r in rows)
    T((name_x, 318), "₽ за смену" if any(r["unit"] == "shift" for r in rows) else "", f(28, 600, False), G, "lm")
    for cx, h in zip(cols, ["до года", "1–3 года", "3+ года"]):
        T((cx, 318), h, f(30, 800, False), Y, "mm")
    top, bottom, gap = 352, 1080, 12
    h = min(104, (bottom - top - gap * (len(rows) - 1)) / max(1, len(rows)))
    for n, r in enumerate(rows):
        y0 = top + n * (h + gap); cy = y0 + h / 2
        d.rounded_rectangle([L * Sx, y0 * Sx, R * Sx, (y0 + h) * Sx], radius=20 * Sx, fill=ROW)
        if r["unit"] != "shift":
            T((name_x, cy - 14), r["name"], f(38, 800, False), TX, "lm")
            T((name_x, cy + 24), {"hour": "₽ за час", "month": "₽ в месяц"}[r["unit"]], f(24, 600, False), G, "lm")
        else:
            T((name_x, cy), r["name"], f(38, 800, False), TX, "lm")
        for cx, v in zip(cols, r["cells"]):
            fv = f(30, 600, False)
            while d.textlength(v, font=fv) > 215 * Sx:
                fv = font(int(fv.size * 0.94), 600, False)
            T((cx, cy), v, fv, TX, "mm")
    yb = top + len(rows) * (h + gap) + 14
    T((540, yb + 14), note, f(28, 500, False), G, "mm")
    d.rounded_rectangle([L * Sx, 1150 * Sx, R * Sx, 1294 * Sx], radius=28 * Sx, fill=Y)
    T((540, 1198), "Сколько с твоим опытом? Посчитает бот", f(38, 800, True), BK, "mm")
    T((540, 1250), "@delaidelo24bot   ·   @delaidelojob", f(32, 700, False), BK, "mm")
    out = io.BytesIO()
    im.resize((W, 1350), Image.LANCZOS).save(out, "JPEG", quality=92)
    return out.getvalue()
