"""Draws the Spare logo (a tilted ticket with a dashed tear line) as PNG icons.

Run from the project folder:  ../.venv/bin/python tools/make_icons.py
The shapes match the SVG mark in templates/base.html and static/favicon.svg.
"""
from PIL import Image, ImageDraw

BG = (21, 19, 43, 255)
ACCENT = (212, 255, 58, 255)
SS = 4  # supersampling for smooth edges


def mark_mask(px):
    """The mark on a 24-unit grid, drawn into a px-by-px mask (white = lime)."""
    big = px * SS
    u = big / 24
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([1.6 * u, 6.2 * u, 22.4 * u, 17.8 * u], radius=2.2 * u, fill=255)
    for cx in (1.6, 22.4):
        d.ellipse([(cx - 2) * u, 10 * u, (cx + 2) * u, 14 * u], fill=0)
    w = 0.9 * u  # the dashed tear line: short rounded dashes, like stroke-dasharray "0.5 1.6"
    y = 7.6
    while y <= 16.4:
        y2 = min(y + 0.5, 16.4)
        d.line([15.6 * u, y * u, 15.6 * u, y2 * u], fill=0, width=round(w))
        for yy in (y, y2):
            d.ellipse([15.6 * u - w / 2, yy * u - w / 2, 15.6 * u + w / 2, yy * u + w / 2], fill=0)
        y += 2.1
    m = m.rotate(10, resample=Image.BICUBIC, center=(big / 2, big / 2))  # tilted, like the SVG's rotate(-10)
    return m.resize((px, px), Image.LANCZOS)


def icon(size, mark_share, corner=0.0, bg=BG):
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if bg:
        if corner:
            ImageDraw.Draw(img).rounded_rectangle([0, 0, big - 1, big - 1], radius=corner * big, fill=bg)
        else:
            img.paste(bg, [0, 0, big, big])
    img = img.resize((size, size), Image.LANCZOS)
    mpx = round(size * mark_share)
    mask = mark_mask(mpx)
    layer = Image.new("RGBA", (mpx, mpx), ACCENT)
    off = (size - mpx) // 2
    img.paste(layer, (off, off), mask)
    return img


if __name__ == "__main__":
    icon(192, 0.68, corner=0.22).save("static/icons/icon-192.png")
    icon(512, 0.68, corner=0.22).save("static/icons/icon-512.png")
    icon(512, 0.56).save("static/icons/icon-maskable-512.png")   # keeps the mark inside the safe zone
    icon(180, 0.66).save("static/icons/apple-touch-icon.png")     # iOS rounds the corners itself
    for scale in (1, 2, 3):
        sfx = "" if scale == 1 else "@%dx" % scale
        icon(29 * scale, 0.72).save("static/wallet/icon%s.png" % sfx)
        icon(50 * scale, 0.9, bg=None).save("static/wallet/logo%s.png" % sfx)
    icon(48, 0.8, corner=0.22).save("static/icons/favicon-48.png")
    print("icons written")
