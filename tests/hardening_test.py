"""Checks for the fixes from the 'Bulletproofing Spare' review (numbers refer to its items)."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; MAIL_WORKS = [False]
notify.send_email = lambda to, subject, body, attachments=(), **kw: (SENT.append((to, subject, body)) or True) if MAIL_WORKS[0] else False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(c)})
class live_test_mode:
    """The real site (not development) with a Stripe test key."""
    def __enter__(self):
        self.saved = (config.DEV, config.STRIPE_SECRET_KEY); config.DEV = False; config.STRIPE_SECRET_KEY = "sk_test_x"
    def __exit__(self, *a):
        config.DEV, config.STRIPE_SECRET_KEY = self.saved

admin, seller, buyer, visitor = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo"); visitor.get("/")
verify_phone(seller, "0611111111"); verify_phone(admin, "0699999999")
D = (date.today() + timedelta(days=3)).isoformat()
admin.post("/admin/events/save", data={"name": "Guard Night", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Guard Night'")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(b"%PDF-1.4 guard"), "g.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); pub = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
with app.app_context(): tid = A.db.one("SELECT id FROM tickets WHERE listing_id=?", lid)["id"]

# --- #22: no real tickets while Stripe is in test mode on the real site
with live_test_mode():
    ok("Coming soon to Rotterdam" in html(visitor.get("/")), "#22 visitors see coming-soon while Stripe is in test mode")
    ok(seller.get("/sell").status_code == 302, "#22 non-admins can't open the sell form")
    ok(buyer.get("/orders/%s/tickets/%d" % (pub, tid)).status_code in (302, 404), "#22 non-admins can't download ticket files")
    page = html(admin.get("/"))
    ok("Coming soon to Rotterdam" not in page and "don&#39;t list or buy real tickets" in page, "#22 admins see the site with a red test-mode warning")
    ok(admin.get("/sell").status_code == 200, "#22 admins can still test selling")
ok(buyer.get("/orders/%s/tickets/%d" % (pub, tid)).status_code == 200, "#22 in development nothing is locked")

# --- #1: one address
saved = (config.DEV, config.BASE_URL); config.DEV = False; config.BASE_URL = "https://sparetickets.eu"
c = app.test_client()
r = c.get("/event?venue=Maassilo&date=%s" % D, base_url="https://nightswap.onrender.com")
ok(r.status_code == 301 and r.location == "https://sparetickets.eu/event?venue=Maassilo&date=%s" % D, "#1 the old address redirects to sparetickets.eu, keeping the page")
ok(c.get("/robots.txt", base_url="https://nightswap.onrender.com").status_code == 200, "#1 Render's health check still works on the old address")
ok(c.post("/tasks/hourly", base_url="https://nightswap.onrender.com").status_code == 404, "#1 the cron job isn't redirected (404 = reached the route without its secret)")
ok(c.get("/robots.txt", base_url="https://sparetickets.eu").status_code == 200, "#1 sparetickets.eu itself isn't redirected")
config.DEV, config.BASE_URL = saved

# --- #2, #3: event tickets, not only club tickets; no "students selling to students" promise
import i18n
for lang in i18n.LANGS:
    ok(len(i18n.STRINGS[lang]["meta_description"]) <= 155, "#2 the %s Google description fits in 155 characters" % lang)
home = html(visitor.get("/"))
ok("Resell event tickets in Rotterdam" in home and "concerts and festivals" in home, "#2 the title, description and footer name more than club tickets")
ok("Students selling to students" not in home and "from students, for students" not in home and "Made for students in Rotterdam" in home,
   "#3 the homepage no longer claims every buyer and seller is a student")
nl = app.test_client(); nl.set_cookie("lang", "nl")
ok("Van studenten, voor studenten" not in html(nl.get("/")) and "Gemaakt voor studenten in Rotterdam" in html(nl.get("/")), "#3 the same in Dutch")

# --- #4: one payout rule everywhere, matching rate_order and release_payout
how, terms = html(visitor.get("/how-it-works")), html(visitor.get("/terms"))
ok("on the day of the event that the ticket worked" in how and "48 hours after the night" in how, "#4 How it works describes the early and the latest payout")
ok("on the day of the event that the ticket worked" in terms and "nothing is paid out until the seller has transferred it" in terms, "#4 the terms say the same")
ok("held until after the event" not in how, "#4 no more 'held until after the event'")

# --- #5: the FAQ is up to date and how-it-works exists in every language
ok("Two kinds" in how and "Ticketmaster" in how and "change the price on your listing" in how, "#5 the FAQ explains app tickets and the price-change form")
for lang in ("fr", "de", "es"):
    c = app.test_client(); c.set_cookie("lang", lang); page = html(c.get("/how-it-works"))
    ok(("<h2>" + {"fr": "Vendre", "de": "Verkaufen", "es": "Vender"}[lang] + "</h2>") in page and "Buying" not in page, "#5 How it works is translated into %s" % lang)

# --- #6: no "verified phone" claim while codes go by email
ok("gave us a phone number" in how and "verified phone number" not in how, "#6 without SMS, the page doesn't claim phones are verified")
config.SMS_ENABLED = True
ok("Every seller has a verified phone number" in html(visitor.get("/how-it-works")), "#6 with real SMS on, it does")
config.SMS_ENABLED = False

# --- #7: the duplicate check is described as what it is
ok("checked against all tickets on Spare" in how and "can only be listed once" not in how, "#7 the duplicate promise says what's checked")

print("FAILED:", fails or "none")
