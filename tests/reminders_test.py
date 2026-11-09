"""Checks for send_reminders: the buyer's 'Inside? Slide Swap' push + email (only after the doors + SWAP_REMIND_MINUTES,
once per order, never for refunds, judged by the door time and not the calendar date) and the 'did it work?' request for
buyers who missed the night. Dates are relative to today and time is faked, so it passes at any hour."""
import os, sys, tempfile
from datetime import date, datetime, timedelta, timezone
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: SENT.append((to, subject, body)) or True
import push; PUSHED = []
push.send = lambda user_id, title, body, url="/", tag=None: PUSHED.append((user_id, title, body, url, tag)) or 1
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

REMIND = config.SWAP_REMIND_MINUTES
real_utcnow, real_today = A.utcnow, A.today
def at(moment):
    """Pretend it is this moment (naive UTC); today() is that moment's date in local (Amsterdam) time."""
    A.utcnow = lambda: moment
    A.today = lambda: moment.replace(tzinfo=timezone.utc).astimezone().date()
def run_reminders():
    SENT.clear(); PUSHED.clear()
    with app.test_request_context(base_url="https://sparetickets.eu"):
        return A.send_reminders()

with app.app_context():
    seller = A.db.run("INSERT INTO users (email, first_name) VALUES ('s@test.nl', 'Sam')")
    buyer = A.db.run("INSERT INTO users (email, first_name) VALUES ('b@test.nl', 'Bo')")
n = [0]
def scene(name, event_date, start_time="22:00", status="paid", rating=None):
    """A sold listing with one order. Returns (public_id, doors as naive UTC, title)."""
    n[0] += 1
    pub = "REM%04d" % n[0]
    with app.app_context():
        lid = A.db.run("""INSERT INTO listings (seller_id, venue, event_name, event_date, face_value_cents, price_cents, start_time, status)
                          VALUES (?, 'Maassilo', ?, ?, 2000, 2000, ?, 'sold')""", seller, name, event_date.isoformat(), start_time)
        A.db.run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, price_cents, fee_cents, total_cents, status, rating,
                                        expires_at, paid_at)
                    VALUES (?, ?, ?, ?, 2000, 199, 2199, ?, ?, datetime('now'), datetime('now'))""", pub, lid, buyer, seller, status, rating)
        doors = A.doors_open_at(A.db.one("SELECT * FROM listings WHERE id=?", lid))
    return pub, doors, "%s · Maassilo" % name
def asked_at(pub):
    with app.app_context(): return A.db.one("SELECT rating_asked_at FROM orders WHERE public_id=?", pub)["rating_asked_at"]
def swap_mails(title): return [m for m in SENT if m[0] == "b@test.nl" and "Slide Swap" in m[1] and title in m[1]]
def rate_mails(title): return [m for m in SENT if m[0] == "b@test.nl" and "Did your ticket work?" in m[1] and title in m[1]]
def swap_pushes(pub): return [p for p in PUSHED if p[4] == "swap-" + pub]
def rate_pushes(pub): return [p for p in PUSHED if p[4] == "rate-" + pub]

today = date.today()
ok(REMIND == 45, "the reminder is due 45 minutes after the doors (SWAP_REMIND_MINUTES)")

# --- (a) not before the doors + 45 minutes, then the push and the email
pub, doors, title = scene("Night A", today)
for label, moment in (("before the doors", doors - timedelta(minutes=30)), ("when the doors open", doors),
                      ("a minute before the reminder is due", doors + timedelta(minutes=REMIND - 1))):
    at(moment)
    nudged, asked = run_reminders()
    ok(asked == 0 and not SENT and not PUSHED and not asked_at(pub), "(a) nothing is sent %s" % label)
at(doors + timedelta(minutes=REMIND + 1))
nudged, asked = run_reminders()
ok(asked == 1 and len(swap_mails("Night A")) == 1, "(a) once the reminder is due the buyer gets 'Inside? Slide Swap' by email")
ok(len(swap_pushes(pub)) == 1 and swap_pushes(pub)[0][0] == buyer and swap_pushes(pub)[0][1] == "Inside? Slide Swap"
   and swap_pushes(pub)[0][3] == "/orders/" + pub, "(a) ...and by push, with a link to the order page")
ok("/orders/" + pub in swap_mails("Night A")[0][2], "(a) the email links to the order page")
ok(not rate_mails("Night A") and not rate_pushes(pub), "(a) it is the Swap reminder, not the 'did it work?' request")
ok(nudged == 0, "(a) no price nudge for a sold listing")

# --- (b) once per order
ok(asked_at(pub) is not None, "(b) the order is marked as asked (rating_asked_at)")
for label, moment in (("a minute later", doors + timedelta(minutes=REMIND + 2)), ("an hour later", doors + timedelta(hours=1)),
                      ("the next morning", doors + timedelta(hours=14))):
    at(moment)
    nudged, asked = run_reminders()
    ok(asked == 0 and not SENT and not PUSHED, "(b) no second reminder %s" % label)

# --- (b) every order gets its own
pub_x, doors_x, title_x = scene("Night B", today)
pub_y, doors_y, title_y = scene("Night C", today)
at(doors_x + timedelta(hours=1))
nudged, asked = run_reminders()
ok(asked == 2 and len(swap_mails("Night B")) == 1 and len(swap_mails("Night C")) == 1 and not swap_mails("Night A"),
   "(b) two new orders get one reminder each, and the first order stays quiet")

# --- (c) refunded orders get nothing; neither do orders the buyer already rated
for status in ("refunded", "disputed", "pending", "cancelled"):
    pub_r, doors_r, title_r = scene("Night " + status, today, status=status)
    at(doors_r + timedelta(hours=1))
    nudged, asked = run_reminders()
    ok(asked == 0 and not SENT and not PUSHED and not asked_at(pub_r), "(c) an order that is %s gets no reminder" % status)
    at(doors_r + timedelta(days=2))
    nudged, asked = run_reminders()
    ok(asked == 0 and not SENT and not PUSHED, "(c) ...and no 'did it work?' request later either (%s)" % status)
pub_r, doors_r, title_r = scene("Night rated", today, rating="up")
at(doors_r + timedelta(hours=1))
nudged, asked = run_reminders()
ok(asked == 0 and not SENT and not PUSHED, "(c) a buyer who already swapped or rated is left alone")

# --- (d) judged by the door time, not the calendar date
# Doors after midnight: the event is dated today, but the doors open tomorrow at 01:00.
pub_d, doors_d, title_d = scene("Night late", today, start_time="01:00")
at(doors_d - timedelta(hours=4))   # the evening of the event date, still the event date
ok(A.today() == today, "(d) set-up: at that moment today() is still the event date")
nudged, asked = run_reminders()
ok(asked == 0 and not SENT, "(d) on the event date itself, before the doors, nothing is sent")
at(doors_d + timedelta(minutes=REMIND - 5))   # past midnight: the date has moved on, the doors have only just opened
ok(A.today() == today + timedelta(days=1), "(d) set-up: after midnight the calendar date is the day after the event date")
nudged, asked = run_reminders()
ok(asked == 0 and not SENT and not PUSHED and not asked_at(pub_d),
   "(d) after midnight the date is past the event date, but the doors opened under 45 minutes ago: still nothing")
at(doors_d + timedelta(minutes=REMIND + 5))
nudged, asked = run_reminders()
ok(asked == 1 and len(swap_mails("Night late")) == 1 and len(swap_pushes(pub_d)) == 1,
   "(d) 45 minutes after the (post-midnight) doors the Swap reminder goes out")

# Doors just before midnight: the reminder falls after midnight, on the next calendar date.
pub_e, doors_e, title_e = scene("Night 2330", today, start_time="23:30")
at(doors_e + timedelta(minutes=REMIND - 5))
ok(A.today() == today + timedelta(days=1), "(d) set-up: 23:30 doors + 40 minutes is on the next calendar date")
nudged, asked = run_reminders()
ok(asked == 0 and not SENT, "(d) a new calendar date alone does not trigger the reminder")
at(doors_e + timedelta(minutes=REMIND + 5))
nudged, asked = run_reminders()
ok(asked == 1 and len(swap_mails("Night 2330")) == 1, "(d) it goes out 45 minutes after the doors, past midnight")

# Event dated yesterday, doors yesterday evening: due only by its doors, and still the Swap reminder within the night.
pub_f, doors_f, title_f = scene("Night yesterday", today - timedelta(days=1))
at(doors_f + timedelta(minutes=REMIND - 5))
nudged, asked = run_reminders()
ok(asked == 0 and not SENT, "(d) an event dated yesterday still waits for its own doors + 45 minutes")

# --- (e) a buyer who missed the night gets the 'did it work?' request instead
pub_m, doors_m, title_m = scene("Night missed", today - timedelta(days=1))
at(doors_m + timedelta(hours=11, minutes=59))
nudged, asked = run_reminders()
ok(len(swap_mails("Night missed")) == 1 and not rate_mails("Night missed"),  # (the 'Night yesterday' order above is due too)
   "(e) up to 12 hours after the doors it is still the Swap reminder")
pub_m, doors_m, title_m = scene("Night missed 2", today - timedelta(days=1))
at(doors_m + timedelta(hours=12))
nudged, asked = run_reminders()
ok(asked == 1 and len(rate_mails("Night missed 2")) == 1 and not swap_mails("Night missed 2"),
   "(e) 12 hours after the doors the buyer gets 'Did your ticket work?' by email")
ok(len(rate_pushes(pub_m)) == 1 and rate_pushes(pub_m)[0][1] == "Did your ticket work?" and not swap_pushes(pub_m),
   "(e) ...and by push")
ok("/orders/" + pub_m in rate_mails("Night missed 2")[0][2], "(e) the email links to the order page")
ok(asked_at(pub_m) is not None, "(e) the request is also only sent once")
at(doors_m + timedelta(days=3))
nudged, asked = run_reminders()
ok(asked == 0 and not SENT and not PUSHED, "(e) ...no repeat days later")
pub_m, doors_m, title_m = scene("Night missed 3", today - timedelta(days=5))
at(doors_m + timedelta(days=4))
nudged, asked = run_reminders()
ok(asked == 1 and len(rate_mails("Night missed 3")) == 1 and not swap_mails("Night missed 3"), "(e) a night days ago also gets the request")
pub_m, doors_m, title_m = scene("Night paid out", today, status="released")
at(doors_m + timedelta(hours=1))
nudged, asked = run_reminders()
ok(asked == 1 and len(rate_mails("Night paid out")) == 1 and not swap_mails("Night paid out"),
   "(e) an order already paid out gets the 'did it work?' request, not the Swap reminder")

A.utcnow, A.today = real_utcnow, real_today
print("\n%d failed" % len(fails) if fails else "\nAll good")
sys.exit(1 if fails else 0)
