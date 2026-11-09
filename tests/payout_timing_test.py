"""When sellers get paid (checked 9 Nov 2026): new sellers (<2 released sales) wait NEW_SELLER_PAYOUT_HOURS even after
Swap; established sellers are paid at Swap only after an iDEAL payment; card payments and no-swap orders wait
PAYOUT_DELAY_HOURS; a failing payout is tried 3 times, then waits for an admin on /admin. Covers payout_due_for, swap,
release_payout, release_due_payouts and POST /tasks/hourly."""
import os, re, sys, tempfile
from datetime import date, datetime, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.TICKET_CHECKS = False
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
config.TASK_SECRET = "secret"
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: SENT.append((to, subject, body)) and False
import app as A
import payments
app = A.app; app.config["TESTING"] = True
app.logger.disabled = True  # the payout failures below are on purpose; keep their tracebacks out of the test output
A.nightly_backup = lambda: None  # (the backup has its own test)
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    with app.app_context(): A.db.run("DELETE FROM hits WHERE key LIKE 'login%' OR key LIKE 'verify%'")
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)

# The pretend Stripe: every transfer is recorded; the orders in FAIL make it refuse.
CALLS, FAIL = [], set()
def fake_transfer(order, seller):
    CALLS.append(order["public_id"])
    if order["public_id"] in FAIL:
        raise RuntimeError("Stripe says no")
    return "tr_" + order["public_id"]
payments.transfer_to_seller = fake_transfer
real_method = payments.payment_method_type  # not live: counts as iDEAL

# The test sets 48 hours for new sellers, so their wait differs from the normal 24.
config.NEW_SELLER_PAYOUT_HOURS = 48
config.PAYOUT_DELAY_HOURS = 24
ok(config.NEW_SELLER_SALES == 2 and config.NEW_SELLER_LIMITS and config.SWAP_INSTANT_IDEAL_ONLY and not payments.live(),
   "setup: new = fewer than 2 released sales, the safety checks are on, Swap pays instantly only after iDEAL, pretend Stripe")

admin, buyer = app.test_client(), app.test_client()
login(admin, "admin@example.com", "Ad"); login(buyer, "b@test.nl", "Bo")
buyer_id = one("SELECT id FROM users WHERE email='b@test.nl'")["id"]
TODAY = date.today().isoformat()
night_end = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
real_now = A.utcnow
COUNT = [0]

def new_seller(email, released=0):
    """A seller with payouts set up and this many released sales."""
    run("INSERT INTO users (email, first_name, payouts_ready) VALUES (?, 'Sel', 1)", email)
    sid = one("SELECT id FROM users WHERE email=?", email)["id"]
    for _ in range(released):
        place(email, status="released")
    return sid

def place(email, status="paid", pay_method=None, due=None):
    """An order for tonight's event, paid by the buyer, with its payout date as buying it would set it."""
    COUNT[0] += 1
    pub = "PT%d" % COUNT[0]
    sid = one("SELECT id FROM users WHERE email=?", email)["id"]
    run("""INSERT INTO listings (seller_id, venue, event_name, event_date, face_value_cents, price_cents, start_time)
           VALUES (?, 'Perron', 'Timing Night', ?, 2000, 2000, '22:00')""", sid, TODAY)
    lid = one("SELECT MAX(id) AS i FROM listings")["i"]
    with app.app_context():
        due_at = A.sqltime(due or A.payout_due_for(TODAY, sid))
    run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, price_cents, fee_cents, total_cents, status,
                               expires_at, paid_at, payout_due_at, pay_method)
           VALUES (?, ?, ?, ?, 2000, 199, 2199, ?, datetime('now'), datetime('now'), ?, ?)""",
        pub, lid, buyer_id, sid, status, due_at, pay_method)
    return pub

def order(pub): return one("SELECT * FROM orders WHERE public_id=?", pub)
def due_of(pub): return A.parse_sqltime(order(pub)["payout_due_at"])
def doors():
    with app.app_context(): return A.doors_open_at(one("SELECT * FROM listings WHERE event_date=? LIMIT 1", TODAY))
def at(dt): A.utcnow = lambda: dt
def hourly():
    r = app.test_client().post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
    assert r.status_code == 200, r.status_code
    return r.get_json()
def swap(pub):
    """The buyer slides Swap 50 minutes after the doors open."""
    at(doors() + timedelta(minutes=50))
    return buyer.post("/orders/%s/swap" % pub, data={"_csrf": csrf(buyer)})
def paid(pub): return pub in CALLS and order(pub)["status"] == "released"
def finish(*pubs):  # keep a finished scenario's leftovers out of the next hourly run
    for p in pubs: run("UPDATE orders SET status='refunded' WHERE public_id=? AND status='paid'", p)
    A.utcnow = real_now

# --- New sellers: 24 hours is the normal wait, 48 (as set here) for a seller with fewer than 2 released sales
for released in (0, 1):
    for method in ("ideal", "card"):
        email = "new%d%s@test.nl" % (released, method)  # a fresh seller each time: a paid-out order ends being new
        new_seller(email, released)
        with app.app_context():
            ok(A.is_new_seller(one("SELECT id FROM users WHERE email=?", email)["id"]), "a seller with %d released sale(s) is still new" % released)
        payments.payment_method_type = lambda pi, m=method: m
        pub = place(email)
        ok(due_of(pub) == night_end + timedelta(hours=48), "new seller (%d sales): the payout is due 48 hours after the night" % released)
        r = swap(pub)
        o = order(pub)
        ok(r.status_code == 302 and o["swapped_at"] and o["rating"] == "up" and o["status"] == "paid" and pub not in CALLS,
           "new seller (%d sales, %s): Swap is recorded but nothing is paid" % (released, method))
        ok(due_of(pub) == night_end + timedelta(hours=48), "...and the payout date doesn't move")
        at(night_end + timedelta(hours=24, minutes=1)); hourly()
        ok(order(pub)["status"] == "paid" and pub not in CALLS, "...nor after the normal 24 hours (%s)" % method)
        at(due_of(pub) - timedelta(minutes=1)); hourly()
        ok(order(pub)["status"] == "paid" and pub not in CALLS, "...nor one minute before the new-seller time (%s)" % method)
        at(due_of(pub) + timedelta(minutes=1)); res = hourly()
        ok(paid(pub) and res["payouts_released"] >= 1 and order(pub)["transfer_ref"] == "tr_" + pub,
           "...but the hourly task pays it once the 48 hours have passed (%s)" % method)
        finish(pub)
payments.payment_method_type = real_method

# --- Established sellers (2 released sales)
new_seller("old@test.nl", released=2)
with app.app_context():
    ok(not A.is_new_seller(one("SELECT id FROM users WHERE email='old@test.nl'")["id"]), "a seller with 2 released sales is no longer new")

pub = place("old@test.nl")
ok(due_of(pub) == night_end + timedelta(hours=24), "established seller: the payout is due PAYOUT_DELAY_HOURS (24) after the night")
r = swap(pub); o = order(pub)
ok(r.status_code == 302 and o["status"] == "released" and CALLS.count(pub) == 1 and o["transfer_ref"] == "tr_" + pub
   and o["pay_method"] == "ideal", "established seller + iDEAL: Swap pays the seller at once, hours before the night is over")
ok(due_of(pub) <= doors() + timedelta(minutes=50), "...and the payout date moves up to the moment of the Swap")
ok(buyer.post("/orders/%s/swap" % pub, data={"_csrf": csrf(buyer)}).status_code == 400 and CALLS.count(pub) == 1,
   "...a second Swap is refused and pays nothing again")
finish(pub)

payments.payment_method_type = lambda pi: "card"
pub = place("old@test.nl")
r = swap(pub); o = order(pub)
ok(r.status_code == 302 and o["swapped_at"] and o["status"] == "paid" and o["pay_method"] == "card" and pub not in CALLS,
   "established seller + card: Swap is recorded but nothing is paid (cards can be charged back)")
ok(due_of(pub) == night_end + timedelta(hours=config.PAYOUT_DELAY_HOURS), "...the payout waits PAYOUT_DELAY_HOURS after the night")
at(due_of(pub) - timedelta(minutes=1)); hourly()
ok(order(pub)["status"] == "paid" and pub not in CALLS, "...the hourly task doesn't pay it a minute early")
at(due_of(pub) + timedelta(minutes=1)); hourly()
ok(paid(pub), "...and pays it a minute after")
finish(pub)
payments.payment_method_type = real_method

pub = place("old@test.nl", pay_method="card")  # Stripe's answer was saved earlier; it is used as it is
swap(pub)
ok(order(pub)["status"] == "paid" and pub not in CALLS, "a payment already saved as 'card' is never paid at Swap")
finish(pub)

config.SWAP_INSTANT_IDEAL_ONLY = False
pub = place("old@test.nl", pay_method="card")
swap(pub)
ok(paid(pub), "with SWAP_INSTANT_IDEAL_ONLY off, an established seller is paid at Swap after a card payment too")
config.SWAP_INSTANT_IDEAL_ONLY = True
finish(pub)

pub = place("old@test.nl")  # iDEAL, nobody slides Swap
at(due_of(pub) - timedelta(minutes=1)); hourly()
ok(order(pub)["status"] == "paid" and pub not in CALLS, "no Swap: the hourly task doesn't pay before PAYOUT_DELAY_HOURS after the night")
at(due_of(pub) + timedelta(minutes=1)); hourly()
ok(paid(pub), "...and pays after it")
finish(pub)

# --- release_payout itself
pub = place("old@test.nl", due=datetime(2000, 1, 1))
run("UPDATE orders SET payout_errors=2 WHERE public_id=?", pub)
with app.test_request_context():
    run("UPDATE users SET blocked_at=datetime('now') WHERE email='old@test.nl'")
    held = A.release_payout(order(pub))
    run("UPDATE users SET blocked_at=NULL, payouts_ready=0 WHERE email='old@test.nl'")
    not_ready = A.release_payout(order(pub))
    run("UPDATE users SET payouts_ready=1 WHERE email='old@test.nl'")
    stale = order(pub)
    waited = (CALLS.count(pub), order(pub)["status"])
    first = A.release_payout(stale)
    second = A.release_payout(stale)  # the same stale copy, as two runs at once would have
ok(held is False and not_ready is False and waited == (0, "paid"),
   "release_payout: a blocked seller or unfinished payout setup is not paid (it returns False)")
ok(first is True and second is False and CALLS.count(pub) == 1, "release_payout pays once; a second call with the same order pays nothing")
ok(order(pub)["status"] == "released" and order(pub)["payout_errors"] == 0, "...the order is released and its error count is cleared")

# --- At most 3 tries, then /admin
A.utcnow = real_now
SENT.clear()
pub = place("old@test.nl", due=datetime(2000, 1, 1)); FAIL.add(pub)
def manual_mails(): return [s for to, s, b in SENT if "manual look" in s and pub in s]
for n in (1, 2):
    res = hourly(); o = order(pub)
    ok(CALLS.count(pub) == n and o["payout_errors"] == n and o["status"] == "paid" and res["payouts_released"] == 0,
       "payout try %d fails: counted, the order is back to 'paid' and will be tried again" % n)
ok("Stripe says no" in order(pub)["payout_last_error"] and not manual_mails() and "need a manual look" not in html(admin.get("/admin")),
   "after 2 failures the error is saved, the admins aren't told yet and /admin doesn't list it")
hourly()
ap = html(admin.get("/admin"))
ok(CALLS.count(pub) == 3 and order(pub)["payout_errors"] == 3 and len(manual_mails()) == 1, "payout try 3 fails: the admins get one email")
ok("need a manual look" in ap and pub in ap and "failed 3×" in ap and "Stripe says no" in ap, "...and /admin lists it with the error")
FAIL.discard(pub)  # Stripe would work now, but the hourly task has given up
for _ in range(3): hourly()
ok(CALLS.count(pub) == 3 and order(pub)["status"] == "paid" and len(manual_mails()) == 1,
   "after 3 failures the hourly task never tries it again (still 3 tries, no more emails)")
with app.test_request_context():
    ok(A.release_due_payouts() == (0, 0) and CALLS.count(pub) == 3, "...release_due_payouts skips it too")
finish(pub)

# a failing order never stops another one, and two failures followed by success are fine
bad, good, flaky = place("old@test.nl", due=datetime(2000, 1, 1)), place("old@test.nl", due=datetime(2000, 1, 1)), place("old@test.nl", due=datetime(2000, 1, 1))
FAIL.update({bad, flaky}); hourly(); hourly()
FAIL.discard(flaky); res = hourly()
ok(paid(good) and order(bad)["payout_errors"] == 3 and paid(flaky) and order(flaky)["payout_errors"] == 0,
   "a failing payout doesn't stop the others; an order that fails twice and then works is paid and its error count cleared")
FAIL.clear(); finish(bad)

# a failure at Swap is not counted: the seller is due now and the hourly task pays it
pub = place("old@test.nl"); FAIL.add(pub)
r = swap(pub); o = order(pub)
ok(r.status_code == 302 and o["status"] == "paid" and o["payout_errors"] == 0 and CALLS.count(pub) == 1 and o["swapped_at"],
   "a payout that fails at Swap leaves the order paid, due now, with no error counted")
FAIL.discard(pub); hourly()
ok(paid(pub) and CALLS.count(pub) == 2, "...and the hourly task pays it")
finish(pub)

print("\n%d failed" % len(fails) if fails else "\nAll payout timing checks passed")
sys.exit(1 if fails else 0)
