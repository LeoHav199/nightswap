"""Checks for the automatic official prices (9 Nov 2026): the hourly task reads an upcoming event's own ticket page
and fills in its official price (#25) when it's missing, so Leo doesn't have to type them into /admin/events.
Only a match on date and name counts; prices set by hand are never overwritten; no real website is fetched here."""
import json, os, sys, tempfile
from datetime import timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATA_DIR = tmp; config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.TASK_SECRET = "secret"; config.PRICE_LOOKUP = True
import notify; notify.send_email = lambda *a, **kw: False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

def day(n): return (A.today() + timedelta(days=n)).isoformat()

def page(*events):
    """A ticket page with schema.org data, like Stager's: (name, date, [prices])."""
    data = [{"@type": "MusicEvent", "name": n, "startDate": d + "T20:00",
             "location": {"name": "Rotown", "address": {"addressLocality": "Rotterdam"}},
             "offers": [{"price": p, "url": "https://shop.example/t"} for p in prices]} for n, d, prices in events]
    return '<script type="application/ld+json">%s</script>' % json.dumps(data)

PAGES = {}
fetched = []
def fake_fetch(url, timeout=15):
    fetched.append(url)
    if url not in PAGES:
        raise A.importer.ImportError_("The website didn't respond.")
    return PAGES[url]
A.importer.fetch = fake_fetch

with app.app_context():
    run = A.db.run
    def add(name, when, url, price=None):
        run("INSERT INTO events (name, venue, city, event_date, ticket_url, face_value_max_cents) VALUES (?, 'Rotown', 'Rotterdam', ?, ?, ?)",
            name, when, url, price)
        return A.db.one("SELECT id FROM events WHERE name=?", name)["id"]
    def price(eid): return A.db.one("SELECT face_value_max_cents FROM events WHERE id=?", eid)["face_value_max_cents"]

    kiwi = add("Kiwi Jr.", day(2), "https://rotown.example/kiwi")
    brown = add("Martin Luke Brown", day(3), "https://rotown.example/brown")
    same_day = add("Grote Geelstaart", day(3), "https://rotown.example/geel")
    by_hand = add("Set By Hand", day(4), "https://rotown.example/hand", price=2500)
    far = add("Far Away Night", day(45), "https://rotown.example/far")
    past = add("Last Week", day(-3), "https://rotown.example/past")
    broken = add("Broken Page", day(5), "https://rotown.example/broken")
    no_data = add("No Data", day(6), "https://annabel.example/nodata")

    # Kiwi's page lists the venue's other nights too (as Stager pages do), with several ticket types.
    PAGES["https://rotown.example/kiwi"] = page(("Kiwi Jr.", day(2), [15, 18.5]),
                                                ("Martin Luke Brown", day(3), [18.5]),
                                                ("Grote Geelstaart", day(3), [15]),
                                                ("Set By Hand", day(4), [99]),
                                                ("Far Away Night", day(45), [30]))
    PAGES["https://annabel.example/nodata"] = "<html><body>No schema here</body></html>"

    filled = A.fill_official_prices()
    ok(price(kiwi) == 1850, "the highest ticket type becomes the official price (18.50)")
    ok(price(brown) == 1850 and price(same_day) == 1500, "two events on the same day each get their own price, matched by name")
    ok(fetched.count("https://rotown.example/kiwi") == 1 and "https://rotown.example/brown" not in fetched,
       "one page fills the other nights it lists, without fetching their pages again")
    ok(price(by_hand) == 2500, "a price set in /admin is never overwritten")
    ok(price(far) is None and "https://rotown.example/far" not in fetched, "events more than 30 days away are left alone")
    ok(price(past) is None and "https://rotown.example/past" not in fetched, "past events are left alone")
    ok(price(broken) is None and price(no_data) is None, "a page that fails or has no event data leaves the price empty")
    ok(filled == 3, "returns how many prices were filled (3)")

    # Looked at once a day at most
    fetched.clear()
    A.fill_official_prices()
    ok(fetched == [], "events already looked at today aren't fetched again")
    run("UPDATE events SET price_checked_at=? WHERE id=?", A.sqltime(A.utcnow() - timedelta(hours=25)), broken)
    PAGES["https://rotown.example/broken"] = page(("Broken Page", day(5), [12]))
    A.fill_official_prices()
    ok(price(broken) == 1200, "a day later the page is tried again")

    # A different name on the same day doesn't count, and silly prices are ignored
    odd = add("Mystery Act", day(7), "https://rotown.example/odd")
    PAGES["https://rotown.example/odd"] = page(("Someone Else Entirely", day(7), [20]))
    cheap = add("Free Entry Night", day(8), "https://rotown.example/free")
    PAGES["https://rotown.example/free"] = page(("Free Entry Night", day(8), [0]))
    A.fill_official_prices()
    ok(price(odd) is None, "a different event on the same day is not used")
    ok(price(cheap) is None, "a free (€0) ticket page sets no price")

    # Cancelled events are skipped
    gone = add("Cancelled Night", day(9), "https://rotown.example/gone")
    run("UPDATE events SET cancelled_at=? WHERE id=?", A.sqltime(A.utcnow()), gone)
    PAGES["https://rotown.example/gone"] = page(("Cancelled Night", day(9), [20]))
    A.fill_official_prices()
    ok(price(gone) is None, "cancelled events are skipped")

    # The time budget stops the run early
    for i in range(3):
        add("Slow %d" % i, day(10), "https://slow.example/%d" % i)
    fetched.clear()
    A.fill_official_prices(budget=-1)
    ok(fetched == [], "with no time left, nothing is fetched")

    # Off switch
    config.PRICE_LOOKUP = False
    ok(A.fill_official_prices() == 0, "NIGHTSWAP_PRICE_LOOKUP=0 switches it off")
    config.PRICE_LOOKUP = True

# Part of the hourly task, after the heartbeat is saved, and reported in its answer
c = app.test_client()
with app.app_context():
    late = add("Late Addition", day(11), "https://rotown.example/late")
PAGES["https://rotown.example/late"] = page(("Late Addition", day(11), [22]))
r = c.post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
ok(r.status_code == 200 and r.get_json().get("official_prices") == 1, "the hourly task fills prices and reports how many")
with app.app_context():
    ok(price(late) == 2200, "the hourly task saved the price")

def boom(*a, **kw): raise RuntimeError("network down")
real = A.importer.extract; A.importer.extract = boom
with app.app_context():
    add("Another One", day(12), "https://rotown.example/late")
r = c.post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
A.importer.extract = real
ok(r.status_code == 200, "a failing page never breaks the hourly task")

print("\n%d failed" % len(fails) if fails else "\nall passed")
