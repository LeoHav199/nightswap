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
BASE_URL = env("BASE_URL").rstrip("/")  # empty: use the address the visitor opened

# Where the database and uploaded files live. On a host, point DATA_DIR at a persistent disk.
DATA_DIR = env("DATA_DIR") or BASE_DIR
INSTANCE_DIR = os.path.join(DATA_DIR, "instance")
DATABASE = os.path.join(INSTANCE_DIR, "nightswap.sqlite3")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
TASK_SECRET = env("TASK_SECRET")   # protects /tasks/hourly, which a cron service calls every hour
LOAD_STARTER_DATA = env("LOAD_STARTER_DATA", "1") == "1"  # a brand-new database starts with starter_data/ (off in tests)

# Business rules -------------------------------------------------------------
# Buyer service fee per ticket: a percentage of the ticket price, with a minimum (and an optional maximum).
FEE_PERCENT = float(env("NIGHTSWAP_FEE_PERCENT", "8"))
FEE_MIN_CENTS = int(env("NIGHTSWAP_FEE_MIN_CENTS", "100"))
FEE_MAX_CENTS = int(env("NIGHTSWAP_FEE_MAX_CENTS", "0"))   # 0 = no maximum
PRICE_CAP_PERCENT = int(env("NIGHTSWAP_PRICE_CAP_PERCENT", "100"))
PAYOUT_DELAY_HOURS = int(env("NIGHTSWAP_PAYOUT_DELAY_HOURS", "48"))
RESERVATION_MINUTES = 30          # Stripe Checkout sessions last at least 30 minutes
MAX_FACE_VALUE_CENTS = 50000
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

SUPPORT_EMAIL = env("SUPPORT_EMAIL", "hello@nightswap.nl")
ADMIN_SECOND_FACTOR = env("ADMIN_SECOND_FACTOR", "1") != "0"   # admins confirm a text code every 12 hours
TRUST_PROXY = env("TRUST_PROXY", "0") == "1"                     # set to 1 behind Render/Fly so client IPs are read correctly

# Integrations ---------------------------------------------------------------
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET")

SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
MAIL_FROM = env("MAIL_FROM", "Nightswap <hello@nightswap.nl>")

TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
TWILIO_FROM = env("TWILIO_FROM")
SMS_ENABLED = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM)  # without it, phone codes go by email (beta)


# Apple Wallet passes. Needs an Apple Developer account: create a Pass Type ID and its
# certificate, export certificate + private key as PEM, and download Apple's WWDR (G4) certificate.
APPLE_PASS_TYPE_ID = env("APPLE_PASS_TYPE_ID")          # e.g. pass.nl.nightswap.ticket
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
