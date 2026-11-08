import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False; config.ADMIN_EMAILS = {"admin@example.com"}
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **k: False
import app as A, i18n, qrcode
from PIL import Image
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
def png(qr):
    img = Image.new("RGB", (800, 1000), "white"); img.paste(qrcode.make(qr).convert("RGB").resize((400, 400)), (200, 300))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()

# Launch-week safety (approved 7 Nov 2026, option 1): a /help page with plain answers, the report route and our email.
c = app.test_client()
r = c.get("/help")
page = html(r)
ok(r.status_code == 200 and "<h1>Help</h1>" in page, "/help opens")
ok(page.count("<details id=") == 8, "eight questions (%d)" % page.count("<details id="))
ok("before 06:00" in page and "after Swap you can't report a problem at the door any more" in page,
   "it states the 06:00 limit and that Swap ends door reports")
ok("%d hours to answer" % A.DISPUTE_REPLY_HOURS in page, "the seller's reply time comes from the code")
ok("%d hours after the night" % config.PAYOUT_DELAY_HOURS in page, "the payout timing comes from the settings")
ok("{{" not in page and "{%" not in page and "()" not in page.split('id="ticket"')[1].split("</p>")[0],
   "no template code left on the page, and the ticket-app list is filled in")
ok("I sold my ticket twice" in page and "How do I cancel?" in page and "cancelled or moved" in page, "the questions from the proposal are there")
ok('href="mailto:hello@sparetickets.eu"' in page and "usually within a few hours" in page, "the email address and the reply time we already promise on About")
ok('href="/account#tickets"' in page, "a button to the buyer's tickets, where the report form is")
ok(not any(n in page for n in ("Leo", "Ilyas", "Havg", "Arnal", "Lombardkade")), "no names or home address on the help page")
ok('href="/help"' in html(c.get("/")) and 'href="/help"' in html(c.get("/how-it-works")), "the footer links to /help")

# Every language
names = {"nl": "Hulp", "fr": "Aide", "de": "Hilfe", "es": "Ayuda"}
for code, word in names.items():
    c.get("/lang/%s?next=/" % code)
    p = html(c.get("/help"))
    ok("<h1>%s</h1>" % word in p and p.count("<details id=") == 8 and "hello@sparetickets.eu" in p,
       "/help in %s, with all eight questions" % code)
    ok(i18n.t(code, "help_contact") != i18n.t("en", "help_contact"), "the contact line is translated (%s)" % code)
c.get("/lang/nl?next=/")
ok("vóór 06:00" in html(c.get("/help")), "the Dutch page states the 06:00 limit")
c.get("/lang/de?next=/")
ok("vor 6 Uhr" in html(c.get("/help")), "the German page writes the time the German way")

# Open in coming-soon mode, like How it works and About, and listed in the sitemap
with app.app_context():
    A.set_setting("coming_soon", "1")
c = app.test_client()
ok(c.get("/help").status_code == 200 and "<details" in html(c.get("/help")), "/help stays open in coming-soon mode")
ok("/help</loc>" in html(c.get("/sitemap.xml")), "the sitemap lists /help")
with app.app_context():
    A.set_setting("coming_soon", "0")

# The order page links to it, for the buyer and the seller, also in door mode
seller, buyer = app.test_client(), app.test_client()
login(seller, "s@student.eur.nl", "Sam"); login(buyer, "b@test.nl", "Bea")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
D = (date.today() + timedelta(days=3)).isoformat()
r = seller.post("/sell", data={"venue": "__other", "other_venue": "Het Sieraad", "city": "Rotterdam", "event_date": D, "face_value": "20",
                               "price": "20", "event_name": "Gala", "category": "student", "name_on_ticket": "no", "no_double_sell": "yes",
                               "adult": "yes", "_csrf": csrf(seller), "ticket": (io.BytesIO(png("T-HELP-1")), "t.png")}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
pub = r.location.split("/")[2].split("?")[0]
ok('class="small order-help"><a href="/help">Help and answers</a>' in html(buyer.get("/orders/" + pub)), "the buyer's order page links to /help")
ok('href="/help">Help and answers' in html(seller.get("/orders/" + pub)), "the seller's order page links to /help")
night = date.today() if datetime.now().hour >= 12 else date.today() - timedelta(days=1)
with app.app_context():
    # door mode runs from noon on the event day to noon the day after, so before noon "tonight" is yesterday's event
    A.db.run("UPDATE listings SET event_date=?, start_time='12:00' WHERE id=?", night.isoformat(), lid)
door_page = html(buyer.get("/orders/" + pub))
ok("is-door" in door_page and 'href="/help">Help and answers' in door_page, "door mode on the night still links to /help")
ok('id="tickets"' in html(buyer.get("/account")), "the account page has the #tickets anchor the help page links to")

print("\n%d failed" % len(fails))
