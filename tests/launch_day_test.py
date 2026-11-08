import os, sys, tempfile, sqlite3, glob
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
def all_(sql, *a):
    with app.app_context(): return A.db.all(sql, *a)
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)

# The command in `flask --app app clear-test-data` (launch day). What it must never touch:
KEPT = {
    "waitlist": "SELECT * FROM waitlist ORDER BY id",
    "events": "SELECT * FROM events ORDER BY id",
    "venues": "SELECT * FROM venues ORDER BY id",
    "settings": "SELECT * FROM settings ORDER BY key",
    "sources": "SELECT * FROM sources ORDER BY slug",
    "source_visits": "SELECT * FROM source_visits ORDER BY source, day",
    "blocked_phones": "SELECT * FROM blocked_phones ORDER BY phone",
}
# Test-mode data that must go
GONE = ("listings", "tickets", "orders", "threads", "messages", "alerts", "codes")
backups = os.path.join(tmp, "backups")

def snapshot():
    """Every kept table as full rows, so a changed or deleted row shows up."""
    snap = {t: [tuple(r) for r in all_(q)] for t, q in KEPT.items()}
    snap["admins"] = [tuple(r) for r in all_("SELECT id, email, first_name, created_at FROM users WHERE email='admin@example.com'")]
    return snap

def count(t):
    return one("SELECT COUNT(*) AS n FROM %s" % t)["n"]

def backup_files():
    return sorted(glob.glob(os.path.join(backups, "before-clear-test-data-*.sqlite3")))

# A database full of test-mode activity, plus the things that must survive the launch
with app.app_context():
    seed.run()
run("INSERT INTO users (email, first_name, stripe_account_id, payouts_ready, bank_fingerprint) "
    "VALUES ('admin@example.com', 'Ad', 'acct_test_123', 1, 'fp_test')")
run("INSERT INTO users (email, first_name) VALUES ('Other.Admin@example.com', 'Notadmin')")  # not in ADMIN_EMAILS: goes
run("""INSERT INTO events (name, venue, city, event_date, created_by)
       VALUES ('Launch Night', 'Annabel', 'Rotterdam', '2026-12-05', (SELECT id FROM users WHERE email='admin@example.com'))""")
run("INSERT INTO waitlist (email, interest, lang, token) VALUES ('fan@student.eur.nl', 'buy', 'en', 'tok123')")
run("INSERT INTO waitlist (email, interest, lang, token) VALUES ('seller@student.eur.nl', 'sell', 'nl', 'tok456')")
run("INSERT INTO settings (key, value) VALUES ('coming_soon', '1')")
run("INSERT INTO settings (key, value) VALUES ('preview_code', 'abc123')")
run("INSERT INTO settings (key, value) VALUES ('sales_paused', '1')")
run("INSERT INTO sources (slug, label) VALUES ('flyers-1', 'Flyers batch 1')")
run("INSERT INTO source_visits (source, day, visits) VALUES ('flyers-1', '2026-11-01', 12)")
run("INSERT INTO blocked_phones (phone, user_id) VALUES ('+31600000000', 99)")
seller = one("SELECT * FROM listings LIMIT 1")
buyer = one("SELECT id FROM users WHERE email != 'admin@example.com' AND id != ? LIMIT 1", seller["seller_id"])["id"]
run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, payment_ref, expires_at)
       VALUES ('ord1', ?, ?, ?, 1, 1500, 199, 1699, 'paid', 'cs_test_abc', datetime('now'))""", seller["id"], buyer, seller["seller_id"])
run("INSERT INTO hits (key) VALUES ('test-hit')")
run("INSERT INTO codes (purpose, target, code_hash, expires_at) VALUES ('login', 'x@example.com', 'h', datetime('now'))")
thread = run("INSERT INTO threads (listing_id, buyer_id, seller_id) VALUES (?, ?, ?)", seller["id"], buyer, seller["seller_id"])
run("INSERT INTO messages (thread_id, sender_id, kind, body) VALUES (?, ?, 'text', 'Hi')", thread, buyer)
ticket_file = one("SELECT file FROM tickets WHERE file != '' LIMIT 1")["file"]
path = os.path.join(config.UPLOAD_DIR, ticket_file)
ok(os.path.exists(path), "(setup) a test ticket file exists")
before = snapshot()
ok(all(before[t] for t in before), "(setup) every kept table has rows: " + ", ".join(t for t in before if not before[t]))
test_counts = {t: count(t) for t in GONE}
ok(all(test_counts.values()), "(setup) every table that should be emptied has rows: " + ", ".join(t for t in GONE if not test_counts[t]))
users_before = [r["email"] for r in all_("SELECT email FROM users ORDER BY id")]
runner = app.test_cli_runner()

# 1. A real-money order stops it completely: no deletion, no backup, whatever the flag
for label, ref, extra in (("only a live order", "cs_live_real", None), ("a live order among test ones", "cs_live_real2", "cs_test_other")):
    if extra:
        run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, payment_ref, expires_at)
               VALUES ('ord2', ?, ?, ?, 1, 1500, 199, 1699, 'paid', ?, datetime('now'))""", seller["id"], buyer, seller["seller_id"], extra)
        run("UPDATE orders SET payment_ref=? WHERE public_id='ord1'", ref)
    else:
        run("UPDATE orders SET payment_ref=? WHERE public_id='ord1'", ref)
    for args in (["clear-test-data", "--yes"], ["clear-test-data"]):
        res = runner.invoke(args=args)
        ok(res.exit_code != 0 and "real money" in res.output and "Nothing was deleted" in res.output,
           "%s: %s is refused" % (label, " ".join(args)))
        ok({t: count(t) for t in GONE} == dict(test_counts, orders=count("orders")) and count("orders") == test_counts["orders"] + (1 if extra else 0),
           "%s: no test row was deleted (%s)" % (label, " ".join(args)))
        ok(snapshot() == before and os.path.exists(path), "%s: kept tables and files untouched (%s)" % (label, " ".join(args)))
        ok(not backup_files(), "%s: no backup made for a refused run (%s)" % (label, " ".join(args)))
        ok([r["email"] for r in all_("SELECT email FROM users ORDER BY id")] == users_before, "%s: no account deleted (%s)" % (label, " ".join(args)))
    run("DELETE FROM orders WHERE public_id='ord2'")
run("UPDATE orders SET payment_ref='cs_test_abc' WHERE public_id='ord1'")
ok(count("orders") == test_counts["orders"], "(setup) back to test-mode orders only")

# 2. Without --yes it only lists what it would delete
res = runner.invoke(args=["clear-test-data"])
out = res.output
ok(res.exit_code == 0 and "Would delete:" in out and "Nothing changed" in out and "Deleting:" not in out and "Done." not in out,
   "no --yes: only lists what would go")
for t in GONE:
    ok(any(l.split()[:2] == [t, str(test_counts[t])] for l in out.splitlines()), "no --yes: lists %s with its count (%d)" % (t, test_counts[t]))
ok("accounts" in out and "admin@example.com" in out, "no --yes: lists the accounts and the admin it keeps")
ok(any(l.split()[:1] == ["files"] and l.split()[1] != "0" for l in out.splitlines()), "no --yes: lists the ticket files it would delete")
ok({t: count(t) for t in GONE} == test_counts and count("hits") == 1, "no --yes: every test table still has its rows")
ok([r["email"] for r in all_("SELECT email FROM users ORDER BY id")] == users_before, "no --yes: no account deleted")
ok(snapshot() == before, "no --yes: kept tables identical")
ok(os.path.exists(path), "no --yes: ticket file still there")
ok(not backup_files() and not os.path.exists(backups), "no --yes: no backup made")
ok(one("SELECT stripe_account_id FROM users WHERE email='admin@example.com'")["stripe_account_id"] == "acct_test_123",
   "no --yes: the admin's Stripe setup untouched")

# 3. With --yes: backup first, test data gone, the rest kept
res = runner.invoke(args=["clear-test-data", "--yes"])
ok(res.exit_code == 0 and "Done." in res.output and "Deleting:" in res.output, "--yes: runs")
found = backup_files()
ok(len(found) == 1, "--yes: exactly one backup file was made")
ok(found and found[0] in res.output, "--yes: the output names the backup file")
if found:
    con = sqlite3.connect(found[0])
    try:
        def bn(t): return con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        ok(con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "backup is a healthy SQLite file")
        ok(all(bn(t) == test_counts[t] for t in GONE), "backup still holds the test data from before the delete")
        ok(sorted(r[0] for r in con.execute("SELECT email FROM users")) == sorted(users_before), "backup holds every account from before")
        ok(bn("waitlist") == 2 and bn("events") == count("events") and bn("settings") == 3, "backup holds the kept tables too")
        ok(con.execute("SELECT stripe_account_id FROM users WHERE email='admin@example.com'").fetchone()[0] == "acct_test_123",
           "backup holds the admin's Stripe setup from before")
    finally:
        con.close()
for t in GONE + ("hits",):
    ok(count(t) == 0, "--yes: %s cleared" % t)
ok(not os.path.exists(path), "--yes: test ticket file deleted")
ok([r["email"] for r in all_("SELECT email FROM users")] == ["admin@example.com"], "--yes: only the admin account is left")
after = snapshot()
for t in KEPT:
    ok(after[t] == before[t] and len(after[t]) > 0, "--yes: %s kept, every row unchanged (%d)" % (t, len(before[t])))
ok(after["admins"] == before["admins"], "--yes: the admin account itself is kept")
adm = one("SELECT * FROM users WHERE email='admin@example.com'")
ok(adm["stripe_account_id"] is None and adm["payouts_ready"] == 0 and adm["bank_fingerprint"] is None,
   "--yes: the admin only loses the test-mode Stripe payout setup")
with app.app_context():
    ok(A.get_setting("coming_soon") == "1" and A.get_setting("preview_code") == "abc123", "--yes: coming-soon and preview settings still set")
ok(app.test_client().get("/").status_code == 200, "--yes: the site still works afterwards")

print("\n%d failed" % len(fails))
