import os, re, sys, tempfile, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
os.environ["TZ"] = "Europe/Amsterdam"; time.tzset()  # door_report_until uses the server's local time; the live site gets this from render.yaml
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **k: False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

AMS = ZoneInfo("Europe/Amsterdam")
def utc(y, mo, d, h, mi=0):  # an Amsterdam wall-clock time as a naive UTC datetime (what door_report_until returns)
    return datetime(y, mo, d, h, mi, tzinfo=AMS).astimezone(timezone.utc).replace(tzinfo=None)
def until(event_date, start_time):
    with app.app_context():
        return A.door_report_until({"event_id": None, "event_date": event_date, "start_time": start_time})
def check(event_date, start_time, expected, label):
    got = until(event_date, start_time)
    ok(got == expected, "%s (got %s UTC, expected %s UTC)" % (label, got, expected))

ok(A.DOOR_REPORT_UNTIL_HOUR == 6, "the cut-off hour is 06:00")
ok(datetime.now().astimezone().utcoffset() == datetime.now(AMS).utcoffset(), "this test runs on Amsterdam time")

# An ordinary winter night (event date 12 Jan 2027, Tuesday)
check("2027-01-12", "23:00", utc(2027, 1, 13, 6), "doors at 23:00: until 06:00 the next day")
check("2027-01-12", "02:00", utc(2027, 1, 13, 6), "doors at 02:00 (after midnight, same night): until 06:00 that same morning")
check("2027-01-12", "05:30", utc(2027, 1, 13, 6), "doors at 05:30: until 06:00 that same morning")
check("2027-01-12", "05:59", utc(2027, 1, 13, 6), "doors at 05:59: until 06:00 that same morning")
check("2027-01-12", "06:00", utc(2027, 1, 13, 6), "doors at exactly 06:00: until 06:00 the next day, not already closed")
check("2027-01-12", "06:01", utc(2027, 1, 13, 6), "doors at 06:01: until 06:00 the next day")
check("2027-01-12", "00:00", utc(2027, 1, 13, 6), "doors at midnight: until 06:00 that same morning")

# The deadline is always 06:00 on the Amsterdam clock and always after the doors, with a window of at most 24 hours
for start in ("23:00", "02:00", "05:30", "06:00", "12:00", "22:00"):
    with app.app_context():
        doors = A.doors_open_at({"event_id": None, "event_date": "2027-01-12", "start_time": start})
    got = until("2027-01-12", start)
    local = got.replace(tzinfo=timezone.utc).astimezone(AMS)
    ok((local.hour, local.minute) == (6, 0) and 0 < (got - doors).total_seconds() <= 24 * 3600,
       "doors %s: the deadline is 06:00 Amsterdam time, after the doors, within 24 hours" % start)

# 28 March 2027: clocks go forward at 02:00 (02:00 to 03:00), so the night is one hour short
check("2027-03-27", "23:00", utc(2027, 3, 28, 6), "28 March 2027, doors Saturday 23:00: until 06:00 CEST that morning (04:00 UTC)")
ok(until("2027-03-27", "23:00") == datetime(2027, 3, 28, 4, 0), "28 March 2027: 06:00 is 04:00 UTC (summer time has begun)")
check("2027-03-27", "01:00", utc(2027, 3, 28, 6), "28 March 2027, doors 01:00 (before the clocks move): until 06:00 that morning")
check("2027-03-27", "05:30", utc(2027, 3, 28, 6), "28 March 2027, doors 05:30 (after the clocks moved): until 06:00 that morning")
check("2027-03-27", "02:30", utc(2027, 3, 28, 6), "28 March 2027, doors 02:30 (a time that doesn't exist): still until 06:00 that morning")
check("2027-03-28", "06:00", utc(2027, 3, 29, 6), "28 March 2027, doors at exactly 06:00: until 06:00 the next day")
check("2027-03-28", "23:00", utc(2027, 3, 29, 6), "the evening after the clocks moved: until 06:00 the next morning (04:00 UTC)")

# 31 October 2027: clocks go back at 03:00 (03:00 to 02:00), so 02:00 to 03:00 happens twice
check("2027-10-30", "23:00", utc(2027, 10, 31, 6), "31 October 2027, doors Saturday 23:00: until 06:00 CET that morning (05:00 UTC)")
ok(until("2027-10-30", "23:00") == datetime(2027, 10, 31, 5, 0), "31 October 2027: 06:00 is 05:00 UTC (winter time has begun)")
check("2027-10-30", "01:00", utc(2027, 10, 31, 6), "31 October 2027, doors 01:00 (still summer time): until 06:00 that morning")
check("2027-10-30", "02:30", utc(2027, 10, 31, 6), "31 October 2027, doors 02:30 (a time that happens twice): still until 06:00 that morning")
check("2027-10-30", "05:30", utc(2027, 10, 31, 6), "31 October 2027, doors 05:30: until 06:00 that morning")
check("2027-10-31", "06:00", utc(2027, 11, 1, 6),"31 October 2027, doors at exactly 06:00: until 06:00 the next day")
check("2027-10-31", "23:00", utc(2027, 11, 1, 6), "the evening after the clocks moved back: until 06:00 the next morning (05:00 UTC)")

# Why TZ matters: on a UTC server the same doors would close an hour or two late
os.environ["TZ"] = "UTC"; time.tzset()
got = until("2027-01-12", "23:00")
ok(got != utc(2027, 1, 13, 6), "on a UTC server the deadline would be wrong (06:00 UTC = 07:00 Amsterdam), so render.yaml must set TZ")
os.environ["TZ"] = "Europe/Amsterdam"; time.tzset()

# render.yaml sets TZ for the live server
with open(os.path.join(sys.argv[1], "render.yaml"), encoding="utf-8") as f:
    yaml_text = f.read()
ok(re.search(r"^\s*-\s*key:\s*TZ\b[^\n]*\n\s*value:\s*Europe/Amsterdam\s*$", yaml_text, re.M) is not None,
   "render.yaml sets TZ to Europe/Amsterdam")
ok(len(re.findall(r"key:\s*TZ\b", yaml_text)) == 1, "render.yaml sets TZ exactly once")

print("\n%d failed" % len(fails))
