"""Payout explainer (17 Nov 2026): "Set up payouts" first shows a short Spare page (how long, what to have ready,
what Stripe asks and why); its button goes to /account/payouts/start, which does the Stripe setup as before."""
import os, re, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
import notify
notify.send_email = lambda to, subject, body, attachments=(), **kw: False
import app as A, i18n
from locales import fr, de, es
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)

c = app.test_client()
r = c.get("/account/payouts")
ok(r.status_code == 302 and "/login" in r.location, "signed out: sent to log in")
c.get("/"); r = c.post("/login", data={"email": "p@test.nl", "_csrf": csrf(c)}, follow_redirects=True)
c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": "Pia", "_csrf": csrf(c)})

r = c.get("/account/payouts"); page = html(r)
ok(r.status_code == 200, "the explainer page opens")
ok("Get paid for your tickets" in page and "About 3 minutes" in page, "it says what it is and how long it takes")
ok("ID or passport" in page and "Your IBAN" in page and "Your phone" in page, "it lists what to have ready")
ok("they text you a code" in page and "Sometimes a photo of your ID" in page, "it lists Stripe's steps")
ok("EU law" in page and "not to us" in page, "it says why and where the details go")
ok('href="/account/payouts/start"' in page and "Continue to Stripe" in page, "the button goes to the Stripe step")
ok('href="/account"' in page and "Not now" in page, "there's a way out")
ok(not one("SELECT payouts_ready FROM users WHERE email='p@test.nl'")["payouts_ready"], "opening the page changes nothing")

r = c.get("/account/payouts/start")
ok(r.status_code == 302, "start: goes on to the setup (development: finishes straight away)")
ok(one("SELECT payouts_ready FROM users WHERE email='p@test.nl'")["payouts_ready"], "start: payouts are set up")
r = c.get("/account/payouts")
ok(r.status_code == 302 and r.location.endswith("/account"), "once set up, the explainer sends you to your account")

c.get("/lang/nl"); c.post("/settings", data={"_csrf": csrf(c)})
with app.app_context(): A.db.run("UPDATE users SET payouts_ready=0 WHERE email='p@test.nl'")
page = html(c.get("/account/payouts"))
ok("Verder naar Stripe" in page, "Dutch: the page is translated")

keys = [k for k in i18n.STRINGS["en"] if k.startswith("po_")]
ok(len(keys) == 18, "18 explainer texts in English")
for lang, table in (("nl", i18n.STRINGS["nl"]), ("fr", fr.STRINGS), ("de", de.STRINGS), ("es", es.STRINGS)):
    missing = [k for k in keys if not table.get(k)]
    ok(not missing, "%s has every explainer text%s" % (lang, (": missing " + ", ".join(missing)) if missing else ""))

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
