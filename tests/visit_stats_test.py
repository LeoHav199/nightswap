"""Checks for the visit counts (16 Nov 2026): page views per day per page type, visitors once a day per session,
checkouts started and orders paid, counted on our own server with no cookies of their own, no IPs and no user ids.
Robots, link previews, admins, /static, /health, /tasks and webhooks aren't counted; /admin/stats shows the last
14 days plus visits per marketing link; the hourly task deletes counts older than 180 days."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.TICKET_CHECKS = False; config.FACE_PROOF_REQUIRED = False; config.NEW_SELLER_LIMITS = False  # (covered in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.TASK_SECRET = "secret"
import notify
notify.send_email = lambda to, subject, body, attachments=(), **kw: False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    with app.app_context(): A.db.run("DELETE FROM hits WHERE key LIKE 'login%' OR key LIKE 'verify%'")
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(c)})
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)
TODAY = date.today().isoformat()
def stat(kind, day=TODAY):
    r = one("SELECT n FROM site_stats WHERE day=? AND kind=?", day, kind)
    return r["n"] if r else 0
def views(): return (one("SELECT COALESCE(SUM(n), 0) AS n FROM site_stats WHERE day=? AND kind LIKE 'view_%'", TODAY))["n"]
PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"

# --- Setup: an admin, a seller with an event and a ticket, a buyer
admin, seller, buyer = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
verify_phone(seller, "0611111111")
run("UPDATE users SET payouts_ready=1 WHERE email='s@test.nl'")
admin.post("/admin/events/save", data={"name": "Count Night", "venue": "Perron", "category": "club", "event_date": TODAY,
                                       "door_time": "23:00", "_csrf": csrf(admin)})
ev = one("SELECT * FROM events WHERE name='Count Night'")
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20",
                               "price": "25", "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 " + os.urandom(8)), "t.pdf"),
                               "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
run("DELETE FROM site_stats")  # start counting from here

# --- A new visitor from a phone: counted once a day, page views per page type
v = app.test_client(); v.environ_base["HTTP_USER_AGENT"] = PHONE
v.get("/")
ok(stat("visitors") == 1 and stat("view_home") == 1, "first page: one visitor, one home page view")
v.get("/"); v.get("/t/%d" % lid); v.get("/how-it-works"); v.get("/help"); v.get("/about")
ok(stat("visitors") == 1, "the same visitor counts once a day, however many pages")
ok(stat("view_home") == 2 and stat("view_listing") == 1 and stat("view_how") == 1 and stat("view_help") == 1 and stat("view_other") == 1,
   "page views by type: home, ticket page, how it works, help, other")
ok(views() == 6, "six page views in all")
r = v.get("/event", query_string={"venue": "Perron", "date": TODAY, "name": "Count Night"})
ok(r.status_code == 200 and stat("view_event") == 1, "event page counted as 'event'")
with v.session_transaction() as s:
    ok(s.get("vd") == TODAY and set(s.keys()) <= {"csrf", "vd", "lang", "_fresh", "seen_sources", "source", "tonight"},
       "the session only holds today's date for counting (no id)")
with v.session_transaction() as s: s["vd"] = (date.today() - timedelta(days=1)).isoformat()
v.get("/")
ok(stat("visitors") == 2, "seen yesterday, back today: counted again for today")
v2 = app.test_client(); v2.environ_base["HTTP_USER_AGENT"] = PHONE
v2.get("/help")
ok(stat("visitors") == 3, "a second phone is a second visitor")

# --- Nothing personal stored: the table only has day, kind and a number
cols = [r[1] for r in A.sqlite3.connect(config.DATABASE).execute("PRAGMA table_info(site_stats)")]
ok(cols == ["day", "kind", "n"], "site_stats has only day, kind and count (no IP, no user id)")
r = v.get("/")
ok(not any("vd" in h or "visit" in h.lower() for h in r.headers.getlist("Set-Cookie")) and
   all(h.startswith("session=") for h in r.headers.getlist("Set-Cookie")), "no cookie of its own, only Spare's session")

# --- Not counted: robots, link previews, admins, static, health, tasks, webhooks, redirects, errors, POSTs
before_v, before_views = stat("visitors"), views()
for ua in ["WhatsApp/2.24.1 A", "Googlebot/2.1 (+http://www.google.com/bot.html)", "facebookexternalhit/1.1",
           "TelegramBot (like TwitterBot)", "Mozilla/5.0 (compatible; bingbot/2.0)", "curl/8.4.0", "python-requests/2.31",
           "UptimeRobot/2.0", "Mozilla/5.0 (Macintosh) HeadlessChrome/120.0", ""]:
    b = app.test_client(); b.environ_base["HTTP_USER_AGENT"] = ua
    b.get("/"); b.get("/t/%d" % lid)
ok(stat("visitors") == before_v and views() == before_views, "robots and link previews (WhatsApp, Google, curl...) aren't counted")
admin.get("/"); admin.get("/admin"); admin.get("/admin/stats"); admin.get("/t/%d" % lid)
ok(views() == before_views, "admins and admin pages aren't counted")
v.get("/static/css/style.css"); v.get("/health"); v.get("/robots.txt"); v.get("/sitemap.xml"); v.get("/manifest.webmanifest")
v.post("/tasks/hourly", headers={"X-Task-Secret": "secret"}); v.post("/webhooks/stripe", data="{}")
ok(views() == before_views, "/static, /health, /robots.txt, /tasks and webhooks aren't counted")
v.get("/no-such-page"); v.get("/t/999999"); v.get("/lang/nl?next=/")
ok(views() == before_views, "404s and redirects aren't page views")
v.get("/lang/en?next=/")

# --- Marketing links: bots don't count either
p = app.test_client(); p.environ_base["HTTP_USER_AGENT"] = "WhatsApp/2.24.1 A"
p.get("/?from=esn-whatsapp")
ok(not one("SELECT 1 FROM source_visits WHERE source='esn-whatsapp'"), "WhatsApp's link preview of a ?from= link isn't a visit")
s1 = app.test_client(); s1.environ_base["HTTP_USER_AGENT"] = PHONE
s1.get("/?from=esn-whatsapp"); s1.get("/?from=esn-whatsapp")
ok(one("SELECT SUM(visits) AS n FROM source_visits WHERE source='esn-whatsapp'")["n"] == 1, "a person opening the link counts once")

# --- Checkouts started and orders paid
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); pub = r.location.split("/")[2]
ok(stat("checkout") == 1 and stat("paid") == 0, "pressing Buy counts a checkout started")
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
ok(one("SELECT status FROM orders WHERE public_id=?", pub)["status"] == "paid" and stat("paid") == 1, "a paid order counts once")
with app.test_request_context():
    A.mark_paid(one("SELECT * FROM orders WHERE public_id=?", pub))
ok(stat("paid") == 1, "marking it paid again (webhook after the success page) doesn't count twice")

# --- Counting never breaks a page
real = A.db.get
class Broken:
    def __init__(self, conn): self.conn = conn
    def executemany(self, *a): raise A.sqlite3.OperationalError("database is locked")
    def rollback(self): pass
    def __getattr__(self, k): return getattr(self.conn, k)
A.db.get = lambda: Broken(real()); app.logger.disabled = True
try:
    r = v2.get("/help")
finally:
    A.db.get = real; app.logger.disabled = False
ok(r.status_code == 200 and "help" in html(r).lower(), "a failing count still shows the page")

# --- /admin/stats: admins only, the last 14 days, the links
ok(v.get("/admin/stats").status_code in (302, 403, 404), "visitors can't open /admin/stats")
ok("/admin/stats" in html(admin.get("/admin")), "/admin links to Visitors")
run("INSERT INTO site_stats (day, kind, n) VALUES (?, 'visitors', 7)", (date.today() - timedelta(days=13)).isoformat())
run("INSERT INTO site_stats (day, kind, n) VALUES (?, 'visitors', 99)", (date.today() - timedelta(days=14)).isoformat())
run("INSERT INTO sources (slug, label) VALUES ('esn-whatsapp', 'ESN WhatsApp group')")
page = html(admin.get("/admin/stats"))
ok("Last 14 days" in page and "Today" in page and "Yesterday" in page, "/admin/stats shows the last 14 days")
rows = re.findall(r"<tr[^>]*><th scope=\"row\">", page.split("<tbody>")[1].split("</tbody>")[0])
ok(len(rows) == 14, "exactly 14 day rows")
for col in ["Visitors", "Pages seen", "Ticket pages", "Checkouts started", "Orders paid"]:
    ok(col in page, "column: " + col)
tfoot = page.split("<tfoot>")[1].split("</tfoot>")[0]
ok(re.findall(r"<td>(\d+)</td>", tfoot)[0] == str(stat("visitors") + 7), "total visitors includes 13 days ago but not 14")
ok("ESN WhatsApp group" in page and "?from=esn-whatsapp" in page, "visits per marketing link, with its name")
ok("Event pages" in page and "Ticket pages" in page and "Help" in page, "views per page type")

# --- The hourly task deletes counts older than 180 days, and keeps the marketing link history
old, keep = (date.today() - timedelta(days=181)).isoformat(), (date.today() - timedelta(days=179)).isoformat()
run("INSERT INTO site_stats (day, kind, n) VALUES (?, 'visitors', 5)", old)
run("INSERT INTO site_stats (day, kind, n) VALUES (?, 'visitors', 5)", keep)
run("INSERT INTO source_visits (source, day, visits) VALUES ('old-flyer', ?, 3)", old)
r = admin.post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
ok(r.status_code == 200 and "tidy" not in r.get_json()["failed"], "the hourly task runs")
ok(stat("visitors", old) == 0 and stat("visitors", keep) == 5, "counts older than 180 days are deleted, newer ones stay")
ok(one("SELECT 1 FROM source_visits WHERE source='old-flyer'"), "all-time visits per marketing link are kept")

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
