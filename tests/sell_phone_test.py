"""The phone check sits at the end of the sell form (7 Nov 2026): sellers fill in the ticket first and confirm
their phone last. Every safety rule stays: a verified number is needed before anything is saved, one verified
number per account, blocked numbers, rate limits, 18+."""
import io, os, re, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False
config.STRIPE_SECRET_KEY = ""
tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up")
config.ADMIN_EMAILS = {"admin@example.com"}
import app as A
app = A.app; app.config["TESTING"] = True
import seed
with app.app_context(): A.db.init(); seed.run()

def csrf(c):
    with c.session_transaction() as s: return s["csrf"]

def login(c, email, name):
    c.get("/")
    r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    code = re.search(r"code is (\d{6})", r.get_data(as_text=True)).group(1)
    r = c.post("/login/verify", data={"code": code, "first_name": name, "_csrf": csrf(c)})
    assert r.status_code == 302, r.status_code

def html(r): return r.get_data(as_text=True)
def jpost(c, url, body): return c.post(url, json=body, headers={"X-CSRF-Token": csrf(c)}).get_json()
def phone_of(email):
    with app.app_context(): return A.db.one("SELECT phone, phone_verified_at FROM users WHERE email=?", email)
def listings():
    with app.app_context(): return A.db.one("SELECT COUNT(*) AS n FROM listings")["n"]
ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg)

pdf_n = [0]
def post(c, **extra):
    pdf_n[0] += 1
    data = {"name_on_ticket": "no", "no_double_sell": "yes", "venue": "Perron", "event_date": "2026-11-08", "face_value": "20",
            "price": "18", "event_name": "Phone Night", "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 phone test " + str(pdf_n[0]).encode()), "t.pdf"), "_csrf": csrf(c)}
    data.update(extra)
    return c.post("/sell", data=data, content_type="multipart/form-data")

seller = app.test_client(); login(seller, "seller@test.nl", "Femke")

# The form is open before the phone is confirmed, with the phone step as the last block.
r = seller.get("/sell"); page = html(r)
ok(r.status_code == 200 and 'name="ticket"' in page and 'name="face_value"' in page, "an unverified seller sees the whole sell form, not a phone page")
ok("data-phone-step" in page and "data-phone-send" in page, "...with a phone step")
ok(page.index('name="no_double_sell"') < page.index("data-phone-step") < page.index(">Post ticket<"), "...placed after the ticket questions and right before the Post button")
ok(page.index('class="receive-line"') > page.index("data-phone-step"), "...and 'You receive' stays directly above the Post button")
ok("/account/phone?next=%2Fsell" in page or "/account/phone?next=/sell" in page, "without JavaScript there is a link to the separate phone page")
ok(seller.get("/account/phone").status_code == 200, "the separate phone page still works")

# Nothing is saved without a verified phone.
before = listings()
r = post(seller, adult="")  # (without 18+ ticked, so the later 18+ check below still means something)
ok(r.status_code == 200 and "Confirm your phone number first" in html(r) and listings() == before, "posting before the phone is confirmed saves nothing and says why")
ok(phone_of("seller@test.nl")["phone_verified_at"] is None, "...and doesn't verify anything")

# The code steps (JSON, no page change).
ok(seller.post("/account/phone/send", json={"phone": "0611111111"}).status_code == 400, "sending a code needs the CSRF token")
res = jpost(seller, "/account/phone/send", {"phone": "abc"})
ok(res["ok"] is False and "valid" in res["error"], "a bad number is refused with a message")
with app.app_context(): A.db.run("INSERT INTO blocked_phones (phone, user_id) VALUES (?, 0)", "+31699999999")
res = jpost(seller, "/account/phone/send", {"phone": "06 99999999"})
ok(res["ok"] is False and "can't be used" in res["error"], "a blocked number is refused: %r" % res.get("error"))
with app.app_context(): A.db.run("DELETE FROM blocked_phones")

other = app.test_client(); login(other, "other@test.nl", "Otto")
res = jpost(other, "/account/phone/send", {"phone": "0611111111"})
code = re.search(r"code is (\d{6})", res["dev"]).group(1)
ok(res["ok"] is True and "+31611111111" in res["note"], "a good number gets a code (shown on screen in development)")
ok(jpost(other, "/account/phone/check", {"code": "000000" if code != "000000" else "111111"})["ok"] is False, "a wrong code is refused")
res = jpost(other, "/account/phone/check", {"code": code})
ok(res["ok"] is True and phone_of("other@test.nl")["phone_verified_at"], "the right code verifies the phone")

# One verified number per account.
res = jpost(seller, "/account/phone/send", {"phone": "0611111111"})
ok(res["ok"] is False and "already verified on another account" in res["error"], "a number verified on another account is refused")
ok(phone_of("seller@test.nl")["phone_verified_at"] is None, "...and the seller stays unverified")

# A number blocked after the code went out can't be confirmed.
res = jpost(seller, "/account/phone/send", {"phone": "0622222222"})
code = re.search(r"code is (\d{6})", res["dev"]).group(1)
with app.app_context(): A.db.run("INSERT INTO blocked_phones (phone, user_id) VALUES (?, 0)", "+31622222222")
res = jpost(seller, "/account/phone/check", {"code": code})
ok(res["ok"] is False and phone_of("seller@test.nl")["phone_verified_at"] is None, "a number blocked since the code was sent can't be confirmed")
with app.app_context(): A.db.run("DELETE FROM blocked_phones"); A.db.run("DELETE FROM codes"); A.db.run("DELETE FROM hits")

# Checking a code without asking for one first.
fresh = app.test_client(); login(fresh, "fresh@test.nl", "Fay")
ok(jpost(fresh, "/account/phone/check", {"code": "123456"})["ok"] is False, "a code check without a number does nothing")

# Rate limit: the same limit as the phone page (5 codes an hour per account).
spam = app.test_client(); login(spam, "spam@test.nl", "Sam")
answers = [jpost(spam, "/account/phone/send", {"phone": "06%08d" % (30000000 + i)}) for i in range(7)]
ok(answers[0]["ok"] and any(not a["ok"] and "too many attempts" in a["error"].lower() for a in answers), "asking for codes over and over is rate limited")
with app.app_context(): A.db.run("DELETE FROM hits"); A.db.run("DELETE FROM codes")

# The whole flow: fill in, confirm the phone last, post.
res = jpost(seller, "/account/phone/send", {"phone": "0633333333"})
code = re.search(r"code is (\d{6})", res["dev"]).group(1)
ok(jpost(seller, "/account/phone/check", {"code": code})["ok"] is True and phone_of("seller@test.nl")["phone"] == "+31633333333", "the seller confirms the phone")
before = listings()
r = post(seller, adult="")
ok(r.status_code == 200 and listings() == before, "18+ is still asked: no confirmation, no listing")
r = post(seller)
ok(r.status_code == 302 and "/t/" in r.location and listings() == before + 1, "after confirming the phone the ticket is posted")
page = html(seller.get("/sell"))
ok("data-phone-step" not in page and 'name="ticket"' in page, "a verified seller no longer sees the phone step")

# SMS_REQUIRED without Twilio on the real site: the step explains instead of sending.
config.SMS_REQUIRED = True; dev_was, key_was = config.DEV, config.STRIPE_SECRET_KEY
config.DEV = False; config.STRIPE_SECRET_KEY = "sk_live_x"
page = html(fresh.get("/sell")); res = jpost(fresh, "/account/phone/send", {"phone": "0644444444"})
config.SMS_REQUIRED = False; config.DEV, config.STRIPE_SECRET_KEY = dev_was, key_was
ok("Selling opens soon" in page and "data-phone-send" not in page and res["ok"] is False, "with SMS_REQUIRED and no Twilio the step says selling opens soon")

# The script and its wiring.
js = open("static/js/app.js").read()
ok("data-phone-step" in js and "data-phone-confirm" in js and "X-CSRF-Token" in js, "the sell script drives the phone step")
