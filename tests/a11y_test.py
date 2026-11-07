import io, os, re, sys, tempfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.ADMIN_SECOND_FACTOR = False; config.ADMIN_EMAILS = {"admin@example.com"}
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **kw: False
import app as A, i18n, qrcode
from PIL import Image
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def png(qr):
    img = Image.new("RGB", (800, 1000), "white")
    img.paste(qrcode.make(qr).convert("RGB").resize((400, 400)), (200, 300))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()

# Reads a page the way a screen reader's checker would: the <html lang>, every image, and every form field with its label.
# (Posters are decorative: the event's name is written right next to them, so alt="" is correct; a missing alt attribute is not.)
class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.lang = None; self.imgs = []; self.fields = []; self.label_for = set(); self.ids = []
        self.labelledby = []; self.lang_links = []; self.label_depth = 0
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html": self.lang = a.get("lang")
        if a.get("id"): self.ids.append(a["id"])
        if a.get("aria-labelledby"): self.labelledby.append(a["aria-labelledby"])
        if tag == "a" and a.get("hreflang"): self.lang_links.append(a)
        if tag == "img": self.imgs.append(a)
        if tag == "label":
            self.label_depth += 1
            if a.get("for"): self.label_for.add(a["for"])
        if tag in ("input", "select", "textarea"):
            if not (tag == "input" and a.get("type", "text") in ("hidden", "submit", "button", "reset")):
                self.fields.append((tag, a, self.label_depth > 0))
    def handle_endtag(self, tag):
        if tag == "label": self.label_depth = max(0, self.label_depth - 1)
def check(label, text, lang):
    p = Page(); p.feed(text); problems = []
    if p.lang != lang: problems.append("html lang is %r, not %r" % (p.lang, lang))
    for a in p.lang_links:
        if a.get("lang") != a["hreflang"]: problems.append("language link %s has lang=%r" % (a["hreflang"], a.get("lang")))
    for a in p.imgs:
        if a.get("alt") is None: problems.append("image without alt: %s" % (a.get("src") or "")[:60])
    for tag, a, wrapped in p.fields:
        named = wrapped or (a.get("id") and a["id"] in p.label_for) or a.get("aria-label", "").strip() or a.get("aria-labelledby") or a.get("title", "").strip()
        if not named:
            problems.append("%s without a label: %s" % (tag, a.get("name") or a.get("id") or a.get("type")))
    for ref in p.labelledby:
        for i in ref.split():
            if i not in p.ids: problems.append("aria-labelledby points at a missing id: " + i)
    for i in set(p.ids):
        if p.ids.count(i) > 1: problems.append("id used twice: " + i)
    return problems, p

# A night with a poster, a ticket on sale, and a paid order.
os.makedirs(os.path.join(config.UPLOAD_DIR, "posters"), exist_ok=True)
poster = "ab" * 16 + ".jpg"
Image.new("RGB", (800, 450), (40, 90, 160)).save(os.path.join(config.UPLOAD_DIR, "posters", poster), "JPEG")
D = (date.today() + timedelta(days=3)).isoformat()
admin, seller, buyer = (app.test_client() for _ in range(3))
login(admin, "admin@example.com", "Ad"); login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bea")
admin.post("/admin/events/save", data={"face_value_max": "500", "name": "Gala", "venue": "Maassilo", "category": "club", "event_date": D, "_csrf": csrf(admin)})
with app.app_context():
    ev = A.db.one("SELECT * FROM events WHERE name='Gala'")
    A.db.run("UPDATE events SET poster_file=? WHERE id=?", poster, ev["id"])
r = seller.post("/account/phone", data={"phone": "0611111111", "_csrf": csrf(seller)}, follow_redirects=True)
seller.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(seller)})
r = seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(ev["id"]), "face_value": "20", "price": "20", "adult": "yes",
                               "ticket": (io.BytesIO(png("A11Y-1")), "t.png"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
lid = int(re.search(r"/t/(\d+)", r.location).group(1))
r = buyer.post("/t/%d/buy" % lid, data={"_csrf": csrf(buyer)})
r = buyer.post(r.location, data={"_csrf": csrf(buyer)})
pub = r.location.split("/")[2].split("?")[0]
night = date.today() if datetime.now().hour >= 12 else date.today() - timedelta(days=1)

seen_img = seen_field = 0
for lang in i18n.LANGS:
    for cl in (admin, seller, buyer): cl.set_cookie("lang", lang)
    anon = app.test_client(); anon.set_cookie("lang", lang)
    anon.get("/"); r = anon.post("/login", data={"email": "new-%s@test.nl" % lang, "_csrf": csrf(anon)}, follow_redirects=True)
    pages = [
        ("home", anon.get("/")),
        ("event", anon.get("/event?venue=Maassilo&date=%s&name=Gala" % D)),
        ("listing", anon.get("/t/%d" % lid)),
        ("sign-in", anon.get("/login")),
        ("sign-in code", r),
        ("sell", seller.get("/sell")),
        ("order", buyer.get("/orders/" + pub)),
    ]
    with app.app_context():
        A.db.run("UPDATE listings SET event_date=?, start_time='12:00' WHERE id=?", night.isoformat(), lid)
    pages.append(("order on the night", buyer.get("/orders/" + pub)))
    with app.app_context():
        A.db.run("UPDATE listings SET event_date=?, start_time=NULL WHERE id=?", D, lid)
    for label, resp in pages:
        text = html(resp)
        problems, p = check(label, text, lang)
        seen_img += len(p.imgs); seen_field += len(p.fields)
        ok(resp.status_code == 200 and not problems, "%s: %s page has lang, alt text and labels%s" % (lang, label, " (" + "; ".join(problems[:4]) + ")" if problems else ""))
ok(seen_img > 0 and seen_field > 20, "the test really looked at images and fields (%d images, %d fields)" % (seen_img, seen_field))

print("\n%d failed" % len(fails))
