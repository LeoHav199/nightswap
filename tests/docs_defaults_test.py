import os, re, sys
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

# The README's table of rules you can change must list the same NIGHTSWAP_* defaults as config.py.
# (The 150 vs 199 and 120 vs 200 mismatches of Nov 2026 would have been caught here.)
# Defaults are read from the config.py source, not from the loaded values, so a local settings file can't change the result.
readme = open("README.md", encoding="utf-8").read()
source = open("config.py", encoding="utf-8").read()

in_code = dict(re.findall(r'env\("(NIGHTSWAP_\w+)",\s*"([^"]*)"\)', source))
rows = re.findall(r"^\|\s*`(NIGHTSWAP_\w+)`\s*\|\s*`([^`]*)`\s*\|(.*)\|\s*$", readme, flags=re.M)

ok(len(rows) >= 7, "the README table has its settings (%d rows)" % len(rows))
ok(len(in_code) >= 7, "config.py defaults were found (%d)" % len(in_code))

seen = set()
for key, default, meaning in rows:
    ok(key not in seen, "%s is listed once in the README" % key)
    seen.add(key)
    ok(key in in_code, "%s from the README exists in config.py" % key)
    if key not in in_code:
        continue
    ok(default == in_code[key], "%s: README default %s matches config.py default %s" % (key, default, in_code[key]))
    # "Minimum buyer fee per ticket (EUR 1.99)": the euro amount in the text must be the same as the cents
    if key.endswith("_CENTS"):
        amount = re.search(r"EUR (\d+)\.(\d{2})", meaning)
        if amount:
            cents = int(amount.group(1)) * 100 + int(amount.group(2))
            ok(str(cents) == in_code[key], "%s: the EUR amount in the README text (%d cents) matches config.py" % (key, cents))
    # The price cap's text explains its own default, e.g. "200 = at most double"
    if key == "NIGHTSWAP_PRICE_CAP_PERCENT":
        ok(("%s = " % in_code[key]) in meaning, "%s: the README text explains the default %s" % (key, in_code[key]))

# The money rules must all be in the table, so adding one to config.py without documenting it is a visible choice
MONEY_RULES = ("NIGHTSWAP_FEE_PERCENT", "NIGHTSWAP_FEE_MIN_CENTS", "NIGHTSWAP_FEE_MAX_CENTS", "NIGHTSWAP_SELLER_FEE_PERCENT",
               "NIGHTSWAP_SELLER_FEE_MIN_CENTS", "NIGHTSWAP_PROFIT_SHARE_PERCENT", "NIGHTSWAP_PRICE_CAP_PERCENT",
               "NIGHTSWAP_PAYOUT_DELAY_HOURS")
for key in MONEY_RULES:
    ok(key in seen, "%s (a money rule) is in the README table" % key)

print("\n%d failed" % len(fails))
