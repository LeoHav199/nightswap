"""Settings, read from the environment (or a .env file next to this one)."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


_load_dotenv()


def env(key, default=""):
    return os.environ.get(key, default).strip()


ENV = env("NIGHTSWAP_ENV", "development")
DEV = ENV != "production"
# The site's public address. Render provides RENDER_EXTERNAL_URL; set BASE_URL once you have your own domain.
BASE_URL = (env("BASE_URL") or env("RENDER_EXTERNAL_URL")).rstrip("/")  # empty: use the address the visitor opened

# Where the database and uploaded files live. On a host, point DATA_DIR at a persistent disk.
DATA_DIR = env("DATA_DIR") or BASE_DIR
INSTANCE_DIR = os.path.join(DATA_DIR, "instance")
DATABASE = os.path.join(INSTANCE_DIR, "nightswap.sqlite3")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
TASK_SECRET = env("TASK_SECRET")   # protects /tasks/hourly, which a cron service calls every hour
JARVIS_FEED_TOKEN = env("JARVIS_FEED_TOKEN")  # lets Leo's own Jarvis assistant read /api/jarvis (counts and event names only)
BACKUP_KEY = env("BACKUP_KEY")     # #16: encrypts backups (make one with: flask --app app backup-key); keep a copy in a password manager
LOAD_STARTER_DATA = env("LOAD_STARTER_DATA", "1") == "1"  # a brand-new database starts with starter_data/ (off in tests)

# Business rules -------------------------------------------------------------
# Buyer service fee per ticket: a percentage of the ticket price, with a minimum (and an optional maximum).
FEE_PERCENT = float(env("NIGHTSWAP_FEE_PERCENT", "8"))
FEE_MIN_CENTS = int(env("NIGHTSWAP_FEE_MIN_CENTS", "199"))  # €1.99 since 6 Nov 2026 (was €1.50): cheap tickets barely covered Stripe
FEE_MAX_CENTS = int(env("NIGHTSWAP_FEE_MAX_CENTS", "0"))   # 0 = no maximum
# Seller fee per ticket, taken from the payout: covers Stripe's per-seller and per-payout costs.
SELLER_FEE_PERCENT = float(env("NIGHTSWAP_SELLER_FEE_PERCENT", "5"))
SELLER_FEE_MIN_CENTS = int(env("NIGHTSWAP_SELLER_FEE_MIN_CENTS", "100"))
# Profit share (Leo and Ilyas, 6 Nov 2026): sellers also pay this percentage of whatever their price is above the
# face value. Selling at or below face value costs nothing extra. 0 = off.
PROFIT_SHARE_PERCENT = float(env("NIGHTSWAP_PROFIT_SHARE_PERCENT", "10"))

# Ticket apps with changing QR codes, where tickets move with the app's own "transfer" button.
TRANSFER_APPS = {"ticketmaster": "Ticketmaster", "weeztix": "Weeztix / Eventix", "paylogic": "Paylogic",
                 "seetickets": "See Tickets", "other": None}  # None: shown as "another app" in the visitor's language
BUYER_REMIND_HOURS = 2       # #31: ask the buyer whether an app ticket arrived this long after the transfer (and at 12:00 on the day)
TRANSFER_REMIND_HOURS = 3  # remind sellers who haven't marked the transfer as done after this long
# Resale price cap as % of face value: 120 = up to 20% above, 200 = at most double (Leo's choice, 5 Nov 2026; it was 120).
# 0 = no cap: sellers set their own price. The face value is always shown next to the price, so buyers see the markup.
PRICE_CAP_PERCENT = int(env("NIGHTSWAP_PRICE_CAP_PERCENT", "200"))
# Payouts (5 Nov 2026): Spare holds the money until the buyer is in. The buyer slides "Swap" at the door and the
# seller is paid straight away; without a swap, the seller is paid automatically this long after the night.
PAYOUT_DELAY_HOURS = int(env("NIGHTSWAP_PAYOUT_DELAY_HOURS", "24"))
# The hourly task fills in missing official prices from the events' own ticket pages (off in the tests: no network).
PRICE_LOOKUP = env("NIGHTSWAP_PRICE_LOOKUP", "1") == "1"
SWAP_OPENS_AFTER_DOORS_MINUTES = 0   # the buyer can swap from the moment the doors open
# A swap pays straight away only when the buyer paid by iDEAL: card payments can be reversed by the bank weeks later,
# so a stolen card plus a fake listing would cost Spare the money. Card orders are paid at the normal time.
SWAP_INSTANT_IDEAL_ONLY = env("NIGHTSWAP_SWAP_INSTANT_IDEAL_ONLY", "1") == "1"
SWAP_REMIND_MINUTES = 45             # "Inside? Swap" push + email this long after the doors open
# Safety checks from the "Bulletproofing" review. Always on; only older tests that check other things switch them off.
FACE_PROOF_REQUIRED = env("NIGHTSWAP_FACE_PROOF", "0") == "1"  # #25: no official price known → proof of the price and an admin check before going live. Off since 14 Nov 2026 (Leo: too much friction for sellers); the face value typed in is trusted, and the terms keep "we may ask for proof".
SMS_REQUIRED = False         # True: on the live site, phones can only be verified by text (needs Twilio). Leo chose
                             # emailed codes until Spare has volume (4 Nov 2026); Stripe's ID check covers identity.
TICKET_CHECKS = True         # #29: unreadable codes go to an admin, re-saved pictures are caught, app tickets need screenshots
NEW_SELLER_LIMITS = True     # #28: limits below for sellers with fewer than NEW_SELLER_SALES paid-out sales
NEW_SELLER_SALES = 2
NEW_SELLER_MAX_TICKETS = 2   # tickets on sale at once
NEW_SELLER_PAYOUT_HOURS = int(env("NIGHTSWAP_NEW_SELLER_PAYOUT_HOURS", "24"))  # paid this long after the night, never early at Swap (was 72 until 6 Nov 2026, Leo's call: door problems now close at 06:00, so 24 hours is enough)
RESERVATION_MINUTES = 30          # Stripe Checkout sessions last at least 30 minutes
MAX_FACE_VALUE_CENTS = 50000
# #38: first-time buyers may pay by card too (internationals often have no Dutch bank), always with 3D Secure.
# False = first purchases by iDEAL only again. Card payments are paid out at the normal time, not at the swap.
FIRST_BUY_CARD = env("NIGHTSWAP_FIRST_BUY_CARD", "1") == "1"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024          # per file
ADMIN_EMAILS = {e.strip().lower() for e in env("ADMIN_EMAILS").split(",") if e.strip()}

# Cities ----------------------------------------------------------------------
# Cities with their own section on the homepage. Add one here when you launch there.
ENABLED_CITIES = ["Rotterdam"]
# Offer an "All of the Netherlands" view. Off while launching in Rotterdam only.
SHOW_ALL_NETHERLANDS = env("SHOW_ALL_NETHERLANDS", "0") == "1"
DEFAULT_CITY = "Rotterdam"
# Suggestions for the city field when posting (festivals often happen outside the big cities).
CITY_SUGGESTIONS = ["Rotterdam", "Amsterdam", "Den Haag", "Utrecht", "Delft", "Leiden", "Eindhoven", "Groningen",
                    "Tilburg", "Breda", "Nijmegen", "Arnhem", "Haarlem", "Maastricht", "Zwolle", "Enschede",
                    "'s-Hertogenbosch", "Amersfoort", "Biddinghuizen", "Hilvarenbeek", "Landgraaf"]

# Categories, in the order they're shown. Festivals and concerts appear in every city's view,
# because students travel for them; student parties and clubs are local.
CATEGORIES = ["student", "club", "festival", "concert"]
NATIONAL_CATEGORIES = {"festival", "concert"}

# Venues offered in the "post a ticket" form (all in Rotterdam). Sellers can also type another venue.
VENUES = [
    "Annabel",
    "BIRD",
    "Club Vie",
    "Maassilo",
    "Perron",
    "Rotown",
    "Thalia Lounge",
    "Toffler",
]

MAX_TICKETS_PER_LISTING = 6
RESELL_HOUR = 12        # local time the day before an event when ticket holders get "can't go? sell it"
NUDGE_HOUR = 14          # local time on the day of the event when unsold sellers get a price reminder

# Email domains that earn a "verified student" badge (subdomains count too, e.g. student.eur.nl).
# Check these against what each school actually issues before launch.
STUDENT_DOMAINS = {
    "eur.nl": "Erasmus University Rotterdam",
    "hr.nl": "Hogeschool Rotterdam",
    "codarts.nl": "Codarts",
    "inholland.nl": "Inholland",
    "tudelft.nl": "TU Delft",
    "hhs.nl": "De Haagse Hogeschool",
    "leidenuniv.nl": "Leiden University",
}

SUPPORT_EMAIL = env("SUPPORT_EMAIL", "hello@sparetickets.eu")
INSTAGRAM = env("NIGHTSWAP_INSTAGRAM", "sparetickets.eu")  # Spare's own (anonymous) account, linked in the footer and on /about; "" hides it
# Who runs Spare, shown in the footer, emails, terms and privacy policy (#19, #20). Fill in after the KvK registration;
# until then the pages show [placeholders].
COMPANY_NAME = env("COMPANY_NAME", "Spare Tickets vof")  # Leo and Ilyas's general partnership (vof), 50/50; KvK registered the name "Spare Tickets" (Nov 2026)
COMPANY_ADDRESS = env("COMPANY_ADDRESS", "Lombardkade 47A, 3011 ZC Rotterdam")  # also the address in the KVK register
KVK_NUMBER = env("KVK_NUMBER")
VAT_NUMBER = env("VAT_NUMBER")
ADMIN_SECOND_FACTOR = env("ADMIN_SECOND_FACTOR", "1") != "0"   # admins confirm a text code every 12 hours
TRUST_PROXY = env("TRUST_PROXY", "0") == "1"                     # set to 1 behind Render/Fly so client IPs are read correctly
PROXY_HOPS = int(env("TRUST_PROXY_HOPS", "1"))  # how many proxies add to X-Forwarded-For; check /admin → Network check
# #41: with 2 hops (Cloudflare in front of Render) the extra hop is only trusted when the request really came from
# Cloudflare; someone sending requests straight to Render can't pick their own IP any more.
# Copied from https://www.cloudflare.com/ips/ on 5 Nov 2026 (that list was last changed 28 Sep 2023).
CLOUDFLARE_RANGES = (
    "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "104.16.0.0/13", "104.24.0.0/14", "108.162.192.0/18",
    "131.0.72.0/22", "141.101.64.0/18", "162.158.0.0/15", "172.64.0.0/13", "173.245.48.0/20", "188.114.96.0/20",
    "190.93.240.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)

# Integrations ---------------------------------------------------------------
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET")

SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
MAIL_FROM = env("MAIL_FROM", "Spare <hello@sparetickets.eu>")

TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
TWILIO_FROM = env("TWILIO_FROM")
SMS_ENABLED = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM)  # without it, phone codes go by email (beta)


# Apple Wallet passes. Needs an Apple Developer account: create a Pass Type ID and its
# certificate, export certificate + private key as PEM, and download Apple's WWDR (G4) certificate.
APPLE_PASS_TYPE_ID = env("APPLE_PASS_TYPE_ID")          # e.g. pass.eu.sparetickets.ticket
APPLE_TEAM_ID = env("APPLE_TEAM_ID")
APPLE_PASS_CERT = env("APPLE_PASS_CERT")                # path to the pass certificate (PEM)
APPLE_PASS_KEY = env("APPLE_PASS_KEY")                  # path to its private key (PEM)
APPLE_PASS_KEY_PASSWORD = env("APPLE_PASS_KEY_PASSWORD")
APPLE_WWDR_CERT = env("APPLE_WWDR_CERT")                # path to Apple's WWDR certificate (PEM)


def secret_key():
    key = env("SECRET_KEY")
    if key:
        return key
    if not DEV:
        raise RuntimeError("SECRET_KEY must be set in production.")
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    path = os.path.join(INSTANCE_DIR, "dev_secret_key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(os.urandom(32).hex())
    with open(path) as fh:
        return fh.read().strip()


def check_production():
    if DEV:
        return
    missing = []
    if not SMTP_HOST:
        missing.append("SMTP_HOST")
    if not STRIPE_SECRET_KEY:
        missing.append("STRIPE_SECRET_KEY")
    if not BASE_URL:
        missing.append("BASE_URL")
    if missing:
        raise RuntimeError("Production needs: " + ", ".join(missing))
