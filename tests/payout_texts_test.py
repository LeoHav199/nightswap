import html as htmllib, os, re, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp; config.DATA_DIR = tmp
import notify; notify.send_email = lambda *a, **kw: False
import app as A, i18n
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]

# Every place that describes what a seller is paid must say the seller fee, and the profit share when it's on
# (PROFIT_SHARE_PERCENT): the sell form (its data attributes feed the live "you receive" line), the seller-fee
# section of the terms and How it works, in all five languages. The legal_numbers test checks the whole pages;
# this one checks each place on its own, so removing the profit share from one of them is caught.
LANGS = ("en", "nl", "fr", "de", "es")

def pct(value):
    return "%g" % value

def seller_label(lang):
    base = i18n.t(lang, "fee_label", pct=pct(config.SELLER_FEE_PERCENT), min=i18n.money(lang, config.SELLER_FEE_MIN_CENTS))
    if not config.PROFIT_SHARE_PERCENT:
        return base
    return i18n.t(lang, "seller_fee_label_share", base=base, share=pct(config.PROFIT_SHARE_PERCENT))

def text_of(markup):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()

def sell_form_problems(page, lang):
    """What is wrong with the sell form's payout description (an empty list when it's fine)."""
    problems = []
    form = re.search(r"<form[^>]*data-sell-form[^>]*>", page)
    tag = form.group(0) if form else ""
    want = {"data-seller-pct": config.SELLER_FEE_PERCENT, "data-seller-min": config.SELLER_FEE_MIN_CENTS,
            "data-profit-share": config.PROFIT_SHARE_PERCENT}
    for attr, value in want.items():
        m = re.search(r'\b%s="([^"]*)"' % attr, tag)
        if not m:
            problems.append("%s is missing" % attr)
        elif float(m.group(1)) != float(value):
            problems.append("%s is %s, the setting is %s" % (attr, m.group(1), value))
    line = re.search(r'<dd data-seller-fee data-label="([^"]*)">([^<]*)</dd>', page)
    label = seller_label(lang)
    if not line:
        problems.append("the seller fee line is missing")
    else:
        if htmllib.unescape(line.group(1)) != label:
            problems.append("data-label is '%s', expected '%s'" % (htmllib.unescape(line.group(1)), label))
        if htmllib.unescape(line.group(2)).strip() != label:
            problems.append("the fee line says '%s', expected '%s'" % (htmllib.unescape(line.group(2)).strip(), label))
    return problems

SELLERS = {}  # one signed-in seller per language, reused (sign-in codes are rate limited)

def check_sell_form(lang):
    c = SELLERS.get(lang)
    if c is None:
        c = SELLERS[lang] = app.test_client(); c.get("/")  # sign in in English (the code text is parsed from the page), then switch language
        r = c.post("/login", data={"email": "seller-%s@test.nl" % lang, "_csrf": csrf(c)}, follow_redirects=True)
        code = re.search(r"code is (\d{6})", r.get_data(as_text=True))  # shown on screen in development
        c.post("/login/verify", data={"code": code.group(1) if code else "", "first_name": "Sam", "_csrf": csrf(c)})
        c.get("/lang/%s?next=/" % lang)
    r = c.get("/sell")
    return r.status_code, r.get_data(as_text=True)

def section(page_markup, label):
    """The terms section (from one <h2> to the next) that contains the seller fee label."""
    parts = re.split(r"(?=<h2>)", page_markup)
    return [text_of(p) for p in parts if label in text_of(p)]

def run_checks(tag):
    for lang in LANGS:
        label = seller_label(lang)
        ok(config.PROFIT_SHARE_PERCENT == 0 or htmllib.unescape(i18n.t(lang, "seller_fee_label_share", base="X", share="Y")) != "X",
           "%s%s: the profit share text exists" % (lang, tag))
        ok(("%g%%" % config.PROFIT_SHARE_PERCENT in label) == bool(config.PROFIT_SHARE_PERCENT),
           "%s%s: the label names the profit share exactly when PROFIT_SHARE_PERCENT is on ('%s')" % (lang, tag, label))

        status, page = check_sell_form(lang)
        problems = sell_form_problems(page, lang) if status == 200 else ["page gives %s" % status]
        ok(not problems, "%s%s: sell form has the seller fee and profit share%s" % (lang, tag, "" if not problems else " (" + "; ".join(problems) + ")"))

        c = app.test_client(); c.get("/lang/%s?next=/" % lang)
        how = c.get("/how-it-works").get_data(as_text=True)
        hits = re.findall(r"<dd>([^<]*)</dd>", how)
        ok(any(label in htmllib.unescape(h) for h in hits), "%s%s: How it works seller line states '%s'" % (lang, tag, label))
        terms = c.get("/terms").get_data(as_text=True)
        found = section(terms, label)
        ok(len(found) == 1 and "%s" % label in found[0], "%s%s: exactly one terms section states the seller fee '%s'" % (lang, tag, label))
        ok(len(found) == 1 and re.match(r"5\. ", found[0]) is not None, "%s%s: ...and it is the 'Buying and fees' section (5)" % (lang, tag))

ok(config.PROFIT_SHARE_PERCENT > 0, "the profit share is on by default (%g%%)" % config.PROFIT_SHARE_PERCENT)
run_checks("")

# The checker has teeth: a sell form without the profit share attribute, or with a wrong one, is rejected
status, page = check_sell_form("en")
ok(status == 200 and not sell_form_problems(page, "en"), "the unchanged sell form passes the checker")
stripped = re.sub(r'\s*data-profit-share="[^"]*"', "", page)
ok(stripped != page and any("data-profit-share" in p for p in sell_form_problems(stripped, "en")),
   "the checker fails when data-profit-share is removed from the sell form")
wrong = re.sub(r'data-profit-share="[^"]*"', 'data-profit-share="0"', page)
ok(any("data-profit-share" in p for p in sell_form_problems(wrong, "en")), "the checker fails when data-profit-share is 0 but the setting is on")
no_label = page.replace(htmllib.escape(seller_label("en")), "5% (min. €1)")
ok(bool(sell_form_problems(no_label, "en")), "the checker fails when the visible fee line leaves out the profit share")

# The live "you receive" script reads the attribute
js = open("static/js/app.js", encoding="utf-8").read()
ok("sell.dataset.profitShare" in js and "profitShare" in js.split("Same as seller_fee_for", 1)[-1][:400],
   "app.js reads data-profit-share and adds the profit share to the seller fee")

# With the profit share switched off, every place follows (no share text, attribute 0)
config.PROFIT_SHARE_PERCENT = 0.0
run_checks(" (profit share off)")

print("\n%d failed" % len(fails))
