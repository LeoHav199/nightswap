"""Checks for the fixes from the 'Bulletproofing Spare' review (numbers refer to its items)."""
import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # switched on in the #28 and #29 sections only
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; MAIL_WORKS = [False]
notify.send_email = lambda to, subject, body, attachments=(), **kw: (SENT.append((to, subject, body)) or True) if MAIL_WORKS[0] else False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    with app.app_context(): A.db.run("DELETE FROM hits WHERE key LIKE 'login%' OR key LIKE 'verify%'")  # many test sign-ins
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(c)})
class live_test_mode:
    """The real site (not development) with a Stripe test key."""
    def __enter__(self):
        self.saved = (config.DEV, config.STRIPE_SECRET_KEY); config.DEV = False; config.STRIPE_SECRET_KEY = "sk_test_x"
    def __exit__(self, *a):
        config.DEV, config.STRIPE_SECRET_KEY = self.saved

admin, seller, buyer, visitor = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo"); visitor.get("/")
verify_phone(seller, "0611111111"); verify_phone(admin, "0699999999")
D = (date.today() + timedelta(days=3)).isoformat()
admin.post("/admin/events/save", data={"face_value_max": "500", "name": "Guard Night", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Guard Night'")
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(b"%PDF-1.4 guard"), "g.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); pub = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
with app.app_context(): tid = A.db.one("SELECT id FROM tickets WHERE listing_id=?", lid)["id"]

# --- #22: no real tickets while Stripe is in test mode on the real site
with live_test_mode():
    ok("Coming soon to Rotterdam" in html(visitor.get("/")), "#22 visitors see coming-soon while Stripe is in test mode")
    ok(seller.get("/sell").status_code == 302, "#22 non-admins can't open the sell form")
    ok(buyer.get("/orders/%s/tickets/%d" % (pub, tid)).status_code in (302, 404), "#22 non-admins can't download ticket files")
    page = html(admin.get("/"))
    ok("Coming soon to Rotterdam" not in page and "don&#39;t list or buy real tickets" in page, "#22 admins see the site with a red test-mode warning")
    ok(admin.get("/sell").status_code == 200, "#22 admins can still test selling")
ok(buyer.get("/orders/%s/tickets/%d" % (pub, tid)).status_code == 200, "#22 in development nothing is locked")

# --- #1: one address
saved = (config.DEV, config.BASE_URL); config.DEV = False; config.BASE_URL = "https://sparetickets.eu"
c = app.test_client()
r = c.get("/event?venue=Maassilo&date=%s" % D, base_url="https://nightswap.onrender.com")
ok(r.status_code == 301 and r.location == "https://sparetickets.eu/event?venue=Maassilo&date=%s" % D, "#1 the old address redirects to sparetickets.eu, keeping the page")
ok(c.get("/robots.txt", base_url="https://nightswap.onrender.com").status_code == 200, "#1 Render's health check still works on the old address")
ok(c.post("/tasks/hourly", base_url="https://nightswap.onrender.com").status_code == 404, "#1 the cron job isn't redirected (404 = reached the route without its secret)")
ok(c.get("/robots.txt", base_url="https://sparetickets.eu").status_code == 200, "#1 sparetickets.eu itself isn't redirected")
config.DEV, config.BASE_URL = saved

# --- #2, #3: event tickets, not only club tickets; no "students selling to students" promise
import i18n
for lang in i18n.LANGS:
    ok(len(i18n.STRINGS[lang]["meta_description"]) <= 155, "#2 the %s Google description fits in 155 characters" % lang)
home = html(visitor.get("/"))
ok("Resell event tickets in Rotterdam" in home and "concerts and festivals" in home, "#2 the title, description and footer name more than club tickets")
ok("Students selling to students" not in home and "from students, for students" not in home and "Made for students in Rotterdam" in home,
   "#3 the homepage no longer claims every buyer and seller is a student")
nl = app.test_client(); nl.set_cookie("lang", "nl")
ok("Van studenten, voor studenten" not in html(nl.get("/")) and "Gemaakt voor studenten in Rotterdam" in html(nl.get("/")), "#3 the same in Dutch")

# --- #4: one payout rule everywhere, matching rate_order and release_payout
how, terms = html(visitor.get("/how-it-works")), html(visitor.get("/terms"))
ok("If the buyer confirms the ticket worked, sooner" in how and "48 hours after the night" in how and "72 hours" in how, "#4 How it works describes the early and the latest payout")
ok("the payout is brought forward" in terms and "nothing is paid out until the seller has transferred it" in terms, "#4 the terms say the same")
ok("held until after the event" not in how, "#4 no more 'held until after the event'")

# --- #5: the FAQ is up to date and how-it-works exists in every language
ok("Two kinds" in how and "Ticketmaster" in how and "change the price on your listing" in how, "#5 the FAQ explains app tickets and the price-change form")
for lang in ("fr", "de", "es"):
    c = app.test_client(); c.set_cookie("lang", lang); page = html(c.get("/how-it-works"))
    ok(("<h2>" + {"fr": "Vendre", "de": "Verkaufen", "es": "Vender"}[lang] + "</h2>") in page and "Buying" not in page, "#5 How it works is translated into %s" % lang)

# --- #6: no "verified phone" claim while codes go by email
ok("gave us a phone number" in how and "verified phone number" not in how, "#6 without SMS, the page doesn't claim phones are verified")
config.SMS_ENABLED = True
ok("Every seller has a verified phone number" in html(visitor.get("/how-it-works")), "#6 with real SMS on, it does")
config.SMS_ENABLED = False

# --- #7: the duplicate check is described as what it is
ok("checked against all tickets on Spare" in how and "can only be listed once" not in how, "#7 the duplicate promise says what's checked")

# --- #9: browser security headers
r = visitor.get("/")
csp = r.headers.get("Content-Security-Policy", "")
ok("script-src 'self'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0] and "frame-ancestors 'none'" in csp,
   "#9 a Content-Security-Policy allows only our own scripts")
ok("https://*.stripe.com" in csp.split("form-action")[1], "#9 forms may still lead on to Stripe Checkout and payout setup")
ok("camera=()" in r.headers.get("Permissions-Policy", ""), "#9 a Permissions-Policy switches off camera, microphone, location and payment")
ok("Strict-Transport-Security" not in r.headers, "#9 no HSTS in development")
dev_was = config.DEV; config.DEV = False
ok("max-age=31536000" in app.test_client().get("/robots.txt").headers.get("Strict-Transport-Security", ""), "#9 HSTS on the real site")
config.DEV = dev_was
import glob
inline = [f for f in glob.glob("templates/**/*.html", recursive=True)
          if re.search(r"<script(?![^>]*\b(src=|type=\"application/ld\+json\"))", open(f, encoding="utf-8").read())
          or re.search(r"\son[a-z]+=\"", open(f, encoding="utf-8").read())]
ok(not inline, "#9 no template has inline scripts or onclick-style handlers (they'd be blocked): %s" % inline)

# --- #10: a forged X-Forwarded-For can't dodge the rate limits
from werkzeug.middleware.proxy_fix import ProxyFix
plain_wsgi = app.wsgi_app; config.TRUST_PROXY = True
app.wsgi_app = ProxyFix(plain_wsgi, x_for=1, x_proto=1, x_host=1)
c = app.test_client(); c.get("/")
limited = False
for i in range(12):
    # The visitor makes up a new address each time; Render's proxy (10.0.0.5) appends the real one.
    r = c.post("/login", data={"email": "spam%d@test.nl" % i, "_csrf": csrf(c)},
               headers={"X-Forwarded-For": "1.2.3.%d, 81.204.12.7" % i}, environ_base={"REMOTE_ADDR": "10.0.0.5"})
    limited = limited or "Too many" in html(r)
ok(limited, "#10 a new fake X-Forwarded-For on every request still hits the sign-in limit")
with app.test_request_context("/", headers={"X-Forwarded-For": "6.6.6.6, 84.30.5.9, 10.1.1.1"}, environ_base={"REMOTE_ADDR": "10.0.0.5"}):
    from flask import request as rq
    rq.environ["REMOTE_ADDR"] = "10.1.1.1"  # what ProxyFix gives when there's one proxy more than TRUST_PROXY_HOPS
    ok(A.client_ip() == "84.30.5.9", "#10 with one proxy too many, it falls back to the visitor's address, not the proxy's or a fake one")
app.wsgi_app = plain_wsgi; config.TRUST_PROXY = False
ok("Network check" in html(admin.get("/admin")), "#10 /admin shows which IP Spare sees, to check the proxy setting")

# --- #11: 90-day sign-ins that can be ended everywhere
ok(app.permanent_session_lifetime.days == 90, "#11 sign-ins last 90 days (renewed on every visit)")
phone1, laptop = app.test_client(), app.test_client()
login(phone1, "two@test.nl", "Tw"); login(laptop, "two@test.nl", "Tw")
ok(laptop.get("/account").status_code == 200, "#11 signed in on two devices")
ok("Sign out everywhere" in html(phone1.get("/settings")), "#11 Settings has a 'Sign out everywhere' button")
phone1.post("/account/sign-out-everywhere", data={"_csrf": csrf(phone1)})
ok(laptop.get("/account").status_code == 302 and phone1.get("/account").status_code == 302, "#11 'Sign out everywhere' ends the session on every device")
login(laptop, "two@test.nl", "Tw")
ok(laptop.get("/account").status_code == 200, "#11 signing in again works")
c = app.test_client(); c.get("/?from=flyer1")
with c.session_transaction() as sess: sess["phone_pending"] = "0600000000"; sess["admin_ok_until"] = 9e12
login(c, "fresh@test.nl", "Fr")
with c.session_transaction() as sess: carried = [k for k in ("phone_pending", "admin_ok_until") if k in sess]
ok(not carried, "#11 signing in starts a clean session (nothing from before carries over): %s" % carried)
c.post("/logout", data={"_csrf": csrf(c)})
with c.session_transaction() as sess: ok("uid" not in sess, "#11 logging out clears the session")
with app.app_context(): blocked_uid = A.db.one("SELECT id FROM users WHERE email='two@test.nl'")["id"]
with app.test_request_context(): A.block_user(blocked_uid, "test")
ok(laptop.get("/account").status_code == 302, "#11 blocking someone signs them out everywhere")

# --- #12: sellers hear about complaints; blocks and removals come with reasons
MAIL_WORKS[0] = True; SENT.clear()
# The Guard Night ticket is already sold: list another one to dispute
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(b"%PDF-1.4 guard two"), "g2.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid2 = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid2, data={"_csrf": csrf(buyer)}); pub2 = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub2, data={"_csrf": csrf(buyer)}); SENT.clear()
buyer.post("/orders/%s/report" % pub2, data={"reason": "The QR code was already scanned at the door", "_csrf": csrf(buyer)})
mail = [b for to, sub, b in SENT if to == "s@test.nl" and "Problem reported" in sub]
ok(mail and "within 24 hours" in mail[0] and "/orders/%s" % pub2 in mail[0], "#12 the seller gets an email about the problem, with 24 hours to reply in the chat")
with app.app_context(): ok(A.db.one("SELECT 1 FROM messages WHERE kind='system' AND body='sys_disputed'") is not None, "#12 the order chat notes the report")
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(b"%PDF-1.4 guard three"), "g3.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid3 = int(re.search(r"/t/(\d+)", r.location).group(1))
buyer.post("/t/%d/report" % lid3, data={"reason": "fake", "details": "looks fake", "_csrf": csrf(buyer)})
with app.app_context(): rep_id = A.db.one("SELECT id FROM reports WHERE listing_id=?", lid3)["id"]
admin.post("/admin/reports/%d/remove" % rep_id, data={"why": "", "_csrf": csrf(admin)})
with app.app_context(): ok(A.db.one("SELECT status FROM listings WHERE id=?", lid3)["status"] == "active", "#12 removing a listing needs a reason")
SENT.clear()
admin.post("/admin/reports/%d/remove" % rep_id, data={"why": "The ticket file was edited.", "_csrf": csrf(admin)})
mail = [b for to, sub, b in SENT if to == "s@test.nl"]
ok(mail and "Why: The ticket file was edited." in mail[0] and "within 6 months" in mail[0] and "b@test.nl" not in mail[0] and "Bo" not in mail[0],
   "#12 the seller gets a statement of reasons with how to object, never who reported them")
with app.app_context(): uid = A.db.one("SELECT id FROM users WHERE email='fresh@test.nl'")["id"]
admin.post("/admin/users/%d/block" % uid, data={"reason": "", "_csrf": csrf(admin)})
with app.app_context(): ok(A.db.one("SELECT blocked_at FROM users WHERE id=?", uid)["blocked_at"] is None, "#12 blocking needs a reason")
SENT.clear()
admin.post("/admin/users/%d/block" % uid, data={"reason": "Tried to sell outside Spare.", "_csrf": csrf(admin)})
ok(any(to == "fresh@test.nl" and "we blocked your account" in b and "Tried to sell outside Spare." in b for to, sub, b in SENT), "#12 a blocked user is told why and how to object")
MAIL_WORKS[0] = False

# --- #13: the buyer's ticket page works without signal at the door
r = buyer.get("/orders/%s" % pub)
ok(r.headers.get("X-Spare-Offline") == (date.fromisoformat(D) + timedelta(days=3)).isoformat() and "data-offline-note" in html(r),
   "#13 the buyer's paid order page is marked for offline use, until 3 days after the event")
ok("X-Spare-Offline" not in seller.get("/orders/%s" % pub).headers, "#13 the seller's view of the order isn't kept offline")
with live_test_mode():
    ok("X-Spare-Offline" not in buyer.get("/orders/%s" % pub).headers, "#13 nothing is kept offline while the real site is test-locked")
sw = open("static/js/sw.js", encoding="utf-8").read()
ok("spare-orders" in sw and '"/logout"' in sw and "X-Spare-Offline" in sw, "#13 the service worker stores marked order pages and forgets them on sign-out")
ok("never push" in open("CLAUDE.md", encoding="utf-8").read().lower(), "#13 CLAUDE.md has the no-deploys-on-event-nights rule")

# --- #14: no decompression bombs
import struct, zlib, ticketcodes, cv2, numpy as np
def png_claiming(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    chunk = lambda kind, body: struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\0" * 64)) + chunk(b"IEND", b"")
bomb = png_claiming(50000, 50000)
ok(len(bomb) < 200 and ticketcodes.check(bomb, "png") == "too_big", "#14 a tiny PNG claiming 50,000 x 50,000 pixels is refused before decoding")
ok(ticketcodes.codes(bomb, "png") == [], "#14 ...and codes() never decodes it")
small = cv2.imencode(".png", np.full((800, 400), 255, np.uint8))[1].tobytes()
ok(ticketcodes.image_size(small, "png") == (400, 800) and ticketcodes.check(small, "png") is None, "#14 a normal screenshot passes")
jpg = cv2.imencode(".jpg", np.full((300, 200), 255, np.uint8))[1].tobytes()
ok(ticketcodes.image_size(jpg, "jpg") == (200, 300), "#14 JPEG sizes are read from the header")
huge_jpg = bytearray(jpg); i = huge_jpg.find(b"\xff\xc0"); huge_jpg[i + 5:i + 9] = struct.pack(">HH", 60000, 60000)
ok(ticketcodes.check(bytes(huge_jpg), "jpg") == "too_big", "#14 a JPEG claiming 60,000 x 60,000 is refused")
import pypdfium2 as pdfium
def pdf_with(pages, w=595, h=842):
    doc = pdfium.PdfDocument.new()
    for _ in range(pages): doc.new_page(w, h)
    buf = io.BytesIO(); doc.save(buf); doc.close(); return buf.getvalue()
ok(ticketcodes.check(pdf_with(11), "pdf") == "too_many_pages" and ticketcodes.check(pdf_with(2), "pdf") is None, "#14 PDFs with more than 10 pages are refused")
sizes = [img.shape for img in ticketcodes._images(pdf_with(1, 14400, 14400), "pdf")]
ok(sizes and sizes[0][0] * sizes[0][1] <= ticketcodes.MAX_PIXELS, "#14 a giant PDF page is rendered at a lower scale, never above 25 million pixels")
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(bomb), "bomb.png"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
ok(r.status_code == 200 and "too large to check" in html(r), "#14 the seller sees a clear error")

# --- #15: the disk doesn't fill up
import backup
with app.app_context():
    old = (date.today() - timedelta(days=20)).isoformat()
    t1 = A.db.one("SELECT * FROM tickets WHERE listing_id=?", lid)  # sold in the first purchase above
    A.db.run("UPDATE listings SET event_date=? WHERE id IN (?, ?)", old, lid, lid2)
    A.db.run("UPDATE orders SET status='released' WHERE public_id=?", pub)   # paid out
    # lid2's order is disputed (reported above): it must stay
    path1 = os.path.join(config.UPLOAD_DIR, t1["file"])
    t2 = A.db.one("SELECT * FROM tickets WHERE listing_id=?", lid2)
ok(os.path.exists(path1), "#15 (setup) the sold ticket's file is on disk")
with app.test_request_context(): n = A.cleanup_ticket_files()
with app.app_context():
    after1 = A.db.one("SELECT * FROM tickets WHERE id=?", t1["id"]); after2 = A.db.one("SELECT * FROM tickets WHERE id=?", t2["id"])
ok(n >= 1 and not os.path.exists(path1) and after1["file"] == "" and after1["file_hash"] == t1["file_hash"],
   "#15 14 days after the event, a paid-out ticket's file is deleted but its hash is kept")
ok(after2["file"] == t2["file"] and os.path.exists(os.path.join(config.UPLOAD_DIR, t2["file"])), "#15 a ticket with an open dispute keeps its file")
r = buyer.get("/orders/%s/tickets/%d" % (pub, t1["id"]))
ok(r.status_code == 302 and "deleted 14 days after the event" in html(buyer.get("/orders/%s" % pub)), "#15 the order page explains the file is gone, politely")
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(b"%PDF-1.4 guard"), "again.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
ok(r.status_code == 200, "#15 the duplicate check still recognises the deleted ticket")
ok(backup.KEEP == 3, "#15 3 backup copies on the disk")
config.DATA_DIR = tmp
with app.app_context(): data, name, _ = backup.for_email()
import zipfile
on_disk = sorted(os.listdir(os.path.join(tmp, "backups")))
ok(on_disk and not any(n.startswith("uploads/") for n in zipfile.ZipFile(os.path.join(tmp, "backups", on_disk[-1])).namelist()),
   "#15 the copies on the disk leave the uploads out")
real_usage = A.shutil.disk_usage
A.shutil.disk_usage = lambda p: type("U", (), {"used": 75, "total": 100, "free": 25})()
MAIL_WORKS[0] = True; SENT.clear()
with app.test_request_context():
    first, second = A.check_disk(), A.check_disk()
ok(first and not second and any("75% full" in sub for to, sub, b in SENT), "#15 the admins get one email a day when the disk passes 70%")
ok("75% full" in html(admin.get("/admin")), "#15 /admin shows the disk usage")
A.shutil.disk_usage = real_usage; MAIL_WORKS[0] = False

# --- #16: encrypted backups
dev_was = config.DEV; config.DEV = False; config.BACKUP_KEY = ""
try:
    backup.seal(b"zip", "x.zip"); ok(False, "#16 the real site refuses unencrypted backups")
except backup.NoKey:
    ok(True, "#16 the real site refuses unencrypted backups")
MAIL_WORKS[0] = True; SENT.clear()
with app.test_request_context(): made = A.nightly_backup(force=True)
ok(not made and any("backup NOT made" in sub for to, sub, b in SENT) and not any(sub.startswith("Spare backup 2") for to, sub, b in SENT),
   "#16 without BACKUP_KEY the nightly backup isn't sent; the admins are told instead")
key = backup.new_key(); config.BACKUP_KEY = key; SENT.clear()
sent_files = []
real_send = notify.send_email
notify.send_email = lambda to, subject, body, attachments=(), **kw: sent_files.extend(attachments) or True
with app.test_request_context(): made = A.nightly_backup(force=True)
notify.send_email = real_send
ok(made and sent_files and sent_files[0][0].endswith(".zip.enc") and not sent_files[0][1].startswith(b"PK"),
   "#16 with BACKUP_KEY the emailed backup is encrypted (.zip.enc, not a readable zip)")
ok(backup.unseal(sent_files[0][1], key).startswith(b"PK"), "#16 the key turns it back into the zip")
ok(max(os.listdir(os.path.join(tmp, "backups")), key=lambda n: os.path.getmtime(os.path.join(tmp, "backups", n))).endswith(".enc"), "#16 the copies on the disk are encrypted too")
r = admin.get("/admin/backup")
ok(r.status_code == 200 and ".zip.enc" in r.headers["Content-Disposition"], "#16 'Download backup' gives the encrypted file")
enc_path = os.path.join(tmp, sent_files[0][0]); open(enc_path, "wb").write(sent_files[0][1])
res = app.test_cli_runner().invoke(args=["decrypt-backup", enc_path, "--key", key])
ok("Saved" in res.output and open(enc_path[:-4], "rb").read().startswith(b"PK"), "#16 'flask --app app decrypt-backup <file>' writes the plain zip")
ok(len(app.test_cli_runner().invoke(args=["backup-key"]).output.strip()) == 44, "#16 'flask --app app backup-key' prints a new key")
config.DEV = dev_was; config.BACKUP_KEY = ""; MAIL_WORKS[0] = False

# --- #17: replies reach hello@, and the launch email has a one-click unsubscribe
import importlib.util
spec = importlib.util.spec_from_file_location("notify_real", "notify.py"); real_notify = importlib.util.module_from_spec(spec); spec.loader.exec_module(real_notify)
outbox = []
class FakeSMTP:
    def __init__(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def starttls(self): pass
    def login(self, *a): pass
    def send_message(self, msg): outbox.append(msg)
real_notify.smtplib.SMTP = FakeSMTP; host_was = config.SMTP_HOST; config.SMTP_HOST = "smtp.example"
with app.app_context(): real_notify.send_email("x@test.nl", "Hi", "Body")
config.SMTP_HOST = host_was
ok(outbox and outbox[0]["Reply-To"] == config.SUPPORT_EMAIL, "#17 every email has Reply-To: hello@sparetickets.eu")
w = app.test_client(); w.get("/"); w.post("/waitlist", data={"email": "wait@test.nl", "_csrf": csrf(w)})
headers_seen = []
notify.send_email = lambda to, subject, body, attachments=(), headers=None, **kw: headers_seen.append((to, headers)) or True
admin.post("/admin/waitlist/notify", data={"_csrf": csrf(admin)})
notify.send_email = lambda to, subject, body, attachments=(), **kw: (SENT.append((to, subject, body)) or True) if MAIL_WORKS[0] else False
h = dict(headers_seen).get("wait@test.nl") or {}
ok("/waitlist/leave/" in h.get("List-Unsubscribe", "") and h.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click",
   "#17 the launch email has List-Unsubscribe headers")
leave_path = re.search(r"<https?://[^/]+(/waitlist/leave/[^>]+)>", h["List-Unsubscribe"]).group(1)
r = app.test_client().post(leave_path, data={"List-Unsubscribe": "One-Click"})
with app.app_context(): gone = A.db.one("SELECT 1 FROM waitlist WHERE email='wait@test.nl'") is None
ok(r.status_code == 200 and gone, "#17 a mail app's one-click unsubscribe (POST, no CSRF token) removes the address")

# --- #18: deleting an account leaves nothing personal behind
dele = app.test_client(); dele.get("/?from=flyer1"); login(dele, "gone@test.nl", "Gwen"); verify_phone(dele, "0655555555")
with app.app_context(): A.db.run("UPDATE users SET avatar='cat' WHERE email='gone@test.nl'")
dele.post("/waitlist", data={"email": "gone@test.nl", "_csrf": csrf(dele)})
r = dele.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                             "ticket": (io.BytesIO(b"%PDF-1.4 Gwen Vermeer ticket"), "gwen.pdf"), "_csrf": csrf(dele)}, content_type="multipart/form-data")
glid = int(re.search(r"/t/(\d+)", r.location).group(1))
buyer.post("/t/%d/ask" % glid, data={"question": "q_name", "_csrf": csrf(buyer)})
with app.app_context(): gtid = A.db.one("SELECT id FROM threads WHERE listing_id=?", glid)["id"]
dele.post("/threads/%d/send" % gtid, data={"body": "Hi from Gwen, see you at Maassilo", "_csrf": csrf(dele)})
dele.post("/threads/%d/send" % gtid, data={"body": "pay me on tikkie 0655555555", "_csrf": csrf(dele)})
r = buyer.post("/t/%d/buy" % glid, data={"_csrf": csrf(buyer)}); gpub = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % gpub, data={"_csrf": csrf(buyer)})
with app.app_context():
    A.db.run("UPDATE orders SET status='released' WHERE public_id=?", gpub)
    gfile = A.db.one("SELECT file FROM tickets WHERE listing_id=?", glid)["file"]
# Gwen also bought an app ticket, giving her ticket-app name and email
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes", "delivery": "transfer",
                               "transfer_app": "ticketmaster", "transfer_qty": "1", "_csrf": csrf(seller)})
alid = int(re.search(r"/t/(\d+)", r.location).group(1))
r = dele.post("/t/%d/buy" % alid, data={"qty": "1", "transfer_name": "Gwen Vermeer", "transfer_email": "gwen.tm@example.com", "_csrf": csrf(dele)})
apub = r.location.split("/")[2]; dele.post("/checkout/%s/test" % apub, data={"_csrf": csrf(dele)})
with app.app_context(): A.db.run("UPDATE orders SET status='released' WHERE public_id=?", apub)
r = dele.post("/account/delete", data={"confirm": "gone@test.nl", "_csrf": csrf(dele)})
import sqlite3; dump = "\n".join(sqlite3.connect(config.DATABASE).iterdump())
leftovers = [w for w in ("gone@test.nl", "Gwen", "0655555555", "655555555", "see you at Maassilo", "tikkie", "gwen.tm@example.com", "Vermeer") if w in dump]
ok(not leftovers, "#18 after deleting the account, nothing personal is left in the database: %s" % leftovers)
ok(not os.path.exists(os.path.join(config.UPLOAD_DIR, gfile)), "#18 the ticket file they uploaded (with their name) is deleted")
with app.app_context():
    o = A.db.one("SELECT * FROM orders WHERE public_id=?", gpub)
ok(o and o["price_cents"] == 2000 and o["paid_at"], "#18 the order's amounts, dates and number are kept for the bookkeeping")
ok("the sender deleted their account" in html(buyer.get("/threads/%d" % gtid)), "#18 the other person sees 'message removed'")

# --- #19: the privacy policy covers what the law asks, matching the code
priv = html(visitor.get("/privacy"))
for words, what in (("[KVK NUMBER]", "who's responsible (placeholder until the KvK details are set)"), ("art. 6(1)(b)", "the legal basis for each use"),
                    ("Stripe", "Stripe"), ("Frankfurt", "Render and where it runs"), ("Brevo", "the email provider"), ("cron-job.org", "cron-job.org"),
                    ("7 years", "the 7-year bookkeeping rule"), ("14 days after the event", "when ticket files are deleted"),
                    ("encrypted backup", "backups"), ("Autoriteit Persoonsgegevens", "the right to complain to the AP")):
    ok(words in priv, "#19 the privacy policy covers %s" % what)
ok("Twilio" not in priv, "#19 Twilio is only listed when SMS is on")
config.SMS_ENABLED = True; ok("Twilio" in html(visitor.get("/privacy")), "#19 ...and listed when it is"); config.SMS_ENABLED = False
config.KVK_NUMBER, config.COMPANY_NAME = "12345678", "Spare Tickets"
ok("KvK 12345678" in html(visitor.get("/privacy")) and "Spare Tickets" in html(visitor.get("/privacy")), "#19 once set on Render, the real company details show")
config.KVK_NUMBER = config.COMPANY_NAME = ""
ok("Autoriteit Persoonsgegevens" in html(nl.get("/privacy")) and "Grond:" in html(nl.get("/privacy")), "#19 the Dutch version too")

# --- #20: the terms cover the business basics; company line in footer and emails
terms = html(visitor.get("/terms"))
for words, what in (("[KVK NUMBER]", "who runs Spare"), ("private individuals", "that sellers are private individuals"), ("18 or older", "the 18+ rule"),
                    ("Cancelled or moved events", "cancelled and moved events"), ("A person at Spare looks at what both sides send", "how disputes are decided"),
                    ("within 6 months", "how to object to a removal"), ("Our liability is limited", "liability"), ("No right of withdrawal", "the right of withdrawal"),
                    ("Dutch law applies", "governing law"), ("Complaints about Spare", "complaints")):
    ok(words in terms, "#20 the terms cover %s" % what)
ok("Geen herroepingsrecht" in html(nl.get("/terms")), "#20 the Dutch terms too")
ok("company-line" not in html(visitor.get("/")), "#20 no company line in the footer until the KvK details are set")
config.COMPANY_NAME, config.COMPANY_ADDRESS, config.KVK_NUMBER = "Spare Tickets", "Coolsingel 1, Rotterdam", "12345678"
ok("KvK 12345678" in html(visitor.get("/")), "#20 then the footer shows name, address and KvK number")
with app.app_context(): ok("KvK 12345678" in real_notify.email_html("Hi", "Body"), "#20 ...and so does every email")
config.COMPANY_NAME = config.COMPANY_ADDRESS = config.KVK_NUMBER = ""
ok(os.path.exists("docs/legal-review.md"), "#20 the points for a lawyer are listed in docs/legal-review.md")

# --- #5 (rest): terms and privacy in French, German and Spanish
for lang, terms_word, priv_word in (("fr", "Pas de droit de rétractation", "Autoriteit Persoonsgegevens"),
                                     ("de", "Kein Widerrufsrecht", "Datenschutzbehörde"), ("es", "Sin derecho de desistimiento", "Copias de seguridad")):
    c = app.test_client(); c.set_cookie("lang", lang)
    tp, pp = html(c.get("/terms")), html(c.get("/privacy"))
    ok(terms_word in tp and "No right of withdrawal" not in tp and priv_word in pp and "What we collect" not in pp,
       "#5 terms and privacy are translated into %s" % lang)

# --- #21: not looking official, and names on tickets
with app.app_context(): A.db.run("UPDATE events SET ticket_url='https://shop.example.com/guard' WHERE id=?", ev["id"])
ep = html(visitor.get("/event?venue=Maassilo&date=%s&name=Guard+Night" % D))
ok("connected to this venue or organiser" in ep and "https://shop.example.com/guard" in ep, "#21 the event page says Spare isn't the venue's, linking the official shop")
sp = html(seller.get("/sell?event=%d" % ev["id"]))
ok("Is there a name on this ticket?" in sp and 'name="name_on_ticket" value="fixed"' in sp, "#21 the sell form asks whether there's a name on the ticket")
base = {"no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes", "delivery": "transfer", "transfer_app": "ticketmaster", "transfer_qty": "1"}
r = seller.post("/sell", data=dict(base, _csrf=csrf(seller)))
ok(r.status_code == 200 and "Tell buyers whether there" in html(r), "#21 the answer is required")
r = seller.post("/sell", data=dict(base, name_on_ticket="fixed", _csrf=csrf(seller)))
ok(r.status_code == 200 and "so it can&#39;t be sold on Spare" in html(r), "#21 a name that can't be changed can't be listed")
r = seller.post("/sell", data=dict(base, name_on_ticket="changeable", _csrf=csrf(seller)))
nlid = int(re.search(r"/t/(\d+)", r.location).group(1))
lp = html(buyer.get("/t/%d" % nlid))
ok("the organiser allows name changes, says the seller" in lp, "#21 buyers see the seller's answer")
ok("connected to this venue" in lp and "Sold by a private person" in lp, "#21 the listing page has the not-affiliated and private-seller lines")
with app.app_context(): ok(A.db.one("SELECT name_on_ticket FROM listings WHERE id=?", nlid)["name_on_ticket"] == "changeable", "#21 the answer is stored on the listing")

# --- #23: an authenticator app for admins, not an email code
import totp, base64, json as _json
ok(totp.code_at(base64.b32encode(b"12345678901234567890").decode(), 1) == "287082", "#23 TOTP matches the RFC 6238 test vector")
config.ADMIN_SECOND_FACTOR = True
boss = app.test_client(); login(boss, "admin@example.com", "Ad")
r = boss.get("/admin")
ok(r.status_code == 302 and "/admin/verify" in r.location, "#23 /admin asks for the extra check")
r = boss.get("/admin/verify")
ok(r.status_code == 302 and "/admin/authenticator" in r.location, "#23 without an authenticator, admins are sent to set one up (no email fallback)")
MAIL_WORKS[0] = True; SENT.clear()
page = html(boss.get("/admin/authenticator"))
secret = re.search(r'class="totp-secret">([A-Z2-7]+)<', page).group(1)
ok("<svg" in page and "otpauth" not in page, "#23 the setup page shows a QR code to scan")
r = boss.post("/admin/authenticator", data={"code": totp.code_at(secret, totp.current_step()), "_csrf": csrf(boss)})
codes = re.findall(r"<code>([a-z2-9]{4}-[a-z2-9]{4})</code>", html(r))
ok(len(codes) == 8, "#23 linking the app shows 8 one-time recovery codes")
ok(any("authenticator set up" in sub for to, sub, b in SENT), "#23 the admins get an email when an authenticator is linked")
with app.app_context(): u = A.db.one("SELECT * FROM users WHERE email='admin@example.com'")
ok(secret not in (u["totp_secret"] or "") and not any(c in (u["totp_recovery"] or "") for c in codes), "#23 the secret is stored encrypted and recovery codes only as hashes")
ok(boss.get("/admin").status_code == 200, "#23 ...and the admin area opens")
def expire(c):
    with c.session_transaction() as sess: sess["admin_ok_until"] = 0
    return c
boss2 = expire(boss)
used = totp.code_at(secret, totp.current_step())
boss2.post("/admin/verify", data={"code": used, "next": "/admin", "_csrf": csrf(boss2)})
ok(boss2.get("/admin").status_code == 302, "#23 a code that was already used is refused")
boss2.post("/admin/verify", data={"code": totp.code_at(secret, totp.current_step() + 1), "next": "/admin", "_csrf": csrf(boss2)})
ok(boss2.get("/admin").status_code == 200, "#23 the next code works (one step of clock drift allowed)")
boss3 = expire(boss)
boss3.post("/admin/verify", data={"code": codes[0], "next": "/admin", "_csrf": csrf(boss3)})
ok(boss3.get("/admin").status_code == 200, "#23 a recovery code opens the admin area")
boss4 = expire(boss)
boss4.post("/admin/verify", data={"code": codes[0], "next": "/admin", "_csrf": csrf(boss4)})
ok(boss4.get("/admin").status_code == 302, "#23 ...but only once")
ok(boss4.get("/admin/authenticator").status_code == 302, "#23 replacing the authenticator needs a verified admin session")
res = app.test_cli_runner().invoke(args=["reset-admin-2fa", "admin@example.com"])
with app.app_context(): ok("Reset" in res.output and A.db.one("SELECT totp_secret FROM users WHERE email='admin@example.com'")["totp_secret"] is None,
                           "#23 'flask --app app reset-admin-2fa <email>' unlinks it if everything is lost")
config.ADMIN_SECOND_FACTOR = False; MAIL_WORKS[0] = False
login(admin, "admin@example.com", "Ad")  # the reset above signed this admin out everywhere

# --- #24: real phone checks, one number per account, blocked numbers stay out
other = app.test_client(); login(other, "other@test.nl", "Ot")
r = other.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(other)}, follow_redirects=True)
ok("already verified on another account" in html(r), "#24 a number verified on another account is refused")
with app.app_context(): sam_id = A.db.one("SELECT id FROM users WHERE email='s@test.nl'")["id"]
sneaky = app.test_client(); login(sneaky, "sneaky@test.nl", "Sn")
r = sneaky.post("/account/phone", data={"phone": "0622222222", "_csrf": csrf(sneaky)}, follow_redirects=True)
code = re.search(r"code is (\d{6})", html(r)).group(1)
with app.app_context(): A.db.run("INSERT INTO blocked_phones (phone, user_id) VALUES (?, 0)", "+31622222222")
r = sneaky.post("/account/phone/verify", data={"code": code, "_csrf": csrf(sneaky)})
with app.app_context(): ok(A.db.one("SELECT phone_verified_at FROM users WHERE email='sneaky@test.nl'")["phone_verified_at"] is None,
                           "#24 a blocked user's number can't be verified again (checked when the code is entered too)")
with app.app_context(): A.db.run("DELETE FROM blocked_phones")
with app.test_request_context(): A.block_user(sam_id, "test")
with app.app_context(): ok(A.db.one("SELECT 1 FROM blocked_phones WHERE phone='+31611111111'") is not None, "#24 blocking a user blocks their verified number")
ok("+31611111111" in html(admin.get("/admin/users")), "#24 /admin/users lists blocked numbers")
admin.post("/admin/blocked-phones/remove", data={"phone": "+31611111111", "_csrf": csrf(admin)})
with app.app_context():
    ok(A.db.one("SELECT 1 FROM blocked_phones WHERE phone='+31611111111'") is None, "#24 ...and an admin can remove one")
    A.db.run("UPDATE users SET blocked_at=NULL WHERE id=?", sam_id)
fresh_phone = app.test_client(); login(fresh_phone, "nosms@test.nl", "No")
dev_was, key_was = config.DEV, config.STRIPE_SECRET_KEY
config.DEV, config.STRIPE_SECRET_KEY = False, "sk_live_x"  # the real site with live payments (no test-mode lock)
page = html(fresh_phone.get("/account/phone"))
r = fresh_phone.post("/account/phone", data={"phone": "0633333333", "_csrf": csrf(fresh_phone)})
config.DEV, config.STRIPE_SECRET_KEY = dev_was, key_was
ok("Selling opens soon" in page and 'name="phone"' not in page and r.status_code == 200, "#24 on the real site without SMS, numbers can't be verified (no email fallback)")
# A seller paid to the same bank account as a blocked user
import payments
fp_of = {"acct_blocked": "fp_same", "acct_new": "fp_same"}
real_fp, real_live, real_transfer = payments.bank_fingerprint, payments.live, payments.transfer_to_seller
payments.bank_fingerprint = lambda acct: fp_of.get(acct); payments.live = lambda: True
payments.transfer_to_seller = lambda order, seller: "tr_x"
MAIL_WORKS[0] = True; SENT.clear()
with app.app_context():
    A.db.run("UPDATE users SET stripe_account_id='acct_blocked', bank_fingerprint='fp_same', blocked_at='2026-01-01' WHERE email='sneaky@test.nl'")
    A.db.run("UPDATE users SET stripe_account_id='acct_new', payouts_ready=1, bank_fingerprint=NULL WHERE email='other@test.nl'")
    newbie = A.db.one("SELECT * FROM users WHERE email='other@test.nl'")
with app.test_request_context(): held = A.bank_matches_blocked(newbie)
ok(held and any("payouts held" in sub for to, sub, b in SENT), "#24 a payout to a blocked user's bank account is held and the admins are told")
with app.app_context(): newbie = A.db.one("SELECT * FROM users WHERE email='other@test.nl'")
SENT.clear()
with app.test_request_context(): ok(A.bank_matches_blocked(newbie) and not SENT, "#24 ...and it stays held without repeat emails")
admin.post("/admin/users/%d/clear_bank_flag" % newbie["id"], data={"_csrf": csrf(admin)})
with app.app_context(): ok(A.db.one("SELECT bank_flagged_at FROM users WHERE id=?", newbie["id"])["bank_flagged_at"] is None, "#24 an admin can clear the hold")
payments.bank_fingerprint, payments.live, payments.transfer_to_seller = real_fp, real_live, real_transfer; MAIL_WORKS[0] = False

# --- #25: a face value we can check
def real_png(text=b""):
    return cv2.imencode(".png", np.full((600, 300), 255, np.uint8))[1].tobytes() + text
admin.post("/admin/events/save", data={"name": "Priced Night", "venue": "Perron", "category": "club", "event_date": D,
                                       "face_value_max": "25", "_csrf": csrf(admin)})
admin.post("/admin/events/save", data={"name": "Unpriced Night", "venue": "Perron", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context():
    priced = A.db.one("SELECT * FROM events WHERE name='Priced Night'"); unpriced = A.db.one("SELECT * FROM events WHERE name='Unpriced Night'")
ok(priced["face_value_max_cents"] == 2500 and unpriced["face_value_max_cents"] is None, "#25 admins can store the official price per event")
seller3 = app.test_client(); login(seller3, "s3@test.nl", "Sy"); verify_phone(seller3, "0644444444")
def sell3(event, face, extra=None):
    data = {"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(event["id"]), "face_value": face, "price": face, "adult": "yes",
            "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(8)), "t.pdf"), "_csrf": csrf(seller3)}
    data.update(extra or {})
    return seller3.post("/sell", data=data, content_type="multipart/form-data")
r = sell3(priced, "40")
ok(r.status_code == 200 and "official price for this event is €25" in html(r), "#25 a face value above the official price is refused")
r = sell3(priced, "25")
plid = int(re.search(r"/t/(\d+)", r.location).group(1))
with app.app_context(): pl = A.db.one("SELECT * FROM listings WHERE id=?", plid)
ok(pl["status"] == "active" and pl["face_checked"] == 1 and "Face value checked" in html(buyer.get("/t/%d" % plid)),
   "#25 within the official price it goes live with 'Face value checked'")
r = sell3(unpriced, "30")
ok(r.status_code == 200 and "original order confirmation" in html(r), "#25 without an official price, proof of the price is required")
MAIL_WORKS[0] = True; SENT.clear()
r = sell3(unpriced, "30", {"face_proof": (io.BytesIO(real_png()), "order.png")})
ulid = int(re.search(r"/t/(\d+)", r.location).group(1))
with app.app_context():
    ul = A.db.one("SELECT * FROM listings WHERE id=?", ulid); proof = A.db.one("SELECT * FROM proofs WHERE listing_id=?", ulid)
ok(ul["status"] == "review" and proof and proof["kind"] == "face", "#25 with proof, the listing waits for a check (not on sale yet)")
ok(buyer.get("/t/%d" % ulid).status_code == 404 and "Waiting for a quick check" in html(seller3.get("/t/%d" % ulid)),
   "#25 buyers can't see it yet; the seller sees it's being checked")
ok(any("listing to check" in sub for to, sub, b in SENT), "#25 the admins are told there's a listing to check")
ok("Listings to check" in html(admin.get("/admin")) and admin.get("/admin/proofs/%d" % proof["id"]).status_code == 200,
   "#25 the admin queue shows it with the proof")
ok(seller3.get("/admin/proofs/%d" % proof["id"]).status_code == 404, "#25 nobody else can open the proof")
admin.post("/admin/listings/%d/approve" % ulid, data={"_csrf": csrf(admin)})
with app.app_context(): ul = A.db.one("SELECT * FROM listings WHERE id=?", ulid)
ok(ul["status"] == "active" and ul["face_checked"] == 1 and any("on sale" in sub for to, sub, b in SENT if to == "s3@test.nl"),
   "#25 approving puts it on sale and tells the seller")
r = sell3(unpriced, "30", {"face_proof": (io.BytesIO(real_png(b"2")), "order2.png")})
rlid = int(re.search(r"/t/(\d+)", r.location).group(1))
admin.post("/admin/listings/%d/reject" % rlid, data={"why": "", "_csrf": csrf(admin)})
with app.app_context(): ok(A.db.one("SELECT status FROM listings WHERE id=?", rlid)["status"] == "review", "#25 rejecting needs a reason")
SENT.clear()
admin.post("/admin/listings/%d/reject" % rlid, data={"why": "The confirmation shows €20, not €30.", "_csrf": csrf(admin)})
with app.app_context(): ok(A.db.one("SELECT status FROM listings WHERE id=?", rlid)["status"] == "removed", "#25 a rejected listing never goes on sale")
ok(any("not €30" in b for to, sub, b in SENT if to == "s3@test.nl"), "#25 ...and the seller gets the reason")
MAIL_WORKS[0] = False
import importer
page_html = '<script type="application/ld+json">{"@type": "Event", "name": "Imported", "startDate": "%sT23:00", "location": {"name": "Perron"}, "offers": {"price": "19.50", "highPrice": "24,00"}}</script>' % D
found = importer.extract(page_html, "https://example.com/e")
ok(found and found[0]["face_value_max"] == "24.00", "#25 the importer reads the official (highest) price")

# --- #26: a payout or refund can't go out twice
import payments
def paid_order():
    r = sell3(priced, "25"); l_id = int(re.search(r"/t/(\d+)", r.location).group(1))
    r = buyer.post("/t/%d/buy" % l_id, data={"_csrf": csrf(buyer)}); pid = r.location.split("/")[2]
    buyer.post("/checkout/%s/test" % pid, data={"_csrf": csrf(buyer)})
    with app.app_context(): return A.db.one("SELECT * FROM orders WHERE public_id=?", pid)
def order_now(o):
    with app.app_context(): return A.db.one("SELECT * FROM orders WHERE id=?", o["id"])
calls = []
real_transfer, real_find, real_refund = payments.transfer_to_seller, payments.find_transfer, payments.refund
payments.transfer_to_seller = lambda order, seller: calls.append(order["public_id"]) or "tr_%d" % len(calls)
with app.app_context(): A.db.run("UPDATE users SET payouts_ready=1, bank_flagged_at=NULL WHERE email='s3@test.nl'")
o1 = paid_order()
with app.test_request_context():
    first, second = A.release_payout(o1), A.release_payout(o1)   # e.g. the 👍 and the hourly task at the same moment
ok(first and not second and len(calls) == 1 and order_now(o1)["status"] == "released", "#26 calling release_payout twice for one order makes only one transfer")
def boom(order, seller): raise RuntimeError("Stripe is down")
payments.transfer_to_seller = boom
o2 = paid_order()
try:
    with app.test_request_context(): A.release_payout(o2)
except RuntimeError:
    pass
ok(order_now(o2)["status"] == "paid", "#26 if Stripe fails, the order goes back to 'paid' to be tried again")
o3 = paid_order()
with app.app_context(): A.db.run("UPDATE orders SET status='releasing', releasing_at=? WHERE id=?", A.sqltime(A.utcnow() - timedelta(minutes=30)), o3["id"])
o4 = paid_order()
with app.app_context(): A.db.run("UPDATE orders SET status='releasing', releasing_at=? WHERE id=?", A.sqltime(A.utcnow() - timedelta(minutes=30)), o4["id"])
payments.find_transfer = lambda order: "tr_found" if order["id"] == o3["id"] else None
with app.test_request_context(): A.release_stuck_payouts()
ok(order_now(o3)["status"] == "released" and order_now(o3)["transfer_ref"] == "tr_found" and order_now(o4)["status"] == "paid",
   "#26 stuck payouts are finished when Stripe has the transfer, otherwise put back to be retried")
refunds = []
payments.refund = lambda order: refunds.append(order["public_id"]) or "re_x"
o5 = paid_order()
with app.test_request_context():
    a, b = A.refund_order(o5, "refund_cancelled"), A.refund_order(o5, "refund_cancelled")
ok(a and not b and len(refunds) == 1 and order_now(o5)["status"] == "refunded", "#26 refunding the same order twice refunds once")
o6 = paid_order()
admin.post("/admin/orders/%s/refund" % o6["public_id"], data={"_csrf": csrf(admin)})
admin.post("/admin/orders/%s/refund" % o6["public_id"], data={"_csrf": csrf(admin)})
ok(refunds.count(o6["public_id"]) == 1, "#26 the admin refund button can't refund twice either")
payments.transfer_to_seller, payments.find_transfer, payments.refund = real_transfer, real_find, real_refund
pay_src = open("payments.py", encoding="utf-8").read()
ok('idempotency_key="payout-"' in pay_src and 'idempotency_key="refund-"' in pay_src, "#26 Stripe transfers and refunds carry an idempotency key per order")

# --- #27: chargebacks and refunds from Stripe; refund after payout; iDEAL for first-time buyers
import stripe
reversals = []
real_parse, real_reverse, real_refund, key_was = payments.parse_webhook, payments.reverse_transfer, payments.refund, config.STRIPE_SECRET_KEY
payments.reverse_transfer = lambda order: reversals.append(order["public_id"]) or "trr_1"
payments.refund = lambda order: "re_x"
def webhook(kind, obj):
    ev = stripe.Event.construct_from({"id": "evt_x", "type": kind, "data": {"object": obj}}, "sk_test_x")
    config.STRIPE_SECRET_KEY = "sk_test_x"; payments.parse_webhook = lambda payload, sig: ev
    r = app.test_client().post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})
    config.STRIPE_SECRET_KEY = key_was; payments.parse_webhook = real_parse
    return r
MAIL_WORKS[0] = True; SENT.clear()
held = paid_order()
with app.app_context(): A.db.run("UPDATE orders SET payment_intent='pi_held' WHERE id=?", held["id"])
r = webhook("charge.dispute.created", {"id": "dp_1", "object": "dispute", "payment_intent": "pi_held", "amount": 2700, "reason": "fraudulent"})
ok(r.status_code == 200 and order_now(held)["status"] == "disputed" and order_now(held)["chargeback_at"], "#27 a chargeback before payout holds the payout")
ok(any("chargeback on order" in sub for to, sub, b in SENT), "#27 ...and the admins are told")
gone = paid_order()
with app.app_context(): A.db.run("UPDATE orders SET payment_intent='pi_gone', status='released', transfer_ref='tr_9' WHERE id=?", gone["id"])
webhook("charge.dispute.created", {"id": "dp_2", "object": "dispute", "payment_intent": "pi_gone", "amount": 2700, "reason": "fraudulent"})
ok(gone["public_id"] in reversals and order_now(gone)["reversal_ref"] == "trr_1", "#27 a chargeback after payout takes the seller's payout back")
webhook("charge.dispute.closed", {"id": "dp_2", "object": "dispute", "payment_intent": "pi_gone", "status": "lost"})
ok(order_now(gone)["chargeback_status"] == "lost", "#27 the outcome of the chargeback is recorded")
ok("Chargebacks" in html(admin.get("/admin")), "#27 /admin lists chargebacks")
dash = paid_order()
with app.app_context(): A.db.run("UPDATE orders SET payment_intent='pi_dash', status='released', transfer_ref='tr_8' WHERE id=?", dash["id"])
webhook("charge.refunded", {"id": "ch_1", "object": "charge", "payment_intent": "pi_dash"})
ok(order_now(dash)["status"] == "refunded" and dash["public_id"] in reversals, "#27 a refund made in the Stripe dashboard is mirrored and the payout taken back")
fake = paid_order()
with app.app_context(): A.db.run("UPDATE orders SET status='released', transfer_ref='tr_7' WHERE id=?", fake["id"])
admin.post("/admin/orders/%s/refund_after_payout" % fake["public_id"], data={"why": "", "_csrf": csrf(admin)})
ok(order_now(fake)["status"] == "released", "#27 refund after payout needs a reason")
SENT.clear()
admin.post("/admin/orders/%s/refund_after_payout" % fake["public_id"], data={"why": "The ticket was a copy.", "_csrf": csrf(admin)})
with app.app_context(): s3 = A.db.one("SELECT * FROM users WHERE email='s3@test.nl'")
ok(order_now(fake)["status"] == "refunded" and fake["public_id"] in reversals and s3["blocked_at"], "#27 an admin can refund after payout: payout taken back, buyer refunded, seller blocked")
ok(any(to == "s3@test.nl" and "The ticket was a copy." in b for to, sub, b in SENT), "#27 ...and the seller is told why")
payments.reverse_transfer, payments.refund = real_reverse, real_refund; MAIL_WORKS[0] = False
with app.app_context(): A.db.run("UPDATE users SET blocked_at=NULL WHERE email='s3@test.nl'"); A.db.run("DELETE FROM blocked_phones")
login(seller3, "s3@test.nl", "Sy")  # blocking signed them out
# iDEAL only for a first purchase
seen = []
real_checkout, real_live = payments.create_checkout, payments.live
payments.create_checkout = lambda order, title, email, ok_url, cancel_url, ideal_only=False: seen.append(ideal_only) or ("cs_x", "https://checkout.stripe.com/x")
payments.live = lambda: True
newbuyer = app.test_client(); login(newbuyer, "first@test.nl", "Fi")
r = sell3(priced, "25"); l_first = int(re.search(r"/t/(\d+)", r.location).group(1))
newbuyer.post("/t/%d/buy" % l_first, data={"_csrf": csrf(newbuyer)})
r = sell3(priced, "25"); l_again = int(re.search(r"/t/(\d+)", r.location).group(1))
buyer.post("/t/%d/buy" % l_again, data={"_csrf": csrf(buyer)})
payments.create_checkout, payments.live = real_checkout, real_live
ok(seen == [True, False], "#27 a first-time buyer pays by iDEAL only; someone who bought before can also use a card")

# --- #28: new sellers: payout setup first, 2 tickets at a time, paid 72 hours after the night, a no-double-sell promise
config.NEW_SELLER_LIMITS = True
newbie = app.test_client(); login(newbie, "newbie@test.nl", "Nia"); verify_phone(newbie, "0666666666")
def newbie_sells(n=1, promise=True):
    data = {"name_on_ticket": "no", "event_id": str(priced["id"]), "face_value": "25", "price": "25", "adult": "yes",
            "delivery": "transfer", "transfer_app": "ticketmaster", "transfer_qty": str(n), "_csrf": csrf(newbie)}
    if promise: data["no_double_sell"] = "yes"
    return newbie.post("/sell", data=data)
sp = html(newbie.get("/sell?event=%d" % priced["id"]))
ok("won&#39;t use it myself" in sp and "New on Spare?" in sp, "#28 the sell form has the no-double-sell promise and explains the new-seller rules")
r = newbie_sells(promise=False)
ok(r.status_code == 200 and "isn&#39;t for sale anywhere else" in html(r), "#28 the promise is required")
real_live = payments.live
payments.live = lambda: True; real_onboard = payments.payout_onboarding_url
payments.payout_onboarding_url = lambda user, ret, refresh: ("acct_nia", "https://connect.stripe.com/setup/x")
r = newbie_sells(1)
payments.live, payments.payout_onboarding_url = real_live, real_onboard
with app.app_context(): nl1 = A.db.one("SELECT * FROM listings l JOIN users u ON u.id = l.seller_id WHERE u.email='newbie@test.nl' ORDER BY l.id DESC")
ok(nl1["status"] == "draft" and nl1["no_double_sell_at"] and "/account/payouts" in r.location, "#28 a new seller's listing waits for payout setup and they're sent to set it up")
ok(buyer.get("/t/%d" % nl1["id"]).status_code == 404, "#28 ...and buyers can't see it yet")
newbie.get("/account/payouts")  # (development: payout setup completes straight away)
with app.app_context(): ok(A.db.one("SELECT status FROM listings WHERE id=?", nl1["id"])["status"] == "active", "#28 once payouts are set up, it goes on sale")
r = newbie_sells(2)
ok(r.status_code == 200 and "2 tickets on sale at a time" in html(r), "#28 new sellers can have at most 2 tickets on sale")
r = newbie_sells(1)
ok(r.status_code == 302, "#28 ...a second ticket is fine")
with app.app_context(): nl2 = A.db.one("SELECT * FROM listings l JOIN users u ON u.id = l.seller_id WHERE u.email='newbie@test.nl' ORDER BY l.id DESC")
r = buyer.post("/t/%d/buy" % nl2["id"], data={"qty": "1", "transfer_name": "Bo", "transfer_email": "bo@example.com", "_csrf": csrf(buyer)})
npub = r.location.split("/")[2]; buyer.post("/checkout/%s/test" % npub, data={"_csrf": csrf(buyer)})
with app.app_context(): no = A.db.one("SELECT * FROM orders WHERE public_id=?", npub)
expected = datetime.combine(date.fromisoformat(D) + timedelta(days=1), datetime.min.time()) + timedelta(hours=72)
ok(no["payout_due_at"] == A.sqltime(expected), "#28 a new seller is paid 72 hours after the night")
with app.app_context(): A.db.run("UPDATE listings SET event_date=? WHERE id=?", date.today().isoformat(), nl2["id"]); A.db.run("UPDATE orders SET transferred_at=? WHERE id=?", A.sqltime(A.utcnow()), no["id"])
buyer.post("/orders/%s/rate" % npub, data={"rating": "up", "_csrf": csrf(buyer)})
with app.app_context(): no = A.db.one("SELECT * FROM orders WHERE public_id=?", npub)
ok(no["status"] == "paid", "#28 a 👍 doesn't pay a new seller early")
config.NEW_SELLER_LIMITS = False

# --- #29: unreadable codes, re-saved pictures and app tickets
config.TICKET_CHECKS = True
from PIL import Image, ImageDraw
import qrcode
def ticket_png(seed, qr=None, scale=1.0, fmt="PNG"):
    rnd = __import__("random").Random(seed)
    img = Image.new("RGB", (600, 900), "white"); d = ImageDraw.Draw(img)
    for _ in range(60):  # a busy, ticket-like picture
        x, y = rnd.randrange(600), rnd.randrange(900)
        d.rectangle([x, y, x + rnd.randrange(20, 160), y + rnd.randrange(10, 80)], fill=tuple(rnd.randrange(256) for _ in range(3)))
    if qr: img.paste(qrcode.make(qr).convert("RGB").resize((300, 300)), (150, 520))
    if scale != 1.0: img = img.resize((int(600 * scale), int(900 * scale)))
    b = io.BytesIO(); img.save(b, fmt, quality=80) if fmt == "JPEG" else img.save(b, fmt); return b.getvalue()
seller4 = app.test_client(); login(seller4, "s4@test.nl", "Sol"); verify_phone(seller4, "0677777777")
def sell4(files, extra=None):
    data = {"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(priced["id"]), "face_value": "25", "price": "25",
            "adult": "yes", "ticket": files, "_csrf": csrf(seller4)}
    data.update(extra or {})
    return seller4.post("/sell", data=data, content_type="multipart/form-data")
r = sell4((io.BytesIO(ticket_png(1)), "nocode.png"))
ok(r.status_code == 200 and "couldn&#39;t read the QR code" in html(r) and 'name="code_review"' in html(r),
   "#29 a ticket without a readable code isn't published: the seller is asked for a clearer file or a manual check")
r = sell4((io.BytesIO(ticket_png(1)), "nocode.png"), {"code_review": "yes"})
with app.app_context(): cl = A.db.one("SELECT * FROM listings WHERE seller_id=(SELECT id FROM users WHERE email='s4@test.nl') ORDER BY id DESC")
ok(cl["status"] == "review" and "code" in cl["review_reason"], "#29 ...sent for a manual check, it waits in the admin queue")
ok("ticket file 1" in html(admin.get("/admin")), "#29 the admin queue links the ticket file")
r = sell4((io.BytesIO(ticket_png(1, scale=0.8, fmt="JPEG")), "resaved.jpg"), {"code_review": "yes"})
ok(r.status_code == 200 and "already" in html(r).lower(), "#29 a re-saved, resized copy of that picture is refused (perceptual hash)")
r = sell4((io.BytesIO(ticket_png(2, qr="TM-2026-AAA")), "a.png"))
ok(r.status_code == 302, "#29 a ticket with a readable code goes through")
r = sell4((io.BytesIO(ticket_png(2, qr="TM-2026-BBB")), "b.png"))
ok(r.status_code == 302, "#29 a different ticket from the same template (different code) isn't mistaken for a copy")
app_base = {"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(priced["id"]), "face_value": "25", "price": "25", "adult": "yes",
            "delivery": "transfer", "transfer_app": "ticketmaster", "transfer_qty": "1"}
r = seller4.post("/sell", data=dict(app_base, _csrf=csrf(seller4)), content_type="multipart/form-data")
ok(r.status_code == 200 and "screenshot from the ticket app" in html(r), "#29 an app ticket needs a screenshot from the app and the order confirmation")
r = seller4.post("/sell", data=dict(app_base, app_screenshot=(io.BytesIO(ticket_png(7)), "app.png"),
                                     app_order=(io.BytesIO(b"%PDF-1.4 order"), "order.pdf"), _csrf=csrf(seller4)), content_type="multipart/form-data")
ok(r.status_code == 302, "#29 ...with both, it's listed")
with app.app_context(): kinds = sorted(p["kind"] for p in A.db.all("SELECT kind FROM proofs WHERE listing_id=(SELECT MAX(id) FROM listings)"))
ok(kinds == ["app_order", "app_screenshot"], "#29 both are kept privately for Spare's team")
r = seller4.post("/sell", data=dict(app_base, app_screenshot=(io.BytesIO(ticket_png(7, scale=0.9, fmt="JPEG")), "same.jpg"),
                                     app_order=(io.BytesIO(b"%PDF-1.4 order2"), "order2.pdf"), _csrf=csrf(seller4)), content_type="multipart/form-data")
ok(r.status_code == 200, "#29 the same app screenshot can't be used for a second listing")
config.TICKET_CHECKS = False

# --- #30: "it worked" only once the doors have been open a while; a 👍 doesn't pay on the spot
o7 = paid_order()   # priced event on D (3 days ahead), doors unknown → 22:00
r = buyer.post("/orders/%s/rate" % o7["public_id"], data={"rating": "up", "_csrf": csrf(buyer)})
ok(r.status_code == 400 and "Did your ticket work" not in html(buyer.get("/orders/%s" % o7["public_id"])), "#30 no rating before the event")
with app.app_context():
    A.db.run("UPDATE events SET door_time='23:00' WHERE id=?", priced["id"])
    A.db.run("UPDATE listings SET event_date=? WHERE id=?", date.today().isoformat(), o7["listing_id"])
    l7 = A.db.one("SELECT * FROM listings WHERE id=?", o7["listing_id"])
    opens = A.doors_open_at(l7)
local_doors = opens.replace(tzinfo=__import__("datetime").timezone.utc).astimezone().strftime("%H:%M")
ok(local_doors == "23:00", "#30 the doors come from the event's door time (Amsterdam time)")
real_utcnow = A.utcnow
A.utcnow = lambda: opens + timedelta(hours=1)
r = buyer.post("/orders/%s/rate" % o7["public_id"], data={"rating": "up", "_csrf": csrf(buyer)})
ok(r.status_code == 400, "#30 one hour after the doors it's still too early to say 'it worked'")
A.utcnow = lambda: opens + timedelta(hours=2, minutes=5)
buyer.post("/orders/%s/rate" % o7["public_id"], data={"rating": "up", "_csrf": csrf(buyer)})
o7 = order_now(o7)
ok(o7["rating"] == "up" and o7["status"] == "paid" and o7["payout_due_at"] == A.sqltime(opens + timedelta(hours=3)),
   "#30 from 2 hours after the doors a 👍 counts, and brings the payout to 3 hours after the doors (not straight away)")
SENT.clear(); MAIL_WORKS[0] = True
buyer.post("/orders/%s/report" % o7["public_id"], data={"reason": "Turned away at 00:30, code already scanned", "_csrf": csrf(buyer)})
ok(order_now(o7)["status"] == "disputed", "#30 the buyer can still report a problem after a 👍, until the money has gone")
A.utcnow = real_utcnow; MAIL_WORKS[0] = False
r = seller3.get("/orders/%s" % o7["public_id"])
with app.app_context(): th = A.db.one("SELECT id FROM threads WHERE order_id=?", o7["id"])
seller3.post("/threads/%d/send" % th["id"], data={"body": "Can you tap it worked already? Thanks!", "_csrf": csrf(seller3)})
with app.app_context(): m = A.db.one("SELECT * FROM messages WHERE thread_id=? ORDER BY id DESC", th["id"])
ok(m["flags"] == "rate_early" and m["kind"] == "text", "#30 asking the buyer to confirm early is flagged for the admins (and still delivered)")
ok("rate_early" in html(admin.get("/admin")), "#30 ...and shown on /admin")

print("FAILED:", fails or "none")
