import os, string, sys
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import i18n
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

# Every language has every key English has, with the same {placeholders}, and no stray keys.
def placeholders(text):
    return sorted(set(name for _, name, _, _ in string.Formatter().parse(text) if name is not None))

en = i18n.STRINGS["en"]
for code in ("nl", "fr", "de", "es"):
    strings = i18n.STRINGS[code]
    missing = sorted(set(en) - set(strings))
    ok(not missing, "%s has every English key (%d missing: %s)" % (code, len(missing), ", ".join(missing[:40])))
    extra = sorted(set(strings) - set(en))
    ok(not extra, "%s has no keys English lacks (%s)" % (code, ", ".join(extra[:40])))
    empty = sorted(k for k, v in strings.items() if not str(v).strip())
    ok(not empty, "%s has no empty texts (%s)" % (code, ", ".join(empty[:40])))
    bad = []
    for key, text in strings.items():
        if key in en and isinstance(text, str) and isinstance(en[key], str):
            try:
                if placeholders(text) != placeholders(en[key]):
                    bad.append("%s en=%s %s=%s" % (key, placeholders(en[key]), code, placeholders(text)))
            except ValueError:
                bad.append("%s has broken braces" % key)
    ok(not bad, "%s uses the same {placeholders} as English (%s)" % (code, "; ".join(bad[:20])))

# The long pages have one file per language.
for page in ("how", "terms", "privacy"):
    gone = [code for code in i18n.LANGS if not os.path.exists(os.path.join("templates", page, code + ".html"))]
    ok(not gone, "templates/%s has a file for every language (%s missing)" % (page, ", ".join(gone) or "none"))

print("\n%d failed" % len(fails) if fails else "\nall passed")
