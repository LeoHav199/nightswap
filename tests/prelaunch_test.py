import io, os, re, sys, sqlite3, tempfile, zipfile
from datetime import date, datetime
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATA_DIR = tmp
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; MAIL_WORKS = [False]
def fake_send(to, subject, body, attachments=()):
    if not MAIL_WORKS[0]:
        return False
    SENT.append((to, subject, body, list(attachments))); return True
notify.send_email = fake_send
import app as A
from flask import render_template_string
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
def at_hour(h):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.combine(date.today(), datetime.min.time()).replace(hour=h)
    return Fixed

admin, seller, visitor = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); visitor.get("/")
os.makedirs(os.path.join(config.UPLOAD_DIR, "tickets"), exist_ok=True)
open(os.path.join(config.UPLOAD_DIR, "tickets", "x.pdf"), "wb").write(b"%PDF-1.4 a ticket")

# --- Backups
r = admin.get("/admin/backup")
z = zipfile.ZipFile(io.BytesIO(r.data))
ok(r.mimetype == "application/zip" and "spare.sqlite3" in z.namelist() and "uploads/tickets/x.pdf" in z.namelist(),
   "admin can download a backup with the database and the uploaded tickets")
with tempfile.TemporaryDirectory() as d:
    z.extract("spare.sqlite3", d)
    users = sqlite3.connect(os.path.join(d, "spare.sqlite3")).execute("SELECT COUNT(*) FROM users").fetchone()[0]
ok(users == 2, "the backed-up database opens and holds the real data")
ok(visitor.get("/admin/backup").status_code in (302, 404), "visitors can't download backups")
real = A.datetime
MAIL_WORKS[0] = True
def nightly():
    with app.test_request_context():
        return A.nightly_backup()
A.datetime = at_hour(14)
ok(nightly() is False and not SENT, "no backup email during the day")
A.datetime = at_hour(3)
ok(nightly() is True and SENT and SENT[-1][3] and SENT[-1][3][0][0].endswith(".zip"), "around 03:00 the admins get the backup as an attachment")
ok(nightly() is False and len(SENT) == 1, "only one backup email per night")
ok(len([f for f in os.listdir(os.path.join(tmp, "backups")) if f.startswith("spare-backup-")]) == 1, "a copy is also kept on the disk")
A.datetime = real
MAIL_WORKS[0] = False

# --- Crash emails
def boom():
    raise RuntimeError("test crash")
app.view_functions["credits"] = boom  # make one page crash on purpose
app.logger.disabled = True  # the crash is on purpose; keep the test output clean
app.config["TESTING"] = False; app.config["PROPAGATE_EXCEPTIONS"] = False
MAIL_WORKS[0] = True; SENT.clear()
r = visitor.get("/credits")
ok(r.status_code == 500 and "Something went wrong" in html(r), "a crash shows a friendly page")
ok(SENT and "RuntimeError" in SENT[-1][1] and "test crash" in SENT[-1][2] and "/credits" in SENT[-1][2], "and emails the admins what broke and where")
for _ in range(8):
    visitor.get("/credits")
ok(len(SENT) <= 5, "at most 5 crash emails an hour")
app.config["TESTING"] = True; MAIL_WORKS[0] = False

# --- Honest badges, own fonts, name note
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
config.SMS_ENABLED = False
with app.test_request_context():
    page = render_template_string('{% import "_macros.html" as m with context %}{{ m.seller_badges(s, None) }}', s={"phone_verified_at": "x"}, sms_enabled=False)
ok("Phone verified" not in page, "no 'phone verified' badge while phone codes go by email")
with app.test_request_context():
    page = render_template_string('{% import "_macros.html" as m with context %}{{ m.seller_badges(s, None) }}', s={"phone_verified_at": "x"}, sms_enabled=True)
ok("Phone verified" in page, "the badge comes back once real SMS checks are on")
home = html(visitor.get("/"))
ok("googleapis" not in home and "gstatic" not in home and "/static/css/fonts.css" in home, "fonts come from Spare itself, not Google")
ok(visitor.get("/static/css/fonts.css").status_code == 200 and visitor.get("/static/fonts/instrument-sans-latin-e190a6.woff2").status_code == 200,
   "the font files are served")
ok("Name on your ticket?" in html(seller.get("/sell")), "the sell form explains changing the name on personal tickets")

print("FAILED:", fails or "none")
