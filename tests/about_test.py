import os, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
import app as A, i18n
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def html(r): return r.get_data(as_text=True)

# Trust without names: "run by two Erasmus University students", and Leo and Ilyas stay anonymous.
c = app.test_client()
about = html(c.get("/about"))
ok("two students at Erasmus University Rotterdam" in about and "usually within a few hours" in about, "About page: two Erasmus University students who reply fast")
ok(not any(n in about for n in ("Leo", "Ilyas", "Havg", "Arnal")), "the About page names nobody")
c.get("/lang/nl?next=/")
ok("Erasmus Universiteit Rotterdam" in html(c.get("/about")), "About page in Dutch")
for code in ("fr", "de", "es"):
    ok(i18n.t(code, "about_title") != i18n.t("en", "about_title") and "Erasmus" in i18n.t(code, "about_lead"), "About page translated (%s)" % code)
c = app.test_client()
home = html(c.get("/"))
ok("Run by two students at Erasmus University Rotterdam." in home and 'href="/about"' in home, "homepage says who makes Spare and links to About")
with app.app_context():
    A.set_setting("coming_soon", "1")
cs = html(c.get("/"))
ok("Run by two students at Erasmus University Rotterdam." in cs, "the coming-soon page says it too")
ok(c.get("/about").status_code == 200 and "About Spare" in html(c.get("/about")), "About stays open in coming-soon mode")
with app.app_context():
    A.set_setting("coming_soon", "0")

# Emails are signed by the team, in the reader's language; extra paragraphs still go above the sign-off.
bodies = {(lang, k): v for lang, s in i18n.STRINGS.items() for k, v in s.items() if k.startswith("mail_") and k.endswith("_body")}
ok(not any(v.endswith("\n\nSpare") for v in bodies.values()), "no email ends with a bare 'Spare' any more")
ok(i18n.t("en", "mail_bought_body").endswith("The Spare team, two students at Erasmus University Rotterdam")
   and i18n.t("nl", "mail_bought_body").endswith("Het Spare-team, twee studenten van de Erasmus Universiteit Rotterdam"), "emails signed by the team (en, nl)")
ok(A.with_extra(i18n.t("en", "mail_bought_body"), "EXTRA").endswith("EXTRA\n\nThe Spare team, two students at Erasmus University Rotterdam"),
   "extra paragraphs go above the sign-off")

# Company details: name and address filled in, no "[PLACEHOLDER]" anywhere, numbers left out until they're set
c = app.test_client()
for path in ("/privacy", "/terms", "/"):
    page = html(c.get(path))
    ok("Spare vof" in page and "Lombardkade 47A, 3011 ZC Rotterdam" in page and "[KVK" not in page and "[VAT" not in page and "[COMPANY" not in page,
       "%s shows Spare vof and its address, without placeholders" % path)
ok("KvK" not in html(c.get("/privacy")).split("Who is responsible")[1].split("</p>")[0], "no empty 'KvK' until the number is set")
config.KVK_NUMBER = "12345678"
ok("KvK 12345678" in html(c.get("/privacy")) and "KvK 12345678" in html(c.get("/")), "the KvK number appears once it's set")
config.KVK_NUMBER = ""
ok("Checking a problem with the organiser" in html(c.get("/privacy")), "the privacy policy says what's shared with an organiser in a dispute")

print("\n%d failed" % len(fails))
