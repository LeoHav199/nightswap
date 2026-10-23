import io, os, re, sys, tempfile, json
from datetime import date, timedelta
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
D = (date.today() + timedelta(days=4)).isoformat()
admin, seller, buyer, v = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo"); v.get("/")
for n in ("Night A", "Night B"):
    admin.post("/admin/events/save", data={"name": n, "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
home = html(v.get("/"))
ok("Be the first to sell" in home and "Coming up" in home and 'class="stub' not in home, "no tickets anywhere: friendly 'be the first' message + compact list, no empty cards")
ok("All of the Netherlands" not in home and 'id="city"' not in home, "Rotterdam-only: no city picker / all-NL option")
ok(html(v.get("/?city=all")).count("Night A") >= 1 and "All of the Netherlands" not in html(v.get("/?city=all")), "?city=all falls back to Rotterdam")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Night A'")
r = seller.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
    "ticket": (io.BytesIO(b"%PDF-1.4 a"), "a.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
home = html(v.get("/"))
ok("Night A" in home.split('class="coming-up"')[0] and "Night B" in home.split('class="coming-up"')[1], "event with tickets is a card; the other stays in 'Coming up'")

# header order: account last
nav = html(buyer.get("/")).split('<nav class="nav">')[1].split("</nav>")[0]
ok(nav.rfind("nav-account") > nav.rfind("/sell"), "Account is the last item in the header (top right)")

# live chat without reloading
r = buyer.post("/t/%d/ask" % lid, data={"question": "q_name", "_csrf": csrf(buyer)})
tid = int(re.search(r"/threads/(\d+)", r.location).group(1))
page = html(seller.get("/threads/%d" % tid))
last = int(re.search(r'data-last="(\d+)"', page).group(1))
r = seller.post("/threads/%d/send" % tid, data={"body": "Yes, my name is on it", "after": str(last), "_csrf": csrf(seller)},
                headers={"X-Requested-With": "fetch"})
d = r.get_json()
ok(r.status_code == 200 and d and "Yes, my name is on it" in d["html"] and d["last"] > last, "sending returns the new message as JSON (no page reload)")
upd = buyer.get("/threads/%d/updates?after=%d" % (tid, last), headers={"X-Requested-With": "fetch"}).get_json()
ok("Yes, my name is on it" in upd["html"], "the other side picks it up through live updates")
r = seller.post("/threads/%d/send" % tid, data={"body": "app me 0612345678", "after": str(d["last"]), "_csrf": csrf(seller)},
                headers={"X-Requested-With": "fetch"})
ok("0612345678" not in r.get_json()["html"] and r.get_json()["warning"], "filter + warning still work in live chat")
r = buyer.post("/threads/%d/send" % tid, data={"body": "hi", "_csrf": csrf(buyer)}, headers={"X-Requested-With": "fetch"})
ok(r.status_code == 400 and "after payment" in r.get_json()["error"], "buyer before payment gets a clear error instead of a reload")

# languages
for code, word in (("fr", "Des billets pour ce soir"), ("de", "Tickets für heute Nacht"), ("es", "Entradas para esta noche")):
    c = app.test_client(); c.get("/lang/%s?next=/" % code)
    ok(word in html(c.get("/")), "%s homepage translated" % code)
c = app.test_client()
ok("Des billets pour ce soir" in html(c.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9"})), "a French phone gets French automatically")
c = app.test_client(); c.get("/lang/de?next=/")
lp = html(c.get("/t/%d" % lid))
ok("21,60 €" in lp, "German price format on listing (20 € + 8% fee = 21,60 €)")
print("FAILED:", fails or "none")
