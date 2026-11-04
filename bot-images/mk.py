from PIL import Image, ImageDraw, ImageFont, ImageFilter
D="/usr/share/fonts/truetype/dejavu/"
Y=(255,198,3); BK=(11,11,11)
def F(n,s): return ImageFont.truetype(D+n,s)
def streaks(im,alpha=40):
    lay=Image.new("RGBA",im.size,(0,0,0,0)); d=ImageDraw.Draw(lay)
    w,h=im.size
    for i,y in enumerate(range(-h,2*h,46)):
        d.line([(0,y),(w,y-int(w*0.25))],fill=Y+(alpha if i%3 else alpha//2,),width=3)
    return Image.alpha_composite(im,lay)
# avatar 640x640
a=Image.new("RGBA",(640,640),BK+(255,)); a=streaks(a,28)
d=ImageDraw.Draw(a)
g=Image.new("RGBA",a.size,(0,0,0,0)); ImageDraw.Draw(g).ellipse((120,90,520,490),fill=Y+(110,)); g=g.filter(ImageFilter.GaussianBlur(40)); a=Image.alpha_composite(a,g); d=ImageDraw.Draw(a)
d.ellipse((150,110,490,450),fill=Y)
d.text((320,282),"₽",font=F("DejaVuSans-Bold.ttf",250),fill=BK,anchor="mm")
d.text((320,525),"ЗАРПЛАТЫ",font=F("DejaVuSans-BoldOblique.ttf",58),fill=(245,245,245),anchor="mm")
d.text((320,578),"HoReCa",font=F("DejaVuSans-BoldOblique.ttf",36),fill=Y,anchor="mm")
a.convert("RGB").save("avatar_640.png")
# description picture 640x360
b=Image.new("RGBA",(640,360),BK+(255,)); b=streaks(b,30); d=ImageDraw.Draw(b)
d.text((36,70),"СКОЛЬКО",font=F("DejaVuSans-BoldOblique.ttf",44),fill=(245,245,245),anchor="ls")
d.text((36,124),"ТЕБЕ ДОЛЖНЫ",font=F("DejaVuSans-BoldOblique.ttf",44),fill=(245,245,245),anchor="ls")
d.text((36,178),"ПЛАТИТЬ?",font=F("DejaVuSans-BoldOblique.ttf",44),fill=Y,anchor="ls")
d.text((38,226),"Кальянщик · бармен · бариста · официант",font=F("DejaVuSans-Bold.ttf",17),fill=(200,200,200),anchor="ls")
d.text((38,252),"повар · хостес · хозперсонал · курьер",font=F("DejaVuSans-Bold.ttf",17),fill=(200,200,200),anchor="ls")
d.rounded_rectangle((36,288,350,336),14,fill=Y)
d.text((193,312),"Узнать за 30 секунд",font=F("DejaVuSans-Bold.ttf",22),fill=BK,anchor="mm")
d.ellipse((480,46,608,174),fill=Y); d.text((544,112),"₽",font=F("DejaVuSans-Bold.ttf",96),fill=BK,anchor="mm")
d.text((544,200),"Москва и МО",font=F("DejaVuSans-Bold.ttf",17),fill=(200,200,200),anchor="mm")
b.convert("RGB").save("description_640x360.png")
# preview avatar as circle
m=Image.new("L",(640,640),0); ImageDraw.Draw(m).ellipse((0,0,640,640),fill=255)
c=Image.new("RGB",(640,640),(40,40,40)); c.paste(a.convert("RGB"),(0,0),m); c.save("avatar_circle_preview.png")
