"""Clip 2 final: new hook (stamp), word-by-word subtitles, per-platform endcard swap. Writes frames to OUT."""
import sys, os, re
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
D = os.path.dirname(os.path.abspath(__file__)); F = D + "/tools/fonts/"
V4 = D + "/../v4"
W, H, FPS = 1080, 1920, 30
Y = (255, 198, 3)
VAR = sys.argv[1]; OUT = sys.argv[2]; os.makedirs(OUT, exist_ok=True)
PREVIEW = [int(x) for x in sys.argv[3:]] if len(sys.argv) > 3 else None
SRC = V4 + "/fr"; N = len(os.listdir(SRC))
HOOK_N = 41; EC_FROM = 772                       # endcard fully visible from this frame (1-based)
EC_DIR = {"vk": "eb_vk_dzen", "youtube": "eb_shorts_tiktok", "tiktok_dzen": "eb_shorts_tiktok"}[VAR]

def fnt(n, s): return ImageFont.truetype(F + n, s)
# ---------- hook: stamp hits the forehead, punch zoom ----------
hook_txt = Image.open(D + "/ov2/hook.png").convert("RGBA")
badge = Image.open(D + "/ov2/badge.png").convert("RGBA"); wm0 = Image.open(D + "/ov2/wm0.png").convert("RGBA")
def hook_frame(i):
    im = Image.open(f"{V4}/st/{i+1:04d}.png").convert("RGB"); w, h = im.size
    k = min(i / 9, 1); k = 1 - (1 - k) ** 3; z = 1.30 + 0.30 * k
    if 9 <= i <= 12: z += 0.04 * (1 - abs(i - 10.5) / 1.5)     # tiny shake on the hit
    cw, ch = w / z, h / z; cx, cy = 0.42 * w, 0.52 * h
    x0 = min(max(cx - cw / 2, 0), w - cw); y0 = min(max(cy - ch / 2, 0), h - ch)
    fr = im.crop((round(x0), round(y0), round(x0 + cw), round(y0 + ch))).resize((W, H), Image.LANCZOS).convert("RGBA")
    for o in (wm0, hook_txt, badge): fr.alpha_composite(o)
    return fr

# ---------- endcard swap (match the user's frame to our vk endcard, take the same index from the variant) ----------
ECV = sorted(os.listdir(D + "/eb_vk_dzen"))
small_vk = [np.asarray(Image.open(f"{D}/eb_vk_dzen/{f}").convert("L").resize((54, 96)), dtype=np.float32) for f in ECV]
def ec_index(fr):
    a = np.asarray(fr.convert("L").resize((54, 96)), dtype=np.float32)
    return int(np.argmin([((a - b) ** 2).mean() for b in small_vk]))

# ---------- subtitles ----------
SPEECH = [(1.46, 2.76), (2.96, 3.84), (4.06, 6.36), (6.54, 7.92), (8.20, 11.04), (11.24, 11.70), (12.28, 12.78), (13.30, 13.78),
          (14.80, 16.14), (16.48, 17.18), (17.34, 17.78), (18.10, 20.34), (20.78, 21.76), (22.04, 22.78), (22.94, 25.36),
          (25.78, 26.24), (26.42, 27.52), (27.68, 27.98), (28.24, 30.20), (30.62, 32.02), (32.40, 33.18)]
CHUNKS = [
    (1.46, 2.76, "ОБЕЩАЛИ *6* *000*,"),
    (2.96, 5.79, "А В КОНВЕРТЕ — / *МОЛЬ* И ДВЕ *МОНЕТКИ*."),
    (5.79, 6.36, "*ШТРАФЫ*!"),
    (6.54, 7.92, "ОПОЗДАЛ НА *2* *МИНУТЫ*,"),
    (8.20, 11.04, "РАЗБИЛ *КОЛБУ*, / «КИСЛОЕ *ЛИЦО*»."),
    (11.24, 11.70, "СПРОСИЛ: «ЗА ЧТО?»"),
    (12.28, 12.78, "*ШТРАФ*."),
    (13.30, 13.78, "ЗА *ВОПРОС*."),
    (14.80, 16.14, "ЗЛОЙ, МОКРЫЙ, / *БЕЗ* *ДЕНЕГ*."),
    (16.48, 17.78, "ОТКРЫЛ *БОТА*, А ТАМ"),
    (18.10, 20.34, "КАЛЬЯНЩИК — / *6–8* *ТЫСЯЧ* ЗА СМЕНУ!"),
    (20.78, 22.78, "НОВЫЙ ЛАУНЖ, / *НОРМАЛЬНАЯ* *СТАВКА*."),
    (22.94, 25.36, "ЗНАТЬ СЕБЕ ЦЕНУ — / *ВЫГОДНО*."),
    (25.78, 27.52, "УЗНАЙ, СКОЛЬКО / ДОЛЖНЫ ПЛАТИТЬ *ТЕБЕ* —"),
    (27.68, 30.20, "БОТ *«ЗАРПЛАТЫ* *HORECA»* / В ТЕЛЕГРАМЕ."),
    (30.62, 32.02, "А ТЕБЯ ЗА ЧТО / *ШТРАФОВАЛИ*?"),
    (32.40, 33.18, "ПИШИ В *КОММЕНТАХ*!"),
]
SUB_F = "Montserrat-ExtraBoldItalic.ttf"
M = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
def syl(w): return max(len(re.findall(r"[АЕЁИОУЫЭЮЯAEIOU0-9]", w.upper())), 1)
def word_times(a, b, words):
    segs = [(max(s, a), min(e, b)) for s, e in SPEECH if e > a and s < b] or [(a, b)]
    tot = sum(e - s for s, e in segs); sy = [syl(w) for w in words]; S = sum(sy); out = []; acc = 0
    for k in sy:
        pos = acc / S * tot * 0.85; acc += k
        for s, e in segs:
            if pos <= e - s + 1e-6: out.append(s + pos); break
            pos -= e - s
    return out
def build_chunk(text, base_single, base_double):
    lines = [l.strip() for l in text.split("/")]; size = 62; f = fnt(SUB_F, size)
    lw = lambda line, f: M.textlength(line.replace("*", ""), font=f)
    while max(lw(l, f) for l in lines) > 960: size -= 2; f = fnt(SUB_F, size)
    lh = int(size * 1.12); words = []; base = base_double if len(lines) == 2 else base_single
    for li, line in enumerate(lines):
        x = (W - lw(line, f)) / 2; y = base + li * lh
        for tok in line.split(" "):
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
            for p in re.split(r"(\*[^*]+\*)", tok):
                if not p: continue
                c = Y if p.startswith("*") else (255, 255, 255); t = p.strip("*")
                d.text((x, y), t, font=f, fill=c); x += d.textlength(t, font=f)
            words.append((tok.replace("*", ""), img)); x += M.textlength(" ", font=f)
    return words
def shadowed(img):
    a = img.split()[3]
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sh.putalpha(a.filter(ImageFilter.GaussianBlur(9)).point(lambda v: min(255, int(v * 1.6))))
    sh2 = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sh2.putalpha(a.filter(ImageFilter.GaussianBlur(2)).point(lambda v: int(v * 0.8)))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0)); out.alpha_composite(sh, (0, 4)); out.alpha_composite(sh2, (0, 3)); out.alpha_composite(img); return out
SUBS = []
for i, (a, b, text) in enumerate(CHUNKS):
    on_ec = a >= 25.6
    ws = build_chunk(text, 1660 if on_ec else 1540, 1630 if on_ec else 1505)
    times = word_times(a, b, [w for w, _ in ws])
    sh = 0.5 if text.startswith(('НОВЫЙ ЛАУНЖ', 'ЗНАТЬ СЕБЕ')) else 0.0
    times = [x + sh for x in times]; a += sh; b += sh
    nxt = CHUNKS[i + 1][0] if i + 1 < len(CHUNKS) else 99
    SUBS.append((a - 0.1, b + 0.5, [(t - 0.1, shadowed(img)) for (w, img), t in zip(ws, times)]))
for i in range(len(SUBS) - 1):
    a0, e0, im0 = SUBS[i]; SUBS[i] = (a0, min(e0, SUBS[i + 1][0] - 0.02), im0)
def fade(im, k):
    if k >= 1: return im
    r = im.copy(); r.putalpha(r.split()[3].point(lambda v: int(v * k))); return r

def frame(n):  # n: 1-based frame index
    t = (n - 1) / FPS
    if n <= HOOK_N: return hook_frame(n - 1)
    fr = Image.open(f"{SRC}/{n:04d}.png").convert("RGBA")
    if n >= EC_FROM and VAR != "vk":
        fr = Image.open(f"{D}/{EC_DIR}/{ECV[ec_index(fr)]}").convert("RGBA")
    for a, b, imgs in SUBS:
        if a <= t < b:
            ok = min(1, (b - t) / 0.12)
            for ts, img in imgs:
                if t >= ts: fr.alpha_composite(fade(img, min(1, (t - ts) / 0.06) * ok))
    return fr

for n in (PREVIEW or range(1, N + 1)):
    frame(n).convert("RGB").save(f"{OUT}/{n:04d}.png")
print("ok", VAR)
