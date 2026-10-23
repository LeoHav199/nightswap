import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.FEE_PERCENT = 8; config.FEE_MIN_CENTS = 100
import notify; notify.send_email = lambda *a: False
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
with app.test_request_context():
    ok(A.fee_for(1500) == 120 and A.fee_for(1000) == 100 and A.fee_for(500) == 100, "8% fee: €15 → €1.20, small tickets pay the €1 minimum")
    ok(A.fee_for(9000) == 720 and A.fee_for(18000) == 1440, "festival tickets: €90 → €7.20, €180 → €14.40")
    ok(A.fee_for(2550) == 204, "rounds to the nearest cent (€25.50 → €2.04)")
    config.FEE_MAX_CENTS = 1000; ok(A.fee_for(18000) == 1000, "optional maximum caps the fee"); config.FEE_MAX_CENTS = 0
D = (date.today() + timedelta(days=5)).isoformat()
admin, seller, buyer = app.test_client(), app.test_client(), app.test_client()
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
admin.post("/admin/events/save", data={"name": "Summer Fest", "venue": "Strijp-S", "city": "Eindhoven", "category": "festival", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Summer Fest'")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "90", "price": "90", "adult": "yes",
    "ticket": [(io.BytesIO(b"%PDF-1.4 a"), "a.pdf"), (io.BytesIO(b"%PDF-1.4 b"), "b.pdf")], "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
ok("€97.20" in html(buyer.get("/")), "homepage card shows €90 + €7.20 = €97.20")
lp = html(buyer.get("/t/%d" % lid))
ok("€7.20" in lp and "€97.20" in lp, "listing shows the €7.20 fee and the total")
ok("8% (min. €1)" in html(buyer.get("/how-it-works")) and "8% (min. €1)" in html(buyer.get("/terms")), "How it works and Terms explain the 8% (min. €1) fee")
r = buyer.post("/t/%d/buy" % lid, data={"qty": "2", "_csrf": csrf(buyer)})
pub = r.location.split("/")[2]
with app.app_context(): o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
ok(o["price_cents"] == 18000 and o["fee_cents"] == 1440 and o["total_cents"] == 19440, "2 tickets: €180 + €14.40 fee = €194.40 charged")
ok("€14.40" in html(buyer.get("/checkout/%s/test" % pub)), "checkout shows the fee")
with app.test_request_context():
    nl = A.i18n.t("nl", "fee_label", pct="8", min=A.i18n.money("nl", 100)); ok(nl == "8% (min. €1)", "Dutch fee label")
print("FAILED:", fails or "none")
