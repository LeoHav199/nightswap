"""Coming-soon mode, page by page, and the launch-day switch (on, off, on, off).
Every GET page of the app is either PUBLIC (listed here with an example address) or a MEMBER page (sign-in or admin needed, told apart by
its address). A new page that is in neither list fails, so whoever adds a page has to decide what visitors get before launch.
While coming-soon mode is on, a visitor gets 200 or a redirect to the sign-up page on every public page; /health, /robots.txt,
/sitemap.xml, /help, /terms, /privacy, the preview link and the Stripe webhook are never redirected."""
import os, re, sys, tempfile
from urllib.parse import urlencode
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATA_DIR = tmp; config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.TASK_SECRET = "secret"; config.JARVIS_FEED_TOKEN = ""
import notify; notify.send_email = lambda *a, **kw: False  # sign-in codes show on screen, like in development
import app as A, seed
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
def setting(key):
    with app.app_context(): return A.get_setting(key)
def switch(action):
    r = admin.post("/admin/launch", data={"action": action, "_csrf": csrf(admin)})
    return r.status_code == 302

with app.app_context():
    seed.run()
    listing = A.db.one("""SELECT l.id, l.venue, l.event_date, l.event_name FROM listings l JOIN tickets t ON t.listing_id = l.id
                          WHERE l.status='active' AND t.status='available' AND l.venue='Annabel' ORDER BY l.id""")
    venue = A.db.one("SELECT slug FROM venues WHERE name='Annabel'")
event = "/event?" + urlencode({"venue": listing["venue"], "date": listing["event_date"], "name": listing["event_name"] or ""})
LID = listing["id"]

# --- Public pages: endpoint -> (example address, statuses that are right once the site is open).
PUBLIC = {
    "static": ("/static/js/app.js", {200}),
    "event_art": ("/art.svg?k=annabel", {200}),
    "index": ("/", {200}),
    "search_suggest": ("/api/search?q=annabel", {200}),
    "event_page": (event, {200}),
    "venue_page": ("/venues/" + venue["slug"], {200}),
    "sitemap": ("/sitemap.xml", {200}),
    "robots": ("/robots.txt", {200}),
    "poster": ("/posters/" + "0" * 32 + ".png", {404}),  # no such file
    "listing_page": ("/t/%d" % LID, {200}),
    "report_listing": ("/t/%d/report" % LID, {200}),
    "events_on_date": ("/api/events?date=" + listing["event_date"], {200}),
    "login": ("/login", {200}),
    "login_verify": ("/login/verify", {200, 302}),  # no sign-in in progress: back to /login
    "set_lang": ("/lang/en", {302}),  # English is the default, so visiting it doesn't change the page texts this test looks for
    "push_key": ("/api/push/key", {200}),
    "manifest": ("/manifest.webmanifest", {200}),
    "service_worker": ("/sw.js", {200}),
    "favicon": ("/favicon.ico", {200}),
    "offline": ("/offline", {200}),
    "credits": ("/credits", {200}),
    "about": ("/about", {200}),
    "how": ("/how-it-works", {200}),
    "help_page": ("/help", {200}),
    "terms": ("/terms", {200}),
    "privacy": ("/privacy", {200}),
    "waitlist_leave": ("/waitlist/leave/nobody", {302}),
    "emails_stop": ("/emails/stop/not-a-token", {404}),
    "jarvis_feed": ("/api/jarvis", {404}),  # no token: the page doesn't exist
    "health": ("/health", {200}),  # (503 "late" if the hourly task hadn't run: health_test.py)
}
# Pages that must never be sent to the sign-up page, whatever the mode (what Leo and the outside services rely on).
STAY_OPEN = ["health", "robots", "sitemap", "help_page", "terms", "privacy", "how", "about", "credits", "login", "static",
             "manifest", "service_worker", "offline", "set_lang", "waitlist_leave", "emails_stop", "jarvis_feed", "login_verify"]
# Public pages that visitors get to see only once the site is open: in coming-soon mode they go to the sign-up page.
SIGNUP_ONLY = ["event_art", "search_suggest", "event_page", "venue_page", "poster", "listing_page", "report_listing", "events_on_date",
               "push_key", "favicon"]
# Everything else that has a GET address needs a sign-in (or admin): told apart by the start of the address.
MEMBER_PREFIXES = ("/admin", "/account", "/orders", "/threads", "/messages", "/checkout", "/sell", "/settings", "/events/")

def example(rule):
    """An example address for a route with <placeholders>."""
    return re.sub(r"<[^>]+>", "x", re.sub(r"<int:[^>]+>", "1", rule))

routes = [(r.endpoint, r.rule) for r in app.url_map.iter_rules() if "GET" in r.methods]
member = [(e, example(rule)) for e, rule in routes if e not in PUBLIC and e != "preview"]
stray = [e for e, path in member if not path.startswith(MEMBER_PREFIXES)]
ok(not stray, "every page is listed as public or is a member page (new pages must be classified here: %s)" % ", ".join(stray))
ok(all(e in {r.endpoint for r in app.url_map.iter_rules()} for e in PUBLIC) and
   all(e in A.OPEN_WHILE_COMING_SOON for e in STAY_OPEN) and
   all(e in {r.endpoint for r in app.url_map.iter_rules()} for e in A.OPEN_WHILE_COMING_SOON),
   "the lists in this test and OPEN_WHILE_COMING_SOON only name pages that exist, and the stay-open pages are all in it")
ok(not [e for e in SIGNUP_ONLY if e in A.OPEN_WHILE_COMING_SOON] and set(STAY_OPEN) | set(SIGNUP_ONLY) <= set(PUBLIC),
   "sign-up-only pages are not in OPEN_WHILE_COMING_SOON")
ok(set(PUBLIC) - set(STAY_OPEN) - set(SIGNUP_ONLY) == {"index"},
   "every public page is a stay-open page, a sign-up-only page, or the home page")

admin, visitor, friend = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); visitor.get("/"); friend.get("/")

def pages(client):
    """Status and redirect target of every public page (not the preview link) for this browser."""
    out = {}
    for endpoint, (path, _) in PUBLIC.items():
        r = client.get(path)
        out[endpoint] = (r.status_code, r.location)
    return out

def shows_signup(client):
    page = html(client.get("/"))
    return "Coming soon to Rotterdam" in page and 'name="email"' in page and '<meta name="robots" content="noindex">' in page

def webhook():
    """The Stripe webhook only answers when Stripe is on, so the key is set for this one request. A bad signature is a 400, not a redirect."""
    config.STRIPE_SECRET_KEY = "sk_test_x"
    try:
        c = app.test_client()
        return c.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=bad"}, content_type="application/json")
    finally:
        config.STRIPE_SECRET_KEY = ""

def must_stay_reachable(label):
    """/health, /robots.txt, /sitemap.xml, /help, /terms, /privacy, the preview link and the Stripe webhook."""
    c = app.test_client()
    for path in ("/health", "/robots.txt", "/sitemap.xml", "/help", "/terms", "/privacy"):
        r = c.get(path)
        ok(r.status_code == 200 and not r.location, "%s: %s answers itself, no redirect (%s)" % (label, path, r.status_code))
    code = setting("preview_code")
    r = c.get("/preview/" + (code or "none"))
    ok((r.status_code == 302 and r.location.endswith("/")) if code else r.status_code == 404, "%s: the preview link is handled by itself (%s)" % (label, r.status_code))
    r = c.get("/preview/wrong-code")
    ok(r.status_code == 404, "%s: a wrong preview code is a 404, not the sign-up page" % label)
    r = webhook()
    ok(r.status_code == 400 and not r.location, "%s: the Stripe webhook is reached (bad signature: 400, no redirect)" % label)
    r = c.post("/webhooks/stripe", data=b"{}", content_type="application/json")
    ok(r.status_code == 404 and not r.location, "%s: with Stripe switched off the webhook is a plain 404" % label)
    r = c.post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
    ok(r.status_code == 200, "%s: the hourly task runs" % label)
    r = c.post("/tasks/hourly")
    ok(r.status_code == 404 and not r.location, "%s: the hourly task without its secret is a 404, not a redirect" % label)
    r = c.get("/health")
    ok(r.status_code == 200 and html(r) == "ok", "%s: /health says ok after the hourly task" % label)

def member_pages_closed(client, label):
    bad = []
    for e, path in member:
        r = client.get(path)
        if r.status_code not in (302, 404) or (r.status_code == 302 and not (r.location.endswith("/") or "/login" in r.location)):
            bad.append("%s -> %s" % (path, r.status_code))
    ok(not bad, "%s: member and admin pages give a visitor nothing (%d checked)%s" % (label, len(member), (": " + ", ".join(bad)) if bad else ""))

# --- 1. Site open (the default): every public page gives what it should
app.test_client().post("/tasks/hourly", headers={"X-Task-Secret": "secret"})  # so /health is "ok" from the start
open_pages = pages(visitor)
wrong = ["%s %s" % (e, open_pages[e]) for e, (p, good) in PUBLIC.items() if open_pages[e][0] not in good]
ok(not wrong, "site open: every public page gives the right answer (%d pages)%s" % (len(PUBLIC), (": " + ", ".join(wrong)) if wrong else ""))
ok(not shows_signup(visitor), "site open: the home page is the normal site")
ok(setting("coming_soon") != "1" and setting("preview_code") is None, "site open: nothing switched on yet")
member_pages_closed(visitor, "site open")
must_stay_reachable("site open")

# --- 2. Launch-day switch: coming-soon ON
ok(switch("coming_soon"), "admin switches coming-soon mode on")
code = setting("preview_code")
ok(setting("coming_soon") == "1" and code, "the setting is saved and a preview code is made")
cs_pages = pages(visitor)
bad = []
for e, (path, good) in PUBLIC.items():
    status, loc = cs_pages[e]
    if e in SIGNUP_ONLY:
        if not (status == 302 and loc.endswith("/") and "/login" not in loc):
            bad.append("%s should go to the sign-up page, got %s %s" % (e, status, loc))
    elif status != open_pages[e][0]:
        bad.append("%s changed from %s to %s" % (e, open_pages[e][0], status))
ok(not bad, "coming-soon: every public page is 200 (as before) or redirected to the sign-up page%s" % ((": " + "; ".join(bad)) if bad else ""))
ok(cs_pages["index"][0] == 200 and shows_signup(visitor), "coming-soon: the home page is the sign-up page, hidden from search engines")
ok(all(cs_pages[e] == open_pages[e] for e in STAY_OPEN), "coming-soon: all %d stay-open pages answer exactly as before" % len(STAY_OPEN))
member_pages_closed(visitor, "coming-soon")
must_stay_reachable("coming-soon")
r = visitor.get("/sitemap.xml")
ok("/help" in html(r) and "/venues/" not in html(r) and "event?" not in html(r), "coming-soon: the sitemap only lists the public pages")
r = visitor.post("/waitlist", data={"email": "fan@student.eur.nl", "interest": "buy", "_csrf": csrf(visitor)}, follow_redirects=True)
ok(r.status_code == 200 and "on the list" in html(r), "coming-soon: the sign-up form works")
ok(visitor.get("/sell").status_code == 302 and visitor.get("/sell").location.endswith("/"), "coming-soon: /sell sends a visitor to the sign-up page")
ok(visitor.get("/account").status_code == 302 and visitor.get("/account").location.endswith("/"), "coming-soon: /account too")

# Admin and friend with the preview link: the full site, the same answers as when the site is open
admin_pages = pages(admin)
ok(all(admin_pages[e][0] in good | ({302} if e == "login" else set()) for e, (p, good) in PUBLIC.items()),  # signed in: /login goes on to the site
   "coming-soon: an admin gets every public page")
ok(not shows_signup(admin), "coming-soon: an admin sees the full site")
r = friend.get("/preview/" + code)
ok(r.status_code == 302 and r.location.endswith("/"), "coming-soon: the preview link works")
friend_pages = pages(friend)
ok(all(friend_pages[e][0] in good for e, (p, good) in PUBLIC.items()) and not shows_signup(friend),
   "coming-soon: a friend with the preview link gets every public page")

# --- 3. Launch day: switch OFF
ok(switch("open"), "admin switches coming-soon mode off")
ok(setting("coming_soon") == "0", "the setting is saved")
now_pages = pages(visitor)
ok(now_pages == open_pages, "launched: a visitor gets the same answer on every public page as before the switch (%d pages)" % len(PUBLIC))
ok(not shows_signup(visitor), "launched: the home page is the normal site again")
r = visitor.get("/")
ok("venues/" + venue["slug"] in html(r) or "Annabel" in html(r), "launched: the home page shows the events")
r = visitor.get("/sitemap.xml")
ok("/venues/" + venue["slug"] in html(r) and "event?" in html(r), "launched: the sitemap lists venues and events again")
ok(visitor.get("/sell").status_code == 302 and "/login" in visitor.get("/sell").location, "launched: /sell asks a visitor to sign in (not the sign-up page)")
ok(visitor.get("/account").status_code == 302 and "/login" in visitor.get("/account").location, "launched: /account too")
member_pages_closed(visitor, "launched")
must_stay_reachable("launched")
ok(pages(friend) == open_pages, "launched: the friend sees the same as everyone")

# --- 4. Switching back on and off again (a rollback) keeps the same preview link
ok(switch("coming_soon") and shows_signup(app.test_client()) and setting("preview_code") == code,
   "switched on again: the sign-up page is back and the preview link is the same")
ok(app.test_client().get("/preview/" + code).status_code == 302, "switched on again: the old preview link still works")
ok(switch("open") and not shows_signup(app.test_client()) and pages(app.test_client()) == open_pages, "switched off again: the full site is back")

# --- 5. Test-mode lock (#22): the live site with Stripe still in test mode behaves like coming-soon mode, whatever the setting says
saved = (config.DEV, config.BASE_URL); config.DEV = False; config.BASE_URL = ""; config.STRIPE_SECRET_KEY = "sk_test_x"
try:
    locked = app.test_client()
    ok(setting("coming_soon") == "0" and shows_signup(locked), "test-mode lock: the live site with Stripe test keys shows the sign-up page")
    locked_pages = pages(locked)
    ok(all(locked_pages[e][0] == (302 if e in SIGNUP_ONLY else open_pages[e][0]) for e in PUBLIC), "test-mode lock: public pages give 200 or the sign-up page")
finally:
    config.DEV, config.BASE_URL = saved; config.STRIPE_SECRET_KEY = ""

print("FAILED:", fails or "none")
