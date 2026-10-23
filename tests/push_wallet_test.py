import base64, datetime, io, json, os, re, sys, tempfile, zipfile
os.chdir(sys.argv[1]); sys.path.insert(0, sys.argv[1])
import config
config.ADMIN_SECOND_FACTOR = False
config.FEE_PERCENT = 0  # these tests were written for a flat €1 fee
tmp = tempfile.mkdtemp()
config.STRIPE_SECRET_KEY = ""; config.DATABASE = os.path.join(tmp, "t.db"); config.UPLOAD_DIR = os.path.join(tmp, "up")
config.INSTANCE_DIR = tmp
import push; push.KEY_PATH = os.path.join(tmp, "vapid.pem")
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography import x509
from cryptography.x509.oid import NameOID
import http_ece, requests

# --- a fake browser: its own key pair + auth secret, like a real PushSubscription
browser_key = ec.generate_private_key(ec.SECP256R1())
browser_pub = browser_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
auth = os.urandom(16)
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
SUB = {"endpoint": "https://push.example.com/send/abc123", "keys": {"p256dh": b64(browser_pub), "auth": b64(auth)}}
CAPTURED = []
class Resp:
    status_code = 201; text = ""; content = b""
def fake_post(url, data=None, headers=None, timeout=None, **kw):
    CAPTURED.append((url, data, headers)); return Resp()
requests.post = fake_post
import pywebpush; pywebpush.requests.post = fake_post

import app as A
app = A.app; app.config["TESTING"] = True
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m); (None if c else fails.append(m))
def csrf(c):
    with c.session_transaction() as s: return s["csrf"]
c = app.test_client(); c.get("/")
r = c.post("/login", data={"email": "p@test.nl", "_csrf": csrf(c)}, follow_redirects=True)
code = re.search(r"code is (\d{6})", r.get_data(as_text=True)).group(1)
c.post("/login/verify", data={"code": code, "first_name": "Pia", "_csrf": csrf(c)})

key = c.get("/api/push/key").get_json()["key"]
ok(len(base64.urlsafe_b64decode(key + "==")) == 65, "server publishes a valid VAPID public key")
ok(c.post("/api/push/subscribe", json=SUB).status_code == 400, "subscribe without CSRF token refused")
r = c.post("/api/push/subscribe", json=SUB, headers={"X-CSRF-Token": csrf(c)})
ok(r.status_code == 200, "subscription saved")
url, body, headers = CAPTURED[-1]
ok(url == SUB["endpoint"] and headers.get("Content-Encoding") == "aes128gcm", "welcome push sent, encrypted (aes128gcm)")
ok(headers.get("Authorization", "").startswith("vapid t="), "push carries a VAPID signature")
plain = json.loads(http_ece.decrypt(body, private_key=browser_key, auth_secret=auth, version="aes128gcm"))
ok(plain["title"] == "Notifications are on" and plain["url"] == "/account", "the phone can decrypt it: %r" % plain["title"])

# push on sale
with app.app_context():
    uid = A.db.one("SELECT id FROM users WHERE email='p@test.nl'")["id"]
    n = push.send(uid, "Sold! 🎉", "Test Night", "/orders/x")
ok(n == 1, "push.send reaches the subscribed device")
class Gone: status_code = 410; text = ""; content = b""; reason = "Gone"
pywebpush.requests.post = lambda *a, **k: Gone()
with app.app_context():
    push.send(uid, "x", "y")
    ok(A.db.one("SELECT COUNT(*) AS n FROM push_subs")["n"] == 0, "expired subscriptions are removed automatically")

# --- Wallet: self-signed stand-ins for Apple's certificates
def make_cert(name, issuer_key=None, issuer_name=None):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name or subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime(2026, 1, 1)).not_valid_after(datetime.datetime(2030, 1, 1))
            .sign(issuer_key or key, hashes.SHA256()))
    return key, cert
wkey, wcert = make_cert("WWDR stand-in")
pkey, pcert = make_cert("Pass Type ID: pass.nl.nightswap.ticket", wkey, wcert.subject)
for name, data in (("pass.pem", pcert.public_bytes(serialization.Encoding.PEM)),
                   ("key.pem", pkey.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
                   ("wwdr.pem", wcert.public_bytes(serialization.Encoding.PEM))):
    open(os.path.join(tmp, name), "wb").write(data)
config.APPLE_PASS_TYPE_ID = "pass.nl.nightswap.ticket"; config.APPLE_TEAM_ID = "ABCDE12345"
config.APPLE_PASS_CERT = os.path.join(tmp, "pass.pem"); config.APPLE_PASS_KEY = os.path.join(tmp, "key.pem"); config.APPLE_WWDR_CERT = os.path.join(tmp, "wwdr.pem")
import wallet
ok(wallet.enabled(), "Wallet switches on when certificates are configured")
with app.app_context():
    seller = A.db.run("INSERT INTO users (email, first_name) VALUES ('s@t.nl', 'Sam')")
    lid = A.db.run("INSERT INTO listings (seller_id, venue, event_name, event_date, face_value_cents, price_cents) VALUES (?, 'Maassilo', 'Halloween Rave', '2026-10-31', 3000, 3000)", seller)
    oid = A.db.run("INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, price_cents, fee_cents, total_cents, status, expires_at, payout_due_at) VALUES ('ORD1', ?, ?, ?, 3000, 100, 3100, 'paid', datetime('now'), '2026-11-03 00:00:00')", lid, uid, seller)
    A.db.run("INSERT INTO tickets (listing_id, file, file_hash, code_hash, code_text, code_format, status, order_id) VALUES (?, 'f.pdf', 'h', 'ch', 'TKT-8F3A-22910', 'QR', 'sold', ?)", lid, oid)
    tid = A.db.one("SELECT id FROM tickets WHERE order_id=?", oid)["id"]
page = c.get("/orders/ORD1").get_data(as_text=True)
ok("Add to Apple Wallet" in page, "order page shows the Add to Apple Wallet button")
r = c.get("/orders/ORD1/tickets/%d/wallet" % tid)
ok(r.status_code == 200 and r.mimetype == "application/vnd.apple.pkpass", "pass downloads as application/vnd.apple.pkpass")
z = zipfile.ZipFile(io.BytesIO(r.data))
names = set(z.namelist())
ok({"pass.json", "manifest.json", "signature", "icon.png", "icon@2x.png", "logo.png"} <= names, "pass contains pass.json, images, manifest and signature")
pj = json.loads(z.read("pass.json"))
ok(pj["barcodes"][0] == {"format": "PKBarcodeFormatQR", "message": "TKT-8F3A-22910", "messageEncoding": "iso-8859-1"}, "pass shows the ticket's real QR code")
ok(pj["eventTicket"]["primaryFields"][0]["value"] == "Halloween Rave" and pj["passTypeIdentifier"] == "pass.nl.nightswap.ticket", "pass has event name and pass type")
import hashlib
man = json.loads(z.read("manifest.json"))
ok(all(hashlib.sha1(z.read(n)).hexdigest() == h for n, h in man.items()), "manifest hashes match every file")
from cryptography.hazmat.primitives.serialization import pkcs7
certs = pkcs7.load_der_pkcs7_certificates(z.read("signature"))
ok(len(certs) == 2, "signature is PKCS#7 DER with pass + WWDR certificates")
other = app.test_client(); other.get("/")
ok(other.get("/orders/ORD1/tickets/%d/wallet" % tid).status_code == 302, "strangers can't download the pass")
print("FAILED:", fails or "none")
