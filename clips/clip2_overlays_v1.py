"""Overlays for clip 2 «Обещали 6 000 — дали 1 200»: hook, fine chips, stamp fine, bot card, badge, watermark."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os
D = os.path.dirname(os.path.abspath(__file__)); F = D + "/tools/fonts/"; OUT = D + "/ov2"
os.makedirs(OUT, exist_ok=True)
W, H = 1080, 1920
Y = (255, 198, 3); RED = (235, 52, 45); INK = (12, 10, 5)
def fnt(n, s): return ImageFont.truetype(F + n, s)
BI, EB, B, SB = "Montserrat-BlackItalic.ttf", "Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf", "Montserrat-SemiBold.ttf"

def shadow(im, blur=12, alpha=120, dy=8):
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(im.split()[3].filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * alpha / 255)))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0)); out.alpha_composite(sh, (0, dy)); out.alpha_composite(im); return out

def caption(lines, name, y0, size):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im); f = fnt(BI, size); y = y0
    for segs in lines:
        tw = sum(d.textlength(t, font=f) for t, _ in segs); x = (W - tw) / 2
        for t, c in segs:
            d.text((x, y), t, font=f, fill=c, stroke_width=10, stroke_fill=INK); x += d.textlength(t, font=f)
        y += int(size * 1.16)
    shadow(im).save(f"{OUT}/{name}.png")

def fine_chip(text, amount, name, y):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    ft, fa = fnt(B, 38), fnt(EB, 40)
    tw, aw = d.textlength(text, font=ft), d.textlength(amount, font=fa)
    w = int(tw + aw + 150); x1 = W - 46; x0 = x1 - w
    d.rounded_rectangle((x0, y, x1, y + 90), 30, fill=(255, 255, 255, 250))
    d.ellipse((x0 + 22, y + 22, x0 + 68, y + 68), fill=RED)
    d.line((x0 + 34, y + 34, x0 + 56, y + 56), fill="white", width=6); d.line((x0 + 56, y + 34, x0 + 34, y + 56), fill="white", width=6)
    d.text((x0 + 88, y + 45), text, font=ft, fill=(25, 25, 30), anchor="lm")
    d.text((x1 - 28, y + 45), amount, font=fa, fill=RED, anchor="rm")
    shadow(im, 10, 110, 6).save(f"{OUT}/{name}.png")

def stamp_fine(name):
    w, h = 760, 300
    s = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(s)
    d.rounded_rectangle((8, 8, w - 8, h - 8), 26, outline=RED + (255,), width=12)
    d.rounded_rectangle((28, 28, w - 28, h - 28), 16, outline=RED + (255,), width=4)
    d.text((w / 2, 112), "ШТРАФ −1 000 ₽", font=fnt(BI, 66), fill=RED, anchor="mm")
    d.text((w / 2, 208), "ЗА ВОПРОС «ЗА ЧТО?»", font=fnt(EB, 46), fill=RED, anchor="mm")
    # rough ink texture
    import random; random.seed(3)
    m = Image.new("L", (w, h), 255); md = ImageDraw.Draw(m)
    for _ in range(900):
        x, y = random.randint(0, w), random.randint(0, h); r = random.randint(1, 4); md.ellipse((x, y, x + r, y + r), fill=random.randint(40, 160))
    a = s.split()[3]; s.putalpha(Image.fromarray(__import__("numpy").minimum(__import__("numpy").asarray(a), __import__("numpy").asarray(m))))
    plate = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle((0, 0, w + 40, h + 40), 34, fill=(255, 250, 240, 235))
    plate.alpha_composite(s, (20, 20))
    plate = plate.rotate(-7, resample=Image.BICUBIC, expand=True)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); im.alpha_composite(plate, ((W - plate.width) // 2, 230))
    shadow(im, 14, 130, 10).save(f"{OUT}/{name}.png")

def bot_card(name):
    w, h = 900, 400; x0 = (W - w) // 2; y0 = 250
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((x0, y0, x0 + w, y0 + h), 40, fill=(255, 255, 255, 252))
    logo = Image.open(D + "/tools/banners/logo.png").convert("RGBA").resize((96, 96), Image.LANCZOS)
    im.alpha_composite(logo, (x0 + 34, y0 + 30))
    d.text((x0 + 150, y0 + 40), "Зарплаты HoReCa", font=fnt(EB, 40), fill=(20, 20, 25))
    d.text((x0 + 150, y0 + 90), "бот · @delaidelo24bot", font=fnt(SB, 30), fill=(36, 120, 230))
    d.line((x0 + 34, y0 + 152, x0 + w - 34, y0 + 152), fill=(230, 230, 235), width=3)
    d.text((x0 + 40, y0 + 178), "Кальянщик · Москва и МО", font=fnt(B, 36), fill=(90, 90, 100))
    d.rounded_rectangle((x0 + 34, y0 + 236, x0 + w - 34, y0 + 336), 24, fill=Y)
    d.text((x0 + w / 2, y0 + 286), "6 000–8 000 ₽ за смену", font=fnt(BI, 58), fill=INK, anchor="mm")
    d.text((x0 + w / 2, y0 + 366), "с % и чаевыми", font=fnt(SB, 30), fill=(90, 90, 100), anchor="mm")
    shadow(im, 16, 140, 10).save(f"{OUT}/{name}.png")

def badge(name):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0, x1, y1 = 46, 108, 388, 192
    ImageDraw.Draw(im).rounded_rectangle((x0, y0, x1, y1), 42, fill=(0, 0, 0, 120))
    logo = Image.open(D + "/tools/banners/logo.png").convert("RGBA").resize((76, 76), Image.LANCZOS)
    im.alpha_composite(logo, (x0 + 4, y0 + 4)); d = ImageDraw.Draw(im)
    d.text((x0 + 90, y0 + 14), "ДЕЛАЙ ДЕЛО JOB", font=fnt(BI, 29), fill="white")
    d.text((x0 + 90, y0 + 48), "@delaidelojob", font=fnt(EB, 23), fill=Y)
    im.save(f"{OUT}/{name}.png")

def wm(name, cy):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((W / 2, cy), "@delaidelojob · ДЕЛАЙ ДЕЛО JOB", font=fnt(EB, 38), fill=(255, 255, 255, 46), anchor="mm")
    im.save(f"{OUT}/{name}.png")

caption([[("ОБЕЩАЛИ ", "white"), ("6 000.", Y)], [("ДАЛИ ", "white"), ("1 200.", (255, 90, 70))]], "hook", 300, 100)
fine_chip("Опоздал на 2 минуты", "−500 ₽", "f1", 250)
fine_chip("Разбил колбу", "−1 000 ₽", "f2", 362)
fine_chip("Кислое лицо", "−300 ₽", "f3", 474)
stamp_fine("stampfine")
bot_card("botcard")
badge("badge")
for i, cy in enumerate([900, 1100, 760, 980, 1180]): wm(f"wm{i}", cy)
print("ok")
