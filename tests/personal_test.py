import io, os, re, sys, tempfile
from datetime import date, datetime
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; notify.send_email = lambda *a: False
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

class Evening(datetime):
    @classmethod
    def now(cls, tz=None): return datetime.combine(date.today(), datetime.min.time()).replace(hour=20)
class Morning(datetime):
    @classmethod
    def now(cls, tz=None): return datetime.combine(date.today(), datetime.min.time()).replace(hour=9)

admin, seller, buyer, visitor = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo"); visitor.get("/")

# --- Settings page and profile icons
ok(visitor.get("/settings").status_code == 200 and "Tap sounds" in html(visitor.get("/settings")), "settings page opens for everyone (sounds)")
ok("Log in to pick a profile icon" in html(visitor.get("/settings")), "visitors are asked to log in for a profile icon")
page = html(seller.get("/settings"))
ok('value="off" checked' in page, "tap sounds are off by default")
ok(len(A.avatars.CHOICES) >= 12 and len(set(A.avatars.CHARACTERS.values())) == len(A.avatars.CHARACTERS), "at least 12 characters, all different")
ok(page.count('name="avatar"') == 1 and page.count("is-locked") == len(A.avatars.CHOICES) and "Sell your first ticket" in page,
   "no sales yet: every character is locked, with a nudge to sell")
seller.post("/settings", data={"avatar": "sunny", "_csrf": csrf(seller)})
with app.app_context(): ok(A.db.one("SELECT avatar FROM users WHERE email='s@test.nl'")["avatar"] is None, "a locked character can't be saved")
with app.app_context(): A.db.run("UPDATE users SET avatar='crown' WHERE email='s@test.nl'")
ok('nav-avatar has-icon' in html(seller.get("/")), "someone who picked an icon from the first version still shows a character")
with app.app_context(): A.db.run("UPDATE users SET avatar=NULL WHERE email='s@test.nl'")
ok(visitor.post("/settings", data={"avatar": "sunny", "_csrf": csrf(visitor)}).status_code == 302, "saving a character needs an account")

# --- A ticket for tonight
today = date.today().isoformat()
admin.post("/admin/events/save", data={"name": "Tonight Rave", "venue": "Maassilo", "category": "club", "event_date": today, "_csrf": csrf(admin)})
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Tonight Rave'")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
    "ticket": (io.BytesIO(b"%PDF-1.4 tonight"), "a.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
def sold(n):
    with app.app_context():
        s_id = A.db.one("SELECT id FROM users WHERE email='s@test.nl'")["id"]; b_id = A.db.one("SELECT id FROM users WHERE email='b@test.nl'")["id"]
        for _ in range(n):
            A.db.run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, expires_at)
                        VALUES (?, ?, ?, ?, 1, 2000, 160, 2160, 'released', '2026-01-01 00:00:00')""", A.secrets.token_urlsafe(6), lid, b_id, s_id)
sold(1)
page = html(seller.get("/settings"))
ok(page.count('name="avatar"') == 6 and "Next icon at 3" in page, "1st sale unlocks the first 5 characters and shows the next goal")
seller.post("/settings", data={"avatar": "dj", "_csrf": csrf(seller)})
with app.app_context(): ok(A.db.one("SELECT avatar FROM users WHERE email='s@test.nl'")["avatar"] is None, "the DJ stays locked after 1 sale")
seller.post("/settings", data={"avatar": "sunny", "_csrf": csrf(seller)})
with app.app_context(): ok(A.db.one("SELECT avatar FROM users WHERE email='s@test.nl'")["avatar"] == "sunny", "an unlocked character saves")
ok('nav-avatar has-icon' in html(seller.get("/")), "the header shows the earned icon")
with app.app_context():
    A.db.run("INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, expires_at) "
             "SELECT 'refunded1', listing_id, buyer_id, seller_id, 9, 2000, 160, 2160, 'refunded', expires_at FROM orders LIMIT 1")
ok("Next icon at 3" in html(seller.get("/settings")), "refunded sales don't count")
sold(9)
ok("Every icon unlocked" in html(seller.get("/settings")), "10 sales unlock everything, including the DJ")
seller.post("/settings", data={"avatar": "dj", "_csrf": csrf(seller)})
listing = html(buyer.get("/t/%d" % lid))
ok('avatar has-icon' in listing and "<svg" in listing.split("avatar has-icon")[1][:80], "the seller's icon shows on their listing")

# --- "Tonight" pop-up
real = A.datetime
A.datetime = Evening
home = html(buyer.get("/"))
ok("data-tonight-pop" in home and "Tonight Rave" in home.split("data-tonight-pop")[1] and "1 ticket for" in home, "evening: the pop-up offers tonight's event")
ok("data-tonight-pop" not in html(buyer.get("/sell")) and "data-tonight-pop" not in html(buyer.get("/t/%d" % lid)),
   "no pop-up while selling or on a listing (never in the way of buying)")
with app.app_context(): A.set_setting("coming_soon", "1")
ok("data-tonight-pop" not in html(visitor.get("/")), "no pop-up on the coming-soon page")
with app.app_context(): A.set_setting("coming_soon", "0")
A.datetime = Morning
ok("data-tonight-pop" not in html(buyer.get("/")), "morning: no pop-up yet")
A.datetime = real

# --- "People looking" count
from datetime import timedelta
day = (date.today() + timedelta(days=5)).isoformat()
admin.post("/admin/events/save", data={"name": "Demand Night", "venue": "Perron", "category": "club", "event_date": day, "_csrf": csrf(admin)})
with app.app_context(): dn = A.db.one("SELECT * FROM events WHERE name='Demand Night'")
url = "/event?venue=Perron&date=%s&name=Demand+Night" % day
ok("looking for a ticket" not in html(visitor.get(url)), "nobody looking: no banner")
fans = [app.test_client() for _ in range(3)]
for i, f in enumerate(fans):
    login(f, "fan%d@test.nl" % i, "Fan")
    f.post("/alerts", data={"venue": "Perron", "date": day, "name": "Demand Night", "_csrf": csrf(f)})
page = html(visitor.get(url))
ok("3 people are looking for a ticket, and nobody is selling yet" in page and "/sell?event=%d" % dn["id"] in page,
   "event page: '3 people are looking', nobody selling, with a sell button for this event")
api = visitor.get("/api/events?date=" + day).get_json()
ok([e["looking"] for e in api["events"] if e["name"] == "Demand Night"] == [3] and api["looking_label"] == "{n} looking", "sell picker gets the looking count")
ok("3 people are looking for a ticket for this night. Yours will likely sell fast" in html(seller.get("/sell?event=%d" % dn["id"])),
   "sell form with this event chosen says people are looking")
ok("3 looking" in html(visitor.get("/")), "event cards say '3 looking'")
seller.post("/sell", data={"event_id": str(dn["id"]), "face_value": "20", "price": "20", "adult": "yes",
    "ticket": (io.BytesIO(b"%PDF-1.4 demand"), "d.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
page = html(visitor.get(url))
ok("3 people are looking for a ticket for this night." in page and "nobody is selling" not in page, "once a ticket is listed, the banner just shows demand")
fans[0].post("/alerts", data={"venue": "Perron", "date": day, "name": "Demand Night", "action": "remove", "_csrf": csrf(fans[0])})
ok("2 people are looking" in html(visitor.get(url)), "turning off 'Notify me' lowers the count")

print("FAILED:", fails or "none")
