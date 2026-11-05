import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **kw: False
import app as A, qrcode
from flask import render_template_string
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
    img = Image.new("RGB", (800, 1000), "white")
    img.paste(qrcode.make(qr).convert("RGB").resize((400, 400)), (200, 300))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()

# Phone-sized photos: /posters/<file>?w=480 is a WebP copy at most 480px wide, made once and kept outside the uploads.
os.makedirs(os.path.join(config.UPLOAD_DIR, "posters"), exist_ok=True)
name = "ab" * 16 + ".jpg"
Image.new("RGB", (1920, 1080), (40, 90, 160)).save(os.path.join(config.UPLOAD_DIR, "posters", name), "JPEG", quality=95)
c = app.test_client()
full = c.get("/posters/" + name).get_data()
small = c.get("/posters/%s?w=480" % name)
ok(small.mimetype == "image/webp" and Image.open(io.BytesIO(small.get_data())).size == (480, 270),
   "a 1920px photo comes back as a 480px WebP copy")
ok(len(small.get_data()) < len(full) / 5, "the phone copy is a fraction of the original's size")
ok(os.path.exists(os.path.join(tmp, "cache", "posters", "ab" * 16 + "-w480.webp")), "the copy is kept in DATA_DIR/cache, not in the uploads (so backups skip it)")
ok(c.get("/posters/%s?w=333" % name).mimetype == "image/jpeg", "an unknown width gets the original")
ok(c.get("/posters/%s?w=800" % ("cd" * 16 + ".jpg")).status_code == 404, "a missing photo is still a 404")
with app.test_request_context():
    app.preprocess_request()
    out = render_template_string("{% import '_macros.html' as m with context %}{{ m.poster('X', 'Maassilo', '2026-11-06', None, '#123', 'card', '" + name + "') }}")
ok("?w=800" in out and "480w" in out and "1280w" in out and 'sizes="(max-width: 640px) 50vw' in out,
   "event cards ask for the photo at the phone's size (srcset 480/800/1280)")

# A seller lists a ticket for the night that's on now, a buyer buys it.
seller = app.test_client(); login(seller, "s@student.eur.nl", "Sam")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
D = (date.today() + timedelta(days=3)).isoformat()
r = seller.post("/sell", data={"venue": "__other", "other_venue": "Het Sieraad", "city": "Rotterdam", "event_date": D, "face_value": "20",
                               "price": "20", "event_name": "Gala", "category": "student", "name_on_ticket": "no", "no_double_sell": "yes",
                               "adult": "yes", "_csrf": csrf(seller), "ticket": (io.BytesIO(png("T-MOBILE-1")), "t.png")},
                content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))

# Event page: the tickets come straight after the name; details, disclaimer and buttons follow the list.
ev = html(c.get("/event?venue=Het+Sieraad&date=%s&name=Gala" % D))
ok(ev.index('class="ticket-list"') < ev.index("not-affiliated") < ev.index('class="event-actions"'),
   "event page: ticket list first, then the disclaimer and the Notify/Share/Directions buttons")
ok(ev.index('class="ticket-list"') > ev.index("<h1"), "the name still comes before the tickets")

buyer = app.test_client(); login(buyer, "b@test.nl", "Bea")
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)})
r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
pub = r.location.split("/")[2].split("?")[0]
page = html(buyer.get("/orders/" + pub))
ok("is-door" not in page and "door-details" not in page, "days before the night the order page looks as before")

# Door mode: from noon on the event day until noon the day after, the code and Swap come first.
night = date.today() if datetime.now().hour >= 12 else date.today() - timedelta(days=1)
with app.app_context():
    A.db.run("UPDATE listings SET event_date=?, start_time='12:00' WHERE id=?", night.isoformat(), lid)
page = html(buyer.get("/orders/" + pub))
ok("is-door" in page, "on the night the buyer's order page opens in door mode")
ok(page.index("ticket-qr") < page.index("swap-card") < page.index("door-details"),
   "door mode: QR code first, Swap right under it, the photo and details last")
ok(page.count('class="swap-card"') == 1, "Swap shows once")
ok(page.index("ticket-qr") < page.index("ticket-facts"), "date, venue and order number move below the code")
seller_page = html(seller.get("/orders/" + pub))
ok("is-door" not in seller_page, "the seller's view of the order doesn't change")
with app.app_context():
    l = A.db.one("SELECT * FROM listings WHERE id=?", lid)
    ok(A.door_night(dict(l, event_date=(night - timedelta(days=1)).isoformat())) is False
       and A.door_night(dict(l, event_date=(night + timedelta(days=2)).isoformat())) is False,
       "door mode is only for the event night, not the day before or after")

# Search tab: a search sheet on every page but the homepage (which has its own box), so one tap puts you in the box.
other = html(c.get("/credits"))
ok("data-search-tab" in other and "data-search-sheet" in other and 'id="q-sheet"' in other, "other pages carry the search sheet for the Search tab")
home = html(c.get("/"))
ok("data-search-tab" in home and "data-search-sheet" not in home and 'id="q"' in home, "the homepage uses its own search box")
ok('href="/?focus=search"' in other, "without JavaScript the Search tab still goes to the homepage search")

print("\n%d failed" % len(fails))
