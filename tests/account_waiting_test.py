"""Checks for the approved proposal 'Show tickets that are waiting on the account page' (7 Nov 2026): listings
being checked (review) or waiting for payout setup (draft) show under "Your listings", greyed with a label, instead of
"You have no tickets for sale"; drafts get a "Set up payouts" button; active listings show as before."""
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
def row(page, lid):
    """The <li> of one listing on the account page, or '' when it isn't listed."""
    m = re.search(r'<li[^>]*>\s*<a href="/t/%d">.*?</li>' % lid, page, re.S)
    return m.group(0) if m else ""

admin, seller, newbie = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(newbie, "n@test.nl", "Noa")
verify_phone(seller, "0611111111"); verify_phone(newbie, "0622222222")
run("UPDATE users SET payouts_ready=1 WHERE email='s@test.nl'")
D = (date.today() + timedelta(days=3)).isoformat()
admin.post("/admin/events/save", data={"name": "Waiting Night", "venue": "Perron", "category": "club", "event_date": D,
                                       "door_time": "23:00", "_csrf": csrf(admin)})
ev = one("SELECT * FROM events WHERE name='Waiting Night'")

def sell(c, price="25"):
    return c.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20",
                                 "price": price, "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(8)), "t.pdf"),
                                 "_csrf": csrf(c)}, content_type="multipart/form-data")
def listed(r): return int(re.search(r"/t/(\d+)", r.location).group(1))

# --- Before: a seller with only a listing being checked used to see "no tickets for sale"
active, review, draft, past, removed = (listed(sell(seller, p)) for p in ("25", "26", "27", "28", "29"))
run("UPDATE listings SET status='review', review_reason='face' WHERE id=?", review)
run("UPDATE listings SET status='draft' WHERE id=?", draft)
run("UPDATE listings SET status='review', event_date=? WHERE id=?", (date.today() - timedelta(days=1)).isoformat(), past)
run("UPDATE listings SET status='removed' WHERE id=?", removed)
page = html(seller.get("/account"))

# --- Active listing: exactly as before
r = row(page, active)
ok(r and "1 left" in r and "€25" in r and "tl-waiting" not in r, "an active listing still shows with 'N left' and its price, not greyed")

# --- Review listing: greyed, labelled, no payout button
r = row(page, review)
ok(r and "tl-waiting" in r and "Waiting for our check" in r and "€26" in r, "a listing being checked shows greyed with 'Waiting for our check'")
ok("left" not in r and "Set up payouts" not in r, "a listing being checked has no 'N left' and no payout button")

# --- Draft listing: greyed, labelled, with the payout button only while payouts aren't set up
r = row(page, draft)
ok(r and "tl-waiting" in r and "Goes on sale after payout setup" in r, "a draft listing shows greyed with 'Goes on sale after payout setup'")
ok("Set up payouts" not in r, "no payout button when payouts are already set up")

# --- Past and removed listings stay hidden; the empty text is gone
ok(not row(page, past) and not row(page, removed), "past and removed listings don't show")
ok("You have no tickets for sale" not in page, "no 'You have no tickets for sale' while tickets are waiting")
ok(page.index('href="/t/%d"' % active) < page.index('href="/t/%d"' % review) < page.index('href="/t/%d"' % draft),
   "listings stay in one list in the order they were posted for the same night")

# --- The real flow: a new seller without payout setup posts a ticket and finds it on the account page
config.NEW_SELLER_LIMITS = True
r = sell(newbie)
ok(r.status_code == 302 and "/account/payouts" in r.location, "setup: a new seller's ticket waits for payout setup")
nid = one("SELECT id FROM listings WHERE seller_id=(SELECT id FROM users WHERE email='n@test.nl')")["id"]
ok(one("SELECT status FROM listings WHERE id=?", nid)["status"] == "draft", "setup: it's saved as a draft")
page = html(newbie.get("/account"))
r = row(page, nid)
ok(r and "Goes on sale after payout setup" in r and 'href="/account/payouts"' in r and "Set up payouts" in r,
   "the new seller sees their waiting ticket with a 'Set up payouts' button")
ok("You have no tickets for sale" not in page, "the new seller no longer reads 'You have no tickets for sale'")
ok(html(newbie.get("/t/%d" % nid)).count("Set up payouts") >= 1, "the row links to the listing page, which explains the wait")
config.NEW_SELLER_LIMITS = False

# --- Dutch
newbie.get("/lang/nl?next=/account")
r = row(html(newbie.get("/account")), nid)
ok("Te koop zodra je uitbetaling is ingesteld" in r and "Uitbetaling instellen" in r, "Dutch label and button")
seller.get("/lang/nl?next=/account")
ok("Wacht op onze controle" in row(html(seller.get("/account")), review), "Dutch label for a listing being checked")

# --- Every language has both labels
import i18n
for lang in i18n.LANGS:
    for key in ("listing_wait_review", "listing_wait_draft"):
        ok(i18n.STRINGS.get(lang, {}).get(key), "%s has %s" % (lang, key))

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
