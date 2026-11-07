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

# The how-it-works, terms and privacy pages exist in five languages, and fr/de/es are translations of en/nl.
# Their rules and numbers must say the same thing everywhere (checked 6 Nov 2026: they did).
LANGS = ("en", "nl", "fr", "de", "es")

def page_text(lang, path):
    c = app.test_client(); c.get("/lang/%s?next=/" % lang)
    r = c.get(path)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", r.get_data(as_text=True)))
    return r.status_code, re.sub(r"\s+", " ", text)

def pct(value):
    return "%g" % value

def buyer_label(lang):
    return i18n.t(lang, "fee_label", pct=pct(config.FEE_PERCENT), min=i18n.money(lang, config.FEE_MIN_CENTS))

def seller_label(lang):
    base = i18n.t(lang, "fee_label", pct=pct(config.SELLER_FEE_PERCENT), min=i18n.money(lang, config.SELLER_FEE_MIN_CENTS))
    if not config.PROFIT_SHARE_PERCENT:
        return base
    return i18n.t(lang, "seller_fee_label_share", base=base, share=pct(config.PROFIT_SHARE_PERCENT))

# What the five languages call "double the face value", "hours after the night", the 06:00 limit and a false report
DOUBLE_TERMS = {"en": "double the face value", "nl": "het dubbele van de nominale waarde", "fr": "le double du prix d'origine",
                "de": "das Doppelte des Originalpreises", "es": "el doble del precio original"}
DOUBLE_HOW = {"en": "2 × face value", "nl": "2 × nominale waarde", "fr": "2 × prix d'origine",
              "de": "2 × Originalpreis", "es": "2 × precio original"}
HOURS_AFTER = {"en": "hours after the night", "nl": "uur na de avond", "fr": "heures après la soirée",
               "de": "Stunden nach dem Abend", "es": "horas después de la noche"}
BEFORE_SIX = {"en": "before 06:00", "nl": "vóór 06:00", "fr": "avant 6 h", "de": "vor 6 Uhr", "es": "antes de las 6:00"}
FALSE_REPORT = {"en": "a report turns out to be false, the seller is paid", "nl": "blijkt een melding vals, dan wordt de verkoper betaald",
                "fr": "un signalement s'avère faux, le vendeur est payé", "de": "stellt sich eine Meldung als falsch heraus, wird der Verkäufer bezahlt",
                "es": "un aviso resulta falso, se paga al vendedor"}
HOURS_48 = {"en": "48 hours", "nl": "48 uur", "fr": "48 heures", "de": "48 Stunden", "es": "48 horas"}
DAYS_90 = {"en": "90 days", "nl": "90 dagen", "fr": "90 derniers jours", "de": "90 Tagen", "es": "90 días"}

ok(A.DOOR_REPORT_UNTIL_HOUR == 6, "the code closes door reports at 06:00")
ok(config.PRICE_CAP_PERCENT == 200 and config.PROFIT_SHARE_PERCENT > 0, "the cap is double the face value and the profit share is on (the texts below depend on it)")

for lang in LANGS:
    status, how = page_text(lang, "/how-it-works")
    ok(status == 200, "%s: how it works loads" % lang)
    ok(buyer_label(lang) in how, "%s how it works: buyer fee '%s'" % (lang, buyer_label(lang)))
    ok(seller_label(lang) in how, "%s how it works: seller fee with profit share '%s'" % (lang, seller_label(lang)))
    ok(DOUBLE_HOW[lang] in how, "%s how it works: maximum price is double the face value" % lang)
    ok("%d %s" % (config.PAYOUT_DELAY_HOURS, HOURS_AFTER[lang]) in how, "%s how it works: payout %d hours after the night" % (lang, config.PAYOUT_DELAY_HOURS))
    ok("%d %s" % (config.NEW_SELLER_PAYOUT_HOURS, HOURS_AFTER[lang]) in how, "%s how it works: new sellers %d hours after the night" % (lang, config.NEW_SELLER_PAYOUT_HOURS))
    ok(BEFORE_SIX[lang] in how, "%s how it works: door problems before 06:00" % lang)

    status, terms = page_text(lang, "/terms")
    ok(status == 200, "%s: terms load" % lang)
    ok(buyer_label(lang) in terms, "%s terms: buyer fee" % lang)
    ok(seller_label(lang) in terms, "%s terms: seller fee with profit share" % lang)
    ok(DOUBLE_TERMS[lang] in terms, "%s terms: price at most double the face value" % lang)
    ok("%d %s" % (config.PAYOUT_DELAY_HOURS, HOURS_AFTER[lang]) in terms, "%s terms: payout %d hours after the night" % (lang, config.PAYOUT_DELAY_HOURS))
    ok(BEFORE_SIX[lang] in terms, "%s terms: door problems before 06:00" % lang)
    ok(FALSE_REPORT[lang] in terms, "%s terms: false report means the seller is paid" % lang)
    ok(HOURS_48[lang] in terms and DAYS_90[lang] in terms, "%s terms: 48 hours for the seller to answer, 90 days clean record" % lang)

    status, privacy = page_text(lang, "/privacy")
    ok(status == 200 and "hello@sparetickets.eu" in privacy, "%s: privacy policy loads" % lang)

# Every number typed into a page (outside the template tags, which take theirs from the settings) is the same list in
# all five languages, so a changed retention period or deadline can't be updated in only some of them.
def typed_numbers(path):
    s = open(path, encoding="utf-8").read()
    s = re.sub(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", " ", s, flags=re.S)
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"(\d+):00", r"\1", s)  # 06:00, 6:00, 6 Uhr and 6 h are all "6"
    return [int(x) for x in re.findall(r"\d+", s)]

for page in ("how", "terms", "privacy"):
    reference = typed_numbers("templates/%s/en.html" % page)
    for lang in LANGS[1:]:
        found = typed_numbers("templates/%s/%s.html" % (page, lang))
        ok(found == reference, "templates/%s/%s.html has the same numbers as the English page" % (page, lang)
           + ("" if found == reference else " (%s vs %s)" % (found, reference)))
ok(len(typed_numbers("templates/terms/en.html")) > 20, "the number check really reads the terms")

print("\n%d failed" % len(fails))
