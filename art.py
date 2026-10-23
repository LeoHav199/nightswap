"""Generated flyer artwork for events without a poster.

Every event gets its own image, seeded by its date, venue and name, so it
always looks the same but no two events look alike. The style follows the
category: club lights, student-party blobs and dots, festival sunsets and
concert spotlights. It's plain SVG, so there are no image rights to worry
about and it stays sharp at any size.
"""
import hashlib
import random

ACCENTS = ["#ff5a8a", "#ffd84d", "#7c5cff", "#3fd0ff", "#ff8a3d", "#5fe3a1", "#ff4fd8"]
W, H = 500, 400


def _mix(hex_a, hex_b, t):
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _blob(rnd, colour, r_min, r_max, opacity):
    return '<circle cx="%d" cy="%d" r="%d" fill="%s" opacity="%.2f" filter="url(#blur)"/>' % (
        rnd.randint(-40, W + 40), rnd.randint(-40, H + 40), rnd.randint(r_min, r_max), colour, opacity)


def _club(rnd, base, accents):
    parts = [_blob(rnd, c, 90, 170, rnd.uniform(.55, .85)) for c in [base] + accents]
    # laser beams fanning out from one point
    ox, oy = rnd.choice([(rnd.randint(0, W), -20), (-20, rnd.randint(0, H // 2)), (W + 20, rnd.randint(0, H // 2))])
    for _ in range(rnd.randint(5, 9)):
        tx, ty = rnd.randint(-100, W + 100), H + 40
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="%.1f" opacity="%.2f"/>' % (
            ox, oy, tx, ty, rnd.choice(accents), rnd.uniform(1, 3.5), rnd.uniform(.25, .6)))
    return parts


def _student(rnd, base, accents):
    parts = [_blob(rnd, c, 110, 190, rnd.uniform(.7, .95)) for c in accents + [base]]
    # halftone dot field in one corner
    cx, cy, colour = rnd.choice([(W, 0), (0, H), (W, H)]) + (rnd.choice(accents),)
    for gx in range(0, W + 20, 18):
        for gy in range(0, H + 20, 18):
            d = ((gx - cx) ** 2 + (gy - cy) ** 2) ** .5
            r = max(0, 6 - d / 55)
            if r > .6:
                parts.append('<circle cx="%d" cy="%d" r="%.1f" fill="%s" opacity=".55"/>' % (gx, gy, r, colour))
    return parts


def _festival(rnd, base, accents):
    sun = rnd.choice(accents)
    horizon = rnd.randint(int(H * .58), int(H * .72))
    sx = rnd.randint(int(W * .25), int(W * .75))
    parts = [_blob(rnd, base, 160, 230, .9), _blob(rnd, rnd.choice(accents), 120, 180, .55),
             '<circle cx="%d" cy="%d" r="%d" fill="%s" opacity=".95"/>' % (sx, horizon - 10, rnd.randint(70, 110), sun)]
    # sun stripes, then the ground
    for i in range(6):
        y = horizon - 60 + i * 13
        parts.append('<rect x="0" y="%d" width="%d" height="%d" fill="#0d0a13" opacity=".85"/>' % (y, W, 2 + i))
    parts.append('<rect x="0" y="%d" width="%d" height="%d" fill="#0d0a13"/>' % (horizon, W, H - horizon))
    for i in range(1, 7):
        y = horizon + i * i * 4
        parts.append('<line x1="0" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="1.2" opacity="%.2f"/>' % (
            y, W, y, sun, .5 - i * .05))
    return parts


def _concert(rnd, base, accents):
    parts = [_blob(rnd, base, 140, 220, .8)]
    for _ in range(rnd.randint(3, 5)):
        x = rnd.randint(30, W - 30)
        spread = rnd.randint(70, 150)
        colour = rnd.choice(accents)
        parts.append('<polygon points="%d,-10 %d,%d %d,%d" fill="%s" opacity="%.2f" filter="url(#soft)"/>' % (
            x, x - spread, H, x + spread, H, colour, rnd.uniform(.18, .35)))
        parts.append('<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="%s" opacity=".35" filter="url(#blur)"/>' % (
            x, H - 30, spread, 30, colour))
    return parts


STYLES = {"club": _club, "student": _student, "festival": _festival, "concert": _concert}


def svg(key, category, base_colour):
    """The artwork as an SVG string. `key` identifies the event (e.g. 'date|venue|name')."""
    seed = int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)
    rnd = random.Random(seed)
    accents = rnd.sample(ACCENTS, 3)
    base = base_colour if base_colour and len(base_colour) == 7 else "#5b2a86"
    bg = _mix(base, "#0d0a13", .72)
    body = STYLES.get(category, _club)(rnd, base, accents)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMid slice">'
        '<defs>'
        '<filter id="blur" x="-50%%" y="-50%%" width="200%%" height="200%%"><feGaussianBlur stdDeviation="38"/></filter>'
        '<filter id="soft"><feGaussianBlur stdDeviation="6"/></filter>'
        '<filter id="grain"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="%d"/>'
        '<feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .55 0"/></filter>'
        '</defs>'
        '<rect width="100%%" height="100%%" fill="%s"/>%s'
        '<rect width="100%%" height="100%%" filter="url(#grain)" opacity=".22"/>'
        '</svg>'
    ) % (W, H, W, H, seed % 997, bg, "".join(body))
