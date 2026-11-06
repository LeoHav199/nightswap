import os, re, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; notify.send_email = lambda *a, **kw: False
import app as A, seed
app = A.app; app.config["TESTING"] = True
with app.app_context(): seed.run()  # events in Rotterdam, Delft and Eindhoven (a festival)
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def locs(r): return re.findall(r"<loc>([^<]*)</loc>", html(r))

visitor, admin = app.test_client(), app.test_client()
login(admin, "admin@example.com", "Ad")
config.ENABLED_CITIES = ["Rotterdam"]

# --- Open site: only events (and venues) in ENABLED_CITIES
r = visitor.get("/sitemap.xml")
sm = locs(r)
ok(r.status_code == 200 and r.mimetype == "application/xml", "sitemap.xml answers with XML")
ok(any("Halloween+Rave" in u for u in sm) and any(u.endswith("/venues/maassilo") for u in sm), "open site: Rotterdam events and venues are listed")
ok(not any("Delft+Student+Gala" in u for u in sm), "open site: events in cities that aren't enabled are left out")
ok(not any("Winter+Warehouse" in u for u in sm), "open site: a festival in another city is left out too")
ok(not any("/venues/tu-aula" in u for u in sm), "open site: venues in cities that aren't enabled are left out")
config.ENABLED_CITIES = ["Rotterdam", "Delft"]
ok(any("Delft+Student+Gala" in u for u in locs(visitor.get("/sitemap.xml"))), "enabling a city adds its events")
config.ENABLED_CITIES = ["Rotterdam"]

# --- Coming-soon mode: nothing visitors can't open
admin.post("/admin/launch", data={"action": "coming_soon", "_csrf": csrf(admin)})
ok(visitor.get("/event?venue=Maassilo&date=2099-01-01&name=x").status_code == 302, "coming-soon: event pages send visitors to the sign-up page")
r = visitor.get("/sitemap.xml")
sm = locs(r)
ok(r.status_code == 200 and len(sm) == 2 and not any("/event?" in u or "/venues/" in u for u in sm),
   "coming-soon: sitemap lists no event or venue pages visitors cannot see")
ok(any(u.endswith("/how-it-works") for u in sm), "coming-soon: the pages visitors can open are still listed")
ok(any("Halloween+Rave" in u for u in locs(admin.get("/sitemap.xml"))), "coming-soon: admins still get the full list")

# --- robots.txt
for label in ("coming-soon", "open"):
    r = visitor.get("/robots.txt")
    lines = html(r).splitlines()
    ok(r.mimetype == "text/plain" and "User-agent: *" in lines, "%s: robots.txt is plain text for all crawlers" % label)
    ok("Disallow: /admin" in lines and "Disallow: /tasks" in lines, "%s: robots.txt disallows /admin and /tasks/hourly" % label)
    ok(any(l.startswith("Sitemap: ") and l.endswith("/sitemap.xml") for l in lines), "%s: robots.txt points to the sitemap" % label)
    admin.post("/admin/launch", data={"action": "open", "_csrf": csrf(admin)})

print("FAILED:", fails or "none")
