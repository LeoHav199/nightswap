import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.ADMIN_SECOND_FACTOR = False
config.FEE_PERCENT = 0  # these tests were written for a flat €1 fee
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify
SENT = []
notify.send_email = lambda to, subject, body: (SENT.append((to, subject, body)), False)[1]
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/")
    r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    code = re.search(r"code is (\d{6})", html(r)).group(1)
    c.post("/login/verify", data={"code": code, "first_name": name, "_csrf": csrf(c)})
seller, buyer, stranger, admin = (app.test_client() for _ in range(4))
login(seller, "sara@test.nl", "Sara"); login(buyer, "bo@test.nl", "Bo"); login(stranger, "x@test.nl", "Xi"); login(admin, "admin@example.com", "Admin")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
D = (date.today() + timedelta(days=4)).isoformat()
r = seller.post("/sell", data={"venue": "Perron", "event_date": D, "face_value": "25", "price": "22", "event_name": "Chat Night",
    "adult": "yes", "ticket": [(io.BytesIO(b"%PDF-1.4 a"), "a.pdf"), (io.BytesIO(b"%PDF-1.4 b"), "b.pdf")], "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))

page = html(buyer.get("/t/%d" % lid))
ok("Is there a name on the ticket?" in page and "Make an offer" in page, "listing shows quick questions and offer box")
r = buyer.post("/t/%d/ask" % lid, data={"question": "q_name", "_csrf": csrf(buyer)})
tid = int(re.search(r"/threads/(\d+)", r.location).group(1))
ok("Is there a name on the ticket?" in html(buyer.get("/threads/%d" % tid)), "question appears in the conversation")
ok(any(to == "sara@test.nl" and "message" in s for to, s, _ in SENT), "seller gets an email about the question")
ok('class="count">1<' in html(seller.get("/")), "seller sees an unread badge")
r = buyer.post("/threads/%d/send" % tid, data={"body": "hey can we talk", "_csrf": csrf(buyer)}, follow_redirects=True)
ok("Free chat opens after payment" in html(r), "buyer can't free-chat before paying")
seller.post("/threads/%d/send" % tid, data={"body": "No name on it! App me 0612345678 for a better deal", "_csrf": csrf(seller)})
bpage = html(buyer.get("/threads/%d" % tid))
ok("0612345678" not in bpage and "•••" in bpage, "seller's phone number is hidden from the buyer")
ok("Never pay by Tikkie" in bpage, "both sides see the stay-protected warning after a flagged message")
ok(stranger.get("/threads/%d" % tid).status_code == 404, "outsiders can't open the conversation")

r = buyer.post("/t/%d/offer" % lid, data={"amount": "25", "_csrf": csrf(buyer)}, follow_redirects=True)
ok("Offer less than the asking price" in html(r), "offer above asking price refused")
buyer.post("/t/%d/offer" % lid, data={"amount": "18", "qty": "2", "_csrf": csrf(buyer)})
spage = html(seller.get("/threads/%d" % tid))
ok("€18" in spage and "Accept" in spage, "seller sees the offer with Accept/Decline")
with app.app_context():
    oid = A.db.one("SELECT id FROM offers WHERE status='pending'")["id"]
seller.post("/offers/%d/accept" % oid, data={"_csrf": csrf(seller)})
bpage = html(buyer.get("/threads/%d" % tid))
ok("Pay €38" in bpage, "buyer can pay the accepted offer: 2 × (€18 + €1)")
r = buyer.post("/t/%d/buy" % lid, data={"offer_id": oid, "back": "/threads/%d" % tid, "_csrf": csrf(buyer)})
pub = r.location.split("/")[2]
buyer.post(r.location, data={"_csrf": csrf(buyer)})
with app.app_context():
    o = A.db.one("SELECT * FROM orders WHERE public_id=?", pub)
    ok(o["price_cents"] == 3600 and o["total_cents"] == 3800 and o["quantity"] == 2, "order uses the offer price")
    ok(A.db.one("SELECT status FROM offers WHERE id=?", oid)["status"] == "used", "offer marked as used")
    ok(A.db.one("SELECT order_id FROM threads WHERE id=?", tid)["order_id"] == o["id"], "conversation linked to the order")
opage = html(buyer.get("/orders/%s" % pub))
ok("sara@test.nl" not in opage and "Message Sara" in opage, "order page hides email, offers in-app message instead")
bpage = html(buyer.get("/threads/%d" % tid))
ok("Payment received" in bpage and 'name="body"' in bpage, "after payment the chat opens with a system note")
buyer.post("/threads/%d/send" % tid, data={"body": "Got it, thanks! See you there", "_csrf": csrf(buyer)})
ok("Got it, thanks!" in html(seller.get("/threads/%d" % tid)), "normal message delivered")
ok("Sara" in html(buyer.get("/messages")) and "Chat Night" in html(buyer.get("/messages")), "inbox lists the conversation")

for i in range(4):
    buyer.post("/threads/%d/send" % tid, data={"body": "insta: bo.party%d" % i, "_csrf": csrf(buyer)})
r = buyer.post("/threads/%d/send" % tid, data={"body": "hello?", "_csrf": csrf(buyer)}, follow_redirects=True)
ok("paused for 24 hours" in html(r), "messaging paused after repeated contact-detail attempts")
apage = html(admin.get("/admin"))
ok("Flagged messages" in apage and "0612345678" in apage and "strikes" not in apage, "admin sees flagged originals")
print("FAILED:", fails or "none")
