"""Risk 1 (17 Nov 2026): a reported problem pushes an urgent alert to every admin's phone, without the buyer's words."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False; config.ADMIN_EMAILS = {"admin@example.com", "ilyas@example.com"}
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify
notify.send_email = lambda *a, **k: False
import push
PUSHED = []
real_send = push.send
def fake_send(user_id, title, body, url="/", tag=None, urgent=False):
    PUSHED.append(dict(user_id=user_id, title=title, body=body, url=url, tag=tag, urgent=urgent))
    return 1 if push_on.get(user_id, True) else 0
push_on = {}
push.send = fake_send
import app as A, qrcode
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
def png(qr):
    img = Image.new("RGB", (800, 1000), "white"); img.paste(qrcode.make(qr).convert("RGB").resize((400, 400)), (200, 300))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): A.db.run(sql, *a)

seller, admin, ilyas, buyer = (app.test_client() for _ in range(4))
login(seller, "s@student.eur.nl", "Sam"); login(admin, "admin@example.com", "Ad"); login(ilyas, "ilyas@example.com", "Il")
login(buyer, "b@test.nl", "Bea")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
m = re.search(r"code is (\d{6})", html(r))
if m: seller.post("/account/phone/verify", data={"code": m.group(1), "_csrf": csrf(seller)})
run("UPDATE users SET payouts_ready=1 WHERE email='s@student.eur.nl'")
D = (date.today() + timedelta(days=3)).isoformat()
r = seller.post("/sell", data={"venue": "__other", "other_venue": "Het Sieraad", "city": "Rotterdam", "event_date": D, "face_value": "20",
                               "price": "20", "event_name": "Gala", "category": "student", "name_on_ticket": "no", "no_double_sell": "yes",
                               "adult": "yes", "phone": "0611111111", "consent_all": "1", "_csrf": csrf(seller),
                               "ticket": (io.BytesIO(png("T-ALERT-1")), "t.png")}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
pub = r.location.split("/")[2].split("?")[0]
ok(one("SELECT status FROM orders WHERE public_id=?", pub)["status"] == "paid", "a paid order to report on")

# --- The report pushes to both admins, urgently, with no buyer words or details on the lock screen
PUSHED.clear()
secret = "The seller is called Jan and his number is 0699999999."
buyer.post("/orders/%s/report" % pub, data={"kind": "wrong_event", "reason": secret, "_csrf": csrf(buyer)})
ok(one("SELECT status FROM orders WHERE public_id=?", pub)["status"] == "disputed", "the report opened a dispute")
admin_ids = {one("SELECT id FROM users WHERE email=?", e)["id"] for e in config.ADMIN_EMAILS}
alerts = [p for p in PUSHED if p["user_id"] in admin_ids]
ok({p["user_id"] for p in alerts} == admin_ids, "both admins get a phone alert")
ok(all(p["urgent"] for p in alerts), "admin alerts are sent as urgent (delivered at night, even in low-power mode)")
ok(all(p["title"] == "Problem reported: wrong event" and "Gala" in p["body"] for p in alerts), "the alert says what and which event: %r" % (alerts[:1],))
ok(all(p["url"] == "/admin/disputes/%s" % pub or p["url"].endswith(pub) for p in alerts), "tapping it opens the dispute in /admin")
ok(not any("Jan" in p["body"] or "0699" in p["body"] or "b@test.nl" in p["body"] for p in alerts), "the buyer's words and details stay off the lock screen")

# --- A broken push never stops the report
push.send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("push service down"))
app.logger.disabled = True
with app.test_request_context():
    ok(A.push_admins("x", "y", "/admin") == 0, "a failing push is swallowed, the dispute still goes through")
app.logger.disabled = False
push.send = fake_send

# --- /admin shows who has no alerts yet, and the test button
run("DELETE FROM push_subs")
page = html(admin.get("/admin"))
ok('id="alerts"' in page and "Send a test alert" in page and "data-push" in page, "/admin has a Phone alerts section with the on/off button and a test")
ok("No alerts yet for: admin@example.com, ilyas@example.com" in page, "it names the admins without alerts")
ok("Every admin gets phone alerts" in page and "✗</span> Every admin gets phone alerts" in page, "the launch check shows it red")
for uid in admin_ids:
    run("INSERT INTO push_subs (user_id, endpoint, p256dh, auth) VALUES (?, ?, 'k', 'a')", uid, "https://push.example.com/%d" % uid)
page = html(admin.get("/admin"))
ok("Every admin has alerts on." in page and "✓</span> Every admin gets phone alerts" in page, "with both phones on, it turns green")

PUSHED.clear()
r = admin.post("/admin/push-test", data={"_csrf": csrf(admin)}, follow_redirects=True)
ok({p["user_id"] for p in PUSHED} == admin_ids and all(p["urgent"] for p in PUSHED), "the test alert goes to every admin device")
ok("Test alert sent to 2 devices." in html(r), "and says how many got it")
ok(buyer.post("/admin/push-test", data={"_csrf": csrf(buyer)}).status_code in (302, 403, 404), "non-admins can't send it")
ok(len([p for p in PUSHED if p["tag"] == "admin-test"]) == 2, "a non-admin's try sent nothing")

# --- push.send really sets the Urgency header
push.send = real_send
import requests, pywebpush
CAPTURED = []
class Resp: status_code = 201; text = ""; content = b""
pywebpush.requests.post = lambda url, data=None, headers=None, timeout=None, **k: CAPTURED.append(headers) or Resp()
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
push.KEY_PATH = os.path.join(tmp, "vapid.pem")
bk = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
run("DELETE FROM push_subs")
uid = min(admin_ids)
run("INSERT INTO push_subs (user_id, endpoint, p256dh, auth) VALUES (?, 'https://push.example.com/real', ?, ?)", uid, b64(bk), b64(os.urandom(16)))
with app.app_context():
    push.send(uid, "t", "b", urgent=True); push.send(uid, "t", "b")
ok(CAPTURED[0].get("Urgency") == "high", "urgent pushes carry Urgency: high")
ok("Urgency" not in CAPTURED[1], "ordinary pushes don't")

print("\n%d failed" % len(fails) if fails else "\nall passed")
