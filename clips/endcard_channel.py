from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os, sys, math
W, H = 1080, 1920
D = os.path.dirname(os.path.abspath(__file__))
F = D + "/tools/fonts/"
Y = (255, 198, 3)
VARIANTS = {"tiktok_dzen": "НАЙДИ В ПОИСКЕ TELEGRAM", "youtube": "TELEGRAM · ССЫЛКА В ПРОФИЛЕ", "vk": "ССЫЛКА НА КАНАЛ — В ОПИСАНИИ"}
var, out, dur, fps = sys.argv[1], sys.argv[2], float(sys.argv[3]), 24
NOQ = len(sys.argv) > 4 and sys.argv[4] == 'noq'
os.makedirs(out, exist_ok=True)
fBI = lambda s: ImageFont.truetype(F + "Montserrat-BlackItalic.ttf", s)
fEB = lambda s: ImageFont.truetype(F + "Montserrat-ExtraBold.ttf", s)
fB = lambda s: ImageFont.truetype(F + "Montserrat-Bold.ttf", s)
logo = Image.open(D + "/tools/banners/logo.png").convert("RGBA")

# static background with soft yellow glow
bg = Image.new("RGBA", (W, H), (11, 9, 5, 255))
g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(g).ellipse((140, 420, 940, 1220), fill=(255, 198, 3, 55))
bg = Image.alpha_composite(bg, g.filter(ImageFilter.GaussianBlur(160)))

def ease(t): t = max(0, min(1, t)); return 1 - (1 - t) ** 3
def layer_text(lines, y, size, colors, spacing=1.15):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    for i, (t, c) in enumerate(zip(lines, colors)):
        d.text((W / 2, y + i * size * spacing), t, font=fBI(size), fill=c, anchor="mm")
    return im
title = layer_text(["ЗАРПЛАТА —", "СРАЗУ В ВАКАНСИИ"], 960, 78, ["white", Y])
sub = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(sub)
txt = "ВАКАНСИИ HORECA · МОСКВА И МО"; f = fEB(30); x = W / 2 - (sum(d.textlength(c, font=f) for c in txt) + 4 * (len(txt) - 1)) / 2
for c in txt: d.text((x, 1130), c, font=f, fill=(170, 160, 140), anchor="lm"); x += d.textlength(c, font=f) + 4
q = layer_text(["А тебя кидали на стажировке?", "Пиши в комментах"], 1590, 44, [(235, 230, 220), Y], 1.35)

def button(pulse):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    a = int(110 + 90 * pulse)
    ImageDraw.Draw(glow).rounded_rectangle((100, 1262, 980, 1428), 34, fill=Y + (a,))
    im = Image.alpha_composite(im, glow.filter(ImageFilter.GaussianBlur(26)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((94, 1256, 986, 1434), 38, outline=Y, width=6)
    d.rounded_rectangle((108, 1270, 972, 1420), 28, fill=Y)
    small = VARIANTS[var]; f = fEB(30); x = W / 2 - (sum(d.textlength(c, font=f) for c in small) + 3 * (len(small) - 1)) / 2
    for c in small: d.text((x, 1306), c, font=f, fill=(25, 18, 0), anchor="lm"); x += d.textlength(c, font=f) + 3
    d.text((W / 2, 1372), "@delaidelojob", font=fBI(72), fill=(10, 8, 0), anchor="mm")
    return im

def fade(im, a):
    if a >= 1: return im
    r = im.copy(); r.putalpha(r.split()[3].point(lambda v: int(v * a))); return r

n = int(round(dur * fps))
for i in range(n):
    t = i / fps
    fr = bg.copy()
    # logo pop
    s = ease(t / 0.45); sc = 0.55 + 0.45 * s + 0.06 * math.sin(min(1, t / 0.45) * math.pi)
    ls = int(380 * sc); L = fade(logo.resize((ls, ls), Image.LANCZOS), s)
    fr.alpha_composite(L, (W // 2 - ls // 2, 610 - ls // 2))
    a = ease((t - 0.35) / 0.4)
    if a > 0:
        tl = fade(title, a); off = int(40 * (1 - a)); fr.alpha_composite(tl, (0, off))
    a = ease((t - 0.6) / 0.4)
    if a > 0: fr.alpha_composite(fade(sub, a))
    a = ease((t - 0.85) / 0.35)
    if a > 0:
        p = 0.5 + 0.5 * math.sin((t - 0.85) * 2 * math.pi / 1.1)
        b = button(p)
        if p > 0.5:  # slight scale pulse
            k = 1 + 0.025 * (p - 0.5) * 2
            bw, bh = int(W * k), int(H * k)
            b = b.resize((bw, bh), Image.BILINEAR).crop(((bw - W) // 2, int(1345 * k) - 1345, (bw - W) // 2 + W, int(1345 * k) - 1345 + H))
        fr.alpha_composite(fade(b, a))
    a = ease((t - 1.6) / 0.4)
    if a > 0 and not NOQ: fr.alpha_composite(fade(q, a))
    fr.convert("RGB").save(f"{out}/{i:04d}.png")
print("frames", n)
