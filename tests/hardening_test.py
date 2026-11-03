"""Checks for the fixes from the 'Bulletproofing Spare' review (numbers refer to its items)."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
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
admin.post("/admin/events/save", data={"name": "Guard Night", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Guard Night'")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
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
ok("on the day of the event that the ticket worked" in how and "48 hours after the night" in how, "#4 How it works describes the early and the latest payout")
ok("on the day of the event that the ticket worked" in terms and "nothing is paid out until the seller has transferred it" in terms, "#4 the terms say the same")
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
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(b"%PDF-1.4 guard two"), "g2.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid2 = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid2, data={"_csrf": csrf(buyer)}); pub2 = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub2, data={"_csrf": csrf(buyer)}); SENT.clear()
buyer.post("/orders/%s/report" % pub2, data={"reason": "The QR code was already scanned at the door", "_csrf": csrf(buyer)})
mail = [b for to, sub, b in SENT if to == "s@test.nl" and "Problem reported" in sub]
ok(mail and "within 24 hours" in mail[0] and "/orders/%s" % pub2 in mail[0], "#12 the seller gets an email about the problem, with 24 hours to reply in the chat")
with app.app_context(): ok(A.db.one("SELECT 1 FROM messages WHERE kind='system' AND body='sys_disputed'") is not None, "#12 the order chat notes the report")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
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
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
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
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
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

print("FAILED:", fails or "none")
