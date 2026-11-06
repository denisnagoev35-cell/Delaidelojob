"""Clip 2 overlays in the channel style: Montserrat Black/ExtraBold Italic, white + yellow/red, black stroke, soft shadow."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os
D = os.path.dirname(os.path.abspath(__file__)); F = D + "/tools/fonts/"; OUT = D + "/ov2"
os.makedirs(OUT, exist_ok=True)
W, H = 1080, 1920
WH, Y, R, INK = (255, 255, 255), (255, 198, 3), (255, 84, 64), (12, 10, 5)
BI, EBI, EB = "Montserrat-BlackItalic.ttf", "Montserrat-ExtraBoldItalic.ttf", "Montserrat-ExtraBold.ttf"
def fnt(n, s): return ImageFont.truetype(F + n, s)
M = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

def shadow(im):
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(im.split()[3].filter(ImageFilter.GaussianBlur(12)).point(lambda v: int(v * 0.5)))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0)); out.alpha_composite(sh, (0, 8)); out.alpha_composite(im); return out

def caption(lines, name, y0, maxw=980, font=BI, gap=1.14):
    """lines: [(size, [(text, color), ...]), ...] — each line auto-shrinks to maxw"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im); y = y0
    for size, segs in lines:
        s = size
        while sum(M.textlength(t, font=fnt(font, s)) for t, _ in segs) > maxw: s -= 2
        f = fnt(font, s); tw = sum(d.textlength(t, font=f) for t, _ in segs); x = (W - tw) / 2
        sw = max(6, int(s * 0.1))
        for t, c in segs:
            d.text((x, y), t, font=f, fill=c, stroke_width=sw, stroke_fill=INK); x += d.textlength(t, font=f)
        y += int(s * gap)
    shadow(im).save(f"{OUT}/{name}.png")

def badge(name):
    f1, f2 = fnt(BI, 29), fnt(EB, 23)
    w = int(max(M.textlength("ДЕЛАЙ ДЕЛО JOB", font=f1), M.textlength("@delaidelojob", font=f2))) + 90 + 30
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); x0, y0 = 46, 108
    ImageDraw.Draw(im).rounded_rectangle((x0, y0, x0 + w, y0 + 84), 42, fill=(0, 0, 0, 125))
    logo = Image.open(D + "/tools/banners/logo.png").convert("RGBA").resize((76, 76), Image.LANCZOS)
    im.alpha_composite(logo, (x0 + 4, y0 + 4)); d = ImageDraw.Draw(im)
    d.text((x0 + 90, y0 + 14), "ДЕЛАЙ ДЕЛО JOB", font=f1, fill="white")
    d.text((x0 + 90, y0 + 48), "@delaidelojob", font=f2, fill=Y)
    im.save(f"{OUT}/{name}.png")

caption([(118, [("ОБЕЩАЛИ ", WH), ("6 000.", Y)]), (118, [("ДАЛИ ", WH), ("1 200.", R)])], "hook", 230)
def bot_card(name):
    w, h = 880, 492; x0 = (W - w) // 2; y0 = 230
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    # chat header strip
    d.rounded_rectangle((x0, y0, x0 + w, y0 + h), 44, fill=(24, 33, 44, 240))
    logo = Image.open(D + "/tools/banners/logo.png").convert("RGBA").resize((84, 84), Image.LANCZOS)
    mask = Image.new("L", (84, 84), 0); ImageDraw.Draw(mask).ellipse((0, 0, 84, 84), fill=255)
    im.paste(logo, (x0 + 30, y0 + 26), mask)
    d.text((x0 + 132, y0 + 34), "Зарплаты HoReCa", font=fnt(EB, 38), fill=WH)
    tw = M.textlength("Зарплаты HoReCa", font=fnt(EB, 38))
    d.rounded_rectangle((x0 + 146 + tw, y0 + 38, x0 + 146 + tw + 74, y0 + 76), 12, fill=(64, 140, 230))
    d.text((x0 + 146 + tw + 37, y0 + 57), "БОТ", font=fnt(EB, 24), fill=WH, anchor="mm")
    d.text((x0 + 132, y0 + 82), "онлайн", font=fnt(EB, 26), fill=(120, 170, 230))
    # incoming bot bubble
    bx0, by0, bx1, by1 = x0 + 30, y0 + 136, x0 + w - 30, y0 + 386
    d.rounded_rectangle((bx0, by0, bx1, by1), 28, fill=(40, 52, 66))
    d.polygon([(bx0 + 6, by1 - 30), (bx0 - 16, by1 + 4), (bx0 + 30, by1 - 6)], fill=(40, 52, 66))
    d.text((bx0 + 30, by0 + 26), "Кальянщик · Москва и МО · опыт 1–3 года", font=fnt(EB, 28), fill=(170, 185, 200))
    d.text((bx0 + 30, by0 + 70), "Твоя вилка:", font=fnt(EB, 34), fill=WH)
    d.text((bx0 + 30, by0 + 118), "6 000–8 000 ₽", font=fnt(BI, 76), fill=Y)
    d.text((bx0 + 30 + M.textlength("6 000–8 000 ₽", font=fnt(BI, 76)) + 18, by0 + 158), "за смену", font=fnt(EB, 32), fill=WH)
    # inline keyboard buttons, like a real bot
    btn = [("Полный разбор", x0 + 30, x0 + w / 2 - 8), ("Свежие вакансии", x0 + w / 2 + 8, x0 + w - 30)]
    for t, a, b in btn:
        d.rounded_rectangle((a, y0 + 404, b, y0 + 470), 18, fill=(58, 74, 92))
        d.text(((a + b) / 2, y0 + 437), t, font=fnt(EB, 30), fill=WH, anchor="mm")
    card = im.crop((x0 - 20, y0 - 10, x0 + w + 20, y0 + h + 20))
    k = 0.62; card = card.resize((int(card.width * k), int(card.height * k)), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0)); out.alpha_composite(card, (W - 40 - card.width, 196))
    shadow(out).save(f"{OUT}/{name}.png")

bot_card("botcard")
badge("badge")
print("ok")
