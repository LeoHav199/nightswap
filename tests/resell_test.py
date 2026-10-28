import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []; MAIL_WORKS = [False]
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
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(c)})
def at_hour(h):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.combine(date.today(), datetime.min.time()).replace(hour=h)
    return Fixed

admin, seller, buyer, other, fan = (app.test_client() for _ in range(5))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo")
login(other, "o@test.nl", "Olli"); login(fan, "f@test.nl", "Fay")
verify_phone(seller, "0611111111"); verify_phone(buyer, "0622222222"); verify_phone(other, "0633333333")

tomorrow = (date.today() + timedelta(days=1)).isoformat()
admin.post("/admin/events/save", data={"name": "Big Night", "venue": "Maassilo", "category": "club", "event_date": tomorrow, "_csrf": csrf(admin)})
with app.app_context(): ev = A.db.one("SELECT * FROM events WHERE name='Big Night'")
pdf = b"%PDF-1.4 the one and only ticket"
def sell(c, data=pdf):
    return c.post("/sell", data={"event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                  "ticket": (io.BytesIO(data), "t.pdf"), "_csrf": csrf(c)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", sell(seller).location).group(1))
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)}); pub = r.location.split("/")[2]
buyer.post("/checkout/%s/test" % pub, data={"_csrf": csrf(buyer)})
for f in (fan, other):
    f.post("/alerts", data={"venue": "Maassilo", "date": tomorrow, "name": "Big Night", "_csrf": csrf(f)})

# --- The reminder
def run():
    with app.test_request_context(base_url="https://sparetickets.eu"):
        return A.send_resell_reminders()
real = A.datetime
MAIL_WORKS[0] = True
A.datetime = at_hour(9)
ok(run() == 0 and not SENT, "not before midday the day before")
A.datetime = at_hour(13)
ok(run() == 1, "midday the day before: one reminder")
to, subject, body = SENT[-1]
ok(to == "b@test.nl" and "tomorrow" in subject and "/sell?event=%d" % ev["id"] in body, "it goes to the buyer, with a link to sell for this event")
ok("2 people are still looking" in body, "it says how many people are looking")
ok(run() == 0 and len(SENT) == 1, "never twice for the same order")
A.datetime = real
MAIL_WORKS[0] = False
r = admin.post("/tasks/hourly", headers={"X-Task-Secret": config.TASK_SECRET or "x"})
ok(r.status_code == 404 or "resell_reminders" in html(r), "the hourly task runs it too")

# --- Reselling a ticket bought on Spare
r = sell(seller)
ok("already" in html(r).lower() and r.status_code != 302, "the original seller can't list the sold ticket again")
r = sell(other)
ok(r.status_code != 302, "a stranger with a copy of the file is refused")
r = sell(buyer)
ok(r.status_code == 302 and "/t/" in r.location, "the buyer can resell the ticket they bought here")
lid2 = int(re.search(r"/t/(\d+)", r.location).group(1))
ok(sell(buyer).status_code != 302, "but not twice at the same time")
r = other.post("/t/%d/buy" % lid2, data={"_csrf": csrf(other)}); pub2 = r.location.split("/")[2]
other.post("/checkout/%s/test" % pub2, data={"_csrf": csrf(other)})
ok(sell(buyer).status_code != 302 and sell(other).status_code == 302, "after it sells on, only the newest buyer may resell it")

# --- Someone who already relisted doesn't get the reminder
with app.app_context():
    A.db.run("UPDATE orders SET resell_reminded_at=NULL")
MAIL_WORKS[0] = True; SENT.clear(); A.datetime = at_hour(13)
run()
A.datetime = real
ok(all(to != "o@test.nl" for to, s, b in SENT), "people who already put their ticket back on sale aren't reminded")

print("FAILED:", fails or "none")
