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

print("FAILED:", fails or "none")
