import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; MAIL_WORKS = [False]
notify.send_email = lambda to, subject, body, attachments=(): (SENT.append((to, subject, body)) or True) if MAIL_WORKS[0] else False
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

admin, seller, buyer = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
D = (date.today() + timedelta(days=3)).isoformat()
admin.post("/admin/events/save", data={"name": "App Night", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='App Night'")

# --- Listing a ticket that lives in an app
page = html(seller.get("/sell?event=%d" % ev["id"]))
ok('name="delivery" value="transfer"' in page and "Ticketmaster" in page and "Weeztix" in page, "the sell form offers 'in an app (I transfer it)'")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes", "delivery": "transfer",
                               "transfer_app": "made-up", "transfer_qty": "2", "_csrf": csrf(seller)})
ok(r.status_code != 302 and "Pick the app" in html(r), "an unknown app is refused")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes", "delivery": "transfer",
                               "transfer_app": "ticketmaster", "transfer_qty": "2", "_csrf": csrf(seller)})
ok(r.status_code == 302 and "/t/" in r.location, "an app ticket can be listed without uploading a file")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
with app.app_context():
    l = A.db.one("SELECT * FROM listings WHERE id=?", lid)
    n = A.db.one("SELECT COUNT(*) AS n FROM tickets WHERE listing_id=? AND status='available'", lid)["n"]
ok(l["delivery"] == "transfer" and l["transfer_app"] == "ticketmaster" and n == 2, "it's stored as 2 Ticketmaster tickets")
ok("Sent via Ticketmaster" in html(buyer.get("/event?venue=Maassilo&date=%s&name=App+Night" % D)), "buyers see it's sent via Ticketmaster")

# --- Buying it
lp = html(buyer.get("/t/%d" % lid))
ok('name="transfer_email"' in lp and "Your email in Ticketmaster" in lp, "the buy form asks for the buyer's Ticketmaster email")
r = buyer.post("/t/%d/buy" % lid, data={"qty": "1", "transfer_name": "", "transfer_email": "nope", "_csrf": csrf(buyer)}, follow_redirects=True)
ok("Fill in your name" in html(r), "buying without transfer details is refused")
r = buyer.post("/t/%d/buy" % lid, data={"qty": "1", "transfer_name": "Bo  de   Vries", "transfer_email": "Bo.TM@Example.com", "_csrf": csrf(buyer)})
pub = r.location.split("/")[2]
ok(order(pub)["transfer_name"] == "Bo de Vries" and order(pub)["transfer_email"] == "bo.tm@example.com", "the transfer name and email are saved with the order")
MAIL_WORKS[0] = True
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
mail_seller = [b for to, s, b in SENT if to == "s@test.nl"]
ok(mail_seller and "transfer the ticket to Bo de Vries (bo.tm@example.com)" in mail_seller[-1] and mail_seller[-1].rstrip().endswith("Spare"),
   "the seller's 'sold' email says who to transfer it to, above the sign-off")
ok(any("send your ticket to bo.tm@example.com in Ticketmaster" in b for to, s, b in SENT if to == "b@test.nl"), "the buyer's email says it's coming via Ticketmaster")
sp = html(seller.get("/orders/%s" % pub))
ok("Transfer the ticket now" in sp and "bo.tm@example.com" in sp and "I&#39;ve transferred it" in sp, "the seller's order page shows the transfer task")
bp = html(buyer.get("/orders/%s" % pub))
ok("will send your ticket to bo.tm@example.com in Ticketmaster" in bp and "Download ticket" not in bp, "the buyer sees it's on its way, with no download button")

# --- No money before the transfer
with app.app_context():
    A.db.run("UPDATE users SET payouts_ready=1 WHERE email='s@test.nl'")
    A.db.run("UPDATE orders SET payout_due_at=? WHERE public_id=?", A.sqltime(A.utcnow() - timedelta(minutes=1)), pub)
with app.test_request_context():
    released, waiting = A.release_due_payouts()
ok(released == 0 and waiting == 1 and order(pub)["status"] == "paid", "no payout while the ticket hasn't been transferred")

# --- Reminder if the seller forgets
with app.app_context(): A.db.run("UPDATE orders SET paid_at=? WHERE public_id=?", A.sqltime(A.utcnow() - timedelta(hours=4)), pub)
SENT.clear()
with app.test_request_context(): ok(A.send_transfer_reminders() == 1, "after a few hours the seller gets a reminder")
ok(SENT and "Don't forget to transfer" in SENT[-1][1] and "bo.tm@example.com" in SENT[-1][2], "the reminder says who to send it to")
with app.test_request_context(): ok(A.send_transfer_reminders() == 0, "only once")

# --- Transfer, confirm, get paid
ok(buyer.post("/orders/%s/transferred" % pub, data={"_csrf": csrf(buyer)}).status_code == 400, "only the seller can mark it as transferred")
SENT.clear()
seller.post("/orders/%s/transferred" % pub, data={"_csrf": csrf(seller)})
ok(order(pub)["transferred_at"] is not None, "the seller marks it as transferred")
ok(any("is on its way" in s for to, s, b in SENT if to == "b@test.nl"), "the buyer gets an email that it's on its way")
bp = html(buyer.get("/orders/%s" % pub))
ok("has sent your ticket to bo.tm@example.com" in bp and "I&#39;ve got it" in bp, "the buyer is asked to check the app and confirm")
buyer.post("/orders/%s/received" % pub, data={"_csrf": csrf(buyer)})
ok(order(pub)["received_at"] is not None and "the ticket arrived" in html(buyer.get("/orders/%s" % pub)), "the buyer confirms it arrived")
with app.app_context(): notes = [m["body"] for m in A.db.all("SELECT body FROM messages WHERE kind='system'")]
ok("sys_transferred" in notes and "sys_received" in notes, "the chat gets notes for 'transferred' and 'arrived'")
with app.test_request_context():
    released, waiting = A.release_due_payouts()
ok(released == 1 and order(pub)["status"] == "released", "once transferred, the seller is paid as normal")
MAIL_WORKS[0] = False

# --- File tickets are unaffected
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(b"%PDF-1.4 normal"), "n.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
with app.app_context(): l2 = A.db.one("SELECT * FROM listings WHERE id=?", int(re.search(r"/t/(\d+)", r.location).group(1)))
ok(l2["delivery"] == "file" and "transfer_email" not in html(buyer.get("/t/%d" % l2["id"])), "ordinary file tickets work as before")
ok("ticket that lives in an app" in html(buyer.get("/privacy")), "the privacy page explains the transfer details")

# --- Stripe's webhook with real Stripe objects (stripe-python 8+ objects have no .get())
import stripe, payments
r = buyer.post("/t/%d/buy" % lid, data={"qty": "1", "transfer_name": "Bo", "transfer_email": "bo@example.com", "_csrf": csrf(buyer)})
pub2 = r.location.split("/")[2]
with app.app_context(): A.db.run("UPDATE orders SET payment_ref='cs_test_webhook' WHERE public_id=?", pub2)
event = stripe.Event.construct_from({"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
    "id": "cs_test_webhook", "object": "checkout.session", "payment_status": "paid", "payment_intent": "pi_test_1"}}}, "sk_test_x")
real_key, real_parse = config.STRIPE_SECRET_KEY, payments.parse_webhook
config.STRIPE_SECRET_KEY = "sk_test_x"; payments.parse_webhook = lambda payload, sig: event
r = app.test_client().post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})
config.STRIPE_SECRET_KEY, payments.parse_webhook = real_key, real_parse
ok(r.status_code == 200 and order(pub2)["status"] == "paid" and order(pub2)["payment_intent"] == "pi_test_1",
   "Stripe's 'checkout completed' webhook marks the order paid (real Stripe objects, no crash)")

print("FAILED:", fails or "none")
