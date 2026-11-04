from PIL import Image, ImageDraw, ImageFont, ImageFilter
import sys
D = "/usr/share/fonts/truetype/dejavu/"
VARIANTS = {
    "tiktok_dzen": "НАЙДИ В ПОИСКЕ TELEGRAM",
    "youtube": "TELEGRAM · ССЫЛКА В ПРОФИЛЕ",
    "vk": "ССЫЛКА НА КАНАЛ — В ОПИСАНИИ",
}
W, H = 1080, 1920
Y = (251, 191, 0)

def spaced(draw, xy_center, text, font, fill, spacing):
    widths = [draw.textlength(c, font=font) for c in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = xy_center[0] - total / 2
    for c, w in zip(text, widths):
        draw.text((x, xy_center[1]), c, font=font, fill=fill, anchor="lm")
        x += w + spacing

for name, small in VARIANTS.items():
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # dark plate hiding the original pulsing button, feathered edges
    plate = Image.new("L", (W, H), 0)
    ImageDraw.Draw(plate).rounded_rectangle((40, 1212, 1040, 1488), 60, fill=255)
    plate = plate.filter(ImageFilter.GaussianBlur(14))
    im.paste((11, 9, 5, 255), (0, 0), plate)
    # glow
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle((100, 1262, 980, 1428), 34, fill=Y + (150,))
    glow = glow.filter(ImageFilter.GaussianBlur(22))
    im = Image.alpha_composite(im, glow)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((94, 1256, 986, 1434), 38, outline=Y, width=6)
    d.rounded_rectangle((108, 1270, 972, 1420), 28, fill=Y)
    # text
    fs = ImageFont.truetype(D + "DejaVuSans-Bold.ttf", 30)
    spaced(d, (540, 1304), small, fs, (25, 18, 0), 3)
    fb = ImageFont.truetype(D + "DejaVuSans-BoldOblique.ttf", 70)
    d.text((540, 1370), "@delaidelojob", font=fb, fill=(10, 8, 0), anchor="mm")
    im.save(f"ov_{name}.png")
print("ok")
