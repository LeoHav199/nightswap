import ast, glob, os, re, sys
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import i18n
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

# Copy lint (6 Nov 2026): wording we decided against must not creep back into any text a visitor or an email
# recipient can read, in any of the 5 languages.
#  - em dashes (they read as machine-written); the "—" shown when a venue photo has no credit is a placeholder
#  - the absolute promise "No scams" (we say "Protected against scams")
#  - the old figures: the €1.50 minimum fee (now €1.99) and the "20% above" price cap (now double)
RULES = [
    ("em dash", re.compile("—|&mdash;|&#0*8212;|&#x0*2014;", re.I)),
    ("'No scams'", re.compile(r"\bno scams\b", re.I)),
    ("old minimum fee €1.50", re.compile(r"€\s?1[.,]50\b|\b1[.,]50\s?(?:€|euro)", re.I)),
    ("old price cap '20% above'", re.compile(
        r"\b20\s?(?:%|percent|procent|pour cent|prozent|por ciento)\s*"
        r"(?:above|over|more|boven|de plus|au-dessus|über|darüber|mehr|por encima|más)", re.I)),
]
# The only allowed hits: (file, exact text of the line's placeholder). Credits show "—" when a photo has no credit/licence.
ALLOWED = {"templates/credits.html": ["{{ v.photo_credit or '—' }}", "{{ v.photo_license or '—' }}"]}

def lint(name, text):
    """Returns the list of (rule, snippet) hits in one text."""
    for okay in ALLOWED.get(name, []):
        text = text.replace(okay, "")
    hits = []
    for rule, rx in RULES:
        for m in rx.finditer(text):
            hits.append((rule, text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")))
    return hits

def py_strings(path):
    """Every string in a Python file (comments left out, they are not shown to anyone)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

sources = {}
for path in sorted(glob.glob("templates/**/*.html", recursive=True)):  # pages, terms/how/privacy per language, email parts
    sources[path] = open(path, encoding="utf-8").read()
for path in ["i18n.py", "notify.py", "app.py"] + sorted(glob.glob("locales/*.py")):  # en and nl live in i18n.py
    if os.path.exists(path):
        sources[path] = "\n".join(py_strings(path))
for code in i18n.LANGS:  # the loaded texts too, so escapes like — are caught
    sources["strings[%s]" % code] = "\n".join(str(v) for v in i18n.STRINGS.get(code, {}).values())

ok(len(sources) > 50 and all(p in sources for p in ("notify.py", "i18n.py", "templates/terms/nl.html", "templates/how/es.html", "templates/privacy/de.html", "locales/fr.py", "locales/de.py", "locales/es.py")), "the lint looks at templates, notify.py and all 5 languages (%d sources)" % len(sources))
ok(all(len(sources["strings[%s]" % c]) > 1000 for c in i18n.LANGS), "all 5 languages have texts to check")
for name, text in sources.items():
    found = lint(name, text)
    ok(not found, "copy lint: %s%s" % (name, "" if not found else " has " + "; ".join("%s near '%s'" % h for h in found[:3])))

# The lint itself must catch each kind of slip (and let the placeholder through)
for sample, rule in (("Hi — welcome", "em dash"), ("Hi &mdash; welcome", "em dash"), ("No scams, ever", "'No scams'"),
                     ("Minimum fee €1.50", "old minimum fee"), ("Minimaal 1,50 euro", "old minimum fee"),
                     ("up to 20% above face value", "old price cap"), ("maximaal 20% boven de nominale prijs", "old price cap"),
                     ("20 % de plus que le prix", "old price cap"), ("bis zu 20 % über dem Preis", "old price cap")):
    ok(any(rule in r for r, _ in lint("x.html", sample)), "the lint catches: %s" % sample)
for sample in ("Fee €1.99", "double the face value", "120% of face value", "Protected against scams", "Photo: {{ v.photo_credit or '—' }}"):
    ok(not lint("templates/credits.html", sample), "the lint leaves this alone: %s" % sample)
ok(any(lint("templates/other.html", "{{ v.photo_credit or '—' }}")), "the credits placeholder is allowed only in credits.html")

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
