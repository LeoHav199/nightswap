"""Search engine tags in the layout: canonical links point at sparetickets.eu (never the Render address), there are no
hreflang tags pointing elsewhere, and coming-soon mode and cities that aren't open yet are noindex."""
import os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.DATA_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
config.BASE_URL = "https://sparetickets.eu"
import notify
notify.send_email = lambda *a, **kw: False
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
def head(page):
    return page.split("</head>")[0]
def canonicals(page):
    import html as htmllib  # the page writes & as &amp; inside attributes
    return [htmllib.unescape(u) for u in re.findall(r'<link rel="canonical" href="([^"]*)"', head(page))]
def noindex(page):
    return '<meta name="robots" content="noindex">' in head(page)

D = (date.today() + timedelta(days=6)).isoformat()
admin, visitor = app.test_client(), app.test_client()
login(admin, "admin@example.com", "Ad"); visitor.get("/")
for name, venue, city, category in (("Open Night", "Maassilo", "Rotterdam", "student"), ("Closed Night", "Paradiso", "Amsterdam", "student"),
                                    ("Big Fest", "Ahoy", "Utrecht", "festival")):
    admin.post("/admin/events/save", data={"face_value_max": "500", "name": name, "venue": venue, "city": city, "category": category,
                                          "event_date": D, "_csrf": csrf(admin)})
with app.app_context():
    for v, c in (("Maassilo", "Rotterdam"), ("Paradiso", "Amsterdam")):
        A.get_venue(v, c, create=True)
    slugs = {r["name"]: r["slug"] for r in A.db.all("SELECT name, slug FROM venues")}
ok("Maassilo" in slugs and "Paradiso" in slugs, "test venues exist")

def event_path(venue, name):
    return "/event?venue=%s&date=%s&name=%s" % (venue.replace(" ", "+"), D, name.replace(" ", "+"))
open_event = "https://sparetickets.eu/event?venue=Maassilo&date=%s&name=Open+Night" % D

# --- Indexable pages, in every language: canonical on sparetickets.eu
for lang in i18n.LANGS:
    c = app.test_client(); c.set_cookie("lang", lang)
    pages = {"home": ("/", "https://sparetickets.eu/"),
             "event": (event_path("Maassilo", "Open Night"), open_event),
             "venue": ("/venues/" + slugs["Maassilo"], "https://sparetickets.eu/venues/" + slugs["Maassilo"])}
    for label, (path, want) in pages.items():
        r = c.get(path); page = html(r)
        ok(r.status_code == 200 and canonicals(page) == [want], "%s: %s page has one canonical, %s" % (lang, label, want))
        ok("nightswap.onrender.com" not in head(page) and "onrender.com" not in head(page), "%s: %s page head never mentions Render's address" % (lang, label))
        ok(not noindex(page), "%s: %s page can be indexed" % (lang, label))
        ok("hreflang" not in head(page), "%s: %s page head has no hreflang links (language is a cookie: one URL per page)" % (lang, label))

# --- The canonical drops search words and tracking parameters on the home page
r = visitor.get("/?q=mass&from=flyer1&day=" + D)
ok(canonicals(html(r)) == ["https://sparetickets.eu/"], "home page with ?q= and ?from= still has the clean canonical")

# --- Cities that aren't open yet are noindex (a festival is listed everywhere)
for lang in i18n.LANGS:
    c = app.test_client(); c.set_cookie("lang", lang)
    page = html(c.get(event_path("Paradiso", "Closed Night")))
    ok(noindex(page) and not canonicals(page), "%s: event in an unlisted city is noindex" % lang)
    page = html(c.get("/venues/" + slugs["Paradiso"]))
    ok(noindex(page) and not canonicals(page), "%s: venue in an unlisted city is noindex" % lang)
ok(not noindex(html(visitor.get(event_path("Ahoy", "Big Fest")))), "a festival is indexable whatever its city")

# --- Coming-soon mode: noindex everywhere, in every language
admin.post("/admin/launch", data={"action": "coming_soon", "_csrf": csrf(admin)})
with app.app_context(): on = A.coming_soon_on()
ok(on, "coming-soon mode is on")
for lang in i18n.LANGS:
    c = app.test_client(); c.set_cookie("lang", lang)
    page = html(c.get("/"))
    ok(noindex(page) and not canonicals(page), "%s: the sign-up page is noindex" % lang)
    c.get("/")
    page = html(c.post("/waitlist", data={"email": "nope", "_csrf": csrf(c)}))
    ok(noindex(page), "%s: the sign-up page with an error is noindex" % lang)
page = html(admin.get("/"))
ok(noindex(page), "admins and preview links see the real site, still noindex while coming-soon mode is on")
print("FAILED:", fails or "none")
