"""Checks for two launch-week extras (10 Nov 2026, agent proposals Leo approved):
1. the launch email has a second "Sell a ticket" button, in all 5 languages, and /admin/waitlist can send a test to yourself;
2. /admin lists events in the next 14 days without an official price, with a box to type it in."""
import os, re, sys, tempfile
from datetime import timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.TASK_SECRET = "secret"
import notify
real_email_html = notify.email_html
sent = []
def fake_send(to, subject, body, attachments=(), headers=None, buttons=None):
    sent.append({"to": to, "subject": subject, "body": body, "headers": headers, "buttons": buttons})
    return True
notify.send_email = fake_send
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
    c.get("/"); c.post("/login", data={"email": email, "_csrf": csrf(c)})
    code = re.search(r"code is (\d{6})", sent[-1]["body"]).group(1)
    c.post("/login/verify", data={"code": code, "first_name": name, "_csrf": csrf(c)})
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)
def day(n):
    with app.app_context(): return (A.today() + timedelta(days=n)).isoformat()

admin, user = app.test_client(), app.test_client()
login(admin, "admin@example.com", "Ad"); login(user, "u@test.nl", "Uma")

# --- 1. The launch email
LABELS = {"en": "Sell a ticket", "nl": "Verkoop een ticket", "fr": "Vendre un billet", "de": "Ticket verkaufen", "es": "Vender una entrada"}
with app.test_request_context():
    for lang, label in LABELS.items():
        subject, body, headers, buttons = A.launch_email(lang, "tok123")
        sell_url = A.base_url() + "/sell"
        ok(sell_url in body.splitlines(), "%s: the plain-text launch email has the sell link on its own line" % lang)
        ok("/waitlist/leave/tok123" in body and "List-Unsubscribe" in headers, "%s: the remove-me link and unsubscribe header stay" % lang)
        ok(buttons == {sell_url: label}, "%s: the sell button is labelled '%s'" % (lang, label))
        page = real_email_html(subject, body, buttons)
        ok(page.count("border-radius:999px") == 2 and ">Open Spare</a>" in page and ">%s</a>" % label in page,
           "%s: the HTML email shows two buttons, Open Spare first, then %s" % (lang, label))
        ok(page.index(">Open Spare</a>") < page.index(">%s</a>" % label), "%s: Open Spare stays the main button" % lang)
        ok("—" not in body, "%s: no em dashes in the launch email" % lang)
    page = real_email_html("x", "Hi\n\nhttps://a.example/x\n\nhttps://a.example/y", {"https://a.example/y": "Second"})
    first, second = page.split("border-radius:999px")[0], page.split("border-radius:999px")[1]
    ok("background:#d4ff3a;color:#15132b" in first and "border:2px solid #d4ff3a" in second, "the first button is filled, later ones outlined")
    ok(real_email_html("x", "Hi\n\nhttps://a.example/x").count(">Open Spare</a>") == 1, "emails without labels still say Open Spare")

run("INSERT INTO waitlist (email, interest, lang, token) VALUES ('w1@test.nl', 'sell', 'nl', 't1')")
run("INSERT INTO waitlist (email, interest, lang, token) VALUES ('w2@test.nl', 'buy', 'fr', 't2')")

sent.clear()
r = admin.post("/admin/waitlist/test", data={"lang": "nl", "_csrf": csrf(admin)}, follow_redirects=True)
ok(len(sent) == 1 and sent[0]["to"] == "admin@example.com", "the test launch email goes only to the admin")
ok(sent and sent[0]["buttons"] and "Verkoop een ticket" in sent[0]["buttons"].values(), "the test email is in the chosen language")
ok(one("SELECT COUNT(*) AS n FROM waitlist WHERE notified_at IS NOT NULL")["n"] == 0, "a test send doesn't mark anyone as emailed")
ok("Nobody on the list got it" in html(r), "the admin sees the test went only to them")
ok("Send me a test" in html(admin.get("/admin/waitlist")), "the waitlist page has a Send me a test button")
sent.clear()
ok(user.post("/admin/waitlist/test", data={"lang": "en", "_csrf": csrf(user)}).status_code == 404 and not sent,
   "only an admin can send a test")

sent.clear()
admin.post("/admin/waitlist/notify", data={"_csrf": csrf(admin)})
by_to = {s["to"]: s for s in sent}
ok(set(by_to) == {"w1@test.nl", "w2@test.nl"}, "the real launch email still goes to everyone waiting")
ok("Verkoop een ticket" in by_to["w1@test.nl"]["buttons"].values() and "Vendre un billet" in by_to["w2@test.nl"]["buttons"].values(),
   "each person gets the sell button in their own language")
ok("/waitlist/leave/t1" in by_to["w1@test.nl"]["body"], "each person gets their own remove-me link")
ok(one("SELECT COUNT(*) AS n FROM waitlist WHERE notified_at IS NULL")["n"] == 0, "everyone sent is marked as emailed")

# --- 2. Events without an official price
def event(name, when, price=None, status="published", cancelled=False, url="https://tickets.example/e"):
    run("""INSERT INTO events (name, venue, city, event_date, ticket_url, face_value_max_cents, status, cancelled_at)
           VALUES (?, 'BIRD', 'Rotterdam', ?, ?, ?, ?, ?)""", name, when, url, price, status, "2026-01-01" if cancelled else None)
    return one("SELECT id FROM events WHERE name=?", name)["id"]

soon = event("No Price Soon", day(3))
event("Has Price", day(3), price=2500)
event("Too Far Away", day(20))
event("Already Past", day(-1))
event("Called Off", day(4), cancelled=True)
event("Hidden One", day(4), status="hidden")
seller_id = one("SELECT id FROM users WHERE email='u@test.nl'")["id"]
for _ in range(2):
    run("""INSERT INTO listings (seller_id, event_id, venue, event_name, event_date, face_value_cents, price_cents, status, review_reason)
           VALUES (?, ?, 'BIRD', 'No Price Soon', ?, 2000, 2500, 'review', 'face value proof')""", seller_id, soon, day(3))

page = html(admin.get("/admin"))
card = page[page.index('id="prices"'):page.index('id="review"')]
ok("No Price Soon" in card, "an event in the next 14 days without a price is listed")
ok(all(n not in card for n in ("Has Price", "Too Far Away", "Already Past", "Called Off", "Hidden One")),
   "priced, far-off, past, cancelled and hidden events are left out")
ok("2 listings waiting" in card, "the row shows how many listings are waiting for that event")
ok("https://tickets.example/e" in card, "the row links to the ticket page to look the price up")

for bad in ("abc", "0.50", "99999"):
    admin.post("/admin/event-price/%d" % soon, data={"price": bad, "_csrf": csrf(admin)})
ok(one("SELECT face_value_max_cents AS c FROM events WHERE id=?", soon)["c"] is None, "a wrong, too-low or too-high price is refused")
ok(user.post("/admin/event-price/%d" % soon, data={"price": "20", "_csrf": csrf(user)}).status_code == 404 and
   one("SELECT face_value_max_cents AS c FROM events WHERE id=?", soon)["c"] is None, "only an admin can set a price")
r = admin.post("/admin/event-price/%d" % soon, data={"price": "24,50", "_csrf": csrf(admin)})
ok(r.status_code == 302 and r.headers["Location"].endswith("/admin#prices"), "saving goes back to the price list")
ok(one("SELECT face_value_max_cents AS c FROM events WHERE id=?", soon)["c"] == 2450, "the price is saved (24,50 → €24.50)")
page = html(admin.get("/admin"))
ok("No Price Soon" not in page[page.index('id="prices"'):page.index('id="review"')], "a priced event drops off the list")
ok(one("SELECT COUNT(*) AS n FROM listings WHERE status='review'")["n"] == 2, "listings already waiting stay in the check queue")
ok(admin.post("/admin/event-price/999999", data={"price": "20", "_csrf": csrf(admin)}).status_code == 404, "an unknown event gives 404")

run("UPDATE events SET face_value_max_cents=1000 WHERE face_value_max_cents IS NULL")
ok("Every event in the next 14 days has an official price" in html(admin.get("/admin")), "an empty list says so")

print("\n%d failed" % len(fails)); sys.exit(1 if fails else 0)
