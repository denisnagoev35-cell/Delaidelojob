import sys, os, subprocess
from PIL import Image
src, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)
tmp = out + "_raw"; os.makedirs(tmp, exist_ok=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.15", "-t", "1.4", "-i", src, "-vf", "fps=30", f"{tmp}/%04d.png"], check=True)
fs = sorted(os.listdir(tmp)); n = len(fs)
CX, CY = 0.53, 0.60
for i, f in enumerate(fs):
    im = Image.open(f"{tmp}/{f}").convert("RGB"); W, H = im.size
    t = i / 30; k = min(t / 0.3, 1); k = 1 - (1 - k) ** 3
    z = 1.75 + 0.35 * k
    cw, ch = W / z, H / z
    x0 = min(max(CX * W - cw / 2, 0), W - cw); y0 = min(max(CY * H - ch / 2, 0), H - ch)
    im.crop((round(x0), round(y0), round(x0 + cw), round(y0 + ch))).resize((1080, 1920), Image.LANCZOS).save(f"{out}/{i+1:04d}.png")
print("hook", n)
