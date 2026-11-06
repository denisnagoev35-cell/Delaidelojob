"""Overlay layer for the voiced video: badge, watermark, word-by-word subtitles, per-platform endcard.
Writes raw RGBA frames (30 fps) to stdout for ffmpeg."""
import sys, os, re
from PIL import Image, ImageDraw, ImageFont, ImageFilter

D = os.path.dirname(os.path.abspath(__file__))
F = D + "/tools/fonts/"
W, H, FPS = 1080, 1920, 30
Y = (255, 198, 3)
VAR = sys.argv[1]; DUR = float(sys.argv[2]); PREVIEW = sys.argv[3:] if len(sys.argv) > 3 else None
EC_START = 25.47      # endcard start in the user's edit
EC_FROM = 26.0        # replace endcard from here (after its fade-in)

def fnt(n, s): return ImageFont.truetype(F + n, s)

# ---------- badge (top-left) ----------
def make_badge():
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0, x1, y1 = 46, 108, 388, 192
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0, x1, y1), 42, fill=(0, 0, 0, 120))
    im = Image.alpha_composite(im, sh)
    logo = Image.open(D + "/tools/banners/logo.png").convert("RGBA").resize((76, 76), Image.LANCZOS)
    im.alpha_composite(logo, (x0 + 4, y0 + 4))
    d = ImageDraw.Draw(im)
    d.text((x0 + 90, y0 + 14), "ДЕЛАЙ ДЕЛО JOB", font=fnt("Montserrat-BlackItalic.ttf", 29), fill="white")
    d.text((x0 + 90, y0 + 48), "@delaidelojob", font=fnt("Montserrat-ExtraBold.ttf", 23), fill=Y)
    return im
BADGE = make_badge()

# ---------- watermark ----------
def make_wm(cy):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f = fnt("Montserrat-ExtraBold.ttf", 38)
    d.text((W / 2, cy), "@delaidelojob · ДЕЛАЙ ДЕЛО JOB", font=f, fill=(255, 255, 255, 46), anchor="mm")
    return im
WM = [(0.0, 7.9, make_wm(930)), (7.9, 13.8, make_wm(700)), (13.8, 19.6, make_wm(1120)), (19.6, EC_FROM, make_wm(860))]

# ---------- subtitles ----------
PAUSES = [(1.58, 1.9), (2.6, 2.72), (3.06, 3.2), (3.58, 3.72), (3.88, 4.0), (4.04, 4.18), (5.1, 5.54), (6.1, 6.28),
          (6.42, 6.58), (6.66, 7.08), (9.46, 9.58), (10.52, 10.66), (11.12, 11.28), (12.34, 14.08), (14.54, 14.68),
          (14.9, 15.1), (16.0, 16.28), (16.96, 17.08), (17.44, 17.56), (19.5, 20.02), (20.82, 20.96), (21.92, 22.18),
          (22.84, 23.2), (25.14, 25.26), (27.2, 27.4), (28.28, 28.68), (29.28, 29.42), (30.68, 30.9), (31.66, 31.82),
          (33.16, 33.3), (33.44, 33.62)]
# (start, end of speech, text) — lines split by "/", *word* = yellow
CHUNKS = [
    (1.90, 4.04, "ТРИДЦАТЬ ШЕСТЬ ЧАСОВ / *СТАЖИРОВКИ*."),
    (4.18, 5.10, "СТОЙКА *БЛЕСТИТ*,"),
    (5.54, 6.66, "В КАРМАНАХ *ПУСТО*."),
    (7.08, 8.51, "А В КОНЦЕ НАЧАЛЬНИК / ГОВОРИТ:"),
    (8.51, 9.46, "«ТЫ НАМ НЕ *ПОДОШЁЛ*»."),
    (9.58, 11.12, "А СТАЖИРОВКА У НАС / *БЕСПЛАТНАЯ*."),
    (11.28, 12.34, "СПАСИБО ЗА *УБОРКУ*."),
    (14.08, 16.00, "ИЗ УШЕЙ *ПАР*, / В КАРМАНЕ *ВЕТЕР*."),
    (16.28, 17.44, "И ТУТ ДРУГ / КИДАЕТ *КАНАЛ*,"),
    (17.56, 19.50, "ГДЕ ПРО ОПЛАТУ / ПИШУТ *СРАЗУ*:"),
    (20.02, 21.92, "*НОВЫЙ* *БАР*, / ПЕРВАЯ СМЕНА,"),
    (22.18, 22.84, "И *КОНВЕРТ*."),
    (23.20, 25.14, "ОКАЗЫВАЕТСЯ, / ТАК ТОЖЕ *МОЖНО*."),
    (25.26, 27.20, "ВАКАНСИИ С *ЗАРПЛАТОЙ* / В ТЕЛЕГРАМ —"),
    (27.40, 28.28, "*ДЕЛАЙ* *ДЕЛО* *JOB*."),
    (28.68, 30.68, "А ТЕБЯ КИДАЛИ / НА *СТАЖИРОВКЕ*?"),
    (30.90, 31.66, "ПИШИ В *КОММЕНТАХ*,"),
    (31.82, 33.72, "СКОЛЬКО СМЕН ОТРАБОТАЛ / *БЕСПЛАТНО*!"),
]
SUB_F = "Montserrat-ExtraBoldItalic.ttf"

def syl(w):
    n = len(re.findall(r"[АЕЁИОУЫЭЮЯAEIOU]", w.upper()))
    return max(n, 1)

def word_times(a, b, words):
    """spread word onsets over voiced time between a and b (skipping pauses), by syllables"""
    segs = []; cur = a
    for p0, p1 in PAUSES:
        if p1 <= a or p0 >= b: continue
        if p0 > cur: segs.append((cur, p0))
        cur = max(cur, p1)
    if cur < b: segs.append((cur, b))
    tot = sum(e - s for s, e in segs)
    sy = [syl(w) for w in words]; S = sum(sy)
    out = []; acc = 0
    for k in sy:
        pos = acc / S * tot; acc += k
        for s, e in segs:
            if pos <= e - s + 1e-6: out.append(s + pos); break
            pos -= e - s
    return out

def build_chunk(text):
    """returns list of (word_img, (x,y)) per word in reading order"""
    lines = [l.strip() for l in text.split("/")]
    size = 62
    f = fnt(SUB_F, size)
    meas = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    def lw(line, f): return meas.textlength(line.replace("*", ""), font=f)
    while max(lw(l, f) for l in lines) > 960: size -= 2; f = fnt(SUB_F, size)
    lh = int(size * 1.12)
    words = []
    base = 1505 if len(lines) == 2 else 1540
    for li, line in enumerate(lines):
        x = (W - lw(line, f)) / 2; y = base + li * lh
        for tok in line.split(" "):
            hl = "*" in tok
            parts = re.split(r"(\*[^*]+\*)", tok)
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img); x0 = x
            for p in parts:
                if not p: continue
                c = Y if p.startswith("*") else (255, 255, 255)
                t = p.strip("*")
                d.text((x, y), t, font=f, fill=c)
                x += d.textlength(t, font=f)
            words.append((tok.replace("*", ""), img, (x0, y)))
            x += meas.textlength(" ", font=f)
    return words

def shadowed(img):
    a = img.split()[3]
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(9)).point(lambda v: min(255, int(v * 1.6))))
    sh2 = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sh2.putalpha(a.filter(ImageFilter.GaussianBlur(2)).point(lambda v: int(v * 0.8)))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 4)); out.alpha_composite(sh2, (0, 3)); out.alpha_composite(img)
    return out

SUBS = []
for i, (a, b, text) in enumerate(CHUNKS):
    ws = build_chunk(text)
    times = word_times(a, b, [w for w, _, _ in ws])
    nxt = CHUNKS[i + 1][0] if i + 1 < len(CHUNKS) else DUR + 1
    show_end = min(nxt - 0.04, b + 0.6)
    imgs = []
    for (w, img, _), t in zip(ws, times):
        imgs.append((t - 0.04, shadowed(img)))
    SUBS.append((a - 0.06, show_end, imgs))

def fade(im, k):
    if k >= 1: return im
    r = im.copy(); r.putalpha(r.split()[3].point(lambda v: int(v * k))); return r

EC = {}
def endcard(t):
    i = min(int((t - EC_START) * 24), 249)
    if i not in EC:
        EC.clear(); EC[i] = Image.open(f"{D}/ecn_{VAR}/{i:04d}.png").convert("RGBA")
    return EC[i]

def frame(t):
    fr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if t >= EC_FROM:
        fr = endcard(t).copy()
    for a, b, wm in WM:
        if a <= t < b: fr.alpha_composite(wm)
    for a, b, imgs in SUBS:
        if a <= t < b:
            out_k = min(1, (b - t) / 0.12)
            for ts, img in imgs:
                if t >= ts:
                    k = min(1, (t - ts) / 0.12) * out_k
                    fr.alpha_composite(fade(img, k))
    fr.alpha_composite(BADGE)
    return fr

if PREVIEW:
    for t in PREVIEW:
        frame(float(t)).save(f"{D}/pv_{t}.png")
else:
    n = int(round(DUR * FPS)); out = sys.stdout.buffer
    for i in range(n):
        out.write(frame(i / FPS).tobytes())
