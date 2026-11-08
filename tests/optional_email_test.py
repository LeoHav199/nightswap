import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; config.ADMIN_EMAILS = {"admin@example.com"}; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(), headers=None, **kw: SENT.append((to, subject, body, headers or {})) and False  # False = "not sent", so sign-in codes show on screen
import app as A
from itsdangerous import URLSafeSerializer
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
def at_hour(h):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.combine(date.today(), datetime.min.time()).replace(hour=h)
    return Fixed
def uid_of(email):
    with app.app_context(): return A.db.one("SELECT id FROM users WHERE email=?", email)["id"]
def off(email):
    with app.app_context(): return A.db.one("SELECT optional_emails_off FROM users WHERE email=?", email)["optional_emails_off"]
def stop_path(user_id):
    with app.test_request_context(base_url="https://sparetickets.eu"):
        return "/emails/stop/" + A.email_stop_url(user_id).rsplit("/emails/stop/", 1)[1]
def to(email): return [m for m in SENT if m[0] == email]

seller, keen, quiet, quiet_seller = (app.test_client() for _ in range(4))  # keen: emails on; quiet: switched off
login(seller, "s@test.nl", "Sam"); login(keen, "keen@test.nl", "Kim"); login(quiet, "quiet@test.nl", "Quinn")
verify_phone(seller, "0611111111")
anon = app.test_client()  # nobody signed in, like a mail app's one-click POST

# --- The stop link
path = stop_path(uid_of("quiet@test.nl"))
ok(off("quiet@test.nl") is None and off("keen@test.nl") is None, "everyone starts with optional emails on")
r = anon.get(path)
ok(r.status_code == 302 and r.location.endswith("/settings#emails"), "the link sends people to the settings switch")
ok(off("quiet@test.nl") is not None, "...and switches optional emails off")
ok(off("keen@test.nl") is None and off("s@test.nl") is None, "...for that user only")
first = off("quiet@test.nl")
anon.post(path)
ok(off("quiet@test.nl") == first, "using it again keeps the first time stamp")
with app.app_context(): A.db.run("UPDATE users SET optional_emails_off=NULL WHERE email='quiet@test.nl'")
r = anon.post(path)
ok(r.status_code == 200 and off("quiet@test.nl") is not None, "the one-click POST from a mail app works without signing in")

good = path.rsplit("/", 1)[1]
key = app.config["SECRET_KEY"]; keen_id = uid_of("keen@test.nl")
bad = {
    # first character of the signature: the last base64 character can carry spare bits, so changing it may not change the signature
    "a changed character": good.rsplit(".", 1)[0] + "." + ("A" if good.rsplit(".", 1)[1][0] != "A" else "B") + good.rsplit(".", 1)[1][1:],
    "a cut-off token": good[:-3],
    "an extra tail": good + "x",
    "the id swapped for another user": URLSafeSerializer(key, salt="emails-stop").dumps(keen_id).split(".")[0] + "." + good.split(".")[-1],
    "another purpose's signature": URLSafeSerializer(key, salt="something-else").dumps(keen_id),
    "another secret key": URLSafeSerializer(key + "x", salt="emails-stop").dumps(keen_id),
    "an empty-looking token": "x",
}
for what, token in bad.items():
    for method in (anon.get, anon.post):
        r = method("/emails/stop/" + token)
        ok(r.status_code == 404, "a stop link with %s is refused (%s)" % (what, "GET" if method == anon.get else "POST"))
ok(off("keen@test.nl") is None, "...and nobody was switched off by them")

# --- A purchase by someone who switched off: order emails still go, with no unsubscribe header
anon.post(stop_path(uid_of("s@test.nl")))  # the seller switched off too
tomorrow = (date.today() + timedelta(days=1)).isoformat()
admin = app.test_client(); login(admin, "admin@example.com", "Ad")
admin.post("/admin/events/save", data={"face_value_max": "500", "name": "Big Night", "venue": "Maassilo", "category": "club", "event_date": tomorrow, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Big Night'")
def sell(data):
    r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                    "ticket": (io.BytesIO(data), "t.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
    return int(re.search(r"/t/(\d+)", r.location).group(1))
def buy(c, lid):
    pub = c.post("/t/%d/buy" % lid, data={"_csrf": csrf(c)}).location.split("/")[2]
    c.post("/checkout/%s/test" % pub, data={"_csrf": csrf(c)})
lid_keen, lid_quiet, lid_open = sell(b"%PDF-1.4 one"), sell(b"%PDF-1.4 two"), sell(b"%PDF-1.4 three")
SENT.clear()
buy(quiet, lid_quiet)
bought = [m for m in to("quiet@test.nl")]
sold = [m for m in to("s@test.nl")]
ok(len(bought) == 1 and "ticket" in bought[0][1].lower(), "a buyer who switched off optional emails still gets the order email")
ok(len(sold) == 1, "a seller who switched off optional emails still gets the sold email")
ok(not any("List-Unsubscribe" in m[3] for m in bought + sold) and not any("/emails/stop/" in m[2] for m in bought + sold),
   "order emails carry no unsubscribe header or stop link")
buy(keen, lid_keen)
ok(len(to("keen@test.nl")) == 1, "a buyer with optional emails on gets the order email too")

# --- Alert emails
with app.app_context():
    for email in ("keen@test.nl", "quiet@test.nl"):
        A.db.run("INSERT INTO alerts (user_id, venue, event_date, event_name) VALUES (?, 'Maassilo', ?, 'Big Night')", uid_of(email), tomorrow)
    listing = A.db.one("SELECT * FROM listings WHERE id=?", lid_open)
SENT.clear()
with app.test_request_context(base_url="https://sparetickets.eu"):
    A.notify_alerts(listing)
mail = to("keen@test.nl")
ok(len(mail) == 1, "an alert email goes to someone who has them on")
h = mail[0][3] if mail else {}
ok(h.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click" and "/emails/stop/" in h.get("List-Unsubscribe", ""),
   "...with the List-Unsubscribe and One-Click headers")
ok("<mailto:" in h.get("List-Unsubscribe", ""), "...and a mailto fallback")
header_url = re.search(r"<https?://[^/]+(/emails/stop/[^>]+)>", h.get("List-Unsubscribe", ""))
ok(header_url and header_url.group(1) in mail[0][2], "...and the same link is in the text of the email")
ok(not to("quiet@test.nl"), "an alert email is skipped for someone who switched off")
if header_url:
    anon.post(header_url.group(1))
    ok(off("keen@test.nl") is not None, "the link in the header really switches that user off")
    with app.app_context(): A.db.run("UPDATE users SET optional_emails_off=NULL WHERE email='keen@test.nl'")

# --- Resell reminders (the day before the event): buyers of keen's and quiet's tickets
real = A.datetime
A.datetime = at_hour(13)
SENT.clear()
with app.test_request_context(base_url="https://sparetickets.eu"):
    A.send_resell_reminders()
A.datetime = real
keen_mail = [m for m in to("keen@test.nl") if "/sell" in m[2]]
ok(len(keen_mail) == 1, "a resell reminder goes to a buyer who has optional emails on")
h = keen_mail[0][3] if keen_mail else {}
ok(h.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click" and "/emails/stop/" in h.get("List-Unsubscribe", ""),
   "...with the List-Unsubscribe and One-Click headers")
ok(not to("quiet@test.nl"), "a resell reminder is skipped for a buyer who switched off")

print("\n%d failed" % len(fails))
