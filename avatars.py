"""Profile pictures people can pick instead of the first letter of their name.

Small cartoon people, drawn here from parts (skin, hair, outfit, extras) on a 64-unit grid, so
there are no image files and nothing to moderate. Every seller still gets a recognisable face.
"""

VIEWBOX = "4 4 56 56"  # a little zoomed in, so faces still read at 28px
INK = "#15132b"  # eyes, smile and outlines: the brand navy

# Hair drawn behind the head (long hair, afros) and in front of it (fringes, buns).
HAIR_BACK = {
    "long": '<path d="M17 30c0-11 6.5-17 15-17s15 6 15 17v20H17z"/>',
    "afro": '<circle cx="32" cy="25" r="17"/><circle cx="17" cy="30" r="7"/><circle cx="47" cy="30" r="7"/>',
    "curls": '<circle cx="21" cy="24" r="6"/><circle cx="43" cy="24" r="6"/><circle cx="19" cy="32" r="5"/><circle cx="45" cy="32" r="5"/>',
    "pigtails": '<circle cx="15" cy="27" r="6"/><circle cx="49" cy="27" r="6"/>',
}
HAIR_FRONT = {
    "short": '<path d="M19.5 29c-1.5-10 4.5-16 12.5-16s14 6 12.5 16c-1.5-4-4-6.5-7-7.5-4 2.5-10 3-15 2-1.5 1.2-2.5 3.2-3 5.5z"/>',
    "swept": '<path d="M19.5 29c-1-10 5-16 13-16 7 0 12.5 5 12 13-6 1-14-1-18-5-2 3-4 5.5-7 8z"/>',
    "long": '<path d="M20 29c0-9 5-15 12-15s12 5 12.5 11c-7 0-13-2.5-16-6.5-2 4.5-5 8-8.5 10.5z"/>',
    "afro": '<circle cx="24" cy="17.5" r="5"/><circle cx="32" cy="15.5" r="5.5"/><circle cx="40" cy="17.5" r="5"/>',
    "curls": '<circle cx="23" cy="18" r="5"/><circle cx="29" cy="14.5" r="5"/><circle cx="35.5" cy="14.5" r="5"/><circle cx="41" cy="18.5" r="5"/>',
    "bun": '<circle cx="32" cy="11" r="5.5"/><path d="M20 27c0-8.5 5-13.5 12-13.5S44 18.5 44 27c-3-4-7-6-12-6s-9 2-12 6z"/>',
    "mohawk": '<path d="M28.5 21c-1.5-6-.5-12 3.5-16 4 4 5 10 3.5 16z"/>',
    "buzz": '<path d="M20.5 25c1-7 5.5-10.5 11.5-10.5S42.5 18 43.5 25c-3-3-7-4.5-11.5-4.5S23.5 22 20.5 25z" opacity=".85"/>',
    "pigtails": '<path d="M20 27c0-8.5 5-13.5 12-13.5S44 18.5 44 27c-4-3-6-6-6-8.5-3 3-9 5-18 8.5z"/>',
    "bob": '<path d="M18.5 37c-2.5-14 4-23 13.5-23s16 9 13.5 23h-3.5c1-6-1-12-4-15-5 2-11 2.5-16 2.5-1 3.5-1.5 8-.5 12.5z"/>',
}

HATS = {
    "cap": '<path d="M19.5 26c0-8.5 5.5-13 12.5-13s12.5 4.5 12.5 13z"/><path d="M32 23.5h17c.5 2.5-1.5 4-4 4H32z"/><circle cx="32" cy="13.5" r="1.4" fill="{ink}" opacity=".35"/>',
    "beanie": '<path d="M19.5 26c0-9 5.5-14.5 12.5-14.5S44.5 17 44.5 26z"/><rect x="18.5" y="22" width="27" height="6" rx="3" fill="{ink}" opacity=".25"/><rect x="18.5" y="22" width="27" height="6" rx="3"/><circle cx="32" cy="10" r="3.5"/>',
}

EXTRAS = {
    "headphones": '<path d="M18 31v-3a14 14 0 0 1 28 0v3" fill="none" stroke="{ink}" stroke-width="2.6"/>'
                  '<rect x="14.5" y="27" width="6.5" height="10" rx="3" fill="{ink}"/><rect x="43" y="27" width="6.5" height="10" rx="3" fill="{ink}"/>'
                  '<rect x="15.5" y="29" width="2" height="6" rx="1" fill="#d4ff3a"/><rect x="46.5" y="29" width="2" height="6" rx="1" fill="#d4ff3a"/>',
    "shades": '<rect x="22.5" y="27" width="8.5" height="6" rx="2.5" fill="{ink}"/><rect x="33" y="27" width="8.5" height="6" rx="2.5" fill="{ink}"/>'
              '<path d="M31 29h2" stroke="{ink}" stroke-width="1.5"/><path d="M24.5 29l2.5-1" stroke="#fff" stroke-width="1" opacity=".7" stroke-linecap="round"/>',
    "hoops": '<circle cx="20.5" cy="36.5" r="2.4" fill="none" stroke="#ffc94a" stroke-width="1.2"/><circle cx="43.5" cy="36.5" r="2.4" fill="none" stroke="#ffc94a" stroke-width="1.2"/>',
    "stud": '<circle cx="44" cy="34.5" r="1.3" fill="#d4ff3a"/>',
    "glitter": '<path d="M41.5 33.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z" fill="#fff"/><circle cx="39" cy="37" r=".7" fill="#fff"/><circle cx="44" cy="31" r=".6" fill="#fff"/>',
    "freckles": '<g fill="{ink}" opacity=".35"><circle cx="24.5" cy="34" r=".6"/><circle cx="26.5" cy="35" r=".6"/><circle cx="25" cy="36" r=".5"/><circle cx="39.5" cy="34" r=".6"/><circle cx="37.5" cy="35" r=".6"/><circle cx="39" cy="36" r=".5"/></g>',
    "beard": '<path d="M20.5 32c0 8 5 12 11.5 12s11.5-4 11.5-12c-2 3-4 3.5-6 3.5-1.5-1-3.5-1.5-5.5-1.5s-4 .5-5.5 1.5c-2 0-4-.5-6-3.5z"/>',
    "flower": '<g transform="translate(42 16)"><circle cx="0" cy="-2.6" r="2.4" fill="#ff8fc1"/><circle cx="2.5" cy="-.8" r="2.4" fill="#ff8fc1"/><circle cx="1.6" cy="2.2" r="2.4" fill="#ff8fc1"/><circle cx="-1.6" cy="2.2" r="2.4" fill="#ff8fc1"/><circle cx="-2.5" cy="-.8" r="2.4" fill="#ff8fc1"/><circle r="1.5" fill="#ffc94a"/></g>',
    "sweatband": '<rect x="19.5" y="20.5" width="25" height="4.5" rx="2.2" fill="#f5f3ec"/><rect x="19.5" y="22" width="25" height="1.4" fill="#ff6fa8"/>',
}

EYES = {
    "dots": '<circle cx="27.5" cy="30" r="1.6"/><circle cx="36.5" cy="30" r="1.6"/>',
    "happy": '<path d="M25.5 30.5q2-2.4 4 0M34.5 30.5q2-2.4 4 0" fill="none" stroke="{ink}" stroke-width="1.5" stroke-linecap="round"/>',
    "wink": '<circle cx="27.5" cy="30" r="1.6"/><path d="M34.5 30.5q2-2.4 4 0" fill="none" stroke="{ink}" stroke-width="1.5" stroke-linecap="round"/>',
    "lashes": '<circle cx="27.5" cy="30" r="1.6"/><circle cx="36.5" cy="30" r="1.6"/><path d="M25.3 28.4l-1-.9M38.7 28.4l1-.9" stroke="{ink}" stroke-width="1" stroke-linecap="round"/>',
    "none": "",
}
MOUTHS = {
    "smile": '<path d="M28.5 35.5q3.5 3.2 7 0" fill="none" stroke="{ink}" stroke-width="1.5" stroke-linecap="round"/>',
    "grin": '<path d="M27.5 35h9q-.5 4.5-4.5 4.5T27.5 35z" fill="{ink}"/><path d="M29 35.4h6v1h-6z" fill="#fff"/>',
    "smirk": '<path d="M29 36.5q3 1.5 6.5-1" fill="none" stroke="{ink}" stroke-width="1.5" stroke-linecap="round"/>',
    "oh": '<ellipse cx="32" cy="36.5" rx="1.8" ry="2.2" fill="{ink}"/>',
}


def draw(skin, hair, hair_colour, bg, top, eyes="dots", mouth="smile", hat=None, hat_colour=None, extras=()):
    """One cartoon person as the inside of a 64×64 SVG."""
    hair_fill = f'<g fill="{hair_colour}">'
    parts = [f'<rect width="64" height="64" fill="{bg}"/>',
             # shoulders and top
             f'<path d="M9 64c0-12 10-19.5 23-19.5S55 52 55 64z" fill="{top}"/>',
             f'<path d="M27 44.5q5 4.5 10 0" fill="none" stroke="{INK}" stroke-width="1" opacity=".25"/>']
    if hair in HAIR_BACK:
        parts.append(hair_fill + HAIR_BACK[hair] + "</g>")
    parts += [f'<rect x="27.5" y="38" width="9" height="8" rx="3" fill="{skin}"/>',
              f'<rect x="27.5" y="38" width="9" height="4" fill="{INK}" opacity=".12"/>',
              f'<circle cx="20.5" cy="31" r="2.8" fill="{skin}"/><circle cx="43.5" cy="31" r="2.8" fill="{skin}"/>',
              f'<ellipse cx="32" cy="29" rx="12" ry="13" fill="{skin}"/>',
              '<circle cx="24.5" cy="35" r="2.3" fill="#ff6fa8" opacity=".25"/><circle cx="39.5" cy="35" r="2.3" fill="#ff6fa8" opacity=".25"/>']
    if "beard" in extras:
        parts.append(hair_fill + EXTRAS["beard"] + "</g>")
    parts.append(f'<g fill="{INK}">' + EYES[eyes].format(ink=INK) + "</g>")
    parts.append(MOUTHS[mouth].format(ink=INK))
    if hair in HAIR_FRONT and not hat:
        parts.append(hair_fill + HAIR_FRONT[hair] + "</g>")
    if hat:
        parts.append(f'<g fill="{hat_colour}">' + HATS[hat].format(ink=INK) + "</g>")
    for extra in extras:
        if extra != "beard":
            parts.append(EXTRAS[extra].format(ink=INK))
    return "".join(parts)


SKIN = ["#f8d9c0", "#efbf98", "#d39a6c", "#a8714a", "#74482c"]

# Twelve-plus characters: a spread of skin tones, hair, colours and party looks.
CHARACTERS = {
    "dj":       draw(SKIN[2], "short", "#3a2416", "#7c5cff", "#15132b", eyes="happy", extras=("headphones",)),
    "raver":    draw(SKIN[0], "bun", "#ff6fa8", "#3ad6c5", "#d4ff3a", mouth="grin", extras=("shades",)),
    "afro":     draw(SKIN[4], "afro", "#1d1a2e", "#ffb547", "#ff6fa8", extras=("hoops",)),
    "punk":     draw(SKIN[1], "mohawk", "#d4ff3a", "#ff6fa8", "#15132b", mouth="smirk", extras=("stud",)),
    "beanie":   draw(SKIN[2], "short", "#3a2416", "#2a2650", "#ffb547", mouth="grin", hat="beanie", hat_colour="#e4572e", extras=("beard",)),
    "cap":      draw(SKIN[3], "buzz", "#1d1a2e", "#7c5cff", "#3ad6c5", mouth="grin", hat="cap", hat_colour="#d4ff3a"),
    "sunny":    draw(SKIN[0], "long", "#f2c86b", "#ff6fa8", "#7c5cff", eyes="lashes", extras=("freckles",)),
    "ginger":   draw(SKIN[0], "curls", "#e4572e", "#2a2650", "#3ad6c5", eyes="wink", extras=("glitter",)),
    "pigtails": draw(SKIN[1], "pigtails", "#9b7bff", "#d4ff3a", "#ff6fa8", mouth="grin"),
    "silver":   draw(SKIN[3], "swept", "#e9e6f2", "#ff6fa8", "#15132b", mouth="smirk", extras=("shades",)),
    "flower":   draw(SKIN[4], "bun", "#1d1a2e", "#3ad6c5", "#ffb547", eyes="lashes", extras=("hoops", "flower")),
    "beard":    draw(SKIN[2], "short", "#1d1a2e", "#ffb547", "#7c5cff", eyes="happy", mouth="grin", extras=("beard",)),
    "shades":   draw(SKIN[4], "buzz", "#1d1a2e", "#d4ff3a", "#e4572e", mouth="smile", extras=("shades", "stud")),
    "bob":      draw(SKIN[1], "bob", "#3a2416", "#3ad6c5", "#ff6fa8", eyes="wink"),
    "sporty":   draw(SKIN[3], "short", "#3a2416", "#e4572e", "#f5f3ec", mouth="grin", extras=("sweatband",)),
}

# Icons from the first version map to a character, so nobody's pick disappears.
OLD_ICONS = {"disco": "raver", "headphones": "dj", "cocktail": "sunny", "star": "pigtails", "ghost": "ginger",
             "bolt": "punk", "moon": "silver", "flame": "ginger", "note": "flower", "crown": "afro",
             "heart": "bob", "sparkle": "shades"}

CHOICES = list(CHARACTERS)
ICONS = dict(CHARACTERS, **{old: CHARACTERS[new] for old, new in OLD_ICONS.items()})

# Characters are earned by selling: sales needed for each one. Everyone starts with their initial,
# so a character next to a name tells buyers "this person has sold tickets here before".
UNLOCK_AT = {
    "sunny": 1, "bob": 1, "beard": 1, "cap": 1, "pigtails": 1,
    "beanie": 3, "ginger": 3, "sporty": 3, "silver": 3, "shades": 3,
    "punk": 5, "afro": 5, "flower": 5,
    "dj": 10, "raver": 10,
}
assert set(UNLOCK_AT) == set(CHOICES)


def unlocked(sales):
    return {key for key, need in UNLOCK_AT.items() if sales >= need}


def next_unlock(sales):
    """Sales needed for the next new character, or None when everything is unlocked."""
    later = [need for need in UNLOCK_AT.values() if need > sales]
    return min(later) if later else None
