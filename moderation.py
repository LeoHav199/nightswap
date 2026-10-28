"""Keeps deals on Spare: hides contact and payment details in messages.

Contact data (phone numbers, emails, links, social handles, IBANs) is masked.
Attempts to move the deal or the payment elsewhere ("whatsapp me", "tikkie",
"meet me and I'll pay cash", "pay you directly") are flagged "off_platform":
the app doesn't deliver those, tells the sender why, and shows them in the
admin page. Ordinary words such as "contact" or "pay" on their own are left
alone: blocking them would only annoy honest users.
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
    # other apps, including common typos
    r"\b(w?h?ats?\s?ap+|watsap+|wa\.me|app\s+me|appen|dm\s*me|text\s*me|call\s*me|bel\s*me|sms|"
    r"insta\w*|snap\w*|telegram|signal|messenger|facebook|discord|"
    # other ways to pay
    r"tikkie|betaalverzoek|paypal|revolut|bunq|venmo|wero|mobilepay|crypto|bitcoin|btc|usdt|"
    r"iban|overmaken|overmaak|maak\s+(\w+\s+){0,5}over|rechtstreeks|onderling|bank\s*transfer|cash|contant|"
    # "on another platform / via een andere app"
    r"(another|other|different|separate|ander\w*)\s+(pl\w*|app\w*|site|website|platform)|"
    r"outside\s+(the\s+)?(app|site|spare|nightswap)|buiten\s+(de\s+)?(app|site|spare|nightswap)|"
    r"without\s+(the\s+)?fees?|zonder\s+(de\s+)?(kosten|fee)|"
    # meeting up to hand over or pay
    r"meet\s*(me|up)|meet\s+(at|in|outside|near|by)|in\s+person|face\s*to\s*face|irl|"
    r"afspreken|ontmoeten|kom\s+langs|langskomen|"
    # paying the other person directly
    r"(send|give|pay|transfer|wire|sturen|stuur|geven|geef|betaal|betalen)\s+(\w+\s+){0,3}(money|cash|geld|euros?|the\s+rest|het\s+geld)|"
    r"pay\s+(you\s+|u\s+)?(directly|direct|privately|myself|personally)|directly\s+to\s+(you|me)|"
    r"(rechtstreeks|direct|zelf)\s+(aan\s+)?(je|jou|u)\s+(betalen|overmaken|geven))\b",
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
