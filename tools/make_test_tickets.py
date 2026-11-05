"""Draws fake ticket PDFs for testing the live site in Stripe test mode: each has its own random QR code and a
different layout, so Spare's duplicate checks (same QR, look-alike picture) treat them as different tickets.

Usage, from the project folder:  ../.venv/bin/python tools/make_test_tickets.py [how many] [event name]
The PDFs land in test-tickets/ (not committed). They say TEST TICKET on them and get nobody into anything.
"""
import os
import random
import secrets
import sys

import qrcode
from PIL import Image, ImageDraw, ImageFont

W, H = 1240, 1754  # A4 at 150 dpi


def font(size):
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def ticket(n, event):
    code = "SPARE-TEST-" + secrets.token_hex(8).upper()
    rnd = random.Random(code)
    page = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(page)
    # A dark block of random height and position: makes each page look different to the look-alike check.
    top = rnd.randint(0, 900)
    d.rectangle([0, top, W, top + rnd.randint(200, 700)], fill=rnd.randint(20, 120))
    qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(code)
    size = rnd.randint(700, 950)
    img = qr.make_image(fill_color="black", back_color="white").convert("L").resize((size, size), Image.NEAREST)
    x, y = rnd.randint(40, W - size - 40), rnd.randint(40, H - size - 260)
    page.paste(img, (x, y))
    d.rectangle([0, H - 230, W, H], fill=255)
    d.text((60, H - 210), "TEST TICKET · not valid for entry", font=font(56), fill=0)
    d.text((60, H - 130), "%s · #%d · %s" % (event, n, code), font=font(34), fill=60)
    return page, code


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    event = sys.argv[2] if len(sys.argv) > 2 else "Spare test night"
    os.makedirs("test-tickets", exist_ok=True)
    for n in range(1, count + 1):
        page, code = ticket(n, event)
        path = os.path.join("test-tickets", "test-ticket-%d-%s.pdf" % (n, code[-6:].lower()))
        page.save(path, "PDF", resolution=150)
        print(path)


if __name__ == "__main__":
    main()
