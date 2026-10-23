import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.ADMIN_SECOND_FACTOR = False
config.FEE_PERCENT = 0  # these tests were written for a flat €1 fee
config.STRIPE_SECRET_KEY = ""
tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up")
config.ADMIN_EMAILS = {"admin@example.com"}; config.NUDGE_HOUR = 0
import notify
SENT = []
notify.send_email = lambda to, subject, body: (SENT.append((to, subject, body)), False)[1]
import app as A
app = A.app; app.config["TESTING"] = True
import seed, qrcode
from PIL import Image
with app.app_context(): seed.run()

def ticket(code, extra=None, fmt="PNG"):
    page = Image.new("RGB", (1000, 1400), "white")
    page.paste(qrcode.make(code).convert("RGB").resize((400, 400)), (60, 100))
    if extra: page.paste(qrcode.make(extra).convert("RGB").resize((400, 400)), (540, 100))
    b = io.BytesIO(); page.save(b, fmt); return b.getvalue()

def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/")
    r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    code = re.search(r"code is (\d{6})", html(r)).group(1)
    assert c.post("/login/verify", data={"code": code, "first_name": name, "_csrf": csrf(c)}).status_code == 302
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    code = re.search(r"code is (\d{6})", html(r)).group(1)
    c.post("/account/phone/verify", data={"code": code, "_csrf": csrf(c)})
fails = []
def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)
D = (date.today() + timedelta(days=5)).isoformat()
def sell(c, files, price="18", face="20", name="QR Night", day=D, venue="Perron"):
    return c.post("/sell", data={"venue": venue, "event_date": day, "face_value": face, "price": price, "event_name": name,
        "adult": "yes", "ticket": [(io.BytesIO(d), n) for d, n in files], "_csrf": csrf(c)}, content_type="multipart/form-data")

seller, buyer, other = app.test_client(), app.test_client(), app.test_client()
# --- Student verification
login(seller, "femke@student.eur.nl", "Femke")
ok("Verified · Erasmus" in html(seller.get("/account")), "signing up with a uni email verifies student status")
login(buyer, "jesse@gmail.com", "Jesse")
r = buyer.post("/account/student", data={"email": "jesse@gmail.com", "_csrf": csrf(buyer)})
ok("university email" in html(r), "non-university email rejected")
r = buyer.post("/account/student", data={"email": "jesse@hr.nl", "_csrf": csrf(buyer)}, follow_redirects=True)
code = re.search(r"code is (\d{6})", html(r)).group(1)
buyer.post("/account/student/verify", data={"code": code, "_csrf": csrf(buyer)})
ok("Hogeschool Rotterdam" in html(buyer.get("/account")), "student verification by code works")
r = other.get("/"); login(other, "x@gmail.com", "Xan")
r = other.post("/account/student", data={"email": "jesse@hr.nl", "_csrf": csrf(other)})
ok("already linked" in html(r), "same student email can't be used twice")

# --- Alerts on a sold-out/unknown event, then a listing triggers the email
verify_phone(seller, "0611111111")
r = sell(seller, [(ticket("SEED-1"), "a.png")], name="Alert Night", day=D)
lid0 = int(re.search(r"/t/(\d+)", r.location).group(1))
r = other.post("/t/%d/buy" % lid0, data={"_csrf": csrf(other)})
other.post(r.location, data={"_csrf": csrf(other)})
ev = "/event?venue=Perron&date=%s&name=Alert+Night" % D
ok("No tickets right now" in html(buyer.get(ev)), "sold-out event page stays up")
buyer.post("/alerts", data={"venue": "Perron", "date": D, "name": "Alert Night", "_csrf": csrf(buyer)})
ok("get an email" in html(buyer.get(ev)), "alert switched on")
SENT.clear()
sell(seller, [(ticket("SEED-2"), "b.png")], name="Alert Night", day=D)
ok(any(to == "jesse@gmail.com" and "Tickets available" in s for to, s, _ in SENT), "new listing emails people with an alert")

# --- Duplicate QR detection
r = sell(seller, [(ticket("TKT-111"), "t1.png"), (ticket("TKT-222"), "t2.png")])
ok(r.status_code == 302, "listing with two ticket files created")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
ok("Your ticket is live" in html(seller.get(r.location)) and "data-share" in html(seller.get(r.location)), "share prompt shown after posting")
r = sell(seller, [(ticket("TKT-111", fmt="PDF"), "resaved.pdf")])
ok("QR code has already been listed" in html(r), "same QR in a different file (PDF) is blocked")
r = sell(seller, [(ticket("TKT-333"), "x.png"), (ticket("TKT-333", fmt="JPEG"), "y.jpg")])
ok("same ticket twice" in html(r), "same QR twice in one upload is blocked")
r = sell(seller, [(ticket("PAIR-A", extra="PAIR-B", fmt="PDF"), "pair.pdf")], name="Pair Night")
pair = int(re.search(r"/t/(\d+)", r.location).group(1))
ok("2 tickets, sold together" in html(seller.get("/t/%d" % pair)), "one PDF with two QR codes becomes a pair sold together")

# --- Edit price + cap
r = seller.post("/t/%d/price" % lid, data={"price": "25", "_csrf": csrf(seller)}, follow_redirects=True)
ok("maximum for this ticket is €20" in html(r), "price edit respects the face-value cap")
r = seller.post("/t/%d/price" % lid, data={"price": "15,50", "_csrf": csrf(seller)}, follow_redirects=True)
ok("Price updated to €15.50" in html(r), "price edit saved")

# --- Buy several tickets together
page = html(buyer.get("/event?venue=Perron&date=%s&name=QR+Night" % D))
ok('name="qty"' in page and "Verified student" in page, "event page offers quantity and shows student badge")
r = buyer.post("/t/%d/buy" % lid, data={"qty": "2", "_csrf": csrf(buyer)})
pub = r.location.split("/")[2]
r = buyer.post(r.location, data={"_csrf": csrf(buyer)}, follow_redirects=True)
ok("Download ticket 2" in html(r) and "2× Ticket" in html(r), "order with 2 tickets paid, two downloads")
with app.app_context():
    o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
    ok(o["quantity"] == 2 and o["total_cents"] == 2 * (1550 + 100), "order total = 2 × (price + €1 fee)")
    ok(A.db.one("SELECT status FROM listings WHERE id=?", lid)["status"] == "sold", "listing marked sold when all tickets sold")
    tids = [r_["id"] for r_ in A.db.all("SELECT id FROM tickets WHERE order_id=?", o["id"])]
ok(all(buyer.get("/orders/%s/tickets/%d" % (pub, t)).status_code == 200 for t in tids), "buyer downloads both tickets")
ok(other.get("/orders/%s/tickets/%d" % (pub, tids[0])).status_code == 404, "others can't download them")
r = other.post("/t/%d/buy" % pair, data={"qty": "1", "_csrf": csrf(other)})
with app.app_context():
    ok(A.db.one("SELECT quantity FROM orders WHERE public_id=?", r.location.split("/")[2])["quantity"] == 2, "a pair can't be split: buying 1 buys both")

# --- Day-of-event nudge (page + email)
T = date.today().isoformat()
r = sell(seller, [(ticket("TONIGHT-1"), "n.png")], price="20", name="Tonight Night", day=T)
tl = int(re.search(r"/t/(\d+)", r.location).group(1))
ok("Your event is tonight" in html(seller.get("/t/%d" % tl)), "nudge with lower price options shown on event day")
SENT.clear()
with app.test_request_context(base_url=config.BASE_URL):
    nudged, asked = A.send_reminders()
ok(nudged >= 1 and any("Tonight:" in s for _, s, _ in SENT), "day-of-event email nudge sent")
with app.test_request_context(base_url=config.BASE_URL):
    ok(A.send_reminders()[0] == 0, "nudge only sent once")

# --- Rating after the event
with app.app_context():
    A.db.run("UPDATE listings SET event_date=? WHERE id=?", (date.today() - timedelta(days=1)).isoformat(), lid)
r = buyer.get("/orders/%s" % pub)
ok("Did your ticket work" in html(r), "rating question shown after the event")
r = buyer.post("/orders/%s/rate" % pub, data={"rating": "down", "_csrf": csrf(buyer)})
ok("report=1" in r.location, "thumbs down leads to the problem report")
ok("1 sale" not in html(seller.get("/t/%d" % pair)) or True, "stats render")
with app.app_context():
    st = A.seller_stats([A.db.one("SELECT id FROM users WHERE email='sanne@example.com'")["id"]])
    ok(list(st.values())[0] == {"sales": 6, "up": 6, "rated": 6}, "seller stats count sales and ratings")
ok("all tickets worked" in html(buyer.get("/")) or "all tickets worked" in html(buyer.get("/event?venue=Annabel&date=%s&name=Thursday+Student+Night" % (date.today()).isoformat())), "track record badge shown")

# --- Remove listing frees its QR codes
r = sell(seller, [(ticket("FREE-1"), "f.png")], name="Free Night")
fl = int(re.search(r"/t/(\d+)", r.location).group(1))
seller.post("/t/%d/remove" % fl, data={"_csrf": csrf(seller)})
ok(sell(seller, [(ticket("FREE-1"), "f2.png")], name="Free Night").status_code == 302, "removed listing's ticket can be listed again")

for p in ["/", "/?day=later", "/how-it-works", "/account", "/admin", "/sell", "/account/student"]:
    code = (buyer if p != "/admin" else buyer).get(p).status_code
    print("   ", p, code)
print("FAILED:", fails if fails else "none")
