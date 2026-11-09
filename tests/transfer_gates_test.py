import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.MAX_TICKETS_PER_LISTING = 10  # one app listing is enough for all the orders below
import notify
notify.send_email = lambda to, subject, body, attachments=(), **kw: False  # never send mail from tests
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
def order(pub):
    with app.app_context(): return A.db.one("SELECT * FROM orders WHERE public_id=?", pub)

# release_payout's app-ticket gates (the "never sent" gate and the #31 gate), one order per case.
admin, seller, buyer = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
D = (date.today() + timedelta(days=3)).isoformat()
admin.post("/admin/events/save", data={"face_value_max": "500", "name": "Gate Night", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context():
    ev = A.db.one("SELECT * FROM events WHERE name='Gate Night'")
    A.db.run("UPDATE users SET payouts_ready=1 WHERE email='s@test.nl'")
base = {"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes", "_csrf": csrf(seller)}
r = seller.post("/sell", data=dict(base, delivery="transfer", transfer_app="ticketmaster", transfer_qty="10"))
app_listing = int(re.search(r"/t/(\d+)", r.location).group(1))
r = seller.post("/sell", data=dict(base, ticket=(io.BytesIO(b"%PDF-1.4 gate"), "g.pdf")), content_type="multipart/form-data")
file_listing = int(re.search(r"/t/(\d+)", r.location).group(1))
with app.app_context():
    ok(A.db.one("SELECT delivery FROM listings WHERE id=?", app_listing)["delivery"] == "transfer"
       and A.db.one("SELECT delivery FROM listings WHERE id=?", file_listing)["delivery"] == "file", "set-up: one app listing and one file listing")

def paid_order(listing_id, **fields):
    """A paid order for the listing, payout date passed, with the given columns set."""
    data = {"qty": "1", "_csrf": csrf(buyer)}
    if listing_id == app_listing: data.update(transfer_name="Bo", transfer_email="bo@example.com")
    r = buyer.post("/t/%d/buy" % listing_id, data=data)
    pub = r.location.split("/")[2]
    buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
    with app.app_context():
        A.db.run("UPDATE orders SET payout_due_at=? WHERE public_id=?", A.sqltime(A.utcnow() - timedelta(minutes=1)), pub)
        for col, val in fields.items(): A.db.run("UPDATE orders SET %s=? WHERE public_id=?" % col, val, pub)
    return pub

def pay(pub):
    with app.test_request_context(): return A.release_payout(order(pub))

NOW = A.sqltime(A.utcnow())

# Not paid
pub = paid_order(app_listing)
ok(order(pub)["status"] == "paid" and pay(pub) is False and order(pub)["status"] == "paid", "an app ticket that was never marked transferred is not paid")
pub = paid_order(app_listing, buyer_reminders=1)
ok(pay(pub) is False and order(pub)["status"] == "paid", "never transferred stays unpaid even after the buyer was reminded")
pub = paid_order(app_listing, transferred_at=NOW)
ok(order(pub)["buyer_reminders"] == 0 and pay(pub) is False and order(pub)["status"] == "paid",
   "transferred, but the buyer hasn't been reminded or confirmed: not paid (#31)")
pub = paid_order(app_listing, transferred_at=NOW, rating="down")
ok(pay(pub) is False and order(pub)["status"] == "paid", "a thumbs-down rating does not open the gate either")

# Paid
pub = paid_order(app_listing, transferred_at=NOW, buyer_reminders=1)
ok(pay(pub) is True and order(pub)["status"] == "released", "transferred and the buyer has been reminded: paid")
pub = paid_order(app_listing, transferred_at=NOW, received_at=NOW)
ok(pay(pub) is True and order(pub)["status"] == "released", "transferred and the buyer confirmed (received_at): paid")
pub = paid_order(app_listing, received_at=NOW)
ok(pay(pub) is True and order(pub)["status"] == "released", "the buyer confirmed without the seller's mark: paid")
pub = paid_order(app_listing, transferred_at=NOW, rating="up")
ok(order(pub)["buyer_reminders"] == 0 and pay(pub) is True and order(pub)["status"] == "released", "a thumbs-up rating pays an app ticket without any reminder")
pub = paid_order(app_listing, rating="up")
ok(pay(pub) is True and order(pub)["status"] == "released", "a thumbs-up rating alone is enough as well")

# File tickets
pub = paid_order(file_listing)
ok(order(pub)["transferred_at"] is None and order(pub)["received_at"] is None and order(pub)["buyer_reminders"] == 0
   and pay(pub) is True and order(pub)["status"] == "released", "a file ticket is paid with no transfer, confirmation or reminder")

print("FAILED:", fails or "none")
