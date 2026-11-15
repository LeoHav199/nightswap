"""Accessibility of the main flows (14 Nov 2026): error and status messages are announced by screen readers
(role="alert" / role="status" / aria-live), the Swap slider has a reachable plain button as alternative, the sell
form's phone check announces its steps, and animations respect prefers-reduced-motion. Looks at the rendered HTML
and at the CSS/JS files. (tests/a11y_test.py checks lang, alt text and labels.)"""
import io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False; config.ADMIN_EMAILS = {"admin@example.com"}
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **kw: False
import app as A, i18n
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    with app.app_context(): A.db.run("DELETE FROM hits WHERE key LIKE 'login%' OR key LIKE 'verify%'")
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)
def tags(page, attr):
    """Every start tag in the page that has the given attribute, as text."""
    return [m for m in re.findall(r"<[a-z][^>]*>", page) if attr in m]

# ---- 1. Sign-in code screen: the error is announced and tied to the field (all five languages)
for lang in i18n.LANGS:
    c = app.test_client(); c.set_cookie("lang", lang); c.get("/")
    c.post("/login", data={"email": "x-%s@test.nl" % lang, "_csrf": csrf(c)})
    page = html(c.get("/login/verify"))
    ok('role="alert"' not in page.split("</h1>")[1].split("<button")[0], "%s: the code screen shows no error before a wrong code" % lang)
    r = c.post("/login/verify", data={"code": "000000", "first_name": "Zed", "_csrf": csrf(c)})
    page = html(r)
    err = [t for t in tags(page, "field-error")]
    ok(len(err) == 1 and 'role="alert"' in err[0] and 'id="code-error"' in err[0], "%s: a wrong sign-in code is shown in a role=alert paragraph" % lang)
    field = [t for t in tags(page, "autocomplete=\"one-time-code\"")]
    ok(len(field) == 1 and 'aria-invalid="true"' in field[0] and 'aria-describedby="code-error"' in field[0], "%s: the code field is marked invalid and points at the message" % lang)
c = app.test_client(); c.get("/")
r = c.post("/login", data={"email": "not-an-email", "_csrf": csrf(c)})
err = tags(html(r), "field-error")
ok(err and 'role="alert"' in err[0], "an email error on the sign-in form is a role=alert paragraph")

# A night, a seller with a verified phone and a ticket, and a buyer.
admin, seller, buyer, fresh = (app.test_client() for _ in range(4))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bea"); login(fresh, "f@test.nl", "Fay")
D = date.today().isoformat()
admin.post("/admin/events/save", data={"face_value_max": "500", "name": "Live Night", "venue": "Perron", "category": "club", "event_date": D,
                                       "door_time": "23:00", "_csrf": csrf(admin)})
ev = one("SELECT * FROM events WHERE name='Live Night'")
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20",
                               "adult": "yes", "ticket": (io.BytesIO(b"%PDF-1.4 a11y live test"), "t.pdf"), "_csrf": csrf(seller)},
                content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))

# ---- 2. Checkout messages: an error flash is role=alert, a success flash stays role=status
r = seller.post("/t/%d/buy" % lid, data={"_csrf": csrf(seller)})  # own ticket: refused
page = html(seller.get(r.location))
fl = tags(page, "flash-error")
ok(len(fl) == 1 and 'role="alert"' in fl[0], "a refused purchase (checkout error) is announced as role=alert")
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)})
r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
pub = r.location.split("/")[2].split("?")[0]
base = open("templates/base.html").read()
ok("role=\"{{ 'alert' if cat == 'error' else 'status' }}\"" in base and 'class="flash flash-{{ cat }}"' in base, "base.html gives error flashes role=alert and the others role=status")
for f in os.listdir("templates"):
    if not f.endswith(".html"): continue
    src = open(os.path.join("templates", f)).read()
    bad = [m for m in re.findall(r"<[a-z]+[^>]*\bfield-error\b[^>]*>", src) if 'role="alert"' not in m]
    ok(not bad, "templates/%s: every field-error paragraph has role=alert" % f) if "field-error" in src else None

# ---- 3. Swap: slider with a keyboard/screen-reader alternative
run("UPDATE listings SET event_date=? WHERE id=?", D, lid)
with app.app_context(): doors = A.doors_open_at(A.db.one("SELECT * FROM listings WHERE id=?", lid))
real_now = A.utcnow
A.utcnow = lambda: doors + timedelta(minutes=50)
for lang in i18n.LANGS:
    buyer.set_cookie("lang", lang)
    page = html(buyer.get("/orders/" + pub))
    slider = tags(page, 'role="slider"')
    ok(len(slider) == 1 and all(a in slider[0] for a in ('tabindex="0"', "aria-label=", "aria-valuemin=", "aria-valuemax=", "aria-valuenow=", 'aria-describedby="swap-desc"')),
       "%s: the Swap slider has a role, a label, a value range, a description and can be focused" % lang)
    btn = [m for m in re.findall(r"<button[^>]*data-swap-fallback[^>]*>[^<]+</button>", page)]
    ok(len(btn) == 1 and 'type="submit"' in btn[0] and " hidden" not in btn[0] and 'tabindex="-1"' not in btn[0] and btn[0].split(">", 1)[1].split("<")[0].strip(),
       "%s: the plain Swap button is in the page, labelled, reachable and not hidden" % lang)
    ok('id="swap-desc"' in page and 'id="swap-title"' in page and 'role="group" aria-labelledby="swap-title"' in page, "%s: the Swap card is a labelled group with a description" % lang)
A.utcnow = real_now
buyer.set_cookie("lang", "en")
js = open("static/js/app.js").read(); css = open("static/css/style.css").read()
ok("fallback.hidden = true" not in js and 'classList.add("swap-alt")' in js, "app.js keeps the plain Swap button instead of hiding it")
m = re.search(r"\.swap-alt:not\(:focus\)[^{]*\{([^}]*)\}", css)
ok(m and "display: none" not in m.group(1) and "visibility" not in m.group(1), "the plain Swap button is only visually hidden (never display:none), and shows when focused")
ok('"keydown"' in js and "ArrowRight" in js and "End" in js, "the slider also works with the arrow keys and End")

# ---- 4. Sell form phone check
page = html(fresh.get("/sell"))
note = tags(page, "data-phone-note"); done = tags(page, "data-phone-done"); perr = tags(page, "data-phone-error")
ok(note and 'aria-live="polite"' in note[0] and 'id="phone-note"' in note[0], "the phone 'code sent' note is a polite live region")
ok(page.index("data-phone-note") < page.index("data-phone-code ") if "data-phone-code " in page else False, "...placed outside the hidden code box, so it is already on the page when filled in")
ok(done and 'role="status"' in done[0] and " hidden" not in done[0], "'phone confirmed' is a role=status region that is on the page from the start")
ok(perr and 'role="alert"' in perr[0] and 'id="phone-error"' in perr[0], "the phone error is role=alert")
ok(perr and re.search(r'role="alert" hidden>', perr[0]), "...and it starts hidden, with the attribute properly separated")
ok(len(tags(page, "data-phone-code-input")) == 1 and "aria-describedby=" in tags(page, "data-phone-code-input")[0] and "aria-label=" in tags(page, "data-phone-code-input")[0], "the code field is labelled and described")
r = fresh.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                              "ticket": (io.BytesIO(b"%PDF-1.4 a11y live test 2"), "t.pdf"), "_csrf": csrf(fresh)}, content_type="multipart/form-data")
perr = tags(html(r), "data-phone-error")
ok(perr and 'role="alert"' in perr[0] and " hidden" not in perr[0], "'confirm your phone first' shows up as a visible role=alert message")
ok("aria-invalid" in js and "dataset.text" in js, "app.js marks the phone fields invalid and announces 'phone confirmed' in the live region")

# ---- 5. Reduced motion
def strip_blocks(text):
    """The CSS split into (rules outside @media, text inside prefers-reduced-motion: reduce blocks)."""
    reduce, out, i = [], "", 0
    while i < len(text):
        if text.startswith("@media", i) or text.startswith("@view-transition", i):
            head_end = text.index("{", i); head = text[i:head_end]; depth = 1; j = head_end + 1
            while depth: depth += {"{": 1, "}": -1}.get(text[j], 0); j += 1
            if "prefers-reduced-motion: reduce" in head: reduce.append(text[head_end + 1:j - 1])
            elif "no-preference" in head or head.startswith("@view-transition"): pass  # only moves for people who allow motion / the opt-in itself (switched off below)
            else: out += text[head_end + 1:j - 1]
            i = j
        else: out += text[i]; i += 1
    return out, "\n".join(reduce)
plain, reduce = strip_blocks(re.sub(r"/\*.*?\*/", "", css, flags=re.S))
ok(len(reduce) > 200, "style.css has prefers-reduced-motion rules")
def covered(sel):
    return any(part.strip() in reduce for part in sel.split(","))
moving = []
for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", plain):
    sel = sel.strip()
    if sel in ("from", "to") or re.fullmatch(r"[\d.%\s]+", sel): continue  # keyframe steps
    if sel.startswith("::view-transition") and "navigation: none" in reduce: continue  # page fades only run while @view-transition is on
    if re.search(r"(^|[;\s])animation\s*:\s*(?!none)", body):
        moving.append((sel, "animation"))
    if re.search(r"(^|[;\s])transition\s*:\s*(?!none)", body) and re.search(r"transform|opacity|left|width", body.split("transition")[1].split(";")[0]):
        moving.append((sel, "transition"))
uncovered = [(s, k) for s, k in moving if not all(covered(p) for p in s.split(","))]
ok(len(moving) > 10 and not uncovered, "every moving animation and sliding/fading transition in style.css is switched off under prefers-reduced-motion (%d checked%s)" % (len(moving), "; missing: %s" % uncovered[:5] if uncovered else ""))
ok('behavior: "smooth"' not in js and "prefers-reduced-motion: reduce" in js, "app.js scrolls smoothly only when reduced motion is not asked for")
ok(re.search(r"@media \(prefers-reduced-motion: no-preference\)[^\n]*swap-thumb", css) is not None, "the Swap slider's snap-back animation only runs when motion is allowed")

print("\n%d failed" % len(fails))
