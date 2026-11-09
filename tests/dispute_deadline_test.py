import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
from urllib.parse import unquote
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False; config.ADMIN_EMAILS = {"admin@example.com"}
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify
SENT = []
notify.send_email = lambda to, subject, body, *a, **k: SENT.append((to, subject, body)) or False
import app as A, payments, qrcode
from PIL import Image
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
def png(qr=None, colour="white"):
    img = Image.new("RGB", (800, 1000), colour)
    if qr: img.paste(qrcode.make(qr).convert("RGB").resize((400, 400)), (200, 300))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): A.db.run(sql, *a)
def order(pub): return one("SELECT * FROM orders WHERE public_id=?", pub)
def age(pub, hours): run("UPDATE orders SET disputed_at=? WHERE public_id=?", A.sqltime(A.utcnow() - timedelta(hours=hours)), pub)
def remind():
    with app.test_request_context(): return A.remind_silent_sellers()
def auto_refund():
    with app.test_request_context(): return A.auto_refund_silent_disputes()

# Stripe's refund is counted, not called
REFUNDS = []
payments.refund = lambda o: REFUNDS.append(o["public_id"]) or "re_test"

seller, admin = app.test_client(), app.test_client()
login(seller, "s@student.eur.nl", "Sam"); login(admin, "admin@example.com", "Ad")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
run("UPDATE users SET payouts_ready=1 WHERE email='s@student.eur.nl'")

n = [0]
def sale(code):
    """A paid order for a new buyer each time (a second dispute by the same buyer would stop the automatic refund)."""
    n[0] += 1
    buyer = app.test_client(); login(buyer, "buyer%d@test.nl" % n[0], "Bea")
    D = (date.today() + timedelta(days=3)).isoformat()
    r = seller.post("/sell", data={"venue": "__other", "other_venue": "Het Sieraad", "city": "Rotterdam", "event_date": D, "face_value": "20",
                                   "price": "20", "event_name": "Gala", "category": "student", "name_on_ticket": "no", "no_double_sell": "yes",
                                   "adult": "yes", "_csrf": csrf(seller), "ticket": (io.BytesIO(png(code)), "t.png")}, content_type="multipart/form-data")
    lid = int(re.search(r"/t/(\d+)", r.location).group(1))
    r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
    return lid, r.location.split("/")[2].split("?")[0], buyer
def tonight(lid):  # the doors opened an hour ago
    now = datetime.now()
    doors = now - timedelta(hours=1)
    if now.hour >= 6 and doors.hour < 6:
        doors = now.replace(hour=6, minute=0)
    day = doors.date() - timedelta(days=1) if doors.hour < 6 else doors.date()
    run("UPDATE listings SET event_date=?, start_time=? WHERE id=?", day.isoformat(), doors.strftime("%H:%M"), lid)
def door_report(code, kind="door_rejected"):
    lid, pub, buyer = sale(code); tonight(lid)
    buyer.post("/orders/%s/report" % pub, data={"kind": kind, "reason": "The scanner said the ticket was already used.", "_csrf": csrf(buyer),
               "photos": (io.BytesIO(png(colour="red")), "door.png")}, content_type="multipart/form-data")
    return pub

ok(A.DISPUTE_REPLY_HOURS == 48 and A.DISPUTE_REMIND_HOURS == 12, "the reply window is 48 hours and the reminder comes after 12")

# --- The report opens the seller's reply window
SENT.clear()
pa = door_report("T-DISP-A")
o = order(pa)
ok(o["status"] == "disputed" and o["dispute_kind"] == "door_rejected" and o["disputed_at"], "a door problem with a photo opens a dispute and starts the clock")
ok(one("SELECT COUNT(*) AS c FROM proofs WHERE order_id=? AND kind='dispute_buyer'", o["id"])["c"] == 1, "the buyer's photo is kept with the dispute")
ok(any(to == "s@student.eur.nl" and "within 48 hours" in b for to, s, b in SENT), "the seller's email says they have 48 hours")
ok("within 48 hours" in html(seller.get("/orders/" + pa)) and 'action="/orders/%s/dispute-reply"' % pa in html(seller.get("/orders/" + pa)),
   "the seller's order page offers the reply form and says 48 hours")
ok(remind() == 0 and auto_refund() == 0 and order(pa)["status"] == "disputed", "right after the report: no reminder, no refund")

# --- The 12h reminder: only after 12 hours, and only once
age(pa, 11)
SENT.clear()
ok(remind() == 0 and not order(pa)["dispute_reminded_at"] and not SENT, "after 11 hours: no reminder yet")
age(pa, 13)
ok(remind() == 1, "after 13 hours: one reminder is sent")
mails = [(to, s, b) for to, s, b in SENT if to == "s@student.eur.nl"]
ok(len(mails) == 1 and "Reminder" in mails[0][1] and "about 36 hours left" in mails[0][2],
   "it goes to the seller and says about 36 hours are left (48 minus 12)")
ok(order(pa)["dispute_reminded_at"], "the reminder is recorded on the order")
SENT.clear()
ok(remind() == 0 and remind() == 0 and not SENT, "running the hourly task again sends no second reminder")
age(pa, 40)
ok(remind() == 0 and not SENT, "and none later either")

# --- No automatic decision before 48 hours
age(pa, 47)
ok(auto_refund() == 0 and order(pa)["status"] == "disputed" and not order(pa)["dispute_escalated_at"] and not REFUNDS,
   "after 47 hours: not refunded, nothing escalated")

# --- A seller who answers is not refunded or reminded
pb = door_report("T-DISP-B")
seller.post("/orders/%s/dispute-reply" % pb, data={"reply": "I sent it with the app, see my account.", "_csrf": csrf(seller)}, content_type="multipart/form-data")
ok(order(pb)["seller_replied_at"], "the seller's reply is stored")
age(pb, 13); SENT.clear()
ok(remind() == 0 and not any("Reminder" in s for to, s, b in SENT), "a seller who already replied gets no reminder")
age(pb, 49); age(pa, 49)

# --- Other cases at 49 hours: other kinds, no photo, a chargeback
pc = door_report("T-DISP-C")   # no photo (removed below)
run("DELETE FROM proofs WHERE order_id=?", order(pc)["id"])
pd = door_report("T-DISP-D")   # chargeback open
run("UPDATE orders SET chargeback_at=? WHERE public_id=?", A.sqltime(A.utcnow()), pd)
pf = door_report("T-DISP-F", kind="wrong_event")  # not a door problem
for p in (pc, pd, pf): age(p, 49)

SENT.clear()
ok(auto_refund() == 1, "after 49 hours exactly one order is refunded automatically")
ok(REFUNDS == [pa], "the refund is the one with a photo, a clean record, no reply and no chargeback")
oa = order(pa)
ok(oa["status"] == "refunded" and "48 hours" in (oa["dispute_decision"] or ""), "it is marked refunded, with the 48 hours in the decision")
ok(any(to == "buyer1@test.nl" for to, s, b in SENT) and any(to == "s@student.eur.nl" for to, s, b in SENT), "buyer and seller are both told about the refund")
ok(order(pb)["status"] == "disputed" and order(pb)["seller_replied_at"], "an order the seller answered stays open for an admin")
for p, why in ((pc, "no photo"), (pd, "chargeback")):
    ok(order(p)["status"] == "disputed" and order(p)["dispute_escalated_at"], "%s: not refunded, escalated to an admin" % why)
ok(sum(1 for to, s, b in SENT if to == "admin@example.com" and "needs a decision" in s) == 2, "the admins get one email per escalated dispute")
ok(order(pf)["status"] == "disputed" and not order(pf)["dispute_escalated_at"], "a problem that is not a door problem is never decided automatically")

# --- Running it again does nothing twice
SENT.clear()
ok(auto_refund() == 0 and REFUNDS == [pa] and not SENT, "a second run: no second refund, no second admin email")

# --- keep_block: the seller is paid, the buyer is blocked
pe = door_report("T-DISP-E")
oe = order(pe)
ok(oe["status"] == "disputed", "(a new dispute for the keep_block case)")
run("UPDATE orders SET payout_due_at=? WHERE public_id=?", A.sqltime(A.utcnow() - timedelta(hours=1)), pe)
with app.app_context(): A.release_due_payouts()
ok(order(pe)["status"] == "disputed", "while the dispute is open the seller is not paid, even when the payout date has passed")
admin.post("/admin/disputes/%s/keep_block" % pe, data={"why": "", "_csrf": csrf(admin)})
ok(order(pe)["status"] == "disputed", "a decision needs a reason")
SENT.clear()
admin.post("/admin/disputes/%s/keep_block" % pe, data={"why": "The organiser confirmed the ticket was scanned at 23:14.", "_csrf": csrf(admin)})
oe = order(pe)
ok(oe["status"] == "paid" and "23:14" in (oe["dispute_decision"] or ""), "keep_block closes the dispute and puts the order back to paid")
bu = one("SELECT * FROM users WHERE id=?", oe["buyer_id"])
ok(bu["blocked_at"] and "False problem report" in (bu["blocked_reason"] or ""), "the buyer is blocked, with the reason saved")
ok(one("SELECT blocked_at FROM users WHERE email='s@student.eur.nl'")["blocked_at"] is None, "the seller is not blocked")
ok(any(to == "s@student.eur.nl" and "23:14" in b for to, s, b in SENT) and any(to == bu["email"] and "23:14" in b for to, s, b in SENT),
   "both sides get the decision and the reason")
with app.app_context(): released, waiting = A.release_due_payouts()
oe = order(pe)
ok(released >= 1 and oe["status"] == "released" and oe["released_at"], "the seller is paid out when the payout date comes")
ok(pe not in REFUNDS and not auto_refund(), "and the buyer is not refunded")

# --- plain keep: seller is paid, buyer is not blocked
pg = door_report("T-DISP-G")
admin.post("/admin/disputes/%s/keep" % pg, data={"why": "The seller's proof is good.", "_csrf": csrf(admin)})
ok(order(pg)["status"] == "paid" and one("SELECT blocked_at FROM users WHERE id=?", order(pg)["buyer_id"])["blocked_at"] is None,
   "plain keep pays the seller but does not block the buyer")

# --- The organiser email carries the ticket's code
with app.app_context():
    o = A.db.one("SELECT * FROM orders WHERE public_id=?", pe)
    listing = A.db.one("SELECT * FROM listings WHERE id=?", o["listing_id"])
    tickets = A.db.all("SELECT * FROM tickets WHERE order_id=?", o["id"])
    mail = A.organiser_mail(o, listing, tickets)
body = unquote(mail.split("body=", 1)[1])
ok(mail.startswith("mailto:?subject=") and "ticket code(s): T-DISP-E" in body and "ticketcode(s): T-DISP-E" in body,
   "organiser_mail has the ticket's code in Dutch and English")
ok("Gala" in body and "Het Sieraad" in body, "and the event and venue")
ok("T-DISP-E" in unquote(html(admin.get("/admin/disputes/" + pe)).split("mailto:", 1)[1].split('"')[0]), "the admin dispute page links to that email")
with app.app_context():
    ok(A.organiser_mail(o, listing, [dict(tk, code_text=None) for tk in tickets]) is None, "without a code there is nothing to ask the organiser")

print("\n%d failed" % len(fails))
