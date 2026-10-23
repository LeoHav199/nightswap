import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.ADMIN_SECOND_FACTOR = False
config.FEE_PERCENT = 0
config.SHOW_ALL_NETHERLANDS = True  # these tests cover the all-NL view  # these tests were written for a flat €1 fee
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; notify.send_email = lambda *a: False
import app as A, seed, qrcode
from PIL import Image
app = A.app; app.config["TESTING"] = True
with app.app_context(): seed.run()
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def png(color="red", qr=None):
    img = Image.new("RGB", (800, 1000), color)
    if qr: img.paste(qrcode.make(qr).convert("RGB").resize((400, 400)), (200, 300))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()

c = app.test_client()
home = html(c.get("/"))
ok("Winter Warehouse Festival" in home, "festivals elsewhere in NL show in the Rotterdam view")
ok("Delft Student Gala" not in home, "student parties in other cities don't clutter the Rotterdam view")
ok("Delft Student Gala" in html(c.get("/?city=all")), "'All of the Netherlands' shows everything")
ok("Student parties" in home and "Festivals" in home and "Concerts" in home, "category chips shown")
club = html(c.get("/?cat=student"))
ok("Thursday Student Night" in club and "Halloween Rave" not in club, "category filter works")
ok("Going fast" in home, "'Going fast' shows for an event many people are waiting for")
ok("Just listed" in home, "'Just listed' shows for new tickets")
with app.test_request_context():
    evs = A.group_events(A.browse())
    order = [e["category"] for e in evs if e["date"] == evs[0]["date"]]
ok(html(c.get("/?city=Rotterdam")).count("Delft Student Gala") == 0, "chosen city is remembered and can be switched back")
v = html(c.get("/venues/maassilo"))
ok("Halloween Rave" in v and "Directions" in v and "google.com/maps" in v, "venue page lists events with directions")
ok(c.get("/venues/nope").status_code == 404, "unknown venue is a 404")
sm = html(c.get("/sitemap.xml"))
ok("/venues/maassilo" in sm and "/event?" in sm, "sitemap lists venues and events")
ev = html(c.get("/event?venue=Maassilo&date=%s&name=Halloween+Rave" % (date.today() + timedelta(days=8)).isoformat()))
ok('"@type": "Event"' in ev and "og:title" in ev, "event page has Google event data and share preview")
ok("view-transition-name: ev-" in ev, "poster has a page-transition name")

# Selling: city needed for an unknown venue, category saved, venue created
seller = app.test_client(); login(seller, "s@student.eur.nl", "Sam")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
D = (date.today() + timedelta(days=3)).isoformat()
form = lambda **kw: dict({"venue": "__other", "other_venue": "Het Sieraad", "event_date": D, "face_value": "20", "price": "20",
                          "event_name": "Gala", "category": "student", "_csrf": csrf(seller)}, **kw)
r = seller.post("/sell", data=dict(form(), adult="yes", ticket=(io.BytesIO(png(qr="T-GALA-1")), "t.png")), content_type="multipart/form-data")
ok("Which city is it in?" in html(r), "an unknown venue needs a city")
r = seller.post("/sell", data=dict(form(city="Amsterdam", start_time="21:30"), adult="yes", ticket=(io.BytesIO(png(qr="T-GALA-1")), "t.png")), content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
with app.app_context():
    l = A.db.one("SELECT * FROM listings WHERE id=?", lid)
    vrow = A.db.one("SELECT * FROM venues WHERE name='Het Sieraad'")
ok(l["category"] == "student" and l["city"] == "Amsterdam" and l["start_time"] == "21:30", "category, city and start time saved")
ok(vrow and vrow["slug"] == "het-sieraad-amsterdam", "new venue gets its own page: /venues/het-sieraad-amsterdam")
ok("Het Sieraad" in html(c.get("/?city=all")) and "Gala" not in html(c.get("/?city=Rotterdam")), "an Amsterdam student party only shows under All of NL")

# Buying: ticket-style order page with the real QR code, celebration, calendar
buyer = app.test_client(); login(buyer, "b@test.nl", "Bea")
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)})
r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
ok("paid=1" in r.location, "after paying you land on the order with the celebration flag")
page = html(buyer.get(r.location))
ok('class="ticket just-paid"' in page and 'class="confetti"' in page, "ticket stub with tear-off animation and confetti")
ok("<svg" in page and "ticket-qr" in page, "the ticket's QR code is shown on the stub")
pub = r.location.split("/")[2].split("?")[0]
ics = buyer.get("/orders/%s/calendar.ics" % pub)
ok(ics.mimetype == "text/calendar" and b"DTSTART;TZID=Europe/Amsterdam:" in ics.data and b"T213000" in ics.data, "add-to-calendar file uses the start time")
ok('class="ticket just-paid"' not in html(buyer.get("/orders/%s" % pub)), "celebration only on the first view")

# Admin: venue photo + event poster
admin = app.test_client(); login(admin, "admin@example.com", "Ad")
ok("Upcoming events" in html(admin.get("/admin/venues")), "admin venues & posters page")
with app.app_context(): mid = A.db.one("SELECT id FROM venues WHERE slug='maassilo'")["id"]
admin.post("/admin/venues", data={"id": mid, "address": "Maashaven Zuidzijde 1, Rotterdam", "website": "maassilo.com", "colour": "#123456",
           "description": "Old grain silo", "photo": (io.BytesIO(png("blue")), "p.png"), "_csrf": csrf(admin)}, content_type="multipart/form-data")
v = html(c.get("/venues/maassilo"))
ok("Maashaven Zuidzijde 1" in v and "https://maassilo.com" in v and "/posters/" in v, "venue address, website and photo shown")
ok("is-photo" in html(c.get("/")), "event cards fall back to the venue photo")
admin.post("/admin/posters", data={"venue": "Maassilo", "date": (date.today() + timedelta(days=8)).isoformat(), "name": "Halloween Rave",
           "poster": (io.BytesIO(png("purple")), "x.png"), "_csrf": csrf(admin)}, content_type="multipart/form-data")
with app.app_context():
    ok(all(r_["poster_file"] for r_ in A.db.all("SELECT poster_file FROM listings WHERE event_name='Halloween Rave'")), "event poster applied to every listing of that event")
ok(app.test_client().get("/admin/venues").status_code in (302, 404), "venue admin is admin-only")
print("FAILED:", fails or "none")
