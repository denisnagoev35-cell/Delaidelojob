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


def render(photo_bytes, f):
    ph = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    s = max(W / ph.width, H / ph.height)
    ph = ph.resize((int(ph.width * s + 1), int(ph.height * s + 1)), Image.LANCZOS)
    im = ph.crop(((ph.width - W) // 2, (ph.height - H) // 2, (ph.width - W) // 2 + W, (ph.height - H) // 2 + H))
    # затемнение: сверху чуть под логотип, снизу сильно под текст
    g = Image.new("RGBA", (W, H)); gd = ImageDraw.Draw(g)
    for y in range(H):
        a = max(int(170 * (1 - y / 220)) if y < 220 else 0, int(255 * min(0.93, max(0.0, (y - 330) / 520))))
        gd.line((0, y, W, y), fill=BK + (a,))
    im = Image.alpha_composite(im, g)
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
