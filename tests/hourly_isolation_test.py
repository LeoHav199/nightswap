"""/tasks/hourly keeps going when one part fails (#34): the secret check, every other task still running, the failure
listed in the answer, `hourly_last_run` saved, and the admin alert sent at most once per 24 hours per task.
(swap_test.py already covers one failing part, the backup; this adds the secret and the payout, alert and last-run cases.)"""
import os, sys, tempfile
from datetime import timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
config.TASK_SECRET = "cron-secret"
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: SENT.append((to, subject, body)) and False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)
def hourly(headers=None):
    return app.test_client().post("/tasks/hourly", headers=headers or {})
def alerts():
    return [s for to, s, b in SENT if "hourly task" in s]

# Every task the route runs, by the name used in `failed`. Each is replaced by a counter so we can see what ran.
TASKS = {"stuck": "release_stuck_payouts", "disputes": "auto_refund_silent_disputes", "dispute_reminders": "remind_silent_sellers",
         "payouts": "release_due_payouts", "reminders": "send_reminders", "resell": "send_resell_reminders",
         "transfer": "send_transfer_reminders", "buyer_transfer": "send_buyer_transfer_reminders", "backup": "nightly_backup",
         "cleanup": "cleanup_ticket_files", "disk": "check_disk"}
real = {fn: getattr(A, fn) for fn in TASKS.values()}
calls = []
def stub(fn, value=None, boom=None):
    def f():
        calls.append(fn)
        if boom: raise boom
        return value
    return f
def install(failing=None):
    calls.clear()
    for fn in TASKS.values():
        setattr(A, fn, stub(fn, (0, 0) if fn in ("release_due_payouts", "send_reminders") else 0,
                            RuntimeError("Stripe said no") if fn == failing else None))

# --- The secret: nothing runs without the right one
install()
r = hourly()
ok(r.status_code == 404 and not calls, "no X-Task-Secret header: 404 and no task runs")
r = hourly({"X-Task-Secret": "wrong"})
ok(r.status_code == 404 and not calls, "a wrong X-Task-Secret: 404 and no task runs")
r = hourly({"X-Task-Secret": "cron-secret "})
ok(r.status_code == 404 and not calls, "the secret with a trailing space: 404 (exact match only)")
r = hourly({"X-Task-Secret": ""})
ok(r.status_code == 404 and not calls, "an empty X-Task-Secret: 404")
ok(app.test_client().get("/tasks/hourly").status_code == 405, "GET isn't allowed on the task route")
ok(one("SELECT value FROM settings WHERE key='hourly_last_run'") is None, "a refused call doesn't save hourly_last_run")
config.TASK_SECRET = ""
r = hourly({"X-Task-Secret": ""})
ok(r.status_code == 404 and not calls, "with no TASK_SECRET set, even an empty header gets 404")
config.TASK_SECRET = "cron-secret"

# --- All fine: every task runs once, nothing failed, the run is stamped
r = hourly({"X-Task-Secret": "cron-secret"})
ok(r.status_code == 200 and r.get_json()["failed"] == [], "the right secret: 200 and nothing failed")
ok(sorted(calls) == sorted(TASKS.values()), "...and every task ran once")
ok(one("SELECT value FROM settings WHERE key='hourly_last_run'") is not None, "...and hourly_last_run is saved")
ok(not alerts(), "...and the admins get no alert")

# --- release_due_payouts raises: the rest still run
app.logger.disabled = True   # (the expected traceback would only be noise)
run("DELETE FROM settings WHERE key='hourly_last_run'")
SENT.clear(); install(failing="release_due_payouts")
r = hourly({"X-Task-Secret": "cron-secret"}); body = r.get_json()
ok(r.status_code == 200, "a failing payout task still answers 200 (the cron job sees no error)")
ok(body["failed"] == ["payouts"], "the response lists exactly the failed task under `failed`: %s" % body["failed"])
ok(sorted(calls) == sorted(TASKS.values()), "every task was still called, including the ones after the payouts")
later = [fn for fn in TASKS.values() if list(TASKS.values()).index(fn) > list(TASKS.values()).index("release_due_payouts")]
ok(all(fn in calls for fn in later), "the tasks after the payouts (%d) all ran" % len(later))
ok(body["payouts_released"] == 0 and body["payouts_waiting"] == 0, "the failed task's numbers fall back to zero")
ok(one("SELECT value FROM settings WHERE key='hourly_last_run'") is not None, "hourly_last_run is still saved after a failure")

# --- The alert: once per 24 hours per task
first = alerts()
ok(len(first) == 1 and "payouts" in first[0] and SENT[0][0] == "admin@example.com", "the admins get one alert naming the task")
ok("Stripe said no" in SENT[0][2] and "once a day" in SENT[0][2], "...with the error and the note that it comes at most once a day")
r = hourly({"X-Task-Secret": "cron-secret"})
ok(r.get_json()["failed"] == ["payouts"] and len(alerts()) == 1, "the next hour it fails again, is still listed, but no second alert")
r = hourly({"X-Task-Secret": "cron-secret"})
ok(r.get_json()["failed"] == ["payouts"] and len(alerts()) == 1, "...and the hour after that")

# a different task failing at the same time gets its own alert
install(failing="send_reminders")
A.release_due_payouts = stub("release_due_payouts", (0, 0), RuntimeError("Stripe said no"))
r = hourly({"X-Task-Secret": "cron-secret"})
ok(sorted(r.get_json()["failed"]) == ["payouts", "reminders"], "two failing tasks are both listed")
ok(len(alerts()) == 2 and any("reminders" in s for s in alerts()), "a different task gets its own alert (one per task, not one for all)")

# 24 hours later the alert may come again
with app.app_context():
    A.db.run("UPDATE hits SET created_at=? WHERE key='hourly-alert:hourly-payouts'", A.sqltime(A.utcnow() - timedelta(hours=23)))
hourly({"X-Task-Secret": "cron-secret"})
ok(len(alerts()) == 2, "after 23 hours: still no new alert")
with app.app_context():
    A.db.run("UPDATE hits SET created_at=? WHERE key='hourly-alert:hourly-payouts'", A.sqltime(A.utcnow() - timedelta(hours=25)))
hourly({"X-Task-Secret": "cron-secret"})
ok(len(alerts()) == 3 and "payouts" in alerts()[-1], "after 25 hours: the alert for the payouts comes again")

# --- Everything raising at once: still a 200, every task listed, last run saved
run("DELETE FROM settings WHERE key='hourly_last_run'")
calls.clear()
for fn in TASKS.values():
    setattr(A, fn, stub(fn, boom=RuntimeError("everything is on fire")))
r = hourly({"X-Task-Secret": "cron-secret"})
ok(r.status_code == 200 and sorted(r.get_json()["failed"]) == sorted(TASKS), "even when every task fails the answer is 200 with all listed")
ok(one("SELECT value FROM settings WHERE key='hourly_last_run'") is not None, "...and hourly_last_run is saved")

for fn, f in real.items(): setattr(A, fn, f)
app.logger.disabled = False
print("\n%d failed" % len(fails) if fails else "\nAll hourly isolation checks passed")
