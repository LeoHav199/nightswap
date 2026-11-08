"""Backup round trip: the encrypted zip opens again with the same data; no key on the real site means nothing is sent or
stored; big uploads stay out; only 3 copies stay on the disk."""
import io, os, sqlite3, sys, tempfile, zipfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.STRIPE_SECRET_KEY = ""; tmp = tempfile.mkdtemp()
config.DATA_DIR = tmp; config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up"); config.INSTANCE_DIR = tmp
config.ADMIN_EMAILS = {"admin@example.com"}
import notify; SENT = []
notify.send_email = lambda to, subject, body, attachments=(), **kw: SENT.append((to, subject, body, list(attachments))) or True
import app as A, backup
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def backups_dir():
    folder = os.path.join(tmp, "backups")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

# Something recognisable in the database and in the uploads
with app.app_context():
    A.db.run("INSERT INTO waitlist (email, interest, lang, token) VALUES ('roundtrip@student.eur.nl', 'buy', 'en', 'tok-roundtrip')")
os.makedirs(os.path.join(config.UPLOAD_DIR, "sub"), exist_ok=True)
ticket_bytes = b"%PDF-1.4 round trip ticket " + os.urandom(2000)
with open(os.path.join(config.UPLOAD_DIR, "sub", "ticket.pdf"), "wb") as fh: fh.write(ticket_bytes)

# --- with BACKUP_KEY on the real site: encrypted, and the key brings everything back
live_was, key = config.DEV, backup.new_key()
config.DEV = False; config.BACKUP_KEY = key
with app.app_context(): data, name, full = backup.for_email()
ok(name.endswith(".zip.enc") and full, "with BACKUP_KEY the backup is a .zip.enc that includes the uploads")
ok(not data.startswith(b"PK") and b"roundtrip@student.eur.nl" not in data and ticket_bytes not in data, "...and nothing in it is readable without the key")
try:
    backup.unseal(data, backup.new_key()); ok(False, "a wrong key can't open it")
except Exception:
    ok(True, "a wrong key can't open it")
enc_path = os.path.join(tmp, name)
with open(enc_path, "wb") as fh: fh.write(data)
res = app.test_cli_runner().invoke(args=["decrypt-backup", enc_path, "--key", key])
zip_path = enc_path[:-4]
ok("Saved" in res.output and os.path.exists(zip_path), "flask decrypt-backup writes the plain zip")
with zipfile.ZipFile(zip_path) as z:
    ok(z.testzip() is None and "spare.sqlite3" in z.namelist(), "the zip is sound and holds the database")
    restored = os.path.join(tmp, "restored.db")
    with open(restored, "wb") as fh: fh.write(z.read("spare.sqlite3"))
    ok(z.read("uploads/sub/ticket.pdf") == ticket_bytes, "the uploaded file comes back with identical bytes")
con = sqlite3.connect(restored)
row = con.execute("SELECT interest, token FROM waitlist WHERE email='roundtrip@student.eur.nl'").fetchone()
live_count = sqlite3.connect(config.DATABASE).execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
ok(row == ("buy", "tok-roundtrip") and con.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0] == live_count,
   "the restored database has the same rows as the live one")
con.close()
ok(len(backups_dir()) == 1 and backups_dir()[0].endswith(".zip.enc"), "one encrypted copy is kept on the disk")
disk = open(os.path.join(tmp, "backups", backups_dir()[0]), "rb").read()
with zipfile.ZipFile(io.BytesIO(backup.unseal(disk, key))) as z:
    ok(z.namelist() == ["spare.sqlite3"], "the disk copy is encrypted and holds the database only")

# --- the nightly email carries the encrypted file
SENT.clear()
with app.test_request_context(): made = A.nightly_backup(force=True)
mails = [m for m in SENT if m[3]]
ok(made and mails and all(a[0].endswith(".zip.enc") and not a[1].startswith(b"PK") for m in mails for a in m[3]), "the nightly email attaches only the encrypted file")

# --- uploads over 15 MB stay out (the database always goes)
big = os.path.join(config.UPLOAD_DIR, "big.bin")
with open(big, "wb") as fh: fh.write(os.urandom(backup.EMAIL_LIMIT + 1024 * 1024))  # random data doesn't compress
with app.app_context(): data, name, full = backup.for_email()
with zipfile.ZipFile(io.BytesIO(backup.unseal(data, key))) as z:
    ok(backup.EMAIL_LIMIT == 15 * 1024 * 1024 and not full and z.namelist() == ["spare.sqlite3"],
       "uploads over 15 MB are left out: the backup holds the database only")
os.remove(big)
with app.app_context(): data, name, full = backup.for_email()
ok(full, "and they are back in once the uploads fit again")

# --- only 3 copies stay on the disk
for stamp in ("2026-01-01-0300", "2026-01-02-0300", "2026-01-03-0300", "2026-01-04-0300"):
    backup.keep_on_disk(b"x", "spare-backup-%s.zip.enc" % stamp)
left = backups_dir()
ok(backup.KEEP == 3 and len(left) == 3, "only 3 copies are kept in DATA_DIR/backups")
ok(not any("2026-01-01" in n or "2026-01-02" in n for n in left) and any("2026-01-04" in n for n in left), "...the oldest ones are the ones removed")

# --- no key on the real site: nothing is sent or stored
config.BACKUP_KEY = ""
for f in backups_dir(): os.remove(os.path.join(tmp, "backups", f))
SENT.clear()
try:
    with app.app_context(): backup.for_email(); ok(False, "without BACKUP_KEY the real site makes no backup")
except backup.NoKey:
    ok(True, "without BACKUP_KEY the real site makes no backup")
ok(backups_dir() == [], "...and nothing is stored on the disk")
with app.test_request_context(): made = A.nightly_backup(force=True)
ok(not made and backups_dir() == [] and not any(m[3] for m in SENT), "...and the nightly task sends no attachment")
ok(any("backup NOT made" in m[1] for m in SENT), "...the admins are told instead")
try:
    with app.app_context(): backup.seal(b"zip", "x.zip"); ok(False, "sealing without a key is refused")
except backup.NoKey:
    ok(True, "sealing without a key is refused")

# --- development without a key keeps working (plain zip)
config.DEV = True
with app.app_context(): data, name, full = backup.for_email()
ok(name.endswith(".zip") and data.startswith(b"PK"), "in development without a key the backup is a plain zip")
config.DEV = live_was

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
