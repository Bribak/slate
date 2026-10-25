"""Draw slate's document icon, slate/web/slate-file.ico: the app icon's dark slate and three bars as a page with a folded
corner, for .py files in Explorer (slate/install.py registers it). Run it after changing the app icon; needs Pillow.

    python tools/icons.py [preview.png]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / 'slate' / 'web' / 'slate-file.ico'
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
SLATE, GUTTER, EDGE, FOLD = (43, 43, 43), (49, 51, 53), (90, 93, 95), (78, 82, 84)
BARS = [((204, 120, 50), 0.62), ((255, 198, 109), 0.92), ((106, 135, 89), 0.74)]  # slate.png's bars: width as a share of the widest


def page(size):
    """One size, drawn 8x larger and scaled down. In units of a 256 px icon: the page spans x 40-216 and y 12-244, as
    Windows' own document icons do, with a 52-unit fold at the top right."""
    k = size * 8 / 256
    u = lambda *v: [round(x * k) for x in v]
    left, top, right, bottom, fold, r = 40, 12, 216, 244, 52, 16

    def sheet(inset, colour):
        """The page shrunk by `inset` on every side, the folded corner's diagonal included."""
        mask = Image.new('L', (size * 8, size * 8))
        m = ImageDraw.Draw(mask)
        m.rounded_rectangle(u(left + inset, top + inset, right - inset, bottom - inset), radius = round((r - inset) * k), fill = 255)
        cut = right - fold - top - inset * 2 ** 0.5  # the page keeps x - y <= cut
        m.polygon([tuple(u(*p)) for p in [(cut - 10, -10), (300, -10), (300, 300 - cut)]], fill = 0)
        layer = Image.new('RGBA', mask.size, colour)
        layer.putalpha(mask)
        return layer

    im = sheet(0, EDGE)
    im.alpha_composite(sheet(6, SLATE))
    im.alpha_composite(sheet(6, GUTTER).crop((0, 0, round((left + 32) * k), im.height)))  # the logo's darker left edge
    d = ImageDraw.Draw(im)
    d.polygon([tuple(u(*p)) for p in [(right - fold, top), (right - fold, top + fold), (right, top + fold)]], fill = FOLD)  # the flap
    x0, x1 = 64, 196
    for i, (colour, share) in enumerate(BARS):
        y = 112 + i * 40
        d.rectangle(u(x0, y, x0 + (x1 - x0) * share, y + 22), fill = colour)
    return im.resize((size, size), Image.LANCZOS)


def main():
    images = [page(s) for s in SIZES]
    images[-1].save(OUT, sizes = [(s, s) for s in SIZES], append_images = images[:-1])
    if len(sys.argv) > 1:
        sheet = Image.new('RGBA', (sum(SIZES) + 10 * len(SIZES), 256), (255, 255, 255, 255))
        x = 0
        for im in images:
            sheet.alpha_composite(im, (x, 0))
            x += im.width + 10
        sheet.save(sys.argv[1])
    print(f'{OUT} ({OUT.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
