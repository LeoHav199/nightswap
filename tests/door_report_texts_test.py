import html as htmllib, os, re, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **kw: False
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

# The door-report rules (report until DOOR_REPORT_UNTIL_HOUR after the doors, the seller has DISPUTE_REPLY_HOURS to
# answer) are in /help, the terms (§8) and How it works, in five languages. /help takes the numbers from the code; the
# terms and How it works have them typed in. So the numbers are read from the code here, written the way each language
# writes them, and looked for in every page: change a setting without changing the text and this test fails.
LANGS = ("en", "nl", "fr", "de", "es")

def page_text(lang, path):
    c = app.test_client(); c.get("/lang/%s?next=/" % lang)
    r = c.get(path)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", r.get_data(as_text=True)))
    return r.status_code, re.sub(r"\s+", " ", text)

def at_hour(lang, hour):
    """The cut-off as a pattern: 06:00 and 6:00 (en, nl, es), 6 h and 06h00 (fr), 6 Uhr (de), never part of a longer number."""
    h = "0?%d" % hour
    forms = {"en": r"before %s:00", "nl": r"vóór %s:00", "fr": r"avant %s(?: h|h00)", "de": r"vor %s Uhr",
             "es": r"antes de las %s:00"}
    return re.compile(r"(?<!\d)" + (forms[lang] % h).replace(" ", r"\s") + r"(?!\d)")

HOURS_WORD = {"en": "hours", "nl": "uur", "fr": "heures", "de": "Stunden", "es": "horas"}
def reply_window(lang, hours):
    return re.compile(r"(?<!\d)%d %s" % (hours, HOURS_WORD[lang]))

def check(lang, pages, hour, reply):
    """The problems for one language: what each page must say about the two numbers, and doesn't."""
    bad = []
    if not at_hour(lang, hour).search(pages["help"]): bad.append("help: door-report cut-off")
    if not reply_window(lang, reply).search(pages["help"]): bad.append("help: seller reply time")
    if not at_hour(lang, hour).search(pages["terms"]): bad.append("terms: door-report cut-off")
    if not reply_window(lang, reply).search(pages["terms"]): bad.append("terms: seller reply time")
    if not at_hour(lang, hour).search(pages["how"]): bad.append("how it works: door-report cut-off")  # (it has no reply time)
    return bad

hour, reply = A.DOOR_REPORT_UNTIL_HOUR, A.DISPUTE_REPLY_HOURS
ok(0 <= hour < 24 and reply > 0, "the two settings are real numbers (%s, %s)" % (hour, reply))

PAGES = {}
for lang in LANGS:
    PAGES[lang] = {}
    for key, path in (("help", "/help"), ("terms", "/terms"), ("how", "/how-it-works")):
        status, text = page_text(lang, path)
        ok(status == 200, "%s %s loads" % (lang, path))
        PAGES[lang][key] = text
    problems = check(lang, PAGES[lang], hour, reply)
    ok(not problems, "%s: /help, terms and How it works match the code (%02d:00, %d hours)%s"
       % (lang, hour, reply, "" if not problems else ": " + ", ".join(problems)))

# The check can fail: with other values in the code, the typed-in texts (terms, How it works) no longer match, so a
# changed setting without matching text is caught. /help is rendered from the code, so it follows the change itself.
other_hour, other_reply = (hour + 1) % 24, reply + 24
A.DOOR_REPORT_UNTIL_HOUR, A.DISPUTE_REPLY_HOURS = other_hour, other_reply
try:
    for lang in LANGS:
        bad = check(lang, PAGES[lang], other_hour, other_reply)
        ok(len(bad) == 5, "%s: a changed setting without new text fails all five checks (%s)" % (lang, ", ".join(bad) or "nothing failed"))
        fresh = dict(PAGES[lang], help=page_text(lang, "/help")[1])
        bad = check(lang, fresh, other_hour, other_reply)
        ok(bad == ["terms: door-report cut-off", "terms: seller reply time", "how it works: door-report cut-off"],
           "%s: /help follows the setting by itself, only the typed-in texts are left to fix (%s)" % (lang, ", ".join(bad)))
finally:
    A.DOOR_REPORT_UNTIL_HOUR, A.DISPUTE_REPLY_HOURS = hour, reply

# The help files take the numbers from the page's variables, they don't type them
for lang in LANGS:
    src = open("templates/help/%s.html" % lang, encoding="utf-8").read()
    ok("door_hour" in src and "reply_hours" in src, "templates/help/%s.html uses door_hour and reply_hours" % lang)

print("\n%d failed" % len(fails))
