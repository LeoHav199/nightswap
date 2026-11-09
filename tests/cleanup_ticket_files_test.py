"""#15: ticket files are deleted 14 days after the event, only once the money is settled; the hashes stay."""
import hashlib, io, os, re, sys, tempfile
from datetime import date, timedelta
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config; config.FACE_PROOF_REQUIRED = config.NEW_SELLER_LIMITS = config.TICKET_CHECKS = False  # (checked in hardening_test.py)
config.STRIPE_SECRET_KEY = ""; config.ADMIN_SECOND_FACTOR = False; tmp = tempfile.mkdtemp()
config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
def html(r): return r.get_data(as_text=True)
def login(c, email, name):
    c.get("/"); r = c.post("/login", data={"email": email, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/login/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "first_name": name, "_csrf": csrf(c)})
def verify_phone(c, phone):
    r = c.post("/account/phone", data={"phone": phone, "_csrf": csrf(c)}, follow_redirects=True)
    c.post("/account/phone/verify", data={"code": re.search(r"code is (\d{6})", html(r)).group(1), "_csrf": csrf(c)})

seller, buyer = app.test_client(), app.test_client()
login(seller, "s@test.nl", "Sam"); login(buyer, "b@test.nl", "Bo"); verify_phone(seller, "0611111111")
with app.app_context():
    sid = A.db.one("SELECT id FROM users WHERE email='s@test.nl'")["id"]; bid = A.db.one("SELECT id FROM users WHERE email='b@test.nl'")["id"]
os.makedirs(os.path.join(config.UPLOAD_DIR, "proofs"), exist_ok=True)

def days_ago(n): return (date.today() - timedelta(days=n)).isoformat()
def pdf_bytes(k): return ("%%PDF-1.4 cleanup test ticket %d" % k).encode()
counter = [0]
def new_listing(event_days_ago):
    with app.app_context():
        return A.db.run("""INSERT INTO listings (seller_id, venue, event_name, event_date, face_value_cents, price_cents, status)
                           VALUES (?, 'Maassilo', 'Old Night', ?, 2000, 2000, 'sold')""", sid, days_ago(event_days_ago))
def new_order(lid, status, public_id):
    with app.app_context():
        return A.db.run("""INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents, total_cents, status, expires_at)
                           VALUES (?, ?, ?, ?, 1, 2000, 199, 2199, ?, '2099-01-01 00:00:00')""", public_id, lid, bid, sid, status)
def make(event_days_ago, order_status):
    """A listing with one ticket (a real file on disk) and, unless order_status is None, one order.
    Returns (listing id, order id, ticket id, file name, k); pdf_bytes(k) are the file's bytes."""
    counter[0] += 1; k = counter[0]
    name = "ticket%d.pdf" % k
    with open(os.path.join(config.UPLOAD_DIR, name), "wb") as fh: fh.write(pdf_bytes(k))
    lid = new_listing(event_days_ago)
    oid = new_order(lid, order_status, "cl%d" % k) if order_status else None
    with app.app_context():
        tid = A.db.run("INSERT INTO tickets (listing_id, file, file_hash, code_hash, status, order_id) VALUES (?, ?, ?, ?, ?, ?)",
                       lid, name, hashlib.sha256(pdf_bytes(k)).hexdigest(), "code-%d" % k, "sold" if oid else "available", oid)
    return lid, oid, tid, name, k

def on_disk(name): return os.path.exists(os.path.join(config.UPLOAD_DIR, name))
def row(tid):
    with app.app_context(): return A.db.one("SELECT * FROM tickets WHERE id=?", tid)
def cleanup():
    with app.test_request_context(): return A.cleanup_ticket_files()

ok(A.TICKET_FILE_DAYS == 14, "the files are kept for 14 days after the event")

# Settled orders, 15 days after the event: the file goes, the row and its hashes stay
settled = {s: make(15, s) for s in ("released", "refunded")}
# Not settled: the file must stay however old the event is
open_orders = {s: make(40, s) for s in ("pending", "paid", "disputed", "releasing", "refunding")}
# Released, but the event was too recently
recent = {d: make(d, "released") for d in (0, 1, 13, 14)}
n = cleanup()
ok(n == 2, "cleanup returns the number of files it deleted (2, got %s)" % n)
for s, (lid, oid, tid, name, k) in settled.items():
    t = row(tid)
    ok(not on_disk(name) and t["file"] == "", "a %s order's file is deleted 15 days after the event" % s)
    ok(t["file_hash"] == hashlib.sha256(pdf_bytes(k)).hexdigest() and t["code_hash"] == "code-%d" % k and t["status"] == "sold" and t["order_id"] == oid,
       "...and the ticket row keeps its file hash, code hash, status and order (%s)" % s)
for s, (lid, oid, tid, name, k) in open_orders.items():
    ok(on_disk(name) and row(tid)["file"] == name, "a %s order keeps its file, even 40 days after the event" % s)
for d, (lid, oid, tid, name, k) in recent.items():
    ok(on_disk(name) and row(tid)["file"] == name, "a released order keeps its file when the event was %d days ago (not yet past the 14 days)" % d)

# The cutoff moves with the date: tomorrow, the event that was 14 days ago is past it
real_today = A.today
A.today = lambda: date.today() + timedelta(days=1)
try:
    cleanup()
finally:
    A.today = real_today
ok(not on_disk(recent[14][3]) and row(recent[14][2])["file"] == "", "one day later, the file of an event that was 14 days ago is deleted")
ok(on_disk(recent[13][3]) and on_disk(recent[0][3]), "...while the ones from 13 days ago and today stay")
ok(all(on_disk(v[3]) for v in open_orders.values()), "...and open orders still keep theirs")

# Tickets nobody bought, and orders that never completed, have nothing left to settle
unsold, expired, cancelled = make(20, None), make(20, "expired"), make(20, "cancelled")
fresh_unsold = make(2, None)
cleanup()
ok(not on_disk(unsold[3]) and not on_disk(expired[3]) and not on_disk(cancelled[3]), "unsold, expired and cancelled tickets lose their file after 14 days")
ok(on_disk(fresh_unsold[3]), "an unsold ticket for a recent event keeps its file")

# One file with several tickets (one PDF, several codes): deleted only when all of them are settled
l1, o1, t1, shared_name, k1 = make(30, "released")
l2 = new_listing(30); o2 = new_order(l2, "paid", "clshared")
with app.app_context():
    t2 = A.db.run("INSERT INTO tickets (listing_id, file, file_hash, code_hash, status, order_id) VALUES (?, ?, 'h', 'code-shared', 'sold', ?)", l2, shared_name, o2)
cleanup()
ok(on_disk(shared_name) and row(t1)["file"] == shared_name and row(t2)["file"] == shared_name, "a shared file stays while one of its tickets is on a paid order")
with app.app_context(): A.db.run("UPDATE orders SET status='refunded' WHERE id=?", o2)
cleanup()
ok(not on_disk(shared_name) and row(t1)["file"] == "" and row(t2)["file"] == "", "...and goes once all of its tickets are settled")

# Running it again, or with the file already gone, is harmless
gone = make(25, "released")
os.remove(os.path.join(config.UPLOAD_DIR, gone[3]))
try:
    cleanup(); cleanup(); fine = True
except Exception:
    fine = False
ok(fine and row(gone[2])["file"] == "", "a file already missing from the disk doesn't break the cleanup, and running it twice is fine")

# Proof files follow the same schedule, unless an order is still open
def proof(made, name):
    with open(os.path.join(config.UPLOAD_DIR, "proofs", name), "wb") as fh: fh.write(b"x")
    with app.app_context():
        return A.db.run("INSERT INTO proofs (listing_id, order_id, user_id, kind, file) VALUES (?, ?, ?, 'face', ?)", made[0], made[1], sid, name)
def proof_row(pid):
    with app.app_context(): return A.db.one("SELECT * FROM proofs WHERE id=?", pid)
def proof_on_disk(name): return os.path.exists(os.path.join(config.UPLOAD_DIR, "proofs", name))
p_done = proof(make(20, "released"), "p_done.png")
p_open = proof(make(20, "disputed"), "p_open.png")
p_new = proof(make(3, "released"), "p_new.png")
cleanup()
ok(proof_row(p_done) is None and not proof_on_disk("p_done.png"), "a proof for a settled old order is deleted")
ok(proof_row(p_open) and proof_on_disk("p_open.png"), "a proof for a disputed order is kept")
ok(proof_row(p_new) and proof_on_disk("p_new.png"), "a proof for a recent event is kept")

# The duplicate check still knows the deleted tickets
with app.app_context():
    A.db.run("INSERT INTO events (name, venue, city, category, event_date) VALUES ('Dup Night', 'Maassilo', 'Rotterdam', 'club', ?)", days_ago(-3))
    evid = A.db.one("SELECT id FROM events WHERE name='Dup Night'")["id"]
def resell(data):
    return seller.post("/sell", data={"name_on_ticket": "no", "no_double_sell": "yes", "event_id": str(evid), "face_value": "20", "price": "20", "adult": "yes",
                       "ticket": (io.BytesIO(data), "again.pdf"), "_csrf": csrf(seller)}, content_type="multipart/form-data")
def listing_count():
    with app.app_context(): return A.db.one("SELECT COUNT(*) AS n FROM listings")["n"]
before = listing_count()
paid_out = settled["released"]
r = resell(pdf_bytes(paid_out[4]))
ok(not on_disk(paid_out[3]) and r.status_code == 200 and "already been listed" in html(r) and listing_count() == before,
   "re-uploading a ticket whose file was deleted after payout is refused as already listed")
r = resell(pdf_bytes(unsold[4]))
ok(not on_disk(unsold[3]) and r.status_code == 200 and "already been listed" in html(r) and listing_count() == before,
   "re-uploading a deleted ticket that never sold is refused too")
r = resell(b"%PDF-1.4 a brand new ticket")
ok(r.status_code == 302 and listing_count() == before + 1, "(control) a ticket never seen before is accepted")

print("\n%d failed" % len(fails) if fails else "\nAll passed")
sys.exit(1 if fails else 0)
