"""Keeps deals on Nightswap: hides contact and payment details in messages.

Contact data (phone numbers, emails, links, social handles, IBANs) is masked.
Mentions of other channels or off-platform payment ("whatsapp me", "tikkie",
"cash") are left readable but flagged, so both people see a warning and
repeat attempts show up in the admin page. Ordinary words such as "contact"
are never blocked: that only annoys honest users.
"""
import re

MASK = "•••"

NUMBER_WORDS = (
    r"zero|one|two|three|four|five|six|seven|eight|nine|oh|"
    r"nul|een|één|twee|drie|vier|vijf|zes|zeven|acht|negen"
)

PATTERNS = [
    # email addresses, including "name at gmail dot com"
    ("email", re.compile(r"[\w.+-]+\s*(@|\(at\)|\[at\]|\sat\s)\s*[\w-]+\s*(\.|\(dot\)|\[dot\]|\sdot\s|\spunt\s)\s*[a-z]{2,}", re.I)),
    # links and bare domains
    ("link", re.compile(r"\b(https?://\S+|www\.\S+|[\w-]+\.(com|nl|net|org|io|me|app|ly|link|be|de|eu)\b(/\S*)?)", re.I)),
    # IBAN (NL91 ABNA 0417 1643 00)
    ("iban", re.compile(r"\b[A-Z]{2}\s?\d{2}\s?[A-Z]{4}(\s?\d){10}\b", re.I)),
    # phone numbers written with digits, spaces, dots or dashes: 0612345678, +31 6 1234 5678, 06-12 34 56 78
    ("phone", re.compile(r"(\+|00)?\d[\d\s().\-/]{7,}\d")),
    # phone numbers written out: "nul zes een twee ..." (six or more number words/digits in a row)
    ("phone", re.compile(r"\b((%s|\d)[\s,.\-]*){6,}" % NUMBER_WORDS, re.I)),
    # social handles: @name, "insta: name", "snap name"
    ("handle", re.compile(r"(?<![\w.])@[\w.]{2,}")),
    ("handle", re.compile(r"\b(insta(gram)?|ig|snap(chat)?|sc|telegram|tg|tiktok|discord)\s*[:=\-]?\s*@?[\w.]{3,}", re.I)),
]

# Readable, but flagged: talking about moving the deal elsewhere.
OFF_PLATFORM = re.compile(
    r"\b(whats\s?app|whatsap|wa\.me|app\s+me|appen|dm\s*me|text\s*me|call\s*me|bel\s*me|sms|"
    r"instagram|insta|snapchat|telegram|signal|messenger|"
    r"tikkie|betaalverzoek|paypal|revolut|bunq|iban|overmaken|bank\s*transfer|cash|contant|"
    r"outside\s+(the\s+)?(app|site|nightswap)|buiten\s+(de\s+)?(app|site|nightswap)|"
    r"without\s+(the\s+)?fee|zonder\s+(de\s+)?kosten)\b",
    re.I,
)


def check(text):
    """Returns (safe_text, flags). flags is a sorted list like ['phone', 'off_platform']."""
    flags = set()
    clean = text

    def mask(kind):
        def repl(match):
            # Short digit runs are prices, dates or times, not phone numbers.
            if kind == "phone" and sum(ch.isdigit() for ch in match.group(0)) < 8 and not re.search(
                    NUMBER_WORDS, match.group(0), re.I):
                return match.group(0)
            flags.add(kind)
            return MASK
        return repl

    for kind, pattern in PATTERNS:
        clean = pattern.sub(mask(kind), clean)
    if OFF_PLATFORM.search(clean):
        flags.add("off_platform")
    return clean, sorted(flags)
