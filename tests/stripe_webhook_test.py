"""Signed Stripe webhooks for chargebacks and dashboard refunds (#27): payouts are held or taken back, a bad signature is refused,
and the same delivery twice changes nothing. The payload is signed for real; only the Stripe call that reverses a transfer is replaced."""
import hashlib, hmac, io, json, os, re, sys, tempfile, time
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; MAIL_WORKS = [False]  # while mail "fails", sign-in codes show on screen (development)
notify.send_email = lambda to, subject, body, attachments=(), **kw: (SENT.append((to, subject, body)) or True) if MAIL_WORKS[0] else False
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
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(c)})

admin, seller, buyer = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
verify_phone(seller, "0611111111")
D = (date.today() + timedelta(days=3)).isoformat()
admin.post("/admin/events/save", data={"face_value_max": "500", "name": "Hook Night", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Hook Night'")

def paid_order(intent):
    """A paid order (made in development, with the pretend checkout) that Stripe knows by this payment intent."""
    r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                                   "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(8)), "t.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
    lid = int(re.search(r"/t/(\d+)", r.location).group(1))
    pub = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}).location.split("/")[2]
    buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
    with app.app_context():
        A.db.run("UPDATE orders SET payment_intent=? WHERE public_id=?", intent, pub)
        return A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
def row(o):
    with app.app_context(): return dict(A.db.one("SELECT * FROM orders WHERE id=?", o["id"]))
def ticket_states(o):
    with app.app_context(): return [t["status"] for t in A.db.all("SELECT status FROM tickets WHERE order_id=?", o["id"])]
def make_released(o, ref):
    with app.app_context(): A.db.run("UPDATE orders SET status='released', transfer_ref=? WHERE id=?", ref, o["id"])

MAIL_WORKS[0] = True
SECRET = "whsec_test_secret"
config.STRIPE_WEBHOOK_SECRET = SECRET
reversals = []; reversal_ids = {}
def fake_reverse(order):
    """Like Stripe with the idempotency key "reversal-<order>": asking again for the same order gives back the same reversal."""
    reversals.append(order["public_id"])
    return reversal_ids.setdefault(order["public_id"], "trr_%d" % (len(reversal_ids) + 1))
payments.reverse_transfer = fake_reverse

def sign(payload, secret=SECRET, at=None):
    t = int(at or time.time())
    return "t=%d,v1=%s" % (t, hmac.new(secret.encode(), ("%d." % t).encode() + payload, hashlib.sha256).hexdigest())
def post_webhook(payload, signature):
    """The webhook only answers when Stripe is switched on, so the key is set for this one request (pretend checkouts need it off)."""
    config.STRIPE_SECRET_KEY = "sk_test_x"
    try:
        headers = {"Stripe-Signature": signature} if signature else {}
        return app.test_client().post("/webhooks/stripe", data=payload, headers=headers, content_type="application/json")
    finally:
        config.STRIPE_SECRET_KEY = ""
def deliver(kind, obj, evt="evt_1", secret=SECRET, signature=True):
    payload = json.dumps({"id": evt, "object": "event", "type": kind, "api_version": "2024-06-20", "data": {"object": obj}}).encode()
    return post_webhook(payload, sign(payload, secret) if signature else None)
def dispute(intent, status="needs_response", dp="dp_1"):
    return {"id": dp, "object": "dispute", "payment_intent": intent, "amount": 2700, "reason": "fraudulent", "status": status}
def chargeback_mails(): return [s for s in SENT if "chargeback" in s[1]]

# --- a chargeback before the payout: the payout is held
held = paid_order("pi_held")
SENT.clear()
r = deliver("charge.dispute.created", dispute("pi_held"), "evt_a")
now = row(held)
ok(r.status_code == 200 and now["status"] == "disputed" and now["chargeback_at"] and now["dispute_reason"] == "Chargeback by the buyer's bank", "a signed chargeback on a paid order holds the payout")
ok(not reversals and not now["reversal_ref"], "...and nothing is taken back, because nothing was paid out")
ok(len(chargeback_mails()) == 1 and "payout is on hold" in chargeback_mails()[0][2], "...and the admins are told once")
with app.app_context(): A.db.run("UPDATE orders SET chargeback_at='2026-01-01 10:00:00' WHERE id=?", held["id"])  # so a rewritten time would show
before = row(held); SENT.clear()
r = deliver("charge.dispute.created", dispute("pi_held"), "evt_a")
# A repeat of charge.dispute.created (Stripe retries deliveries) changes nothing at all and mails nobody again.
ok(r.status_code == 200 and row(held) == before and not reversals and not chargeback_mails(), "the same chargeback delivered again changes nothing and mails nobody")
r = deliver("charge.dispute.closed", dispute("pi_held", "lost"), "evt_b")
ok(r.status_code == 200 and row(held)["chargeback_status"] == "lost" and row(held)["status"] == "disputed", "the outcome is recorded, the order stays on hold")
before = row(held); SENT.clear()
r = deliver("charge.dispute.closed", dispute("pi_held", "lost"), "evt_b")
ok(r.status_code == 200 and row(held) == before and not reversals and not SENT, "the same 'closed' delivered again changes nothing and mails nobody")

# --- a chargeback after the payout: the payout is taken back
paidout = paid_order("pi_paidout"); make_released(paidout, "tr_1")
SENT.clear()
r = deliver("charge.dispute.created", dispute("pi_paidout", dp="dp_2"), "evt_c")
now = row(paidout)
ok(r.status_code == 200 and reversals == [paidout["public_id"]] and now["reversal_ref"] == "trr_1" and now["chargeback_at"], "a signed chargeback on a paid-out order takes the seller's payout back")
ok(now["status"] == "released" and any("payout was taken back" in s[2] for s in chargeback_mails()), "...and the admins are told it was taken back")
with app.app_context(): A.db.run("UPDATE orders SET chargeback_at='2026-01-01 10:00:00' WHERE id=?", paidout["id"])
before = row(paidout); SENT.clear(); reversals.clear()
r = deliver("charge.dispute.created", dispute("pi_paidout", dp="dp_2"), "evt_c")
ok(r.status_code == 200 and row(paidout) == before and not reversals and not chargeback_mails(), "the same chargeback delivered again doesn't ask Stripe again, change the order or mail anyone")
ok('idempotency_key="reversal-"' in open("payments.py", encoding="utf-8").read(), "...and a repeated reversal request is harmless at Stripe: the call carries an idempotency key per order")

# --- a chargeback after the payout, but Stripe can't take the payout back: the admins are told and /admin can act on it
real_reverse = payments.reverse_transfer
def failing_reverse(order):
    reversals.append(order["public_id"]); raise RuntimeError("balance_insufficient")
payments.reverse_transfer = failing_reverse
app.logger.disabled = True  # the app logs the refusal with a traceback, which run_all.sh would mistake for a crash
failed = paid_order("pi_failed"); make_released(failed, "tr_3")
reversals.clear(); SENT.clear()
r = deliver("charge.dispute.created", dispute("pi_failed", dp="dp_5"), "evt_i")
now = row(failed)
ok(r.status_code == 200 and reversals == [failed["public_id"]], "a chargeback on a paid-out order tries to take the payout back exactly once")
ok(any("couldn't take back a payout" in s[1] and failed["public_id"] in s[1] and "balance_insufficient" in s[2] for s in SENT), "...and when Stripe refuses, the admins get a 'couldn't take back a payout' email with the reason")
ok(len(chargeback_mails()) == 1 and "taking it back FAILED" in chargeback_mails()[0][2], "...and the chargeback email says taking it back FAILED")
ok(now["status"] == "released" and now["chargeback_at"] and not now["reversal_ref"], "...and the order stays released, with the chargeback noted and no reversal recorded")
page = html(admin.get("/admin"))
cb_section = page.split('id="chargebacks"')[1].split("</section>")[0]
ok(failed["public_id"] in cb_section, "...and /admin lists it under Chargebacks")
cb_row = [li for li in cb_section.split("<li>") if failed["public_id"] in li][0]
ok("payout taken back" not in cb_row and "payout taken back" in cb_section, "...without claiming the payout was taken back (the earlier order that was taken back still says so)")
ok("/admin/orders/%s/refund_after_payout" % failed["public_id"] in page, "...and the 'Refund after payout' form is still offered so an admin can finish it")
before = row(failed); SENT.clear(); reversals.clear()
r = deliver("charge.dispute.created", dispute("pi_failed", dp="dp_5"), "evt_i")
ok(r.status_code == 200 and row(failed) == before and not reversals and not SENT, "the same chargeback delivered again doesn't retry the take-back or mail anyone")

# --- no transfer to reverse yet (None instead of an error): same outcome
app.logger.disabled = False
payments.reverse_transfer = lambda order: (reversals.append(order["public_id"]) or None)
notyet = paid_order("pi_notyet"); make_released(notyet, "tr_4")
reversals.clear(); SENT.clear()
r = deliver("charge.dispute.created", dispute("pi_notyet", dp="dp_6"), "evt_j")
now = row(notyet)
ok(r.status_code == 200 and reversals == [notyet["public_id"]] and now["status"] == "released" and not now["reversal_ref"], "a take-back that finds no transfer yet is tried once and leaves the order released without a reversal")
ok(any("couldn't take back a payout" in s[1] and "no Stripe transfer to reverse" in s[2] for s in SENT) and any("FAILED" in s[2] for s in chargeback_mails()), "...and the admins are told, never 'taken back'")
payments.reverse_transfer = real_reverse

# --- a refund made in the Stripe dashboard
dash = paid_order("pi_dash"); make_released(dash, "tr_2")
reversals.clear(); SENT.clear()
r = deliver("charge.refunded", {"id": "ch_1", "object": "charge", "payment_intent": "pi_dash"}, "evt_d")
now = row(dash)
ok(r.status_code == 200 and now["status"] == "refunded" and set(ticket_states(dash)) == {"void"}, "a signed dashboard refund marks the order refunded and voids its tickets")
ok(reversals == [dash["public_id"]] and now["reversal_ref"], "...and takes the seller's payout back")
before = row(dash); SENT.clear(); reversals.clear()
r = deliver("charge.refunded", {"id": "ch_1", "object": "charge", "payment_intent": "pi_dash"}, "evt_d")
ok(r.status_code == 200 and row(dash) == before and not reversals and not SENT, "the same refund delivered again changes nothing")
unpaid = paid_order("pi_unpaid")
r = deliver("charge.refunded", {"id": "ch_2", "object": "charge", "payment_intent": "pi_unpaid"}, "evt_e")
ok(r.status_code == 200 and row(unpaid)["status"] == "refunded" and not row(unpaid)["reversal_ref"] and not reversals, "a dashboard refund before the payout needs no reversal")

# --- refused deliveries
safe = paid_order("pi_safe"); before = row(safe); reversals.clear(); SENT.clear()
for label, kw in (("a wrong signature", {"secret": "whsec_someone_else"}), ("no signature", {"signature": False})):
    r = deliver("charge.dispute.created", dispute("pi_safe", dp="dp_3"), "evt_f", **kw)
    ok(r.status_code == 400 and row(safe) == before and not SENT and not reversals, "%s is refused (400) and the order is left alone" % label)
payload = json.dumps({"id": "evt_g", "object": "event", "type": "charge.refunded", "data": {"object": {"payment_intent": "pi_safe"}}}).encode()
r = post_webhook(payload, sign(payload + b" "))
ok(r.status_code == 400 and row(safe) == before, "a payload changed after signing is refused")
r = post_webhook(payload, sign(payload, at=time.time() - 3600))
ok(r.status_code == 400 and row(safe) == before, "an old signed delivery (replayed an hour later) is refused")
r = deliver("charge.dispute.created", dispute("pi_unknown", dp="dp_4"), "evt_h")
ok(r.status_code == 200 and row(safe) == before, "a signed event for a payment Spare doesn't know is accepted and ignored")

print("FAILED:", fails or "none")
