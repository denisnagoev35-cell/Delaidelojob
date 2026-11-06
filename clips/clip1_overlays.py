from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os, sys
W, H = 1080, 1920
F = os.path.join(os.path.dirname(__file__), "tools/fonts/")
Y = (255, 198, 3)
def font(n, s): return ImageFont.truetype(F + n, s)
BI = "Montserrat-BlackItalic.ttf"; EB = "Montserrat-ExtraBold.ttf"; SB = "Montserrat-SemiBold.ttf"; B = "Montserrat-Bold.ttf"
OUT = sys.argv[1]

def caption(lines, name, y0=210, size=78):
    """lines: list of [(text, color)] segments; white/yellow with thick black stroke"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f = font(BI, size); y = y0
    for segs in lines:
        tw = sum(d.textlength(t, font=f) for t, _ in segs)
        x = (W - tw) / 2
        for t, c in segs:
            d.text((x, y), t, font=f, fill=c, stroke_width=9, stroke_fill=(12, 10, 5))
            x += d.textlength(t, font=f)
        y += int(size * 1.18)
    sh = im.copy().split()[3].filter(ImageFilter.GaussianBlur(10))
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0)); base.paste((0, 0, 0, 120), (0, 0), sh)
    Image.alpha_composite(base, im).save(f"{OUT}/{name}.png")

def chip(text, name, y, icon=(229, 57, 53)):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f = font(EB, 38); tw = d.textlength(text, font=f)
    w = int(tw + 130); x1 = W - 50; x0 = x1 - w
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y + 8, x1, y + 96), 30, fill=(0, 0, 0, 110))
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(10))); d = ImageDraw.Draw(im)
    d.rounded_rectangle((x0, y, x1, y + 88), 30, fill=(255, 255, 255, 250))
    d.ellipse((x0 + 22, y + 21, x0 + 68, y + 67), fill=icon)
    d.line((x0 + 34, y + 33, x0 + 56, y + 55), fill="white", width=6); d.line((x0 + 56, y + 33, x0 + 34, y + 55), fill="white", width=6)
    d.text((x0 + 90, y + 44), text, font=f, fill=(25, 25, 30), anchor="lm")
    im.save(f"{OUT}/{name}.png")

def bubble(name):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0, x1, y1 = 150, 1300, 1010, 1570
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + 10, x1, y1 + 10), 36, fill=(0, 0, 0, 120))
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(12))); d = ImageDraw.Draw(im)
    d.rounded_rectangle((x0, y0, x1, y1), 36, fill=(255, 255, 255, 252))
    d.ellipse((40, y0 + 20, 130, y0 + 110), fill=(255, 140, 40), outline="white", width=5)
    d.text((85, y0 + 65), "Д", font=font(EB, 46), fill="white", anchor="mm")
    d.text((x0 + 36, y0 + 30), "Дима", font=font(EB, 34), fill=(36, 120, 230))
    f = font(B, 42)
    d.text((x0 + 36, y0 + 82), "Хватит пахать бесплатно!", font=f, fill=(20, 20, 25))
    d.text((x0 + 36, y0 + 134), "Глянь канал — там оплату", font=f, fill=(20, 20, 25))
    d.text((x0 + 36, y0 + 186), "стажировки пишут сразу", font=f, fill=(20, 20, 25))
    im.save(f"{OUT}/{name}.png")

os.makedirs(OUT, exist_ok=True)
caption([[("ОТРАБОТАЛ ", "white"), ("3 СМЕНЫ", Y)], [("НА СТАЖИРОВКЕ.", "white")], [("УГАДАЙ, СКОЛЬКО", "white")], [("ЗАПЛАТИЛИ", Y)]], "hook", y0=200, size=72)
chip("Ты нам не подошёл", "chip1", 210)
chip("Стажировка не оплачивается", "chip2", 320)
chip("Удачи!", "chip3", 430, icon=(120, 120, 130))
bubble("bubble")
caption([[("СТАЖИРОВКА —", "white")], [("ТОЖЕ СМЕНА.", "white")], [("И ОНА ", "white"), ("ОПЛАЧИВАЕТСЯ", Y)]], "cap4", y0=210, size=68)
print("ok")
