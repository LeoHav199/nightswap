"""Authenticator-app codes for admins (#23): TOTP as in RFC 6238, with recovery codes.

An authenticator app (1Password, Google Authenticator, Aegis...) and Spare share a secret; both turn
the current 30-second time step into the same 6-digit code. Nothing is sent by email or text, so
someone who gets into an admin's inbox still can't open /admin.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30      # seconds per code
DIGITS = 6
DRIFT = 1      # also accept the code from one step before or after (phone clocks drift)


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP)


def code_at(secret, step):
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def verify(secret, code, last_step=None, now=None):
    """The time step the code belongs to, or None. A step at or before last_step was already used: refused."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT, step + DRIFT + 1):
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate if last_step is None or candidate > last_step else None
    return None


def uri(secret, account, issuer="Spare"):
    """What the authenticator app's QR code contains."""
    return "otpauth://totp/%s:%s?secret=%s&issuer=%s&digits=%d&period=%d" % (
        quote(issuer), quote(account), secret, quote(issuer), DIGITS, STEP)


def recovery_codes(n=8):
    """One-time codes for when the phone is lost, like 'k7mq-2xpd'. Show once, store only hashes."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["%s-%s" % ("".join(secrets.choice(alphabet) for _ in range(4)), "".join(secrets.choice(alphabet) for _ in range(4)))
            for _ in range(n)]


def hash_recovery(code):
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()


def _fernet(secret_key):
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"spare-totp:" + secret_key.encode()).digest()))


def seal(secret, secret_key):
    """The TOTP secret is stored encrypted with a key derived from SECRET_KEY, so a copy of the database alone isn't enough."""
    return _fernet(secret_key).encrypt(secret.encode()).decode()


def unseal(sealed, secret_key):
    return _fernet(secret_key).decrypt(sealed.encode()).decode()
