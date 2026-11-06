import os, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATA_DIR = tmp; config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; notify.send_email = lambda *a, **kw: False
import app as A, seed
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)

# A database full of test-mode activity, plus the things that must survive the launch
with app.app_context():
    seed.run()
run("INSERT INTO users (email, first_name, stripe_account_id, payouts_ready) VALUES ('admin@example.com', 'Ad', 'acct_test_123', 1)")
run("INSERT INTO waitlist (email, interest, lang, token) VALUES ('fan@student.eur.nl', 'buy', 'en', 'tok123')")
seller = one("SELECT * FROM listings LIMIT 1")
buyer = one("SELECT id FROM users WHERE email != 'admin@example.com' AND id != ? LIMIT 1", seller["seller_id"])["id"]
run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, payment_ref, expires_at)
       VALUES ('ord1', ?, ?, ?, 1, 1500, 199, 1699, 'paid', 'cs_test_abc', datetime('now'))""", seller["id"], buyer, seller["seller_id"])
ticket_file = one("SELECT file FROM tickets WHERE file != '' LIMIT 1")["file"]
path = os.path.join(config.UPLOAD_DIR, ticket_file)
ok(os.path.exists(path), "(setup) a test ticket file exists")
events_before = one("SELECT COUNT(*) AS n FROM events")["n"]
runner = app.test_cli_runner()

# Without --yes nothing changes
out = runner.invoke(args=["clear-test-data"]).output
ok("Would delete:" in out and "Nothing changed" in out, "a dry run only shows what would go")
ok(one("SELECT COUNT(*) AS n FROM listings")["n"] > 0 and os.path.exists(path), "...and deletes nothing")

# A real-money order stops it completely
run("UPDATE orders SET payment_ref='cs_live_real' WHERE public_id='ord1'")
res = runner.invoke(args=["clear-test-data", "--yes"])
ok(res.exit_code != 0 and "real money" in res.output and one("SELECT COUNT(*) AS n FROM listings")["n"] > 0 and os.path.exists(path),
   "a live (real-money) order stops it, and nothing is deleted")
run("UPDATE orders SET payment_ref='cs_test_abc' WHERE public_id='ord1'")

# With --yes: test data gone, the rest kept
res = runner.invoke(args=["clear-test-data", "--yes"])
ok(res.exit_code == 0 and "Done." in res.output, "with --yes it runs")
for t in ("listings", "tickets", "orders", "threads", "messages", "alerts", "codes"):
    ok(one("SELECT COUNT(*) AS n FROM %s" % t)["n"] == 0, "%s cleared" % t)
ok(not os.path.exists(path), "test ticket files are deleted")
with app.app_context():
    emails = [r["email"] for r in A.db.all("SELECT email FROM users")]
ok(emails == ["admin@example.com"], "only the admin account is left")
adm = one("SELECT * FROM users WHERE email='admin@example.com'")
ok(adm["stripe_account_id"] is None and adm["payouts_ready"] == 0, "the admin's test-mode Stripe payout setup is cleared")
ok(one("SELECT COUNT(*) AS n FROM waitlist")["n"] == 1, "the waitlist stays")
ok(one("SELECT COUNT(*) AS n FROM events")["n"] == events_before, "events stay")
ok(any(f.startswith("before-clear-test-data-") for f in os.listdir(os.path.join(tmp, "backups"))), "a backup was made first")
ok(app.test_client().get("/").status_code == 200, "the site still works afterwards")

print("\n%d failed" % len(fails))
