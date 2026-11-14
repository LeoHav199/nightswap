"""Launch settings from CLAUDE.md must hold in the deploy blueprint and in config.py; a wrong value fails here."""
import importlib.util, os, re, shutil, sys, tempfile
ROOT = sys.argv[1]
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

# (blueprint variable, config.py attribute or None, expected value as text). The attribute is compared as text too.
SETTINGS = [
    ("TZ", None, "Europe/Amsterdam"),
    ("TRUST_PROXY_HOPS", "PROXY_HOPS", "2"),
    ("SHOW_ALL_NETHERLANDS", "SHOW_ALL_NETHERLANDS", "0"),
    ("NIGHTSWAP_PRICE_CAP_PERCENT", "PRICE_CAP_PERCENT", "200"),
    ("NIGHTSWAP_FEE_PERCENT", "FEE_PERCENT", "8"),
    ("NIGHTSWAP_FEE_MIN_CENTS", "FEE_MIN_CENTS", "199"),
    ("NIGHTSWAP_SELLER_FEE_PERCENT", "SELLER_FEE_PERCENT", "5"),
    ("NIGHTSWAP_SELLER_FEE_MIN_CENTS", "SELLER_FEE_MIN_CENTS", "100"),
    ("NIGHTSWAP_PROFIT_SHARE_PERCENT", "PROFIT_SHARE_PERCENT", "10"),
]
MUST_BE_IN_BLUEPRINT = {"TZ", "TRUST_PROXY_HOPS"}   # config.py has no TZ, and its hops default (1) is not the live value
SAFETY_FLAGS = ["NEW_SELLER_LIMITS", "TICKET_CHECKS"]  # FACE_PROOF_REQUIRED is off by Leo's choice since 14 Nov 2026


def blueprint_vars(text):
    """{name: value} for the 'envVars' entries that have a value (a small reader, so no YAML library is needed)."""
    out, key = {}, None
    for line in text.splitlines():
        line = re.sub(r"\s+#.*$", "", line.rstrip())
        m = re.match(r"\s*-\s*key:\s*(\S+)", line)
        if m:
            key = m.group(1); continue
        m = re.match(r"\s*value:\s*(.*)$", line)
        if m and key:
            out[key] = m.group(1).strip().strip('"').strip("'"); key = None
    return out


def num(text):
    try: return float(text)
    except (TypeError, ValueError): return text


def same(a, b):
    return num(str(a)) == num(str(b))


def load_clean_config():
    """config.py without .env and without NIGHTSWAP_* variables, so only its own defaults count."""
    tmp = tempfile.mkdtemp(); shutil.copy(os.path.join(ROOT, "config.py"), tmp)
    saved = {k: os.environ.pop(k) for k in list(os.environ)
             if k.startswith("NIGHTSWAP_") or k in ("SHOW_ALL_NETHERLANDS", "TRUST_PROXY_HOPS")}
    try:
        spec = importlib.util.spec_from_file_location("clean_config", os.path.join(tmp, "config.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    finally:
        os.environ.update(saved); shutil.rmtree(tmp, ignore_errors=True)
    return mod


def problems(bp, cfg):
    """Everything that differs from CLAUDE.md. bp = blueprint variables, cfg = a config module."""
    found = []
    for var, attr, want in SETTINGS:
        if var in bp:
            if not same(bp[var], want): found.append("blueprint %s=%s, expected %s" % (var, bp[var], want))
        elif var in MUST_BE_IN_BLUEPRINT:
            found.append("blueprint is missing %s (expected %s)" % (var, want))
        if attr and var not in MUST_BE_IN_BLUEPRINT:
            have = getattr(cfg, attr, None)
            if isinstance(have, bool): have = int(have)
            if var not in bp and not same(have, want): found.append("config.%s=%s, expected %s" % (attr, have, want))
    if list(getattr(cfg, "ENABLED_CITIES", [])) != ["Rotterdam"]:
        found.append("config.ENABLED_CITIES=%s, expected ['Rotterdam']" % (getattr(cfg, "ENABLED_CITIES", None),))
    if "ENABLED_CITIES" in bp and bp["ENABLED_CITIES"] != "Rotterdam":
        found.append("blueprint ENABLED_CITIES=%s, expected Rotterdam" % bp["ENABLED_CITIES"])
    for flag in SAFETY_FLAGS:
        if getattr(cfg, flag, None) is not True: found.append("config.%s is not True" % flag)
    return found


path = os.path.join(ROOT, "render.yaml")
ok(os.path.exists(path), "the deploy blueprint exists")
bp = blueprint_vars(open(path, encoding="utf-8").read())
cfg = load_clean_config()

ok(bp.get("TZ") == "Europe/Amsterdam", "blueprint: TZ=Europe/Amsterdam")
ok(bp.get("TRUST_PROXY_HOPS") == "2", "blueprint: TRUST_PROXY_HOPS=2 (Cloudflare + Render)")
ok(bp.get("TRUST_PROXY") == "1", "blueprint: TRUST_PROXY=1, so visitor IPs are read from the proxy")
ok(list(cfg.ENABLED_CITIES) == ["Rotterdam"], "config: ENABLED_CITIES is Rotterdam only")
ok(cfg.SHOW_ALL_NETHERLANDS is False, "config: SHOW_ALL_NETHERLANDS is off")
ok(cfg.PRICE_CAP_PERCENT == 200, "config: price cap is 200% (double the face value)")
ok(cfg.FEE_PERCENT == 8 and cfg.FEE_MIN_CENTS == 199, "config: buyer fee is 8%, minimum €1.99")
ok(cfg.SELLER_FEE_PERCENT == 5 and cfg.SELLER_FEE_MIN_CENTS == 100, "config: seller fee is 5%, minimum €1")
ok(cfg.PROFIT_SHARE_PERCENT == 10, "config: profit share is 10%")
for flag in SAFETY_FLAGS:
    ok(getattr(cfg, flag, None) is True, "config: %s is True" % flag)
ok(problems(bp, cfg) == [], "blueprint and config match CLAUDE.md: %s" % (problems(bp, cfg) or "no differences"))

# A wrong value must be caught (proves the check itself works).
class Wrong:  # a stand-in config module with one value changed at a time
    def __init__(self, **changes): self.__dict__.update({k: getattr(cfg, k) for k in dir(cfg) if k.isupper()}); self.__dict__.update(changes)
for name, wrong_bp, wrong_cfg in [
    ("TZ=UTC in the blueprint", {"TZ": "UTC"}, {}),
    ("TZ missing from the blueprint", {"TZ": None}, {}),
    ("TRUST_PROXY_HOPS=1 in the blueprint", {"TRUST_PROXY_HOPS": "1"}, {}),
    ("a different price cap set in the blueprint", {"NIGHTSWAP_PRICE_CAP_PERCENT": "120"}, {}),
    ("a different buyer fee minimum set in the blueprint", {"NIGHTSWAP_FEE_MIN_CENTS": "150"}, {}),
    ("another city in the blueprint", {"ENABLED_CITIES": "Amsterdam"}, {}),
    ("ENABLED_CITIES in config", {}, {"ENABLED_CITIES": ["Rotterdam", "Amsterdam"]}),
    ("SHOW_ALL_NETHERLANDS on in config", {}, {"SHOW_ALL_NETHERLANDS": True}),
    ("PRICE_CAP_PERCENT in config", {}, {"PRICE_CAP_PERCENT": 120}),
    ("FEE_PERCENT in config", {}, {"FEE_PERCENT": 10}),
    ("FEE_MIN_CENTS in config", {}, {"FEE_MIN_CENTS": 150}),
    ("SELLER_FEE_PERCENT in config", {}, {"SELLER_FEE_PERCENT": 4}),
    ("SELLER_FEE_MIN_CENTS in config", {}, {"SELLER_FEE_MIN_CENTS": 50}),
    ("PROFIT_SHARE_PERCENT in config", {}, {"PROFIT_SHARE_PERCENT": 0}),
    ("NEW_SELLER_LIMITS off", {}, {"NEW_SELLER_LIMITS": False}),
    ("TICKET_CHECKS off", {}, {"TICKET_CHECKS": False}),
]:
    changed = dict(bp)
    for k, v in wrong_bp.items():
        if v is None: changed.pop(k, None)
        else: changed[k] = v
    ok(problems(changed, Wrong(**wrong_cfg)) != [], "a wrong value fails the check: " + name)

# The reader copes with comments and quotes the way the blueprint is written.
ok(blueprint_vars('  - key: A   # note\n    value: "2"\n  - key: B\n    sync: false\n  - key: C\n    value: x y  # c\n') == {"A": "2", "C": "x y"},
   "blueprint reader: comments, quotes and entries without a value")

print("\n%d failed" % len(fails) if fails else "\nall passed")
