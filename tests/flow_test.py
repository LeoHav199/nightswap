import io, os, re, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.ADMIN_SECOND_FACTOR = False
config.FEE_PERCENT = 0; config.FEE_MIN_CENTS = 100  # these tests were written for a flat €1 fee
config.SELLER_FEE_PERCENT = 0; config.SELLER_FEE_MIN_CENTS = 0  # ...and no seller fee
config.STRIPE_SECRET_KEY = ""
tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up")
config.ADMIN_EMAILS = {"admin@example.com"}
import app as A
app = A.app; app.config["TESTING"] = True
import seed
with app.app_context(): A.db.init(); seed.run()

def csrf(c):
    with c.session_transaction() as s: return s["csrf"]

def login(c, email, name):
    c.get("/")
    r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    code = re.search(r"code is (\d{6})", r.get_data(as_text=True)).group(1)
    r = c.post("/login/verify", data={"code": code, "first_name": name, "_csrf": csrf(c)})
    assert r.status_code == 302, r.status_code

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg)

seller = app.test_client(); buyer = app.test_client()
r = seller.get("/"); ok(r.status_code == 200 and "Thursday Student Night" in r.get_data(as_text=True), "home lists tickets")
ok(seller.post("/login", data={"email": "x@y.nl"}).status_code == 400, "POST without csrf rejected")
login(seller, "seller@test.nl", "Femke")
r = seller.get("/sell"); ok(r.status_code == 302 and "/account/phone" in r.location, "sell requires phone")
r = seller.post("/account/phone", data={"phone": "06 1234 5678", "next": "/sell", "_csrf": csrf(seller)}, follow_redirects=True)
code = re.search(r"code is (\d{6})", r.get_data(as_text=True)).group(1)
r = seller.post("/account/phone/verify", data={"code": code, "_csrf": csrf(seller)})
ok(r.location.endswith("/sell"), "phone verified, back to sell")
pdf = b"%PDF-1.4 unique test ticket"
def post(face, price, data=pdf):
    return seller.post("/sell", data={"venue": "Perron", "event_date": "2026-11-07", "face_value": face, "price": price,
        "event_name": "Test Night", "adult": "yes", "ticket": (io.BytesIO(data), "t.pdf"), "poster": (io.BytesIO(b"\x89PNG fake poster"), "p.png"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
r = post("20", "25"); ok("maximum for this ticket is €24" in r.get_data(as_text=True), "price cap enforced (face value + 20%)")
ok(A.max_price(2000) == 2400 and A.max_price(1550) == 1860, "sellers may ask up to 20% above face value")
r = post("20", "18,50"); ok(r.status_code == 302 and "/t/" in r.location, "listing created")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
r = post("20", "18"); ok("already been listed" in r.get_data(as_text=True), "duplicate file blocked")
r = post("20", "18", b"not a pdf"); ok("Upload a PDF" in r.get_data(as_text=True), "bad file type blocked")

r = buyer.get("/t/%d" % lid); ok("€19.50" in r.get_data(as_text=True), "detail shows total incl €1 fee")
r = buyer.get("/"); ok("Test Night" in r.get_data(as_text=True) and "/posters/" in r.get_data(as_text=True), "home shows event card with poster")
r = buyer.get("/?day=2026-11-07"); ok("Test Night" in r.get_data(as_text=True), "day tab filters to that night")
r = buyer.get("/?day=2026-11-08"); ok("Test Night" not in r.get_data(as_text=True), "other day excludes it")
r = buyer.get("/event?venue=Perron&date=2026-11-07&name=Test+Night"); ok(r.status_code == 200 and "€19.50" in r.get_data(as_text=True), "event page lists ticket")
import re as _re
pf = _re.search(r"/posters/([0-9a-f]+\.png)", buyer.get("/").get_data(as_text=True)).group(1)
ok(buyer.get("/posters/" + pf).status_code == 200, "poster served publicly")
ok(buyer.get("/posters/../app.py").status_code == 404, "poster path traversal blocked")
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); ok("/login" in r.location, "buy requires login")
login(buyer, "buyer@test.nl", "Jesse")
r = seller.post("/t/%d/buy" % lid, data={"_csrf": csrf(seller)}, follow_redirects=True); ok("buy your own" in r.get_data(as_text=True), "can't buy own")
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); ok("/checkout/" in r.location, "redirect to checkout")
pub = r.location.split("/")[2]
_h = buyer.get("/").get_data(as_text=True); ok("€19.50" not in _h and "Coming up" in _h, "ticket being paid for is off sale (event moves to 'Coming up')")
ok(buyer.get("/orders/%s/tickets/1" % pub).status_code == 404, "no ticket before payment")
r = buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)}, follow_redirects=True)
ok("got your ticket" in r.get_data(as_text=True), "payment completes")
tid = re.search(r"/orders/%s/tickets/(\d+)" % pub, buyer.get("/orders/%s" % pub).get_data(as_text=True)).group(1)
r = buyer.get("/orders/%s/tickets/%s" % (pub, tid)); ok(r.status_code == 200 and r.data == pdf, "buyer downloads ticket")
ok(app.test_client().get("/orders/%s" % pub).status_code == 302, "stranger redirected to login")
r = seller.get("/orders/%s" % pub); ok("receive €18.50" in r.get_data(as_text=True), "seller sees payout amount")
r = buyer.post("/orders/%s/report" % pub, data={"reason": "QR code rejected at the door", "_csrf": csrf(buyer)}, follow_redirects=True)
ok("received your report" in r.get_data(as_text=True), "problem reported")
admin = app.test_client(); login(admin, "admin@example.com", "Admin")
ok("QR code rejected" in admin.get("/admin").get_data(as_text=True), "admin sees dispute")
r = admin.post("/admin/orders/%s/refund" % pub, data={"_csrf": csrf(admin)}, follow_redirects=True)
ok("Refunded €19.50" in r.get_data(as_text=True), "admin refund")
ok(buyer.get("/admin").status_code == 404, "admin hidden from others")
r = buyer.get("/lang/nl?next=/", follow_redirects=True); ok("Tickets voor vanavond" in r.get_data(as_text=True), "Dutch toggle")
for p in ["/how-it-works", "/terms", "/privacy", "/account", "/sell", "/nope"]:
    print("   ", p, buyer.get(p).status_code)
