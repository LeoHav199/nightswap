import io, os, re, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; MAIL_WORKS = [False]  # False: sign-in codes show on screen, like in development
notify.send_email = lambda to, subject, body: (SENT.append((to, subject, body)) or True) if MAIL_WORKS[0] else False
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

admin, visitor, friend = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad")

# --- Off by default: the normal site
ok("Coming soon to Rotterdam" not in html(visitor.get("/")), "coming-soon mode is off by default")

# --- Admin switches it on
r = admin.post("/admin/launch", data={"action": "coming_soon", "_csrf": csrf(admin)}, follow_redirects=True)
ok("Coming-soon mode is on" in html(r), "admin can switch on coming-soon mode")
home = html(visitor.get("/"))
ok("Coming soon to Rotterdam" in home and 'name="email"' in home, "visitors see the sign-up page")
ok('class="tabbar"' not in home and "Test mode" not in home, "sign-up page hides the tab bar and test-mode bar")
ok(visitor.get("/sell").status_code == 302 and visitor.get("/sell").location.endswith("/"), "other pages send visitors to the sign-up page")
ok(visitor.get("/terms").status_code == 200 and visitor.get("/privacy").status_code == 200 and visitor.get("/how-it-works").status_code == 200,
   "terms, privacy and how-it-works stay readable")
ok(visitor.get("/login").status_code == 200 and visitor.get("/robots.txt").status_code == 200, "sign-in (for admins) and robots.txt stay reachable")
ok(visitor.post("/tasks/hourly").status_code == 404, "hourly task still answers (404 without the secret, not a redirect)")
ok("Coming soon to Rotterdam" not in html(admin.get("/")) and "Coming-soon mode is on" in html(admin.get("/")),
   "admins see the full site with a reminder bar")

# --- Joining the waitlist
r = visitor.post("/waitlist", data={"email": "not-an-email", "interest": "sell", "_csrf": csrf(visitor)})
ok(r.status_code == 400 and "valid email" in html(r).lower(), "bad email is refused with a message")
r = visitor.post("/waitlist", data={"email": "Sam@Student.EUR.nl ", "interest": "sell", "_csrf": csrf(visitor)}, follow_redirects=True)
ok("on the list" in html(r) and "sam@student.eur.nl" in html(r), "joining shows a thank-you with the email")
ok("wa.me" in html(r), "thank-you page has a WhatsApp share button")
visitor.post("/waitlist", data={"email": "sam@student.eur.nl", "interest": "organise", "_csrf": csrf(visitor)})
with app.app_context():
    rows = A.db.all("SELECT * FROM waitlist")
ok(len(rows) == 1 and rows[0]["interest"] == "organise", "signing up twice updates instead of duplicating")
other = app.test_client(); other.get("/")
other.post("/waitlist", data={"email": "x@test.nl", "interest": "hack", "_csrf": csrf(other)})
with app.app_context():
    ok(A.db.one("SELECT interest FROM waitlist WHERE email='x@test.nl'")["interest"] == "buy", "unknown interest falls back to 'buy'")
ok(other.post("/waitlist", data={"email": "y@test.nl"}).status_code == 400, "sign-up needs the CSRF token")

# --- Preview link for friends
admin_page = html(admin.get("/admin"))
link = re.search(r'value="(http[^"]*/preview/[^"]+)"', admin_page)
ok(link is not None, "admin page shows the preview link")
path = "/preview/" + link.group(1).rsplit("/preview/", 1)[1]
ok(friend.get("/preview/wrong").status_code == 404, "a wrong preview code is refused")
friend.get(path)
ok("Coming soon to Rotterdam" not in html(friend.get("/")) and friend.get("/sell").status_code in (200, 302) and
   not friend.get("/how-it-works").location, "the preview link opens the full site")
admin.post("/admin/launch", data={"action": "new_link", "_csrf": csrf(admin)})
ok("Coming soon to Rotterdam" in html(friend.get("/")), "a new preview link switches the old one off")

# --- Admin list, CSV and launch email
wl = html(admin.get("/admin/waitlist"))
ok("sam@student.eur.nl" in wl and "x@test.nl" in wl, "admin sees the waitlist")
r = admin.get("/admin/waitlist?format=csv")
ok(r.mimetype == "text/csv" and "sam@student.eur.nl,organise" in html(r), "waitlist downloads as CSV")
ok(visitor.get("/admin/waitlist").status_code in (302, 404), "visitors can't see the waitlist")
admin.post("/admin/launch", data={"action": "open", "_csrf": csrf(admin)})
ok("Coming soon to Rotterdam" not in html(visitor.get("/")), "opening the site shows everyone the full site")
MAIL_WORKS[0] = True
r = admin.post("/admin/waitlist/notify", data={"_csrf": csrf(admin)}, follow_redirects=True)
ok(len(SENT) == 2 and "0 still to go" in html(r), "launch email goes to everyone on the list")
mail = [b for to, s, b in SENT if to == "sam@student.eur.nl"][0]
leave = re.search(r"(http\S+/waitlist/leave/\S+)", mail)
ok(leave is not None and "sparetickets.eu" in mail, "launch email has a remove-me link")
admin.post("/admin/waitlist/notify", data={"_csrf": csrf(admin)})
ok(len(SENT) == 2, "nobody gets the launch email twice")
visitor.get("/waitlist/leave/" + leave.group(1).rsplit("/", 1)[1])
with app.app_context():
    ok(A.db.one("SELECT COUNT(*) AS n FROM waitlist WHERE email='sam@student.eur.nl'")["n"] == 0, "the remove-me link deletes the entry")

# --- Marketing links (?from=) and QR codes
r = admin.post("/admin/sources", data={"label": "Flyers Maassilo", "_csrf": csrf(admin)}, follow_redirects=True)
ok("/?from=flyers-maassilo" in html(r) and "<svg" in html(r), "admin makes a link with a QR code (code made from the name)")
r = admin.post("/admin/sources", data={"label": "Again", "slug": "flyers-maassilo", "_csrf": csrf(admin)})
ok("already a link" in html(r), "the same code can't be used twice")
qr = admin.get("/admin/sources/flyers-maassilo/qr.svg")
ok(qr.mimetype == "image/svg+xml" and "attachment" in qr.headers.get("Content-Disposition", ""), "QR code downloads as a print-ready SVG")
ok(visitor.get("/admin/sources").status_code in (302, 404) and visitor.get("/admin/sources/flyers-maassilo/qr.svg").status_code in (302, 404),
   "visitors can't see the links page or QR codes")
admin.post("/admin/launch", data={"action": "coming_soon", "_csrf": csrf(admin)})
scan = app.test_client()
scan.get("/?from=flyers-maassilo"); scan.get("/?from=flyers-maassilo"); scan.get("/?from=ESN")
scan.post("/waitlist", data={"email": "scan@test.nl", "interest": "sell", "_csrf": csrf(scan)})
app.test_client().get("/?from=flyers-maassilo")
app.test_client().get("/?from=<script>")
with app.app_context():
    ok(A.db.one("SELECT source FROM waitlist WHERE email='scan@test.nl'")["source"] == "flyers-maassilo", "a sign-up remembers the first link they came from")
    ok(A.db.one("SELECT SUM(visits) AS n FROM source_visits WHERE source='flyers-maassilo'")["n"] == 2, "visits count once per browser, not per page load")
    ok(not A.db.one("SELECT 1 FROM source_visits WHERE source LIKE '%script%'"), "junk link codes are ignored")
page = html(admin.get("/admin/sources"))
ok("Flyers Maassilo" in page and "2</strong> visits" in page and "1</strong> sign-ups (50%)" in page, "links page shows visits, sign-ups and conversion")
ok(">esn" in page and "no name" in page, "codes nobody made (e.g. ?from=esn) still show up, unnamed")

print("FAILED:", fails or "none")
