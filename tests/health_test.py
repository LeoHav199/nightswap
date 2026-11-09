"""Checks for the heartbeat (approved 8 Nov 2026): /health says "ok" while the hourly task ran in the last 3 hours
and "late" (503) otherwise, for an outside uptime monitor; /admin shows the same as a green or red pill."""
import os, re, sys, tempfile
from datetime import timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATA_DIR = tmp; config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}; config.TASK_SECRET = "secret"
import notify; notify.send_email = lambda *a, **kw: False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def set_last(value):
    with app.app_context():
        if value is None: A.db.run("DELETE FROM settings WHERE key='hourly_last_run'")
        else: A.set_setting("hourly_last_run", value)
def ago(**kw): return A.sqltime(A.utcnow() - timedelta(**kw))

c = app.test_client()

# Never ran (missing row): late
set_last(None)
r = c.get("/health")
ok(r.status_code == 503 and html(r) == "late", "missing row: 503 late")
ok(r.headers.get("Cache-Control") == "no-store" and r.mimetype == "text/plain", "plain text, never cached")
ok("Set-Cookie" not in r.headers, "no session cookie for the monitor")

# A fresh run by the cron job: ok
r = c.post("/tasks/hourly", headers={"X-Task-Secret": "secret"})
ok(r.status_code == 200, "the hourly task runs")
r = c.get("/health")
ok(r.status_code == 200 and html(r) == "ok", "right after the hourly task: 200 ok")

# Just inside and just past the 3-hour limit
set_last(ago(hours=2, minutes=59))
ok(c.get("/health").status_code == 200, "2 h 59 min ago: still ok")
set_last(ago(hours=3, minutes=1))
r = c.get("/health")
ok(r.status_code == 503 and html(r) == "late", "3 h 1 min ago: 503 late")
ok(html(r) == "late" and len(r.get_data()) < 10, "says nothing but ok or late")

# Database failure: late, not a crash page
import sqlite3
real = A.db.one
def broken(*a, **kw): raise sqlite3.OperationalError("disk I/O error")
A.db.one = broken; app.logger.disabled = True  # the logged traceback is expected here
try:
    r = c.get("/health")
finally:
    A.db.one = real; app.logger.disabled = False
ok(r.status_code == 503 and html(r) == "late", "database error: 503 late")

# Open in coming-soon mode and while sales are paused, and on the old Render address
set_last(ago(minutes=5))
with app.app_context():
    A.set_setting("coming_soon", "1"); A.set_setting("sales_paused", "1")
r = c.get("/health")
ok(r.status_code == 200 and html(r) == "ok", "works in coming-soon mode and while sales are paused")
saved = (config.BASE_URL, config.DEV)
config.BASE_URL, config.DEV = "https://sparetickets.eu", False
try:
    r = c.get("/health", base_url="https://nightswap.onrender.com")
finally:
    config.BASE_URL, config.DEV = saved
ok(r.status_code == 200, "not redirected on the old Render address")
with app.app_context():
    A.set_setting("coming_soon", "0"); A.set_setting("sales_paused", "0")

# /admin: green pill with minutes, red when late or never
admin = app.test_client()
with app.app_context(): A.db.run("DELETE FROM hits")
admin.get("/"); r = admin.post("/login", data={"email": "admin@example.com", "_csrf": csrf(admin)}, follow_redirects=True)
admin.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": "Ad", "_csrf": csrf(admin)})
set_last(ago(minutes=12))
page = html(admin.get("/admin"))
ok(re.search(r'tag-released">Hourly task: last ran 12 min ago', page) is not None, "/admin: green 'last ran 12 min ago'")
ok("Hourly task ran in the last 3 hours" in page, "launch check uses the same 3-hour limit")
set_last(ago(hours=4, minutes=5))
page = html(admin.get("/admin"))
ok(re.search(r'tag-paused">Hourly task: last ran 4 h 5 min ago', page) is not None, "/admin: red after 3 hours")
set_last(None)
page = html(admin.get("/admin"))
ok(re.search(r'tag-paused">Hourly task: never ran', page) is not None, "/admin: red 'never ran' without the row")

print("\n%d failed" % len(fails))
