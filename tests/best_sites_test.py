"""Ideas from TicketSwap, DICE, Fixr and Shotgun (24 Nov 2026): a photo strip of this week's real photos, "Most wanted
this week" from real waiting counts, a Galas category, an organisers section, the home-screen tip from the second
visit, "sold on Spare" counts and a track record that only show once real, and following a venue."""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.TICKET_CHECKS = False; config.FACE_PROOF_REQUIRED = False; config.NEW_SELLER_LIMITS = False
import notify
mails = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: mails.append((to, subject, body)) and False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
admin, seller, fan, v = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(fan, "f@test.nl", "Fay"); v.get("/")
run("UPDATE users SET payouts_ready=1, phone='+31611111111', adult_confirmed_at=datetime('now') WHERE email='s@test.nl'")
def add(name, days, venue="Maassilo", cat="club"):
    d = (date.today() + timedelta(days=days)).isoformat()
    admin.post("/admin/events/save", data={"face_value_max": "500", "name": name, "venue": venue, "category": cat, "event_date": d, "_csrf": csrf(admin)})
    return d

# Galas
d_gala = add("Erasmus Winter Ball", 2, "Hotel New York", "gala")
page = html(v.get("/"))
ok(one("SELECT category FROM events WHERE name='Erasmus Winter Ball'")["category"] == "gala" and "Galas &amp; balls" in page, "galas are a category with their own chip")
ok("Gala / ball" in page.split('class="this-week"')[1] if 'class="this-week"' in page else False, "a gala card says it's a gala")

# Photo strip: only from 3 real photos/posters
ok('class="photo-strip"' not in page, "no photo strip without real photos")
for i, n in enumerate(("Pic One", "Pic Two", "Pic Three")):
    add(n, 3 + i)
    run("UPDATE events SET poster_file=? WHERE name=?", "%032x.jpg" % (i + 1), n)
page = html(v.get("/"))
strip = page.split('class="photo-strip"')[1].split("</ul>")[0] if 'class="photo-strip"' in page else ""
ok(all(n in strip for n in ("Pic One", "Pic Two", "Pic Three")) and "Erasmus Winter Ball" not in strip, "3 real posters: the strip shows them (no generated art)")

# Most wanted: real waiting counts, from 3
d_loud = add("Loudness", 4)
for i in range(3):
    run("INSERT INTO users (email, first_name) VALUES (?, 'W')", "w%d@t.nl" % i)
    run("INSERT INTO alerts (user_id, event_date, venue, event_name) SELECT id, ?, 'Maassilo', 'Loudness' FROM users WHERE email=?", d_loud, "w%d@t.nl" % i)
page = html(v.get("/"))
mw = page.split('class="most-wanted"')[1].split("</section>")[0] if 'class="most-wanted"' in page else ""
ok("Loudness" in mw and "3 looking" in mw, "3 people waiting: Most wanted this week shows it")
ok("Loudness" not in page.split('class="this-week"')[1].split("</section>")[0], "...and it isn't repeated under This week")

# Follow a venue, and hear about new tickets there
vslug = one("SELECT slug FROM venues WHERE name='Maassilo'")["slug"]
ok("Follow" in html(v.get("/venues/" + vslug)) and v.post("/venues/%s/follow" % vslug, data={"_csrf": csrf(v)}).status_code == 302, "a visitor sees Follow and is sent to log in")
r = fan.post("/venues/%s/follow" % vslug, data={"_csrf": csrf(fan)}, follow_redirects=True)
ok("You follow Maassilo" in html(r) and "Following" in html(r), "following a venue")
mails.clear()
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(one("SELECT id FROM events WHERE name='Pic One'")["id"]),
                               "face_value": "20", "price": "20", "ticket": (io.BytesIO(b"%PDF-1.4 x"), "t.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
ok(any(m[0] == "f@test.nl" and "New ticket at Maassilo" in m[1] for m in mails), "a follower is emailed when a ticket for that venue appears")
ok(not any(m[0] == "s@test.nl" and "New ticket at" in m[1] for m in mails), "the seller isn't told about their own ticket")
r = fan.post("/venues/%s/follow" % vslug, data={"_csrf": csrf(fan)}, follow_redirects=True)
ok("no longer follow" in html(r) and not one("SELECT 1 FROM venue_follows"), "unfollowing")

# Sold counts and track record only once real
lid = one("SELECT id FROM listings")["id"]
ev_url = "/event?venue=Maassilo&date=%s&name=Pic+One" % (date.today() + timedelta(days=3)).isoformat()
ok("sold on Spare" not in html(v.get(ev_url)), "no 'sold on Spare' line without sales")
buyer_id = one("SELECT id FROM users WHERE email='f@test.nl'")["id"]
for i in range(3):
    run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, paid_at, expires_at)
           SELECT ?, ?, ?, seller_id, 1, 2000, 199, 2199, 'paid', datetime('now'), datetime('now') FROM listings WHERE id=?""", "P%d" % i, lid, buyer_id, lid)
ok("3 tickets sold on Spare for this night" in html(v.get(ev_url)), "3 sold: the event page says so")
ok("resold on Spare" not in html(v.get("/")), "3 resold in total: too few for the homepage track record")
for i in range(22):
    run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, paid_at, expires_at)
           SELECT ?, ?, ?, seller_id, 1, 2000, 199, 2199, 'released', datetime('now'), datetime('now') FROM listings WHERE id=?""", "R%d" % i, lid, buyer_id, lid)
ok("25 tickets resold on Spare" in html(v.get("/")), "25 resold: the homepage shows the track record")

# Organisers, footer, home-screen tip
page = html(v.get("/about"))
ok('id="organisers"' in page and "Organisers and associations" in page, "the about page has an organisers section")
ok("/about#organisers" in html(v.get("/")) and "For organisers" in html(v.get("/")), "the footer links to it")
js = open("static/js/app.js").read()
ok("ns-visits" in js and "visits < 2" in js, "the home-screen tip waits for the second visit")

import i18n
from locales import fr, de, es
keys = ["cat_gala", "cat_gala_plural", "most_wanted_title", "home_stats_resold", "sold_on_spare", "follow_venue", "followed_venue",
        "mail_follow_subject", "mail_follow_body", "about_org_title", "about_org_body", "footer_organisers"]
for lang, table in (("nl", i18n.STRINGS["nl"]), ("fr", fr.STRINGS), ("de", de.STRINGS), ("es", es.STRINGS)):
    ok(all(table.get(k) for k in keys), "%s has the new texts" % lang)

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
