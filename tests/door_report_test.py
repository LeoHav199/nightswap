import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False; config.ADMIN_EMAILS = {"admin@example.com"}
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **k: False
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
def png(qr=None, colour="white"):
    img = Image.new("RGB", (800, 1000), colour)
    if qr: img.paste(qrcode.make(qr).convert("RGB").resize((400, 400)), (200, 300))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): A.db.run(sql, *a)

# The deadline: 06:00 after the doors, Amsterdam time
with app.app_context():
    day = date.today() + timedelta(days=10)  # any day gives the same answer
    next_morning = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(hour=6)
    l = {"event_id": None, "event_date": day.isoformat(), "start_time": "22:00"}
    until = A.door_report_until(l).replace(tzinfo=A.timezone.utc).astimezone().replace(tzinfo=None)
    ok(until == next_morning, "doors at 22:00: door problems can be reported until 06:00 the next morning")
    l["start_time"] = "01:00"
    until = A.door_report_until(l).replace(tzinfo=A.timezone.utc).astimezone().replace(tzinfo=None)
    ok(until == next_morning, "doors at 01:00 (the same night, after midnight): still until 06:00 the next morning")

seller, buyer, admin = app.test_client(), app.test_client(), app.test_client()
login(seller, "s@student.eur.nl", "Sam"); login(buyer, "b@test.nl", "Bea"); login(admin, "admin@example.com", "Ad")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
def sale(code):
    D = (date.today() + timedelta(days=3)).isoformat()
    r = seller.post("/sell", data={"venue": "__other", "other_venue": "Het Sieraad", "city": "Rotterdam", "event_date": D, "face_value": "20",
                                   "price": "20", "event_name": "Gala", "category": "student", "name_on_ticket": "no", "no_double_sell": "yes",
                                   "adult": "yes", "_csrf": csrf(seller), "ticket": (io.BytesIO(png(code)), "t.png")}, content_type="multipart/form-data")
    lid = int(re.search(r"/t/(\d+)", r.location).group(1))
    r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
    return lid, r.location.split("/")[2].split("?")[0]
def tonight(lid):  # the doors opened an hour ago
    doors = datetime.now() - timedelta(hours=1)
    day = doors.date() - timedelta(days=1) if doors.hour < 6 else doors.date()  # doors before 06:00 belong to the night of the day before
    run("UPDATE listings SET event_date=?, start_time=? WHERE id=?", day.isoformat(), doors.strftime("%H:%M"), lid)
def report(pub, kind, photo=True):
    data = {"kind": kind, "reason": "The scanner said the ticket was already used.", "_csrf": csrf(buyer)}
    if photo: data["photos"] = (io.BytesIO(png(colour="red")), "door.png")
    return html(buyer.post("/orders/%s/report" % pub, data=data, content_type="multipart/form-data", follow_redirects=True))

lid, pub = sale("T-DOOR-1"); tonight(lid)
page = html(buyer.get("/orders/" + pub))
ok("We check with the organiser whether your ticket was scanned" in page, "the report form warns that false reports are checked and blocked")
ok('value="door_rejected"' in page and "required if you were turned away at the door" in page, "on the night: door problems can be chosen, a photo is required")
ok("Add a photo for a problem at the door" in report(pub, "door_rejected", photo=False) and one("SELECT status FROM orders WHERE public_id=?", pub)["status"] == "paid",
   "a door problem without a photo is refused")
report(pub, "door_rejected")
ok(one("SELECT status FROM orders WHERE public_id=?", pub)["status"] == "disputed", "with a photo, on the night: the report goes through")

# After Swap: no door problems
lid2, pub2 = sale("T-DOOR-2"); tonight(lid2)
run("UPDATE orders SET swapped_at=?, rating='up' WHERE public_id=?", A.sqltime(A.utcnow()), pub2)
ok("You slid Swap" in report(pub2, "already_scanned") and one("SELECT status FROM orders WHERE public_id=?", pub2)["status"] == "paid",
   "after Swap a door problem can't be reported")
ok('value="door_rejected"' not in html(buyer.get("/orders/" + pub2)), "after Swap the door options are gone from the form")

# The morning after (06:00 has passed): door problems are closed, other problems aren't
lid3, pub3 = sale("T-DOOR-3")
two_days_ago = date.today() - timedelta(days=2)  # (the night of yesterday would still be on before 06:00 today)
run("UPDATE listings SET event_date=?, start_time='22:00' WHERE id=?", two_days_ago.isoformat(), lid3)
ok("only be reported on the night itself" in report(pub3, "door_rejected"), "after 06:00 the next day a door problem is refused")
ok('value="door_rejected"' not in html(buyer.get("/orders/" + pub3)), "and the door options are gone from the form")
report(pub3, "wrong_event", photo=False)
ok(one("SELECT status FROM orders WHERE public_id=?", pub3)["status"] == "disputed", "other problems can still be reported until the payout")

# The admin asks the organiser, and blocks a buyer whose report was false
dp = html(admin.get("/admin/disputes/" + pub))
ok("Email the organiser: was it scanned?" in dp and "mailto:?subject=" in dp and "T-DOOR-1" in dp.split("mailto:", 1)[1].split('"')[0].replace("%2D", "-"),
   "the dispute page has a ready email to the organiser with the ticket's code")
admin.post("/admin/disputes/%s/keep_block" % pub, data={"why": "The organiser confirmed the ticket was scanned at 23:14.", "_csrf": csrf(admin)})
o = one("SELECT * FROM orders WHERE public_id=?", pub)
ok(o["status"] == "paid" and "23:14" in (o["dispute_decision"] or ""), "false report: the dispute closes and the seller is paid as normal")
ok(one("SELECT blocked_at FROM users WHERE email='b@test.nl'")["blocked_at"] is not None, "and the buyer is blocked")

# The terms say so, in every language
for code, word in (("en", "before 06:00"), ("nl", "vóór 06:00"), ("fr", "avant 6 h"), ("de", "vor 6 Uhr"), ("es", "antes de las 6:00")):
    c = app.test_client(); c.get("/lang/%s?next=/" % code)
    ok(word in html(c.get("/terms")), "terms explain the door rule (%s)" % code)

print("\n%d failed" % len(fails))
