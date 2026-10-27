import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.ADMIN_SECOND_FACTOR = False
config.FEE_PERCENT = 0  # these tests were written for a flat €1 fee
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; notify.send_email = lambda to, s, b: (SENT.append((to, s)), False)[1]
import app as A
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
def png(color):
    b = io.BytesIO(); Image.new("RGB", (800, 1000), color).save(b, "PNG"); return b.getvalue()
D = (date.today() + timedelta(days=4)).isoformat()
admin, org, visitor = app.test_client(), app.test_client(), app.test_client()
login(admin, "admin@example.com", "Ad"); login(org, "promo@exchangeparty.nl", "Pim"); visitor.get("/")
admin.post("/admin/events/save", data={"name": "Halloween Rotterdam", "venue": "Maassilo", "category": "student", "event_date": D, "_csrf": csrf(admin)})
with app.app_context():
    ev = A.db.one("SELECT * FROM events WHERE name='Halloween Rotterdam'")
    vid = A.db.one("SELECT id FROM venues WHERE slug='maassilo'")["id"]
url = "/event?venue=Maassilo&date=%s&name=Halloween+Rotterdam" % D
ok("Organiser? Add your poster" in html(visitor.get(url)), "event page invites the organiser to add a poster")
r = org.post("/events/%d/poster" % ev["id"], data={"organisation": "Exchange Party", "poster": (io.BytesIO(png("purple")), "p.png"), "_csrf": csrf(org)},
             content_type="multipart/form-data")
ok("Please confirm you" in html(r) and "allowed to share this poster" in html(r), "permission checkbox is required")
r = org.post("/events/%d/poster" % ev["id"], data={"organisation": "Exchange Party", "credit": "Exchange Party", "consent": "yes",
             "poster": (io.BytesIO(png("purple")), "p.png"), "_csrf": csrf(org)}, content_type="multipart/form-data")
ok(r.status_code == 302 and any("poster sent in" in s for _, s in SENT), "poster submitted and admin notified")
ok("credits#" not in html(visitor.get(url)) and "is-art" in html(visitor.get(url)), "poster not shown before approval")
apage = html(admin.get("/admin/events"))
ok("Posters sent in" in apage and "promo@exchangeparty.nl" in apage, "admin sees the submission with the sender's email")
ok("Message to send organisers" in apage and "Hoi! Ik ben van Spare" in apage, "outreach message template (EN + NL) on the admin page")
with app.app_context(): sid = A.db.one("SELECT id FROM poster_submissions")["id"]
admin.post("/admin/posters/%d/approve" % sid, data={"_csrf": csrf(admin)})
page = html(visitor.get(url))
ok("credits#e-%d" % ev["id"] in page and "Organiser? Add your poster" not in page and 'class="credit"' not in page, "approved poster is live, credit linked below it (not on it)")
cr = html(visitor.get("/credits"))
ok("Poster: Exchange Party" in cr and 'id="e-%d"' % ev["id"] in cr, "poster credit listed on the credits page")

# Free-licence venue photo with credit
admin.post("/admin/venues", data={"id": vid, "photo": (io.BytesIO(png("gray")), "m.png"), "photo_credit": "Luke Price", "photo_license": "CC BY 2.0",
           "photo_license_url": "https://creativecommons.org/licenses/by/2.0/", "photo_source_url": "https://commons.wikimedia.org/wiki/File:Maassilo.jpg",
           "_csrf": csrf(admin)}, content_type="multipart/form-data")
v = html(visitor.get("/venues/maassilo"))
ok("/credits#v-maassilo" in v and "Luke Price" not in v, "venue page links to its credit instead of printing it on the photo")
cr = html(visitor.get("/credits"))
ok("Photo: Luke Price" in cr and ">CC BY 2.0<" in cr and "creativecommons.org/licenses/by/2.0" in cr and "commons.wikimedia.org/wiki/File:Maassilo.jpg" in cr, "credits page: photographer, linked licence and source")
ok("Photo credits" in html(visitor.get("/how-it-works")), "credits page linked from the footer on every page")
admin.post("/admin/events/save", data={"name": "Club Night", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
evp = html(visitor.get("/event?venue=Maassilo&date=%s&name=Club+Night" % D))
ok("is-photo" in evp and 'class="credit"' not in evp, "events use the venue photo, with no credit drawn on it")
print("FAILED:", fails or "none")
