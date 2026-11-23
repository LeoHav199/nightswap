"""Busier homepage (23 Nov 2026): this week's events without tickets show as photo cards, a slim "no tickets yet"
line replaces the big empty box, day tabs get a dot on days with events, and a line of real numbers
("N events this week · N people looking") shows only once the numbers are at least HOME_STATS_MIN."""
import os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify
notify.send_email = lambda *a, **kw: False
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
admin, v = app.test_client(), app.test_client()
admin.get("/"); r = admin.post("/login", data={"email": "admin@example.com", "_csrf": csrf(admin)}, follow_redirects=True)
admin.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": "Ad", "_csrf": csrf(admin)})
def add(name, days):
    d = (date.today() + timedelta(days=days)).isoformat()
    admin.post("/admin/events/save", data={"face_value_max": "500", "name": name, "venue": "Maassilo", "category": "club", "event_date": d, "_csrf": csrf(admin)})
    return d

v.get("/")
page = html(v.get("/"))
ok('class="this-week"' not in page and "home-stats" not in page, "no events: no cards and no numbers line")

d1 = add("Week One", 2); add("Week Two", 3); add("Far Away", 20)
page = html(v.get("/"))
week = page.split('class="this-week"')[1].split("</section>")[0] if 'class="this-week"' in page else ""
ok("Week One" in week and "Week Two" in week and "Far Away" not in week, "this week's events are cards; later ones aren't")
ok("Far Away" in page.split('class="coming-up"')[1] if 'class="coming-up"' in page else False, "later events stay in the short list")
ok("No tickets listed yet for these nights" in page and "Be the first to sell" not in page, "a slim line instead of the big empty box")
ok("home-stats" not in page, "2 events this week: too few, so no numbers line")
tab = re.search(r'<a class="day-tab[^"]*"[^>]*href="[^"]*day=%s[^"]*"[^>]*>.*?</a>' % d1, page, re.S)
ok(tab and "day-dot" in tab.group(0) and "is-empty" not in tab.group(0), "a day with an event gets a dot and isn't greyed out")
empty_day = (date.today() + timedelta(days=5)).isoformat()
tab = re.search(r'<a class="day-tab[^"]*"[^>]*href="[^"]*day=%s[^"]*"[^>]*>.*?</a>' % empty_day, page, re.S)
ok(tab and "is-empty" in tab.group(0) and "day-dot" not in tab.group(0), "a day without events stays greyed out")

for i in range(3):
    add("Extra %d" % i, 4)
page = html(v.get("/"))
ok("5 events this week" in page and "looking for tickets" not in page, "5 events: the numbers line shows, without a waiting count below 5")
for i in range(5):
    run("INSERT INTO users (email, first_name) VALUES (?, 'W')", "w%d@test.nl" % i)
    run("INSERT INTO alerts (user_id, event_date, venue, event_name) SELECT id, ?, 'Maassilo', 'Week One' FROM users WHERE email=?", d1, "w%d@test.nl" % i)
page = html(v.get("/"))
ok("5 people looking for tickets" in page, "5 people waiting: the waiting count shows too")
ok("home-stats" not in html(v.get("/?q=week")), "no numbers line on a search")

# ---- First impression (24 Nov 2026)
fresh = app.test_client()
page = html(fresh.get("/"))
ok("Student tickets for Rotterdam nights." in page and "Tickets for tonight" not in page, "no tickets: the title says what Spare is, not 'tickets for tonight'")
ok(page.count('class="promises"') == 1 and "Money held until after the night" in page and "Refund if it doesn&#39;t work" in page, "three promises under the title")
ok('class="intro-steps"' in page and "How Spare works" in page, "a first visit shows the 3-step intro")
ok("intro_seen=1" in fresh.get("/").headers.get("Set-Cookie", "") or 'class="intro-steps"' not in html(fresh.get("/")), "...once: the cookie hides it next time")
ok('class="intro-steps"' not in html(fresh.get("/")), "second visit: no intro")
ok('class="pay-badges"' in page and "iDEAL" in page and "Mastercard" in page, "payment badges on the homepage")
add("Concert Thing", 1)
admin.post("/admin/events/save", data={"face_value_max": "500", "name": "Student Bash", "venue": "Maassilo", "category": "student",
                                       "event_date": (date.today() + timedelta(days=6)).isoformat(), "_csrf": csrf(admin)})
run("UPDATE events SET category='concert' WHERE name='Concert Thing'")
page = html(fresh.get("/"))
week = page.split('class="this-week"')[1].split("</section>")[0]
ok(week.index("Student Bash") < week.index("Concert Thing"), "parties lead the This week cards, even when a concert is sooner")

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
