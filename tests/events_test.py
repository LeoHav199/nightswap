import io, os, re, sys, tempfile, json
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.ADMIN_SECOND_FACTOR = False
config.FEE_PERCENT = 0  # these tests were written for a flat €1 fee
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; notify.send_email = lambda *a: False
import app as A, importer
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
D = (date.today() + timedelta(days=6)).isoformat()
admin, seller, visitor = app.test_client(), app.test_client(), app.test_client()
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); visitor.get("/")

# Admin adds a real event by hand
r = admin.post("/admin/events/save", data={"name": "Exchange Halloween", "venue": "Maassilo", "city": "Rotterdam", "category": "student",
    "event_date": D, "door_time": "23:00", "end_time": "05:00", "min_age": "18", "ticket_url": "exchangeparty.nl/halloween",
    "description": "Biggest student party", "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Exchange Halloween'")
ok(ev and ev["status"] == "published" and ev["ticket_url"] == "https://exchangeparty.nl/halloween", "admin adds an event (link gets https://)")
home = html(visitor.get("/"))
ok("Exchange Halloween" in home and "Coming up" in home and 'class="coming-row"' in home, "event without tickets shows in the compact 'Coming up' list")
page = html(visitor.get("/event?venue=Maassilo&date=%s&name=Exchange+Halloween" % D))
ok("23:00 – 05:00" in page and "18+" in page and "exchangeparty.nl" in page and "Biggest student party" in page, "event page shows doors, age, official link and description")
ok("Got a ticket? Sell it here" in page and "sell?event=%d" % ev["id"] in page, "event page links straight to selling for it")
r = admin.post("/admin/events/save", data={"name": "Exchange Halloween", "venue": "Maassilo", "event_date": D, "_csrf": csrf(admin)}, follow_redirects=True)
ok("already exists" in html(r), "duplicates are refused")

# Picker API + selling for a chosen event
api = visitor.get("/api/events?date=%s" % D).get_json()
ok(api["events"] and api["events"][0]["name"] == "Exchange Halloween", "picker lists events on that date")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
ok("Exchange Halloween" in html(seller.get("/sell?event=%d" % ev["id"])), "sell page preselects the event")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "25", "price": "22", "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 x1"), "t.pdf"),
    "_csrf": csrf(seller)}, content_type="multipart/form-data")
ok(r.status_code == 302 and "/t/" in r.location, "selling with only event + price + file works")
with app.app_context():
    l = A.db.one("SELECT * FROM listings ORDER BY id DESC LIMIT 1")
ok(l["event_id"] == ev["id"] and l["venue"] == "Maassilo" and l["category"] == "student" and l["start_time"] == "23:00", "listing takes the event's details")
ok("€23" in html(visitor.get("/")), "event card now shows the price")

# Seller adds an event that isn't listed
r = seller.post("/sell", data={"event_id": "new", "venue": "__other", "other_venue": "Het Sieraad", "city": "Amsterdam", "event_date": D,
    "event_name": "Gala Night", "category": "student", "face_value": "30", "price": "30", "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 x2"), "t.pdf"),
    "_csrf": csrf(seller)}, content_type="multipart/form-data")
with app.app_context(): new = A.db.one("SELECT * FROM events WHERE name='Gala Night'")
ok(new and new["status"] == "review", "seller-added event goes live but waits for a check")
ok("Gala Night" in html(admin.get("/admin/events")) and "Added by sellers" in html(admin.get("/admin/events")), "admin sees it in the review queue")
admin.post("/admin/events/%d/approve" % new["id"], data={"_csrf": csrf(admin)})
with app.app_context(): ok(A.db.one("SELECT status FROM events WHERE id=?", new["id"])["status"] == "published", "admin approves it")

# Editing an event updates its tickets
admin.post("/admin/events/save", data={"id": ev["id"], "name": "Halloween Rotterdam", "venue": "Maassilo", "city": "Rotterdam", "category": "student",
    "event_date": D, "door_time": "22:30", "_csrf": csrf(admin)})
with app.app_context(): l2 = A.db.one("SELECT * FROM listings WHERE id=?", l["id"])
ok(l2["event_name"] == "Halloween Rotterdam" and l2["start_time"] == "22:30", "renaming an event updates its listings")
admin.post("/admin/events/%d/hide" % new["id"], data={"_csrf": csrf(admin)})
ok("Gala Night" not in html(visitor.get("/api/events?date=%s" % D)), "hidden events disappear from the picker")

# Import from a link (fetch replaced with a fixture page)
fixture = '''<script type="application/ld+json">[{"@type":"MusicEvent","name":"Techno Marathon","startDate":"%sT23:00:00+01:00",
 "endDate":"%sT07:00:00+01:00","location":{"name":"Perron","address":{"addressLocality":"Rotterdam","streetAddress":"Schiestraat 18"}},
 "offers":{"url":"https://tickets.example.com/tm"}},{"@type":"Event","name":"Past Thing","startDate":"2020-01-01"}]</script>''' % (D, D)
importer.fetch = lambda url: fixture
r = admin.post("/admin/events/import", data={"url": "https://perron.example/agenda", "_csrf": csrf(admin)})
page = html(r)
ok("Found 1 events" in page and "Techno Marathon" in page and "Past Thing" not in page, "import finds upcoming events only")
payload = re.search(r'name="payload" value="([^"]+)"', page).group(1)
import html as H
r = admin.post("/admin/events/import/confirm", data={"payload": H.unescape(payload), "url": "https://perron.example/agenda", "pick": "0",
    "venue_0": "Perron", "category_0": "club", "_csrf": csrf(admin)}, follow_redirects=True)
with app.app_context():
    tm = A.db.one("SELECT * FROM events WHERE name='Techno Marathon'")
    perron = A.db.one("SELECT * FROM venues WHERE slug='perron'")
ok(tm and tm["door_time"] == "23:00" and tm["end_time"] == "07:00" and tm["source_url"] == "https://perron.example/agenda", "imported event saved with times and source")
ok(perron and perron["address"] == "Schiestraat 18, Rotterdam", "import fills in the venue address")
ok(visitor.get("/admin/events").status_code in (302, 404), "events admin is admin-only")
try:
    importer.__dict__.pop("fetch", None); import importlib; importlib.reload(importer)
    importer.fetch("http://127.0.0.1:5001/"); ok(False, "importer blocks local addresses")
except importer.ImportError_:
    ok(True, "importer blocks local addresses")
print("FAILED:", fails or "none")
