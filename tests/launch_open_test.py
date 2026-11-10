import os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
config.ENABLED_CITIES = ["Rotterdam"]; config.SHOW_ALL_NETHERLANDS = False  # the launch settings
import notify; notify.send_email = lambda *a, **kw: False
import app as A, seed, i18n
app = A.app; app.config["TESTING"] = True
with app.app_context(): seed.run()  # Rotterdam, plus a Delft student night and an Eindhoven festival
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})

# Events nobody sells yet ("Coming up"): one in Rotterdam, two in cities that are not open
day = (date.today() + timedelta(days=6)).isoformat()
with app.app_context():
    for name, venue, city, cat in (("Rotterdam Quiet Night", "Annabel", "Rotterdam", "club"),
                                   ("Amsterdam Canal Party", "Paradiso", "Amsterdam", "club"),
                                   ("Utrecht Student Mixer", "Tivoli", "Utrecht", "student")):
        A.db.run("INSERT INTO events (name, venue, city, category, event_date, status) VALUES (?, ?, ?, ?, ?, 'published')",
                 name, venue, city, cat, day)

admin = app.test_client(); login(admin, "admin@example.com", "Ad")
ROTTERDAM = ("Halloween Rave", "Rotterdam Quiet Night")  # a listing with tickets, and a ticketless event
OTHER_CITIES = ("Delft Student Gala", "Amsterdam Canal Party", "Utrecht Student Mixer")

def visitor_in(lang):
    c = app.test_client(); c.set_cookie("lang", lang); return c

# --- Before launch: logged-out visitors only get the sign-up page
admin.post("/admin/launch", data={"action": "coming_soon", "_csrf": csrf(admin)})
page = html(visitor_in("en").get("/"))
ok("Halloween Rave" not in page and 'name="email"' in page, "coming-soon on: a visitor sees the sign-up page, not the events")

# --- The launch-day switch
r = admin.post("/admin/launch", data={"action": "open", "_csrf": csrf(admin)})
ok(r.status_code == 302, "launch day: the admin opens the site")
with app.app_context(): ok(not A.coming_soon_on(), "launch day: coming-soon is off")
ok(config.ENABLED_CITIES == ["Rotterdam"] and config.SHOW_ALL_NETHERLANDS is False, "launch settings: Rotterdam only, no all-of-NL view")

for lang in i18n.LANGS:
    v = visitor_in(lang)
    r = v.get("/"); page = html(r)
    ok(r.status_code == 200 and '<html lang="%s"' % lang in page, "[%s] logged-out visitor gets the homepage (200)" % lang)
    ok('name="interest"' not in page and "/preview/" not in page, "[%s] it is the real homepage, not the sign-up page" % lang)
    ok(all(n in page for n in ROTTERDAM), "[%s] Rotterdam events show: one with tickets, one in Coming up" % lang)
    ok(not any(n in page for n in OTHER_CITIES), "[%s] no club or student events from other cities" % lang)
    ok("Rotterdam" in page and "Amsterdam" not in page and "Delft" not in page, "[%s] only Rotterdam is named as a city" % lang)
    ok("city=all" not in page, "[%s] no 'all of the Netherlands' choice" % lang)
    # Other cities can't be forced through the address or an old cookie
    for label in ("?city=Delft", "?city=Amsterdam", "?city=all"):
        resp = v.get("/" + label); p = html(resp)
        ok(resp.status_code == 200 and all(n in p for n in ROTTERDAM) and not any(n in p for n in OTHER_CITIES),
           "[%s] %s still shows Rotterdam only" % (lang, label))
    v2 = visitor_in(lang); v2.set_cookie("city", "Delft")
    p = html(v2.get("/"))
    ok(all(n in p for n in ROTTERDAM) and not any(n in p for n in OTHER_CITIES), "[%s] a saved city cookie for Delft still shows Rotterdam only" % lang)

# --- Festivals and concerts are national by design: they show whatever the city, so the checks above leave them out
ok("Winter Warehouse Festival" in html(visitor_in("en").get("/")), "festivals show in every city by design (NATIONAL_CATEGORIES)")

print("FAILED:", fails or "none")
