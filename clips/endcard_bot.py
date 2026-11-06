"""Endcard for the bot: frames at 30 fps."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os, sys, math
W, H = 1080, 1920
D = os.path.dirname(os.path.abspath(__file__)); F = D + "/tools/fonts/"
Y = (255, 198, 3)
VAR = {"vk_dzen": "ССЫЛКА НА БОТА — В ОПИСАНИИ", "shorts_tiktok": "НАЙДИ В ПОИСКЕ TELEGRAM"}
var, out, dur, fps = sys.argv[1], sys.argv[2], float(sys.argv[3]), 30
os.makedirs(out, exist_ok=True)
fBI = lambda s: ImageFont.truetype(F + "Montserrat-BlackItalic.ttf", s)
fEB = lambda s: ImageFont.truetype(F + "Montserrat-ExtraBold.ttf", s)
logo = Image.open(D + "/tools/banners/logo.png").convert("RGBA")
bg = Image.new("RGBA", (W, H), (11, 9, 5, 255))
g = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ImageDraw.Draw(g).ellipse((140, 380, 940, 1180), fill=(255, 198, 3, 55))
bg = Image.alpha_composite(bg, g.filter(ImageFilter.GaussianBlur(160)))
def ease(t): t = max(0, min(1, t)); return 1 - (1 - t) ** 3
def spaced(d, cx, cy, text, f, fill, sp):
    x = cx - (sum(d.textlength(c, font=f) for c in text) + sp * (len(text) - 1)) / 2
    for c in text: d.text((x, cy), c, font=f, fill=fill, anchor="lm"); x += d.textlength(c, font=f) + sp
title = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(title)
d.text((W / 2, 900), "СКОЛЬКО ДОЛЖНЫ", font=fBI(84), fill="white", anchor="mm")
d.text((W / 2, 1000), "ПЛАТИТЬ ТЕБЕ?", font=fBI(84), fill=Y, anchor="mm")
sub = Image.new("RGBA", (W, H), (0, 0, 0, 0)); spaced(ImageDraw.Draw(sub), W / 2, 1100, "БОТ «ЗАРПЛАТЫ HORECA» · 30 СЕК · БЕСПЛАТНО", fEB(28), (170, 160, 140), 3)
prof = Image.new("RGBA", (W, H), (0, 0, 0, 0)); dp = ImageDraw.Draw(prof)
chips = ["Кальянщик", "Бармен", "Официант", "Повар", "Хостес"]; f = fEB(30)
ws = [dp.textlength(c, font=f) + 44 for c in chips]; x = (W - sum(ws) - 14 * (len(ws) - 1)) / 2
for c, w in zip(chips, ws):
    dp.rounded_rectangle((x, 1500, x + w, 1562), 31, outline=(255, 198, 3, 200), width=3)
    dp.text((x + w / 2, 1531), c, font=f, fill=(235, 230, 220), anchor="mm"); x += w + 14
def button(p):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(gl).rounded_rectangle((100, 1232, 980, 1398), 34, fill=Y + (int(110 + 90 * p),))
    im = Image.alpha_composite(im, gl.filter(ImageFilter.GaussianBlur(26))); d = ImageDraw.Draw(im)
    d.rounded_rectangle((94, 1226, 986, 1404), 38, outline=Y, width=6); d.rounded_rectangle((108, 1240, 972, 1390), 28, fill=Y)
    spaced(d, W / 2, 1276, VAR[var], fEB(30), (25, 18, 0), 3)
    d.text((W / 2, 1342), "@delaidelo24bot", font=fBI(70), fill=(10, 8, 0), anchor="mm"); return im
def fade(im, a):
    if a >= 1: return im
    r = im.copy(); r.putalpha(r.split()[3].point(lambda v: int(v * a))); return r
for i in range(int(round(dur * fps))):
    t = i / fps; fr = bg.copy()
    s = ease(t / 0.45); sc = 0.55 + 0.45 * s + 0.06 * math.sin(min(1, t / 0.45) * math.pi)
    ls = int(360 * sc); fr.alpha_composite(fade(logo.resize((ls, ls), Image.LANCZOS), s), (W // 2 - ls // 2, 580 - ls // 2))
    a = ease((t - 0.35) / 0.4)
    if a > 0: fr.alpha_composite(fade(title, a), (0, int(40 * (1 - a))))
    a = ease((t - 0.6) / 0.4)
    if a > 0: fr.alpha_composite(fade(sub, a))
    a = ease((t - 0.85) / 0.35)
    if a > 0: fr.alpha_composite(fade(button(0.5 + 0.5 * math.sin((t - 0.85) * 2 * math.pi / 1.1)), a))
    a = ease((t - 1.3) / 0.4)
    if a > 0: fr.alpha_composite(fade(prof, a))
    fr.convert("RGB").save(f"{out}/{i:04d}.png")
print("frames", i + 1)
