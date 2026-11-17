"""'Still for sale?' check (17 Nov 2026): the day before the event, sellers with unsold tickets get an email (and push)
asking whether the ticket is still for sale, so one sold in a group chat comes down before a Spare buyer pays for it.
Their listing page asks the same, with "Yes, still for sale" and "No, sold elsewhere: remove it"."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.TICKET_CHECKS = False; config.FACE_PROOF_REQUIRED = False; config.NEW_SELLER_LIMITS = False
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.TASK_SECRET = "secret"; config.RESELL_HOUR = 0  # (the check runs from RESELL_HOUR; 0 = any time in this test)
import notify
mails = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: mails.append((to, subject, body)) and False  # (False: login codes show on screen in tests)
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)
def login(c, email, name):
    with app.app_context(): A.db.run("DELETE FROM hits")
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})

seller, other = app.test_client(), app.test_client()
login(seller, "s@test.nl", "Sam"); login(other, "o@test.nl", "Oli")
run("UPDATE users SET payouts_ready=1, phone='+31611111111', adult_confirmed_at=datetime('now')")
TOMORROW, LATER = (date.today() + timedelta(days=1)).isoformat(), (date.today() + timedelta(days=5)).isoformat()
def sell(day, name):
    r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "venue": "Perron", "event_date": day, "event_name": name,
                                   "face_value": "20", "price": "20", "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(8)), "t.pdf"),
                                   "_csrf": csrf(seller)}, content_type="multipart/form-data")
    return int(re.search(r"/t/(\d+)", r.location).group(1))
tom, later, fresh, gone = sell(TOMORROW, "Tomorrow Night"), sell(LATER, "Later Night"), sell(TOMORROW, "Fresh Night"), sell(TOMORROW, "Gone Night")
run("UPDATE listings SET created_at=datetime('now', '-2 days') WHERE id IN (?, ?, ?)", tom, later, gone)
run("UPDATE tickets SET status='sold' WHERE listing_id=?", gone)

page = html(seller.get("/t/%d" % tom))
ok("Is this ticket still for sale?" not in page, "before the check: no question on the listing")
ok("Sold it elsewhere? Remove listing" in page, "the remove button says what it's for")

def hourly(): return other.post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
mails.clear(); r = hourly()
ok(r.status_code == 200 and (r.get_json() or {}).get("still_for_sale_checks") == 1, "the hourly task asks once: %r" % (r.get_json() or {}).get("still_for_sale_checks"))
asked = [m for m in mails if m[0] == "s@test.nl" and "Still for sale?" in m[1]]
ok(len(asked) == 1 and "Tomorrow Night" in asked[0][1] and "/t/%d" % tom in asked[0][2], "the seller gets one email for tomorrow's listing, with a link to it")
ok(one("SELECT still_asked_at FROM listings WHERE id=?", later)["still_asked_at"] is None, "not for an event later in the week")
ok(one("SELECT still_asked_at FROM listings WHERE id=?", fresh)["still_asked_at"] is None, "not for a listing posted in the last 12 hours")
ok(one("SELECT still_asked_at FROM listings WHERE id=?", gone)["still_asked_at"] is None, "not when every ticket is already sold")
mails.clear(); hourly()
ok(not [m for m in mails if "Still for sale?" in m[1]], "never twice")

page = html(seller.get("/t/%d" % tom))
ok("Is this ticket still for sale?" in page and "Yes, still for sale" in page and "No, sold elsewhere: remove it" in page, "the listing page asks the same question")
ok("Is this ticket still for sale?" not in html(other.get("/t/%d" % tom)), "buyers never see the question")
ok(other.post("/t/%d/still" % tom, data={"_csrf": csrf(other)}).status_code == 404, "someone else can't answer for the seller")

r = seller.post("/t/%d/still" % tom, data={"_csrf": csrf(seller)}, follow_redirects=True)
ok("Your ticket stays on sale" in html(r) and "Is this ticket still for sale?" not in html(r), "yes: thanks, and the question goes away")
ok(one("SELECT status FROM listings WHERE id=?", tom)["status"] == "active", "...and the ticket stays on sale")

run("UPDATE listings SET still_confirmed_at=NULL WHERE id=?", tom)
r = seller.post("/t/%d/remove" % tom, data={"_csrf": csrf(seller)})
ok(r.status_code == 302 and one("SELECT status FROM listings WHERE id=?", tom)["status"] == "removed", "no, sold elsewhere: the listing comes down")

import i18n
from locales import fr, de, es
keys = ["mail_still_subject", "mail_still_body", "push_still_title", "still_title", "still_body", "still_yes", "still_no", "still_thanks", "remove_sold_elsewhere"]
for lang, table in (("en", i18n.STRINGS["en"]), ("nl", i18n.STRINGS["nl"]), ("fr", fr.STRINGS), ("de", de.STRINGS), ("es", es.STRINGS)):
    ok(all(table.get(k) for k in keys), "%s has every still-for-sale text" % lang)

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
