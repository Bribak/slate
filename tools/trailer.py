"""slate's 25-second trailer: a title card, the README's screenshots in docs/ panned under a caption each, the "Small and fast"
numbers and an end card, drawn frame by frame with Pillow and encoded by ffmpeg (H.264, 1920x1080, 30 fps).

    python tools/trailer.py OUT.mp4 [--stills]

--stills also saves a frame from each scene next to OUT, for a look before sharing. Run tools/shots.py first when the
screenshots are out of date. Windows only (Segoe UI and Consolas from C:\\Windows\\Fonts); ffmpeg comes from $FFMPEG, the
PATH or Shotcut. The numbers card follows the README's "Small and fast" section, so update both together. The glycan on the
title and end cards is trailer-glycan.png next to this file: GlycoDraw('Neu5Ac(a2-3)Gal(b1-4)[Fuc(a1-3)]GlcNAc', sticker = True).
"""
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent / 'docs'
W, H, FPS = 1920, 1080, 30
BG, FG, DIM, ACCENT = (30, 31, 34), (236, 238, 241), (150, 156, 166), (104, 151, 187)
BOLD, SEMI, LIGHT, MONO = 'segoeuib.ttf', 'seguisb.ttf', 'segoeuisl.ttf', 'consola.ttf'
BOX = (80, 200, 1840, 1040)  # the screenshot panel under the caption; crops below keep its 2.095 aspect
XF = 0.4  # crossfade between scenes, through the background
# seconds, screenshot, crop at the start and at the end (in the screenshot's pixels), caption
PANELS = [
    (3.0, 'motif-search', (500, 40, 2140, 823), (1060, 250, 2140, 766), 'Your glycans, drawn right in the code'),
    (3.0, 'typing', (0, 0, 1156, 552), (124, 0, 1280, 552), 'The next linkage suggested as you type'),
    (3.0, 'glycans', (100, 359, 1400, 978), (160, 400, 1360, 971), 'Hover for class, mass, composition and motifs'),
    (3.4, 'table', (0, 0, 1959, 935), (0, 70, 1700, 881), 'Your abundance tables, every glycan drawn'),
    (3.0, 'motif-search', (0, 0, 2140, 1019), (0, 0, 1500, 714), 'Search all your folders by motif'),
    (3.4, 'console', (0, 560, 1959, 1493), (0, 806, 1500, 1522), 'GlycoDraw, plots and Markdown in the console'),
]
NUMBERS = [('430 MB', 'of RAM with eight files open', 'PyCharm: 4.5 GB'), ('22 ms', 'from keystroke to screen', "the display's own frame time"),
           ('1.8 s', 'to open a double-clicked file', '0.25 s when slate is already open')]
font = lambda name, size: ImageFont.truetype(str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / name), size)
ease = lambda x: 0.5 - 0.5 * math.cos(math.pi * min(max(x, 0), 1))
mix = lambda colour, a: tuple(int(BG[i] + (colour[i] - BG[i]) * a) for i in range(3))  # colour faded in from the background


def background():
    img = Image.new('RGB', (W, H), BG)
    glow = Image.new('L', (W, H), 0)
    ImageDraw.Draw(glow).ellipse((-400, -700, 1400, 500), fill = 40)
    return Image.composite(Image.new('RGB', (W, H), (52, 70, 92)), img, glow.filter(ImageFilter.GaussianBlur(220)))


BACK = background()
GLYCAN = Image.open(HERE / 'trailer-glycan.png').convert('RGBA')


def glycan(img, cy, height, progress):
    """GlycoDraw's drawing, faded in from left to right with a soft edge."""
    g = GLYCAN.resize((round(GLYCAN.width * height / GLYCAN.height), height), Image.LANCZOS)
    soft = 0.35
    ramp = Image.linear_gradient('L').rotate(90).resize((g.width, g.height))  # 0 at the left edge, 255 at the right
    edge = Image.eval(ramp, lambda v: int(max(0, min(1, (progress * (1 + soft) - v / 255) / soft)) * 255))
    alpha = Image.composite(g.getchannel('A'), Image.new('L', g.size, 0), edge)
    img.paste(g, (round((W - g.width) / 2), round(cy - height / 2)), alpha)


def centered(d, y, text, f, fill):
    d.text(((W - d.textlength(text, font = f)) / 2, y), text, font = f, fill = fill)


def title(t):
    img = BACK.copy()
    d = ImageDraw.Draw(img)
    glycan(img, 270, 270, ease(t / 1.1))
    centered(d, 420, 'slate', font(BOLD, 210), FG)
    a = ease((t - 0.6) / 0.6)
    centered(d, 700, 'a small, fast Python editor', font(LIGHT, 64), mix(DIM, a))
    centered(d, 790, 'made for glycobiology', font(SEMI, 64), mix(ACCENT, a))
    return img


def panel(shot, start, end, caption, t, dur):
    """A screenshot in a rounded panel with a shadow, its crop moving from start to end over the scene."""
    img = BACK.copy()
    ImageDraw.Draw(img).text((BOX[0], 62), caption, font = font(SEMI, 76), fill = FG)
    x = ease(t / dur)
    box = [start[i] + (end[i] - start[i]) * x for i in range(4)]
    bw, bh = BOX[2] - BOX[0], BOX[3] - BOX[1]
    crop = shot.resize((bw, bh), Image.LANCZOS, box = box)
    mask = Image.new('L', (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), 22, fill = 255)
    shadow = Image.new('L', (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((BOX[0] + 6, BOX[1] + 14, BOX[2] + 6, BOX[3] + 14), 22, fill = 150)
    img = Image.composite(Image.new('RGB', (W, H), (8, 8, 10)), img, shadow.filter(ImageFilter.GaussianBlur(18)))
    img.paste(crop, BOX[:2], mask)
    return img


def numbers(t):
    img = BACK.copy()
    d = ImageDraw.Draw(img)
    d.text((BOX[0], 62), 'Small and fast', font = font(SEMI, 76), fill = FG)
    for i, (big, what, note) in enumerate(NUMBERS):
        a = ease((t - 0.25 - i * 0.45) / 0.5)
        y = 290 + i * 250 + int((1 - a) * 30)
        d.text((BOX[0] + 40, y), big, font = font(BOLD, 140), fill = mix(ACCENT, a))
        d.text((BOX[0] + 640, y + 28), what, font = font(SEMI, 60), fill = mix(FG, a))
        d.text((BOX[0] + 640, y + 112), note, font = font(LIGHT, 46), fill = mix(DIM, a))
    return img


def end(t):
    img = BACK.copy()
    d = ImageDraw.Draw(img)
    glycan(img, 175, 210, 1)
    centered(d, 320, 'slate', font(BOLD, 170), FG)
    centered(d, 560, 'Free on GitHub · Windows and macOS', font(SEMI, 60), FG)
    centered(d, 650, 'github.com/Bribak/slate', font(SEMI, 60), ACCENT)
    a = ease((t - 0.4) / 0.6)
    cmd = 'pip install git+https://github.com/Bribak/slate'
    f = font(MONO, 48)
    w = d.textlength(cmd, font = f)
    x0, y0 = (W - w) / 2, 820
    d.rounded_rectangle((x0 - 36, y0 - 26, x0 + w + 36, y0 + 78), 16, fill = mix((18, 19, 21), a))
    d.text((x0, y0), cmd, font = f, fill = mix(FG, a))
    return img


def ffmpeg():
    for path in (os.environ.get('FFMPEG'), shutil.which('ffmpeg'), r'C:\Program Files\Shotcut\ffmpeg.exe'):
        if path and Path(path).is_file():
            return path
    raise SystemExit('ffmpeg not found: install it, or set FFMPEG to an ffmpeg.exe (Shotcut ships one)')


def main():
    out = Path(sys.argv[1])
    shots = {name: Image.open(DOCS / f'{name}.png').convert('RGB') for name in {p[1] for p in PANELS}}
    d = ImageDraw.Draw(BACK.copy())
    for caption in [p[4] for p in PANELS]:
        if d.textlength(caption, font = font(SEMI, 76)) > BOX[2] - BOX[0]:
            raise SystemExit(f'caption wider than the panel: {caption}')
    scenes = [(2.6, title), *[(p[0], lambda t, p = p: panel(shots[p[1]], *p[2:], t, p[0])) for p in PANELS], (3.2, numbers), (3.6, end)]
    starts, t0 = [], 0.0
    for dur, _ in scenes:
        starts.append(t0)
        t0 += dur - XF
    total = t0 + XF
    stills = {int((s + scenes[k][0] * 0.7) * FPS) for k, s in enumerate(starts)} if '--stills' in sys.argv else set()
    proc = subprocess.Popen([ffmpeg(), '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                             '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(out)],
                            stdin = subprocess.PIPE)
    for i in range(round(total * FPS)):
        t = i / FPS
        live = [(k, t - s) for k, s in enumerate(starts) if 0 <= t - s < scenes[k][0]]
        frames = [scenes[k][1](lt) for k, lt in live]
        img = frames[0]
        if len(frames) == 2:  # dip through the background, so two captions never overlap
            a = ease(live[1][1] / XF)
            img = Image.blend(frames[0], BACK, a * 2) if a < 0.5 else Image.blend(BACK, frames[1], a * 2 - 1)
        if i in stills:
            img.save(out.with_name(f'{out.stem}_frame{i:03d}.png'))
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise SystemExit(f'ffmpeg failed ({proc.returncode})')
    print(f'{total:.1f} s, {round(total * FPS)} frames: {out}')


if __name__ == '__main__':
    main()
