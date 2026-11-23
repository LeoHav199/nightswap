"""Discovery features (24 Nov 2026): "Can't find your party?" requests with an admin list that emails askers,
importing several event links at once, the shareable /this-week page, and a welcome line for ?from= visitors."""
import os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify
mails = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: mails.append((to, subject, body)) and False
import importer
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
admin, user, other, visitor = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(user, "u@test.nl", "Uma"); login(other, "o@test.nl", "Oli"); visitor.get("/")

# --- 1. Can't find your party?
page = html(visitor.get("/?q=loudness"))
ok("Nothing found for" in page and "/request-event?name=loudness" in page, "a search with no results offers to ask for the party")
ok("Can&#39;t find your party?" in html(visitor.get("/")), "the homepage has a 'Can't find your party?' link")
page = html(visitor.get("/request-event?name=Loudness"))
ok('value="Loudness"' in page and "Log in to ask" in page, "the form is prefilled; a visitor is asked to log in")
r = visitor.post("/request-event", data={"name": "Loudness", "_csrf": csrf(visitor)})
ok(r.status_code == 302 and "/login" in r.location and one("SELECT COUNT(*) AS n FROM event_requests")["n"] == 0, "posting without login saves nothing")
r = user.post("/request-event", data={"name": "  Loudness  ", "venue": "Maassilo", "_csrf": csrf(user)}, follow_redirects=True)
ok("try to add Loudness" in html(r) and one("SELECT name, venue FROM event_requests")["name"] == "Loudness", "a signed-in visitor asks; thanks shown")
user.post("/request-event", data={"name": "loudness", "_csrf": csrf(user)})
ok(one("SELECT COUNT(*) AS n FROM event_requests")["n"] == 1, "asking twice for the same party counts once")
other.post("/request-event", data={"name": "Loudness", "_csrf": csrf(other)})
r = user.post("/request-event", data={"name": "X", "_csrf": csrf(user)})
ok("Type the name" in html(r), "a too-short name is refused")
r = user.post("/request-event", data={"name": "Old Party", "date": "2020-01-01", "_csrf": csrf(user)})
ok("from today on" in html(r), "a date in the past is refused")
ok(user.get("/admin/requests").status_code in (302, 403, 404), "only admins see the requests")
page = html(admin.get("/admin/requests"))
ok("Loudness" in page and "2 people" in page and "Maassilo" in page, "the admin list groups requests and counts people")
mails.clear()
admin.post("/admin/requests/done", data={"k": "loudness", "action": "added", "_csrf": csrf(admin)})
sent = sorted(m[0] for m in mails if "is on Spare now" in m[1])
ok(sent == ["o@test.nl", "u@test.nl"] and all("q=Loudness" in m[2] for m in mails if "is on Spare now" in m[1]), "'Added' emails everyone who asked, with a link")
ok(one("SELECT COUNT(*) AS n FROM event_requests WHERE notified_at IS NULL")["n"] == 0 and "No open requests" in html(admin.get("/admin/requests")), "and closes the request")

# --- 2. Several links at once
pages = {"https://a.example/1": [{"name": "Night One", "venue": "Perron", "event_date": (date.today() + timedelta(days=3)).isoformat(), "city": "Rotterdam", "category": "club"}],
         "https://b.example/2": [{"name": "Night Two", "venue": "Toffler", "event_date": (date.today() + timedelta(days=4)).isoformat(), "city": "Rotterdam", "category": "club"},
                                 {"name": "Night One", "venue": "Perron", "event_date": (date.today() + timedelta(days=3)).isoformat(), "city": "Rotterdam", "category": "club"}]}
def fake_fetch(url):
    if url not in pages: raise importer.ImportError_("couldn't open that page")
    return url
importer.fetch = fake_fetch
importer.extract = lambda page, url="": [dict(e) for e in pages[page]]
r = admin.post("/admin/events/import", data={"urls": "https://a.example/1\nhttps://b.example/2\nhttps://broken.example/x", "_csrf": csrf(admin)})
page = html(r)
ok("Found 2 events" in page and "3 links" in page and "broken.example" in page, "several links in one go: events found, duplicates merged, the broken link reported")
import html as H, json
payload = H.unescape(re.search(r'name="payload" value="([^"]*)"', page).group(1))
ok(sorted(e["source_url"] for e in json.loads(payload)) == ["https://a.example/1", "https://b.example/2"], "each event remembers which link it came from")
admin.post("/admin/events/import/confirm", data={"payload": payload, "pick": ["0", "1"], "_csrf": csrf(admin)})
ok(one("SELECT source_url FROM events WHERE name='Night Two'")["source_url"] == "https://b.example/2", "added events keep their own source link")

# --- 3. This week
page = html(visitor.get("/this-week"))
ok("This week in Rotterdam" in page and "Night One" in page and "Night Two" in page and "wa.me" in page, "the week page lists the events and has a WhatsApp share")
ok('property="og:title" content="This week in Rotterdam"' in page, "it has its own link preview title")
ok("/this-week" in html(visitor.get("/")), "the homepage links to the full week")

# --- 4. Welcome line
ok("Welcome to Spare!" in html(app.test_client().get("/?from=esn")), "a visitor from one of our links gets a welcome line")
ok("Welcome to Spare!" not in html(visitor.get("/")), "...nobody else does")

import i18n
from locales import fr, de, es
keys = [k for k in i18n.STRINGS["en"] if k.startswith(("request_", "tw_", "cant_find", "search_none", "welcome_from", "mail_request_", "err_request_"))]
for lang, table in (("nl", i18n.STRINGS["nl"]), ("fr", fr.STRINGS), ("de", de.STRINGS), ("es", es.STRINGS)):
    ok(all(table.get(k) for k in keys), "%s has all %d new texts" % (lang, len(keys)))

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
