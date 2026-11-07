"""Ролик 2 «Обещали 6 000»: хук со штампом, пословные субтитры по голосу, концовка под площадку.
SRC=чистый.mov HOOK_SRC=старый_vk.mp4 python3 -I clip2_v2_subs.py <vk|youtube|tiktok_dzen|vk_ads> <out.mp4> [кадры для превью...]
Запуск из рабочей папки, где лежит ссылка tools -> ветка tools и папки eb_* от endcard_bot.py.
Тайминги слов размечены вручную по спектрограмме голоса (07.11)."""
import sys, os, re, subprocess
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter

D = os.path.dirname(os.path.abspath(__file__)); F = D + "/tools/fonts/"
W, H, FPS = 1080, 1920, 30
Y = (255, 198, 3)
SRC = os.environ.get("SRC", "src.mov")                                   # чистый ролик Дениса (озвучка+музыка, без субтитров)
HOOK_SRC = os.environ.get("HOOK_SRC", "obeshchali_vk_old.mp4")  # ролик со старым хуком: берём кадры «печать на лоб»
HOOK_N = 41                                                      # кадры 1..41 — хук
EC_FROM = 772                                                    # с этого кадра концовка полностью на экране
VAR = sys.argv[1]; OUT = sys.argv[2]
PREVIEW = [int(x) for x in sys.argv[3:]] if len(sys.argv) > 3 else None
EC_DIR = {"vk": None, "youtube": "eb_shorts_tiktok", "tiktok_dzen": "eb_shorts_tiktok", "vk_ads": "eb_vk_ads"}[VAR]

# ---------- субтитры: начало каждого слова в голосе (сек), размечено по спектрограмме ----------
LEAD = 0.10   # слово появляется чуть раньше голоса
CHUNKS = [  # (конец речи фразы, текст, начала слов по порядку токенов)
    (2.85, "ОБЕЩАЛИ *6* *000*,", [1.43, 2.00, 2.37]),
    (5.62, "А В КОНВЕРТЕ — / *МОЛЬ* И ДВЕ *МОНЕТКИ*.", [2.93, 3.06, 3.13, 3.80, 4.03, 4.35, 4.50, 4.69]),
    (6.42, "*ШТРАФЫ*!", [5.68]),
    (7.98, "ОПОЗДАЛ НА *2* *МИНУТЫ*,", [6.50, 6.92, 7.08, 7.29]),
    (10.48, "РАЗБИЛ *КОЛБУ*, / «КИСЛОЕ *ЛИЦО*».", [8.20, 8.86, 9.45, 10.10]),
    (11.80, "СПРОСИЛ: «ЗА ЧТО?»", [10.50, 11.17, 11.37]),
    (12.88, "*ШТРАФ*.", [12.25]),
    (13.95, "ЗА *ВОПРОС*.", [13.27, 13.38]),
    (16.20, "ЗЛОЙ, МОКРЫЙ, / *БЕЗ* *ДЕНЕГ*.", [14.76, 15.20, 15.69, 15.92]),
    (17.85, "ОТКРЫЛ *БОТА*, А ТАМ", [16.46, 16.85, 17.50, 17.62]),
    (20.45, "КАЛЬЯНЩИК — / *6–8* *ТЫСЯЧ* ЗА СМЕНУ!", [18.10, 18.60, 18.76, 19.43, 19.88, 20.00]),
    (23.45, "НОВЫЙ ЛАУНЖ, / *НОРМАЛЬНАЯ* *СТАВКА*.", [20.72, 21.10, 21.97, 22.80]),
    (25.42, "ЗНАТЬ СЕБЕ ЦЕНУ — / *ВЫГОДНО*.", [23.88, 24.17, 24.55, 24.85, 24.93]),
    (28.05, "УЗНАЙ, СКОЛЬКО / ДОЛЖНЫ ПЛАТИТЬ *ТЕБЕ* —", [25.80, 26.25, 26.67, 27.21, 27.64, 27.95]),
    (30.28, "БОТ *«ЗАРПЛАТЫ* *HORECA»* / В ТЕЛЕГРАМЕ.", [28.13, 28.47, 29.08, 29.56, 29.62]),
    (32.08, "А ТЕБЯ ЗА ЧТО / *ШТРАФОВАЛИ*?", [30.60, 30.70, 31.03, 31.20, 31.45]),
    (33.30, "ПИШИ В *КОММЕНТАХ*!", [32.39, 32.69, 32.75]),
]
SUB_F = "Montserrat-ExtraBoldItalic.ttf"
fnt = lambda n, s: ImageFont.truetype(F + n, s)
M = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

def build_chunk(text, base_single, base_double):   # стиль как в clip2_final.py
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
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0)); out.alpha_composite(sh, (0, 4)); out.alpha_composite(sh2, (0, 3)); out.alpha_composite(img)
    bb = out.getbbox(); return out.crop(bb), bb[:2]

SUBS = []
for end, text, times in CHUNKS:
    on_ec = times[0] >= 25.6
    ws = build_chunk(text, 1660 if on_ec else 1540, 1630 if on_ec else 1505)
    assert len(ws) == len(times), (text, [w for w, _ in ws])
    SUBS.append([times[0] - LEAD, end + 0.5, [(t - LEAD, *shadowed(img)) for (w, img), t in zip(ws, times)]])
for i in range(len(SUBS) - 1):
    SUBS[i][1] = min(SUBS[i][1], SUBS[i + 1][0] - 0.02)

def fade(im, k):
    if k >= 1: return im
    r = im.copy(); r.putalpha(r.split()[3].point(lambda v: int(v * k))); return r

# ---------- концовка под площадку: подбираем кадр нашей vk-концовки и берём тот же из варианта ----------
if EC_DIR:
    ECV = sorted(os.listdir(D + "/eb_vk_dzen"))
    small_vk = [np.asarray(Image.open(f"{D}/eb_vk_dzen/{f}").convert("L").resize((54, 96)), dtype=np.float32) for f in ECV]
    def ec_index(fr):
        a = np.asarray(fr.convert("L").resize((54, 96)), dtype=np.float32)
        return int(np.argmin([((a - b) ** 2).mean() for b in small_vk]))

src = cv2.VideoCapture(SRC); hook = cv2.VideoCapture(HOOK_SRC)
N = int(src.get(cv2.CAP_PROP_FRAME_COUNT))
def rgb(cap): ok, x = cap.read(); return Image.fromarray(cv2.cvtColor(x, cv2.COLOR_BGR2RGB)) if ok else None

def frame(n, base, hk):
    t = (n - 1) / FPS
    if n <= HOOK_N: return hk                      # хук без субтитров, как раньше
    fr = base.convert("RGBA")
    if EC_DIR and n >= EC_FROM:
        fr = Image.open(f"{D}/{EC_DIR}/{ECV[ec_index(fr)]}").convert("RGBA")
    for a, b, imgs in SUBS:
        if a <= t < b:
            ok = min(1, (b - t) / 0.12)
            for ts, img, xy in imgs:
                if t >= ts: fr.alpha_composite(fade(img, min(1, (t - ts) / 0.06) * ok), xy)
    return fr.convert("RGB")

if PREVIEW:
    os.makedirs(OUT, exist_ok=True)
for n in range(1, N + 1):
    base = rgb(src); hk = rgb(hook) if n <= HOOK_N else None
    if base is None: break
    if PREVIEW:
        if n in PREVIEW: frame(n, base, hk).save(f"{OUT}/{n:04d}.png")
        if n >= max(PREVIEW): break
        continue
    if n == 1:
        enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                                "-i", SRC, "-map", "0:v", "-map", "1:a", "-af", "loudnorm=I=-14:TP=-1:LRA=11,aresample=48000",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", OUT], stdin=subprocess.PIPE)
    enc.stdin.write(np.asarray(frame(n, base, hk)).tobytes())
if not PREVIEW:
    enc.stdin.close(); enc.wait(); print("ok", VAR, OUT)
