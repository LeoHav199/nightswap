import os, sys, tempfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATA_DIR = tmp; config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
import notify; notify.send_email = lambda *a, **kw: False
import app as A, seed
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def run(sql, *a):
    with app.app_context(): return A.db.run(sql, *a)
def one(sql, *a):
    with app.app_context(): return A.db.one(sql, *a)

with app.app_context():
    seed.run()
c = app.test_client()

# No token set: the feed doesn't exist
config.JARVIS_FEED_TOKEN = ""
ok(c.get("/api/jarvis").status_code == 404, "without JARVIS_FEED_TOKEN the feed is a 404")
config.JARVIS_FEED_TOKEN = "secret-feed-token-123"
ok(c.get("/api/jarvis").status_code == 404, "no token sent: 404")
ok(c.get("/api/jarvis", headers={"X-Jarvis-Token": "wrong"}).status_code == 404, "wrong token: 404")

# A paid order today shows up, without anyone's personal details
before = c.get("/api/jarvis", headers={"X-Jarvis-Token": "secret-feed-token-123"}).get_json()["today"]
l = one("SELECT * FROM listings LIMIT 1")
buyer = one("SELECT id, email FROM users WHERE id != ? LIMIT 1", l["seller_id"])
run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, seller_fee_cents, total_cents, status, paid_at, expires_at)
       VALUES ('feed1', ?, ?, ?, 2, 3000, 240, 150, 3240, 'paid', ?, datetime('now'))""", l["id"], buyer["id"], l["seller_id"], A.sqltime(A.utcnow()))
r = c.get("/api/jarvis", headers={"X-Jarvis-Token": "secret-feed-token-123"})
d = r.get_json()
ok(r.status_code == 200 and d["today"]["orders"] == before["orders"] + 1 and d["today"]["tickets"] == before["tickets"] + 2
   and d["today"]["volume_cents"] == before["volume_cents"] + 3240, "today's sale is counted")
ok(d["today"]["fees_cents"] == before["fees_cents"] + 390, "fees today = buyer fee + seller fee")
ok(any(o["event"] == l["event_name"] and o["quantity"] == 2 and o["total_cents"] == 3240 for o in d["recent_orders"]), "the latest order shows the event and number of tickets")
body = r.get_data(as_text=True)
seller_email = one("SELECT email FROM users WHERE id=?", l["seller_id"])["email"]
ok(buyer["email"] not in body and seller_email not in body and "phone" not in body, "no emails or phone numbers in the feed")
ok(all(k in d for k in ("open_disputes", "open_reports", "review_queue", "payout_failures", "listings_live", "waitlist", "users")), "all the counts are there")
ok(r.headers.get("Cache-Control") == "no-store", "never cached")

# Works in coming-soon mode too (Jarvis isn't signed in)
with app.app_context(): A.set_setting("coming_soon", "1")
ok(c.get("/api/jarvis", headers={"X-Jarvis-Token": "secret-feed-token-123"}).status_code == 200, "open while coming-soon mode is on")

print("\n%d failed" % len(fails))
