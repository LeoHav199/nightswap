import io, os, re, sys, tempfile, json
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.FEE_PERCENT = 0
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []
notify.send_email = lambda to, s, b: (SENT.append((to, s, b)), False)[1]
import payments; REFUNDS = []
_orig_refund = payments.refund
payments.refund = lambda o: (REFUNDS.append(o["public_id"]), _orig_refund(o))[1]
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def code_of(r): return re.search(r"code is (\d{6})", html(r)).group(1)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": code_of(r), "first_name": name, "_csrf": csrf(c)})
def phone(c, num):
    r = c.post("/account/phone", data={"phone": num, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": code_of(r), "_csrf": csrf(c)})
D = (date.today() + timedelta(days=5)).isoformat()
admin, seller, buyer, buyer2, visitor = (app.test_client() for _ in range(5))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo"); login(buyer2, "c@test.nl", "Cy"); visitor.get("/")

# ---- Admin second factor
r = admin.get("/admin")
ok(r.status_code == 302 and "/admin/verify" in r.location, "admin area asks for the extra check")
r = admin.get("/admin/verify", follow_redirects=True)
ok("verified phone number" in html(r), "admins must have a verified phone first")
phone(admin, "0699999999")
r = admin.get("/admin/verify", follow_redirects=True)
real = code_of(r)
admin.post("/admin/verify", data={"code": "000000" if real != "000000" else "111111", "next": "/admin", "_csrf": csrf(admin)})
ok(admin.get("/admin").status_code == 302, "wrong admin code doesn't get you in")
admin.post("/admin/verify", data={"code": real, "next": "/admin", "_csrf": csrf(admin)})
ok(admin.get("/admin").status_code == 200, "correct text code opens the admin area")

# ---- 18+ to sell
phone(seller, "0611111111")
admin.post("/admin/events/save", data={"name": "Big Night", "venue": "Maassilo", "category": "club", "event_date": D, "door_time": "23:00", "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Big Night'")
sell = lambda data, **kw: seller.post("/sell", data=dict({"event_id": str(ev["id"]), "face_value": "20", "price": "20", "_csrf": csrf(seller),
                                       "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(6)), "t.pdf")}, **data), content_type="multipart/form-data")
ok("18 or older" in html(sell({})), "selling needs the 18+ confirmation")
r = sell({"adult": "yes"})
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
ok("18 or older" not in html(seller.get("/sell")), "confirmed once, not asked again")
sell({}); sell({})
with app.app_context(): lids = [x["id"] for x in A.db.all("SELECT id FROM listings WHERE seller_id=(SELECT id FROM users WHERE email='s@test.nl')")]

# ---- Report a listing (works signed out too)
r = visitor.post("/t/%d/report" % lid, data={"reason": "fake", "details": "Same QR posted on Instagram", "_csrf": csrf(visitor)}, follow_redirects=True)
ok("valid email" in html(r), "signed-out reporters must give an email")
visitor.post("/t/%d/report" % lid, data={"reason": "fake", "details": "Same QR posted on Instagram", "email": "x@y.nl", "_csrf": csrf(visitor)})
ok(any("listing reported" in s for _, s, _ in SENT), "admins are emailed about the report")
ap = html(admin.get("/admin"))
ok("Reported listings" in ap and "Same QR posted on Instagram" in ap, "report shows in admin with the details")
ok("Report this listing" in html(buyer.get("/t/%d" % lid)) and ">Report<" in html(buyer.get("/event?venue=Maassilo&date=%s&name=Big+Night" % D)), "report links on listing and event pages")

# ---- Buy two tickets, then the event moves
for c in (buyer, buyer2):
    r = c.post("/t/%d/buy" % lids[1 if c is buyer else 2], data={"_csrf": csrf(c)}); c.post(r.location, data={"_csrf": csrf(c)})
with app.app_context(): orders = {o["buyer_id"]: o for o in A.db.all("SELECT * FROM orders WHERE status='paid'")}
NEW = (date.today() + timedelta(days=9)).isoformat()
SENT.clear()
admin.post("/admin/events/save", data={"id": ev["id"], "name": "Big Night", "venue": "Maassilo", "category": "club", "event_date": NEW, "door_time": "23:00", "_csrf": csrf(admin)})
ok(sum(1 for to, s, _ in SENT if s.startswith("Changed:")) == 4, "date change emails both buyers and both sellers")
with app.app_context():
    o1 = A.db.one("SELECT * FROM orders WHERE buyer_id=(SELECT id FROM users WHERE email='b@test.nl')")
    ok(o1["payout_due_at"].startswith((date.fromisoformat(NEW) + timedelta(days=3)).isoformat()), "payout date moves with the event")
page = html(buyer.get("/orders/%s" % o1["public_id"]))
ok("This event changed" in page and "Get a full refund" in page, "buyer sees the change and a refund button")
buyer.post("/orders/%s/change-refund" % o1["public_id"], data={"_csrf": csrf(buyer)})
with app.app_context():
    ok(A.db.one("SELECT status FROM orders WHERE id=?", o1["id"])["status"] == "refunded", "buyer refunded after the change")
    ok(A.db.one("SELECT status FROM listings WHERE id=?", lids[1])["status"] == "active", "the ticket goes back on sale for the seller")

# ---- Cancel the event: everyone refunded
REFUNDS.clear()
admin.post("/admin/events/%d/cancel" % ev["id"], data={"_csrf": csrf(admin)})
with app.app_context():
    st = [r_["status"] for r_ in A.db.all("SELECT status FROM orders")]
    live = A.db.one("SELECT COUNT(*) AS n FROM listings WHERE event_id=? AND status='active'", ev["id"])["n"]
ok(len(REFUNDS) == 1 and st.count("refunded") == 2, "cancel refunds the remaining buyer in full")
ok(live == 0, "all tickets for the cancelled event go off sale")
ep = html(visitor.get("/event?venue=Maassilo&date=%s&name=Big+Night" % NEW))
ok("This event is cancelled" in ep, "event page says it's cancelled")
ok("Big Night" not in html(visitor.get("/api/events?date=%s" % NEW)), "cancelled events can't be picked when selling")
seller2 = app.test_client(); login(seller2, "s2@test.nl", "Sue"); phone(seller2, "0622222222")
with app.app_context(): before = A.db.one("SELECT COUNT(*) AS n FROM listings")["n"]
r = seller2.post("/sell", data={"event_id": "new", "venue": "Maassilo", "event_name": "Big Night", "event_date": NEW, "category": "club",
    "face_value": "20", "price": "20", "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 sneaky"), "t.pdf"), "_csrf": csrf(seller2)}, content_type="multipart/form-data")
with app.app_context(): after = A.db.one("SELECT COUNT(*) AS n FROM listings")["n"]
ok(before == after and "isn" in html(r), "re-adding a cancelled event by hand is refused (no listing created)")

# ---- Block a user
admin.post("/admin/events/save", data={"name": "Other Night", "venue": "Toffler", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev2 = A.db.one("SELECT * FROM events WHERE name='Other Night'")
r = seller.post("/sell", data={"event_id": str(ev2["id"]), "face_value": "20", "price": "20", "_csrf": csrf(seller),
                               "ticket": (io.BytesIO(b"%PDF-1.4 other"), "t.pdf")}, content_type="multipart/form-data")
ok("Sam" in html(admin.get("/admin/users?q=s@test")), "admin finds users by email")
with app.app_context(): sid = A.db.one("SELECT id FROM users WHERE email='s@test.nl'")["id"]
admin.post("/admin/users/%d/block" % sid, data={"reason": "Sold fake tickets", "_csrf": csrf(admin)})
with app.app_context():
    ok(A.db.one("SELECT COUNT(*) AS n FROM listings WHERE seller_id=? AND status='active'", sid)["n"] == 0, "blocked seller's listings go off sale")
ok(seller.get("/account").status_code == 302, "blocked user is signed out")
r = seller.post("/login", data={"email": "s@test.nl", "_csrf": csrf(seller)}, follow_redirects=True)
r = seller.post("/login/verify", data={"code": code_of(r), "_csrf": csrf(seller)})
ok("has been blocked" in html(r), "blocked user can't sign back in")

# ---- Rate limits
rl = app.test_client(); rl.get("/")
hits = [rl.post("/login", data={"email": "spam%d@x.nl" % i, "_csrf": csrf(rl)}) for i in range(11)]
ok("Too many attempts" in html(hits[-1]), "login codes are rate-limited per device")

# ---- Your data: export + delete
exp = buyer2.get("/account/export")
data = json.loads(exp.data)
ok(exp.mimetype == "application/json" and data["account"]["email"] == "c@test.nl" and data["purchases"], "data export contains account and purchases")
r = buyer2.post("/account/delete", data={"confirm": "wrong@x.nl", "_csrf": csrf(buyer2)}, follow_redirects=True)
ok("doesn't match" in html(r) or "doesn&#39;t match" in html(r), "deleting needs the email typed exactly")
buyer2.post("/account/delete", data={"confirm": "c@test.nl", "_csrf": csrf(buyer2)})
with app.app_context():
    u = A.db.one("SELECT * FROM users WHERE id=(SELECT buyer_id FROM orders WHERE status='refunded' ORDER BY id DESC LIMIT 1)")
    ok(u["deleted_at"] and u["email"].endswith("@deleted.invalid") and u["phone"] is None, "account anonymised; orders kept for bookkeeping")
ok(buyer2.get("/account").status_code == 302, "deleted user is signed out")

# ---- Branded email
import notify as N
h = N.email_html("x", "Hi Bo,\n\nYour ticket:\nhttps://nightswap.nl/orders/X\n\nNightswap")
ok("Open Nightswap" in h and "nightswap" in h and "#ff5a8a" in h, "emails get a branded HTML version with a button")
print("FAILED:", fails or "none")
