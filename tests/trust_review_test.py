import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **k: False
import app as A, i18n
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    ok("Check your spam or junk folder" in html(r), "the code screen says to check the spam folder")
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})

# The site's promises match the rules (6 Nov 2026 review)
c = app.test_client()
how = html(c.get("/how-it-works"))
ok("before 06:00, with a photo" in how and "any time before the seller is paid" not in how, "How it works: door problems are reported on the night, with a photo")
ok("At the door, slide Swap." in how, "How it works tells buyers to slide Swap once they're in")
ok("a 8%" not in how and "plus a service fee of 8%" in how, "How it works: no 'a 8%' typo")
for code in ("nl", "fr", "de", "es"):
    ok(i18n.t(code, "how_swap_step") != i18n.t("en", "how_swap_step") and "Swap" in i18n.t(code, "how_swap_step"), "the Swap step is translated (%s)" % code)
ok("24 hours after the night" not in i18n.t("en", "cs_point_sell_body"), "the coming-soon page doesn't promise every seller 24 hours")

# Link previews on every page
home = html(c.get("/"))
ok('property="og:image" content="' in home and "og-image.png" in home and 'name="twitter:card" content="summary_large_image"' in home,
   "the homepage has a link preview with the branded image")
ok(home.count('property="og:image"') == 1, "one preview image per page")
ok(c.get("/static/og-image.png").status_code == 200, "the preview image exists")

# Sell form: true steps for a new seller, 'You receive' above the Post button
seller = app.test_client(); login(seller, "s@student.eur.nl", "Sam")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
sp = html(seller.get("/sell"))
ok("set up payouts with Stripe (a few minutes" in sp and "appears in the list right away" not in sp, "a new seller is told the ticket goes on sale after payout setup")
ok("paid 24 hours after the night. After 2 completed sales" in sp and "at the latest 24 hours" not in sp, "a new seller is told they're paid 24 hours after the night")
ok("after your first sale" not in sp, "no more 'add your bank details after your first sale'")
ok(sp.index('class="receive-line"') < sp.index(">Post ticket<") or sp.index('class="receive-line"') < sp.index("post_ticket"), "'You receive' sits right above the Post button")
acc = html(seller.get("/account"))
ok('class="head-actions"' in acc, "the Account buttons are in a row that can wrap")
ok(".head-actions { display: flex; gap: 8px; flex-wrap: wrap; }" in open("static/css/style.css").read(), "...and it wraps on small phones")

# The Report link is no longer next to the price on event pages
D = (date.today() + timedelta(days=3)).isoformat()
with app.app_context():
    A.db.run("UPDATE users SET payouts_ready=1 WHERE email='s@student.eur.nl'")
import qrcode
from PIL import Image
img = Image.new("RGB", (800, 1000), "white"); img.paste(qrcode.make("T-REPORT-1").convert("RGB").resize((400, 400)), (200, 300))
b = io.BytesIO(); img.save(b, "PNG")
seller.post("/sell", data={"venue": "__other", "other_venue": "Het Sieraad", "city": "Rotterdam", "event_date": D, "face_value": "20", "price": "20",
    "event_name": "Gala", "category": "student", "name_on_ticket": "no", "no_double_sell": "yes", "adult": "yes", "_csrf": csrf(seller),
    "ticket": (io.BytesIO(b.getvalue()), "t.png"), "face_proof": (io.BytesIO(b.getvalue()), "p.png")}, content_type="multipart/form-data")
with app.app_context(): A.db.run("UPDATE listings SET status='active'")
ev = html(c.get("/event?venue=Het+Sieraad&date=%s&name=Gala" % D))
row = ev.split('class="ticket-row"', 1)[1].split("</li>", 1)[0] if 'class="ticket-row"' in ev else ""
ok(row and "Report</a> · " not in row and 'class="report-link tr-report"' in row, "the Report link has its own quiet line, not in front of the face value")

print("\n%d failed" % len(fails))
