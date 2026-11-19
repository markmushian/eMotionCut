"""Rebuild the eMotionCut brand art from one geometric definition.

Pillow is build-time only. Everything else in the app reads the files this
writes, so the mark stays identical across the window icon, the tray, the
installer and the in-app brand.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

root = Path(__file__).resolve().parent.parent
public = root / 'public'

INK = '#12141A'        # graphite ground
ACCENT = '#2EE6C5'     # electric mint
S = 1024               # master canvas
R = 0.2235 * S         # squircle-ish corner radius


def mark(draw, box, scale):
    """Draw the mark inside a square of side `scale` starting at `box`."""
    x, y = box
    # playback triangle, optically centred
    tri = [(x + .255 * scale, y + .268 * scale),
           (x + .595 * scale, y + .500 * scale),
           (x + .255 * scale, y + .732 * scale)]
    draw.polygon(tri, fill=ACCENT)
    # playhead bar — the "cut"
    bx0 = x + .665 * scale
    bx1 = x + .745 * scale
    by0 = y + .232 * scale
    by1 = y + .768 * scale
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=.040 * scale, fill=ACCENT)


def render(size):
    s = size * 4
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=R / S * s, fill=INK)
    # soft top-left lift so the ground is not flat
    glow = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-s * .35, -s * .55, s * .95, s * .55], fill=(46, 230, 197, 26))
    glow = glow.filter(ImageFilter.GaussianBlur(s * .10))
    mask = Image.new('L', (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=R / S * s, fill=255)
    im.paste(glow, (0, 0), Image.composite(glow.split()[3], Image.new('L', (s, s), 0), mask))
    mark(ImageDraw.Draw(im), (0, 0), s)
    return im.resize((size, size), Image.Resampling.LANCZOS)


if __name__ == '__main__':
    for size in (1024, 512, 256):
        out = public / f'emotioncut-icon-{size}.png'
        render(size).save(out, optimize=True)
        print(out)

    ico = public / 'emotioncut.ico'
    render(256).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(ico)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
        '<rect width="96" height="96" rx="21.5" fill="#12141A"/>'
        '<path d="M24.5 25.7 L57.1 48 L24.5 70.3 Z" fill="#2EE6C5"/>'
        '<rect x="63.8" y="22.3" width="7.7" height="51.4" rx="3.8" fill="#2EE6C5"/>'
        '</svg>'
    )
    (public / 'emotioncut-mark.svg').write_text(svg, encoding='utf-8')
    print(public / 'emotioncut-mark.svg')
