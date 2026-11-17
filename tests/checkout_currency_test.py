"""Checkout is always in euros (17 Nov 2026): Stripe's Adaptive Pricing is switched off per session, so a buyer
whose connection looks foreign isn't shown dollars (and keeps iDEAL, which only works in euros)."""
import os, sys, types
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = "sk_test_x"
import payments
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))

seen = {}
def create(**kw):
    seen.update(kw); return types.SimpleNamespace(id="cs_test", url="https://checkout.stripe.com/x")
fake = types.SimpleNamespace(checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=create)))
payments._stripe = lambda: fake
order = {"price_cents": 2000, "fee_cents": 199, "public_id": "ABC123"}
for ideal_only in (False, True):
    seen.clear()
    sid, url = payments.create_checkout(order, "Friday Club Night", "b@test.nl", "https://x/{CHECKOUT_SESSION_ID}", "https://x/c", ideal_only=ideal_only)
    label = "first-time buyer (iDEAL only)" if ideal_only else "returning buyer"
    ok(seen.get("adaptive_pricing") == {"enabled": False}, "%s: Adaptive Pricing is off, so no dollars" % label)
    ok(all(i["price_data"]["currency"] == "eur" for i in seen.get("line_items", [])), "%s: every line is in euros" % label)
    ok(sid == "cs_test" and url.startswith("https://checkout.stripe.com/"), "%s: the session is returned" % label)

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
