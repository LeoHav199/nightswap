import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; notify.send_email = lambda *a, **kw: False
import payments
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
def eur(cents):
    """Own copy of how English prices are written (€20, €1.99), so the test doesn't lean on the app's formatter."""
    return "€%d" % (cents // 100) if cents % 100 == 0 else "€%d.%02d" % (cents // 100, cents % 100)

# --- The launch settings. Changing one of these on purpose means changing this test too.
ok(config.FEE_PERCENT == 8 and config.FEE_MIN_CENTS == 199 and config.FEE_MAX_CENTS == 0, "buyer fee settings: 8%, minimum €1.99, no maximum")
ok(config.SELLER_FEE_PERCENT == 5 and config.SELLER_FEE_MIN_CENTS == 100, "seller fee settings: 5%, minimum €1")
ok(config.PROFIT_SHARE_PERCENT == 10, "profit share setting: 10%")

# (price, face value, buyer fee, seller fee), all in cents, worked out by hand:
#   buyer fee  = max(199, round-half-up(8% of price))
#   seller fee = max(100, round-half-up(5% of price)) + round-half-up(10% of (price - face)) when price > face
CASES = [
    # the four main prices with a face value below the price
    (500, 300, 199, 120),     # €5 on €3: 0.40 -> min 1.99; 0.25 -> min 1.00 + 10% of 2.00 = 0.20
    (2000, 1000, 199, 200),   # €20 on €10: 1.60 -> min 1.99; 1.00 + 10% of 10 = 1.00
    (4000, 2500, 320, 350),   # €40 on €25: 3.20; 2.00 + 10% of 15 = 1.50
    (10000, 5000, 800, 1000), # €100 on €50: 8.00; 5.00 + 10% of 50 = 5.00
    # at or below the face value: no profit share
    (500, 500, 199, 100),
    (2000, 2000, 199, 100),
    (2000, 2500, 199, 100),
    (4000, 4000, 320, 200),
    (10000, 10000, 800, 500),
    (10000, 12000, 800, 500),
    # rounding
    (2000, 1333, 199, 167),   # 10% of 6.67 = 0.667 -> 0.67
    (2005, 2000, 199, 101),   # 10% of 0.05 = 0.5 cent -> rounds up to 1 cent (not banker's rounding)
    (2010, 2010, 199, 101),   # 5% of 20.10 = 100.5 cents -> rounds up to 101
    (2506, 2506, 200, 125),   # 8% of 25.06 = 200.48 -> 200; 5% = 125.3 -> 125
    (2507, 2507, 201, 125),   # 8% of 25.07 = 200.56 -> 201; 5% = 125.35 -> 125
    (4999, 4999, 400, 250),   # 8% of 49.99 = 399.92 -> 400; 5% = 249.95 -> 250
]

# --- The formulas
with app.test_request_context():
    for price, face, bfee, sfee in CASES:
        label = "%s on a %s face value" % (eur(price), eur(face))
        ok(A.fee_for(price) == bfee, "buyer fee, %s: %s" % (label, eur(bfee)))
        ok(A.with_fee(price) == price + bfee, "buyer total, %s: %s" % (label, eur(price + bfee)))
        ok(A.seller_fee_for(price, face) == sfee, "seller fee, %s: %s" % (label, eur(sfee)))
        ok(A.payout_for({"price_cents": price, "seller_fee_cents": sfee}) == price - sfee, "payout, %s: %s" % (label, eur(price - sfee)))
    ok(A.seller_fee_for(2000) == 100 and A.seller_fee_for(2000, None) == 100 and A.seller_fee_for(2000, 0) == 100,
       "no face value known: only the 5% (min. €1) fee, no profit share")
    ok(A.seller_fee_for(50, 20) == 50, "the seller fee never goes above the price")

# --- The sell page: "You receive" and "Buyer pays" are worked out in the browser from the page's settings.
js = open(os.path.join("static", "js", "app.js"), encoding="utf-8").read()
for line in ("function feeFor(p) { var f = Math.max(feeMin, Math.round(p * feePct / 100)); return feeMax ? Math.min(f, feeMax) : f; }",
             "var fee = (sellerPct || sellerMin) ? Math.max(sellerMin, Math.round(p * sellerPct / 100)) : 0;",
             "if (profitShare && f && p > f) fee += Math.round((p - f) * profitShare / 100);",
             "return Math.min(p, fee);",
             'el.textContent = p ? money(p - sellerFeeFor(p, f)) : "–"',
             'buyerPays.textContent = p ? money(p + feeFor(p)) : "–"'):
    ok(line in js, "app.js keeps this sell-page formula: " + line[:60])

def js_round(x): return int(x + 0.5)  # Math.round for positive numbers
def js_buyer_fee(p, a):
    f = max(a["fee-min"], js_round(p * a["fee-pct"] / 100)); return min(f, a["fee-max"]) if a["fee-max"] else f
def js_seller_fee(p, f, a):
    fee = max(a["seller-min"], js_round(p * a["seller-pct"] / 100)) if (a["seller-pct"] or a["seller-min"]) else 0
    if a["profit-share"] and f and p > f: fee += js_round((p - f) * a["profit-share"] / 100)
    return min(p, fee)

D = (date.today() + timedelta(days=5)).isoformat()
admin, seller, buyer = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
admin.post("/admin/events/save", data={"name": "Fee Math Night", "venue": "Perron", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context():
    ev = A.db.one("SELECT * FROM events WHERE name='Fee Math Night'")
    A.db.run("UPDATE users SET stripe_account_id='acct_test' WHERE email='s@test.nl'")
sell_page = html(seller.get("/sell?event=%d" % ev["id"]))
attrs = {}
for key in ("fee-pct", "fee-min", "fee-max", "seller-pct", "seller-min", "profit-share"):
    m = re.search(r'data-%s="([\d.]+)"' % key, sell_page)
    attrs[key] = float(m.group(1)) if m else None
ok(all(v is not None for v in attrs.values()) and attrs == {"fee-pct": 8, "fee-min": 199, "fee-max": 0, "seller-pct": 5, "seller-min": 100, "profit-share": 10},
   "the sell page hands the browser 8 / 199 / no max / 5 / 100 / 10 (got %s)" % attrs)
if all(v is not None for v in attrs.values()):
    for price, face, bfee, sfee in CASES:
        ok(js_buyer_fee(price, attrs) == bfee and js_seller_fee(price, face, attrs) == sfee,
           "sell page works out %s on %s as buyer fee %s, you receive %s" % (eur(price), eur(face), eur(bfee), eur(price - sfee)))

# --- A fake Stripe, so the amounts that would be sent can be read
class Obj:
    def __init__(self, **kw): self.__dict__.update(kw)
SESSIONS, TRANSFERS = [], []
def fake_create(**kw):
    SESSIONS.append(kw); return Obj(id="cs_%d" % len(SESSIONS), url="https://checkout.stripe.com/x")
def fake_transfer(**kw):
    TRANSFERS.append(kw); return Obj(id="tr_%d" % len(TRANSFERS))
fake_stripe = Obj(checkout=Obj(Session=Obj(create=fake_create)),
                  Transfer=Obj(create=fake_transfer),
                  PaymentIntent=Obj(retrieve=lambda pi: Obj(latest_charge="ch_1")))
real_stripe = payments._stripe
payments._stripe = lambda: fake_stripe

def price_text(cents): return "%d.%02d" % (cents // 100, cents % 100)
def sell(price, face, qty):
    files = [(io.BytesIO(b"%%PDF-1.4 fee math %d %d of %d no. %d" % (price, face, qty, i)), "t%d.pdf" % i) for i in range(qty)]
    r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": price_text(face),
        "price": price_text(price), "adult": "yes", "ticket": files, "_csrf": csrf(seller)}, content_type="multipart/form-data")
    m = re.search(r"/t/(\d+)", r.location or "")
    return int(m.group(1)) if m else None

def run_case(price, face, bfee, sfee, qty):
    label = "%s on a %s face value, %d ticket%s" % (eur(price), eur(face), qty, "s" if qty > 1 else "")
    lid = sell(price, face, qty)
    if not lid:
        ok(False, "could not list " + label); return
    total, shown_price = (price + bfee) * qty, price * qty
    # the listing page: the buyer sees price + fee, the seller sees what they will receive
    bp = html(buyer.get("/t/%d" % lid))
    ok(('data-price="%d" data-fee="%d"' % (price, bfee)) in bp, "listing page (buyer), %s: price and fee as the browser gets them" % label)
    # (the page starts at one ticket; the browser multiplies when the buyer picks more)
    ok(re.search(r"data-feesum>%s<" % re.escape(eur(bfee)), bp) is not None
       and re.search(r"data-total>%s<" % re.escape(eur(price + bfee)), bp) is not None, "listing page (buyer), %s: one ticket, fee %s, total %s" % (label, eur(bfee), eur(price + bfee)))
    sp = html(seller.get("/t/%d" % lid))
    ok(re.search(r"receive %s per ticket" % re.escape(eur(price - sfee)), sp) is not None, "listing page (seller), %s: you receive %s" % (label, eur(price - sfee)))
    # buying: Stripe is asked for the ticket price and the fee, which add up to the order total
    config.STRIPE_SECRET_KEY = "sk_test_fake"; SESSIONS.clear()
    r = buyer.post("/t/%d/buy" % lid, data={"qty": str(qty), "_csrf": csrf(buyer)})
    config.STRIPE_SECRET_KEY = ""
    pub = (r.location or "").split("/")[2] if "/" in (r.location or "") else None
    with app.app_context(): o = A.db.one("SELECT * FROM orders WHERE listing_id=? ORDER BY id DESC", lid)
    if not SESSIONS or not o:
        ok(False, "no Stripe checkout was started for " + label); return
    pub = o["public_id"]; items = SESSIONS[0]["line_items"]
    amounts = [i["price_data"]["unit_amount"] * i["quantity"] for i in items]
    ok(o["price_cents"] == shown_price and o["fee_cents"] == bfee * qty and o["total_cents"] == total and o["seller_fee_cents"] == sfee * qty,
       "order, %s: price %s, fee %s, total %s, seller fee %s" % (label, eur(shown_price), eur(bfee * qty), eur(total), eur(sfee * qty)))
    ok(sum(amounts) == total == o["total_cents"], "Stripe is charged %s, the same as the order total" % eur(total))
    ok(amounts[0] == shown_price and (amounts[1:] == [bfee * qty]), "Stripe line items: tickets %s and service fee %s" % (eur(shown_price), eur(bfee * qty)))
    # the checkout page (the pretend one used without a Stripe key) and the order page
    cp = html(buyer.get("/checkout/%s/test" % pub))
    ok(all(re.search(r"<dd>%s</dd>" % re.escape(eur(x)), cp) for x in (shown_price, bfee * qty, total)) and ("Pay " + eur(total)) in cp,
       "checkout page, %s: %s + %s = %s and the pay button says so" % (label, eur(shown_price), eur(bfee * qty), eur(total)))
    buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
    op = html(buyer.get("/orders/%s" % pub))
    ok(all(re.search(r"<dd>%s</dd>" % re.escape(eur(x)), op) for x in (shown_price, bfee * qty, total)),
       "order page (buyer), %s: %s + %s = %s" % (label, eur(shown_price), eur(bfee * qty), eur(total)))
    sop = html(seller.get("/orders/%s" % pub))
    ok(re.search(r"<dd>%s</dd>" % re.escape(eur(shown_price)), sop) is not None and ("<dt>Total</dt>" not in sop)
       and re.search(r"holds your %s\b" % re.escape(eur(shown_price - sfee * qty)), sop) is not None,
       "order page (seller), %s: ticket price %s (no buyer fee), Spare holds %s for the seller" % (label, eur(shown_price), eur(shown_price - sfee * qty)))
    # the payout to the seller: the price minus the seller fee, never the buyer's fee
    with app.app_context():
        o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub); s = A.db.one("SELECT * FROM users WHERE email='s@test.nl'")
        TRANSFERS.clear(); config.STRIPE_SECRET_KEY = "sk_test_fake"
        try: payments.transfer_to_seller(o, s)
        finally: config.STRIPE_SECRET_KEY = ""
    ok(TRANSFERS and TRANSFERS[0]["amount"] == shown_price - sfee * qty,
       "Stripe pays the seller %s (%s minus seller fee %s)" % (eur(shown_price - sfee * qty), eur(shown_price), eur(sfee * qty)))

for price, face, bfee, sfee in CASES:
    run_case(price, face, bfee, sfee, 1)
# two tickets bought together: both fees are charged per ticket
run_case(4000, 2500, 320, 350, 2)
run_case(2000, 2000, 199, 100, 2)

payments._stripe = real_stripe
print("FAILED:", fails or "none")
