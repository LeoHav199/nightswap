import os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(), headers=None, **kw: SENT.append((to, subject, body, headers or {})) and False
import app as A, i18n
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

# The checklist from "20 things so your app doesn't get sued" (6 Nov 2026)
c = app.test_client()
terms = html(c.get("/terms"))
ok("16 or older to use Spare" in terms and "18 or older to sell" in terms, "terms: 16+ to use Spare, 18+ to sell")
ok("Only upload an event poster or photo you're allowed to use" in terms.replace("&#39;", "'"), "terms: only upload posters you may use")
for code, word in (("nl", "16 jaar"), ("fr", "16 ans"), ("de", "16 Jahre"), ("es", "16 años")):
    cc = app.test_client(); cc.get("/lang/%s?next=/" % code)
    ok(word in html(cc.get("/terms")), "the age rule is in the %s terms" % code)
ok(i18n.t("en", "cs_point_safe_title") == "Protected against scams" and "No scams" not in i18n.t("en", "cs_point_safe_title"), "no absolute 'No scams' promise")
css = open("static/css/style.css").read()
ok("--faint: #928eb4;" in css, "the hint grey passes 4.5:1 contrast")
ok("SIL OPEN FONT LICENSE" in open("static/fonts/OFL.txt").read() and c.get("/static/fonts/OFL.txt").status_code == 200, "the font licence ships with the fonts")
ok("OFL.txt" in html(c.get("/credits")), "the credits page links the font licence")

# Alert emails: a stop link and one-click unsubscribe; order emails keep coming
buyer = app.test_client(); login(buyer, "b@test.nl", "Bea")
with app.app_context():
    uid = A.db.one("SELECT id FROM users WHERE email='b@test.nl'")["id"]
    seller_id = A.db.run("INSERT INTO users (email, first_name) VALUES ('s@test.nl', 'Sam')")
    D = (date.today() + timedelta(days=3)).isoformat()
    A.db.run("INSERT INTO alerts (user_id, venue, event_date, event_name) VALUES (?, 'Perron', ?, 'Gala')", uid, D)
    listing = {"event_date": D, "venue": "Perron", "event_name": "Gala", "seller_id": seller_id, "price_cents": 2000, "id": 1, "city": "Rotterdam"}
with app.test_request_context():
    SENT.clear(); A.notify_alerts(listing)
mail = [m for m in SENT if m[0] == "b@test.nl"]
ok(mail and "Stop them with one click" in mail[0][2] and "/emails/stop/" in mail[0][2], "alert emails have a stop link")
ok(mail and mail[0][3].get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click", "...and the one-click unsubscribe mail apps show")
ok(mail and mail[0][2].rstrip().endswith("The Spare team, two students at Erasmus University Rotterdam"), "the stop line sits above the sign-off")
stop = re.search(r"(/emails/stop/[^\s]+)", mail[0][2]).group(1)
ok(c.post(stop).status_code == 200, "one click from a mail app stops them, without signing in")
with app.app_context():
    ok(A.db.one("SELECT optional_emails_off FROM users WHERE id=?", uid)["optional_emails_off"] is not None, "...and it's saved")
    A.db.run("UPDATE alerts SET last_notified_at=NULL")
with app.test_request_context():
    SENT.clear(); A.notify_alerts(listing)
ok(not [m for m in SENT if m[0] == "b@test.nl"], "after stopping, no more alert emails")
ok(c.get("/emails/stop/not-a-real-token").status_code == 404, "a made-up stop link does nothing")
st = html(buyer.get("/settings"))
ok('id="emails"' in st and 'name="optional_emails"' in st, "Settings has a switch to turn them back on")
buyer.post("/settings/emails", data={"optional_emails": "on", "_csrf": csrf(buyer)})
with app.app_context():
    ok(A.db.one("SELECT optional_emails_off FROM users WHERE id=?", uid)["optional_emails_off"] is None, "...which works")

print("\n%d failed" % len(fails))
