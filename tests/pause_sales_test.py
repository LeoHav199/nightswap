"""Checks for the 'pause sales' switch at /admin → Launch (approved 7 Nov 2026): while it's on nobody can buy or post
a listing, but order pages, Swap, reports, messages and the hourly task keep working for people who already paid."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.TICKET_CHECKS = False; config.FACE_PROOF_REQUIRED = False; config.NEW_SELLER_LIMITS = False  # (covered in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.TASK_SECRET = "secret"
import notify
notify.send_email = lambda to, subject, body, attachments=(), **kw: False
import app as A
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

admin, seller, buyer, dutch = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
dutch.get("/lang/nl?next=/")
verify_phone(seller, "0611111111")
run("UPDATE users SET payouts_ready=1 WHERE email='s@test.nl'")
D = date.today().isoformat()
admin.post("/admin/events/save", data={"name": "Pause Night", "venue": "Perron", "category": "club", "event_date": D,
                                       "door_time": "23:00", "_csrf": csrf(admin)})
ev = one("SELECT * FROM events WHERE name='Pause Night'")

def sell():
    return seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20",
                                      "price": "25", "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(8)), "t.pdf"),
                                      "_csrf": csrf(seller)}, content_type="multipart/form-data")
def listed(r): return int(re.search(r"/t/(\d+)", r.location).group(1))

# --- Before the pause: a buyer gets a ticket, and there are two more on sale
lid1, lid2, lid3 = listed(sell()), listed(sell()), listed(sell())
r = buyer.post("/t/%d/buy" % lid1, data={"_csrf": csrf(buyer)}); pub = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
r = buyer.post("/t/%d/buy" % lid2, data={"_csrf": csrf(buyer)}); pub2 = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub2, data={"_csrf": csrf(buyer)})
ok(one("SELECT status FROM orders WHERE public_id=?", pub)["status"] == "paid", "setup: a paid order before the pause")
ok("PAUSED" not in html(admin.get("/admin")) and "Pause new sales" in html(admin.get("/admin")),
   "/admin → Launch offers 'Pause new sales' and shows no PAUSED pill while sales are open")
ok("Sales are paused" not in html(buyer.get("/t/%d" % lid3)), "no banner while sales are open")

# --- Only admins can pause
ok(buyer.post("/admin/launch", data={"action": "pause", "_csrf": csrf(buyer)}).status_code in (302, 403, 404)
   and one("SELECT value FROM settings WHERE key='sales_paused'") is None, "a normal user can't pause sales")

# --- Pause, with a short message in English and Dutch
r = admin.post("/admin/launch", data={"action": "pause", "msg_en": "  Back in about   10 minutes. ", "msg_nl": "Over 10 minuten terug.",
                                      "_csrf": csrf(admin)}, follow_redirects=True)
ok(one("SELECT value FROM settings WHERE key='sales_paused'")["value"] == "1", "the switch is stored in the settings table (no redeploy)")
ap = html(r)
ok("tag-paused" in ap and "PAUSED" in ap and "Resume sales" in ap, "/admin shows a red PAUSED pill and a Resume button")
ok(one("SELECT value FROM settings WHERE key='sales_paused_msg_en'")["value"] == "Back in about 10 minutes.",
   "the English message is tidied and saved")

# --- Buying is refused, on every buy button
before = one("SELECT COUNT(*) AS n FROM orders")["n"]
r = buyer.post("/t/%d/buy" % lid3, data={"_csrf": csrf(buyer)}, follow_redirects=True)
ok(one("SELECT COUNT(*) AS n FROM orders")["n"] == before and "Sales are paused for a short while" in html(r),
   "buying is refused server-side and the buyer sees why")
ok(one("SELECT COUNT(*) AS n FROM tickets WHERE listing_id=? AND status='available'", lid3)["n"] == 1, "no ticket is held")
lp = html(buyer.get("/t/%d" % lid3))
ok("Sales are paused for a short while. Your tickets and Swap still work." in lp and "Back in about 10 minutes." in lp
   and re.search(r'type="submit" disabled>[^<]*Buy', lp) and "buybar" not in lp,
   "the listing page shows the calm banner with the message, a disabled Buy button and no phone buy bar")
with app.test_request_context(): ev_url = A.event_url("Perron", D, "Pause Night")
ep = html(buyer.get(ev_url))
ok("Sales are paused for a short while" in ep, "the event page shows the banner above the tickets")
dp = html(dutch.get("/t/%d" % lid3))
ok("De verkoop staat even op pauze" in dp and "Over 10 minuten terug." in dp and "Back in about" not in dp,
   "Dutch visitors get the Dutch text and the Dutch message")
import i18n
ok(all(i18n.t(code, "sales_paused") != i18n.t("en", "sales_paused") for code in ("nl", "fr", "de", "es")),
   "the banner is translated into all 5 languages")

# --- Listing is refused
n_listings = one("SELECT COUNT(*) AS n FROM listings")["n"]
r = sell()
ok(r.status_code == 200 and one("SELECT COUNT(*) AS n FROM listings")["n"] == n_listings and "Sales are paused" in html(r),
   "posting a listing is refused server-side and the form comes back with the banner")
sp = html(seller.get("/sell"))
ok("Sales are paused" in sp and re.search(r'type="submit" disabled>[^<]*Post', sp), "the sell form shows the banner and a disabled Post button")

# --- Everything buyers already have keeps working
r = buyer.get("/orders/%s" % pub)
ok(r.status_code == 200 and "Sales are paused" not in html(r), "the buyer's order page opens as usual")
tk = one("SELECT id FROM tickets WHERE order_id=(SELECT id FROM orders WHERE public_id=?)", pub)
ok(buyer.get("/orders/%s/tickets/%d" % (pub, tk["id"])).status_code == 200, "the ticket file still downloads")
real_now = A.utcnow
with app.app_context(): doors = A.doors_open_at(one("SELECT * FROM listings WHERE id=?", lid1))
A.utcnow = lambda: doors + timedelta(minutes=30)
r = buyer.post("/orders/%s/swap" % pub, data={"_csrf": csrf(buyer)})
ok(r.status_code == 302 and one("SELECT swapped_at FROM orders WHERE public_id=?", pub)["swapped_at"], "Swap still works while paused")
r = buyer.post("/orders/%s/report" % pub2, data={"reason": "The organiser cancelled my entry", "_csrf": csrf(buyer)})
ok(one("SELECT status FROM orders WHERE public_id=?", pub2)["status"] == "disputed", "reporting a problem still works")
A.utcnow = real_now
r = buyer.get("/orders/%s/chat" % pub)
tid = int(re.search(r"/threads/(\d+)", r.location).group(1))
ok(buyer.get("/threads/%d" % tid).status_code == 200, "the order chat still opens")
seller.post("/threads/%d/send" % tid, data={"body": "Enjoy the night", "_csrf": csrf(seller)})
ok(one("SELECT 1 FROM messages WHERE thread_id=? AND body='Enjoy the night'", tid), "messages are still delivered")
r = app.test_client().post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
ok(r.status_code == 200, "the hourly task (payouts, reminders, backups) still runs")

# --- Resume
admin.post("/admin/launch", data={"action": "resume", "_csrf": csrf(admin)})
ok("PAUSED" not in html(admin.get("/admin")), "after Resume the PAUSED pill is gone")
r = buyer.post("/t/%d/buy" % lid3, data={"_csrf": csrf(buyer)})
ok("/checkout/" in r.location, "buying works again after Resume")
ok(sell().status_code == 302, "listing works again after Resume")

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
