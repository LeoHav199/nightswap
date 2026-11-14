"""Checks for 5 Nov 2026: Swap at the door (money held until the buyer is in, paid at the swap or 24 hours after the
night), free prices (no cap, the face value shown next to the price) and fixes #34 to #42."""
import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta, timezone
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.FACE_PROOF_REQUIRED = True  # off by default since 14 Nov 2026; covered here when switched on
config.TICKET_CHECKS = False  # (picture checks are covered in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: SENT.append((to, subject, body)) and False
import app as A
import payments
app = A.app; app.config["TESTING"] = True
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
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(c)})
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)

# --- The new defaults
ok(config.PRICE_CAP_PERCENT == 200 and config.PAYOUT_DELAY_HOURS == 24 and config.SWAP_OPENS_AFTER_DOORS_MINUTES == 0,
   "defaults: at most double the face value, paid 24 hours after the night, Swap from when the doors open")
ok(config.NEW_SELLER_LIMITS, "the new-seller limits stay switched on in the code")

admin, seller, buyer, visitor = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo"); visitor.get("/")
verify_phone(seller, "0611111111")
D = (date.today() + timedelta(days=3)).isoformat()
admin.post("/admin/events/save", data={"name": "Eno Night", "venue": "Perron", "category": "club", "event_date": D,
                                       "door_time": "23:00", "_csrf": csrf(admin)})
ev = one("SELECT * FROM events WHERE name='Eno Night'")

# --- The cap: at most double the face value (Leo's choice, 5 Nov 2026)
def sell(face, price, name="Eno Night"):
    e = one("SELECT * FROM events WHERE name=?", name)
    return seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(e["id"]), "face_value": face,
                                      "price": price, "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(8)), "t.pdf"),
                                      "_csrf": csrf(seller)}, content_type="multipart/form-data")
run("UPDATE users SET payouts_ready=1 WHERE email='s@test.nl'")
r = sell("20", "41")
ok(r.status_code == 200 and "maximum for this ticket is €40" in html(r), "with the 100% cap a €20 ticket can't be listed for €41")
home = html(visitor.get("/"))
ok("Never more than double the face value." in home and "100% above" not in home, "the homepage says 'never more than double'")
ok("at most double the face value" in html(visitor.get("/terms")) and "2 × face value" in html(visitor.get("/how-it-works")),
   "terms and How it works say 'double'")
import i18n
ok("het dubbele" in i18n.t("nl", "hero_sub_pct", pct=100) and "au double" in i18n.t("fr", "price_hint_pct", pct=100)
   and "50%" in i18n.t("en", "hero_sub_pct", pct=50) and i18n.t("en", "above_face_pct", pct=100) == "100% above face value",
   "'double' in every language for a 100% cap; other caps and a listing's markup keep the percentage")

# --- Free prices (PRICE_CAP_PERCENT=0 is still an option)
config.PRICE_CAP_PERCENT = 0
r = sell("20", "70")
ok(r.status_code == 302 and "/t/" in r.location, "a €20 ticket can be listed for €70: sellers set their own price")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
l = one("SELECT * FROM listings WHERE id=?", lid)
ok(l["status"] == "active" and not l["review_reason"], "without a cap there's no proof of the face value to check, so it's on sale straight away")
page = html(visitor.get("/t/%d" % lid))
ok("250% above face value" in page and "€20" in page, "the listing shows the face value and how far above it the price is")
with app.test_request_context():
    ok(A.max_price(2000) is None and not A.over_cap(99900, 2000) and A.markup_percent(7000, 2000) == 250 and A.markup_percent(1500, 2000) == 0,
       "max_price is None without a cap; the markup is worked out from the face value")
home = html(visitor.get("/"))
ok("Never above face value" not in home and "money is held until you" in home, "the homepage no longer promises a cap")
ok("You set your own price" in html(visitor.get("/terms")) and "Your choice" in html(visitor.get("/how-it-works")), "terms and How it works say sellers choose")
sell_page = html(seller.get("/sell?event=%d" % ev["id"]))
ok('data-cap="0"' in sell_page and 'name="face_proof"' not in sell_page and "Buyers see the face value" in sell_page,
   "the sell form has no cap and no proof upload")
seller.post("/t/%d/price" % lid, data={"price": "95", "_csrf": csrf(seller)})
ok(one("SELECT price_cents FROM listings WHERE id=?", lid)["price_cents"] == 9500, "sellers can change the price freely too")
config.PRICE_CAP_PERCENT = 150
r = sell("20", "31")
ok(r.status_code == 200 and "maximum for this ticket is €30" in html(r), "the cap still works when switched on (PRICE_CAP_PERCENT=150 → €30)")
config.PRICE_CAP_PERCENT = 0

# --- Buying, and the money held until the swap
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); pub = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
o = one("SELECT * FROM orders WHERE public_id=?", pub)
night_end = datetime.combine(date.fromisoformat(D) + timedelta(days=1), datetime.min.time())
ok(o["status"] == "paid" and o["payout_due_at"] == A.sqltime(night_end + timedelta(hours=24)), "a new seller's payout waits 24 hours after the night (#28)")
sp = html(seller.get("/orders/%s" % pub))
ok("Spare holds your" in sp and "swap at the door" in sp, "the seller sees Spare is holding the money until the buyer swaps")
ok("Slide to swap" not in html(buyer.get("/orders/%s" % pub)), "no Swap before the event")
ok(buyer.post("/orders/%s/swap" % pub, data={"_csrf": csrf(buyer)}).status_code == 400, "swapping days before is refused")

# make the seller established (2 paid-out sales) so a swap can pay at once
for i in range(2):
    run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, price_cents, fee_cents, total_cents, status, expires_at)
           VALUES (?, ?, (SELECT id FROM users WHERE email='b@test.nl'), (SELECT id FROM users WHERE email='s@test.nl'), 100, 0, 100, 'released', datetime('now'))""",
        "OLD%d" % i, lid)
with app.app_context():
    ok(not A.is_new_seller(o["seller_id"]), "after 2 paid-out sales the seller is no longer new")
    A.db.run("UPDATE listings SET event_date=? WHERE id=?", date.today().isoformat(), lid)
    A.db.run("UPDATE orders SET payout_due_at=? WHERE id=?", A.sqltime(A.payout_due_for(date.today().isoformat(), o["seller_id"])), o["id"])
    l = A.db.one("SELECT * FROM listings WHERE id=?", lid)
    doors = A.doors_open_at(l)
with app.app_context(): due_est = A.payout_due_for(date.today().isoformat(), o["seller_id"])
ok(due_est == datetime.combine(date.today() + timedelta(days=1), datetime.min.time()) + timedelta(hours=24),
   "an established seller is paid 24 hours after the night without a swap")
real_now = A.utcnow
A.utcnow = lambda: doors - timedelta(minutes=10)
ok("Slide to swap" not in html(buyer.get("/orders/%s" % pub)), "no Swap before the doors open")
SENT.clear()
with app.test_request_context(): A.send_reminders()
ok(not any("Swap" in s for to, s, b in SENT), "no door reminder before the doors open")
A.utcnow = lambda: doors + timedelta(minutes=50)
bp = html(buyer.get("/orders/%s" % pub))
ok("Slide to swap" in bp and "Are you in?" in bp and 'data-swap-fallback' in bp and "Only swap once you" in bp,
   "from the doors the buyer sees the Swap slider (with a plain button without JavaScript) and the warning")
with app.test_request_context(): A.send_reminders()
ok(any(to == "b@test.nl" and "Slide Swap" in s for to, s, b in SENT), "about 45 minutes after the doors the buyer gets 'Inside? Slide Swap'")
SENT.clear()
with app.test_request_context(): A.send_reminders()
ok(not any("Swap" in s for to, s, b in SENT), "...only once")

# card payment: swapped, but paid at the normal time
real_pm = payments.payment_method_type
payments.payment_method_type = lambda pi: "card"
r = buyer.post("/orders/%s/swap" % pub, data={"_csrf": csrf(buyer)}, follow_redirects=True)
o = one("SELECT * FROM orders WHERE public_id=?", pub)
ok(o["swapped_at"] and o["rating"] == "up" and o["status"] == "paid" and o["pay_method"] == "card" and "usual time" in html(r),
   "a swap on a card payment is recorded, and the seller is paid at the usual time (cards can be charged back)")
ok(buyer.post("/orders/%s/swap" % pub, data={"_csrf": csrf(buyer)}).status_code == 400, "you can only swap once")
payments.payment_method_type = real_pm

# iDEAL: paid straight away
A.utcnow = real_now
r = sell("20", "40"); lid2 = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid2, data={"_csrf": csrf(buyer)})
pub2 = r.location.split("/")[2]
run("UPDATE listings SET event_date=? WHERE id=?", date.today().isoformat(), lid2)
buyer.post("/checkout/%s/test" % pub2, data={"_csrf": csrf(buyer)})
A.utcnow = lambda: doors + timedelta(minutes=50)
r = buyer.post("/orders/%s/swap" % pub2, data={"_csrf": csrf(buyer)}, follow_redirects=True)
o2 = one("SELECT * FROM orders WHERE public_id=?", pub2)
ok(o2["status"] == "released" and o2["transfer_ref"] == "test-transfer" and "being paid" in html(r),
   "a swap on an iDEAL payment sends the seller the held money straight away")
ok("Swapped" in html(buyer.get("/orders/%s" % pub2)), "the buyer's order page shows it's swapped")
A.utcnow = real_now

# early-swap pressure in the chat is flagged
import moderation
ok(all(moderation.RATE_EARLY.search(m) for m in ("Can you swap already?", "pls slide swap now", "kun je alvast swappen")) and
   not moderation.RATE_EARLY.search("Enjoy the night!"), "asking the buyer to swap early is flagged for the admins")

# --- #34: one failing payout never stops the others
orders = []
for _ in range(2):
    r = sell("20", "25"); lid3 = int(re.search(r"/t/(\d+)", r.location).group(1))
    r = buyer.post("/t/%d/buy" % lid3, data={"_csrf": csrf(buyer)}); p = r.location.split("/")[2]
    buyer.post("/checkout/%s/test" % p, data={"_csrf": csrf(buyer)}); orders.append(p)
run("UPDATE orders SET payout_due_at=? WHERE public_id IN (?, ?)", "2000-01-01 00:00:00", *orders)
app.logger.disabled = True  # the failures below are on purpose; keep their tracebacks out of the test output
real_transfer = payments.transfer_to_seller
payments.transfer_to_seller = lambda order, s: (_ for _ in ()).throw(RuntimeError("Stripe says no")) if order["public_id"] == orders[0] else "tr_ok"
with app.test_request_context(): released, waiting = A.release_due_payouts()
ok(released == 1 and one("SELECT status FROM orders WHERE public_id=?", orders[1])["status"] == "released"
   and one("SELECT payout_errors FROM orders WHERE public_id=?", orders[0])["payout_errors"] == 1,
   "#34 one order that Stripe refuses doesn't stop the next one; the error is counted")
SENT.clear()
with app.test_request_context():
    A.release_due_payouts(); A.release_due_payouts(); r4 = A.release_due_payouts()
ok(one("SELECT payout_errors FROM orders WHERE public_id=?", orders[0])["payout_errors"] == 3 and r4 == (0, 0),
   "#34 after 3 failures it isn't retried automatically")
ok(any("manual look" in s for to, s, b in SENT), "#34 ...and the admins are told")
payments.transfer_to_seller = real_transfer
ap = html(admin.get("/admin"))
ok("need a manual look" in ap and orders[0] in ap, "#34 /admin lists payouts that need a manual look")
config.TASK_SECRET = "secret"
real_backup = A.nightly_backup
A.nightly_backup = lambda: (_ for _ in ()).throw(OSError("disk full"))
r = app.test_client().post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
A.nightly_backup = real_backup
app.logger.disabled = False
ok(r.status_code == 200 and "backup" in r.get_json()["failed"] and r.get_json()["ticket_files_deleted"] is not None,
   "#34 the hourly task carries on after one part fails, and says which")

# --- #39: no pretend reversal with live keys
real_live = payments.live
payments.live = lambda: True
ok(payments.reverse_transfer({"transfer_ref": None, "public_id": "X"}) is None, "#39 with live keys and no transfer yet, nothing is reported as taken back")
payments.live = real_live

# --- #41: only Cloudflare's own servers may pass on the visitor's address
saved = (config.TRUST_PROXY, config.PROXY_HOPS)
config.TRUST_PROXY, config.PROXY_HOPS = True, 2
with app.test_request_context(environ_base={"REMOTE_ADDR": "162.158.1.1"}, headers={"X-Forwarded-For": "81.2.3.4, 162.158.1.1"}):
    ok(A.client_ip() == "81.2.3.4", "#41 through Cloudflare, the visitor's address is used")
with app.test_request_context(environ_base={"REMOTE_ADDR": "81.9.9.9"}, headers={"X-Forwarded-For": "1.1.1.1, 81.9.9.9"}):
    ok(A.client_ip() == "81.9.9.9", "#41 a request sent straight to Render can't pick its own address with a fake header")
config.TRUST_PROXY, config.PROXY_HOPS = saved

# --- #37: the importer reads price lists
import importer
page_html = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"Event","name":"List Night",
"startDate":"%sT23:00","location":{"@type":"Place","name":"Perron"},
"offers":[{"@type":"Offer","price":"12.50","url":"https://shop.example/a"},{"@type":"Offer","price":"18"}]}</script>""" % D
found = importer.extract(page_html, "https://shop.example/e")
ok(found and str(found[0].get("face_value_max")) == "18.00", "#37 with several ticket types, the importer takes the highest price")

# --- #42: the launch check
ap = html(admin.get("/admin"))
ok("Launch check" in ap and "BACKUP_KEY set" in ap and "Hourly task ran" in ap and "Chargeback/refund events" in ap,
   "#42 /admin shows the launch check")
ok(app.config["SECRET_KEY"] not in ap, "#42 ...without showing secret values")

print("\n%d failed" % len(fails) if fails else "\nAll swap checks passed")
sys.exit(1 if fails else 0)
