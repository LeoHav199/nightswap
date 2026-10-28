import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(): SENT.append((to, subject, body)) and False
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
    code = re.search(r"code is (\d{6})", html(r)).group(1)
    c.post("/login/verify", data={"code": code, "first_name": name, "_csrf": csrf(c)})

# --- The default fees (what Spare launches with)
ok(config.FEE_PERCENT == 8 and config.FEE_MIN_CENTS == 150, "buyers: 8%, minimum €1.50")
ok(config.SELLER_FEE_PERCENT == 5 and config.SELLER_FEE_MIN_CENTS == 100, "sellers: 5%, minimum €1")
with app.test_request_context():
    ok(A.fee_for(1000) == 150 and A.fee_for(2000) == 160 and A.fee_for(5000) == 400, "buyer fee: €10 → €1.50, €20 → €1.60, €50 → €4")
    ok(A.seller_fee_for(1000) == 100 and A.seller_fee_for(2000) == 100 and A.seller_fee_for(3000) == 150 and A.seller_fee_for(5000) == 250,
       "seller fee: €10 → €1, €20 → €1, €30 → €1.50, €50 → €2.50")
    ok(A.seller_fee_for(50) == 50, "the seller fee never exceeds the ticket price")
    # Worst case per Stripe's Dutch prices: iDEAL €0.29, €2 per paid seller per month, payout 0.25% + €0.10
    for price in (1000, 2000, 3000, 5000):
        cost = 29 + 200 + round(price * 0.0025) + 10
        ok(A.fee_for(price) + A.seller_fee_for(price) > cost, "a one-off €%d sale by iDEAL covers Stripe's costs" % (price // 100))

# --- A real sale
D = (date.today() + timedelta(days=5)).isoformat()
admin, seller, buyer = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
admin.post("/admin/events/save", data={"name": "Fee Night", "venue": "Perron", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Fee Night'")
sell_page = html(seller.get("/sell?event=%d" % ev["id"]))
ok('data-seller-pct="5.0"' in sell_page and 'data-seller-min="100"' in sell_page and "5% (min. €1)" in sell_page,
   "the sell form knows the seller fee, to show 'you receive' live")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
    "ticket": (io.BytesIO(b"%PDF-1.4 fee"), "f.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
ok("receive €19 per ticket" in html(seller.get("/t/%d" % lid)), "the seller's listing says they'll receive €19")
ok("€21.60" in html(buyer.get("/t/%d" % lid)), "the buyer pays €20 + €1.60")
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); pub = r.location.split("/")[2]
with app.app_context(): o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
ok(o["price_cents"] == 2000 and o["fee_cents"] == 160 and o["seller_fee_cents"] == 100, "the order stores the €1 seller fee at purchase")
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
ok(re.search(r"receive €19\b", html(seller.get("/orders/%s" % pub))) is not None, "after the sale the seller sees €19 coming")
ok(any("receive €19 " in body for to, s, body in SENT if to == "s@test.nl"), "the 'sold' email says €19")
with app.app_context():
    o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
    ok(A.payout_for(o) == 1900, "the payout is €19.00")
    A.db.run("UPDATE orders SET seller_fee_cents=0 WHERE id=?", o["id"])
    ok(A.payout_for(A.db.one("SELECT * FROM orders WHERE id=?", o["id"])) == 2000, "orders from before the seller fee still pay out in full")
    A.db.run("UPDATE orders SET seller_fee_cents=100 WHERE id=?", o["id"])
ok("€2.60 fees" in html(admin.get("/admin")), "admin totals include both fees (€1.60 + €1)")

# --- Paid as soon as the buyer confirms the ticket worked
with app.app_context():
    A.db.run("UPDATE listings SET event_date=? WHERE id=?", date.today().isoformat(), lid)  # the night is here
    A.db.run("UPDATE users SET payouts_ready=1 WHERE email='s@test.nl'")
    o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
ok(o["status"] == "paid" and o["payout_due_at"] > A.sqltime(A.utcnow()), "before confirming, the payout waits for its date")
buyer.post("/orders/%s/rate" % pub, data={"rating": "up", "_csrf": csrf(buyer)})
with app.app_context(): o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
ok(o["status"] == "released" and o["rating"] == "up", "the buyer's thumbs-up pays the seller straight away")
ok("on its way" in html(seller.get("/orders/%s" % pub)), "the seller sees the money is on its way")
ok("as soon as the buyer confirms the ticket worked" in html(buyer.get("/how-it-works")).lower(), "How it works explains the early payout")

# --- Explained honestly
how = html(buyer.get("/how-it-works"))
ok("5% (min. €1) per ticket, taken from the payout" in how and "8% (min. €1.50)" in how and ">Free<" not in how, "How it works lists both fees")
terms = html(buyer.get("/terms"))
ok("seller fee of 5% (min. €1)" in terms and "Sellers pay no fees" not in terms, "the terms mention the seller fee")
ok("Selling is free" not in html(seller.get("/sell")), "no more 'selling is free'")

print("FAILED:", fails or "none")
