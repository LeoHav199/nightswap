import os, re, sys
os.chdir(sys.argv[1])
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

# docs/launch-day-runbook.md is the short checklist for 11 Nov 2026. It must stay true to the code.
doc = open("docs/launch-day-runbook.md", encoding="utf-8").read()
config_src = open("config.py", encoding="utf-8").read()
app_src = open("app.py", encoding="utf-8").read()

# Every setting name (ALL_CAPS with an underscore) is read from the environment in config.py
names = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", doc)))
ok(len(names) >= 5, "the runbook names its settings (%s)" % ", ".join(names))
for n in names:
    ok(re.search(r'env\("%s"' % n, config_src) is not None, "%s is a setting in config.py" % n)
for n in ("BACKUP_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "TRUST_PROXY_HOPS"):
    ok(n in names, "the runbook mentions %s" % n)

# Every flask command it tells Leo to run exists
for cmd in sorted(set(re.findall(r"flask --app app ([a-z-]+)", doc))):
    ok('@app.cli.command("%s")' % cmd in app_src, "flask command %s exists" % cmd)
ok("flask --app app clear-test-data`" in doc and "flask --app app clear-test-data --yes`" in doc,
   "clear-test-data is run first as a list, then with --yes")
ok(doc.index("clear-test-data --yes") < doc.index("`STRIPE_SECRET_KEY`"), "the data is cleared before the live key goes in")
ok("live Stripe" in doc and "paid with real money" in doc, "it covers the check that no order was paid with live Stripe")

# The live webhook lists all 6 events, the same ones the code handles
events = ("checkout.session.completed", "checkout.session.async_payment_succeeded", "checkout.session.expired",
          "charge.dispute.created", "charge.dispute.closed", "charge.refunded")
step4 = doc.split("## 4.")[1].split("## 5.")[0]
ok(sorted(re.findall(r"^  - `([a-z_.]+)`$", step4, re.M)) == sorted(events), "step 4 lists exactly the 6 events")
for e in events:
    ok(('"%s"' % e) in app_src, "event %s is handled in app.py" % e)

# The admin screens and buttons it names exist
admin = open("templates/admin.html", encoding="utf-8").read() + open("templates/admin_waitlist.html", encoding="utf-8").read()
for label in ("Launch check", "Network check", "Open the site to everyone", "Send launch email (next", "Pause new sales"):
    ok(label in admin, "'%s' exists in the admin pages" % label)
ok("first batch of 20" in doc and "LIMIT 20" in app_src, "the first waitlist batch is 20, as in the code")

# The deploy ban: Thursday to Saturday evenings, 18:00 to 04:00, starting the day after launch
ok("Wednesday 11 November 2026" in doc and "Thursday 12 Nov, 18:00 to Friday 13 Nov, 04:00" in doc, "the ban starts Thursday 12 Nov 18:00")
ok("Saturday 14 Nov, 18:00 to Sunday 15 Nov, 04:00" in doc, "the ban runs to Sunday 04:00")

# No secret in it: no real-looking keys, and no instruction to paste one into a chat
ok(not re.search(r"\b(sk|rk|pk|whsec)_(live|test)?_?[A-Za-z0-9]{16,}", doc), "no key-looking strings")
ok("never into a chat" in doc.lower().replace("\n", " ") or "Never into a chat" in doc, "it says secrets never go into a chat")
ok(not re.search(r"(paste|send|share|tell)[^.\n]{0,40}(key|secret|password|token)[^.\n]{0,40}(to Claude|in the chat|here)", doc, re.I),
   "it never asks for a secret to be pasted into the chat")

print("\n%d failed" % len(fails) if fails else "\nAll passed")
sys.exit(1 if fails else 0)
