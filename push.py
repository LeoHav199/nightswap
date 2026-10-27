"""Web push notifications (works in installed web apps on iPhone and in Chrome/Firefox/Edge).

The VAPID key pair identifies Spare to the browsers' push services. It is
created on first use and kept in instance/vapid_private.pem, or set
VAPID_PRIVATE_KEY (PEM) in the environment. Keep it stable: changing it
invalidates every existing subscription.
"""
import base64
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import current_app

import config
import db

KEY_PATH = os.path.join(config.INSTANCE_DIR, "vapid_private.pem")


def _private_key():
    pem = config.env("VAPID_PRIVATE_KEY").replace("\\n", "\n").encode()
    if not pem:
        if not os.path.exists(KEY_PATH):
            os.makedirs(config.INSTANCE_DIR, exist_ok=True)
            key = ec.generate_private_key(ec.SECP256R1())
            with open(KEY_PATH, "wb") as fh:
                fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        with open(KEY_PATH, "rb") as fh:
            pem = fh.read()
    return serialization.load_pem_private_key(pem, password=None)


def public_key():
    """The applicationServerKey browsers need, URL-safe base64 without padding."""
    raw = _private_key().public_key().public_bytes(serialization.Encoding.X962,
                                                   serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _vapid_key_b64():
    value = _private_key().private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def save(user_id, sub):
    keys = sub.get("keys") or {}
    endpoint, p256dh, auth = sub.get("endpoint"), keys.get("p256dh"), keys.get("auth")
    if not (endpoint and endpoint.startswith("https://") and p256dh and auth):
        return False
    db.run("""INSERT INTO push_subs (user_id, endpoint, p256dh, auth) VALUES (?, ?, ?, ?)
              ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth""",
           user_id, endpoint, p256dh, auth)
    return True


def remove(endpoint):
    db.run("DELETE FROM push_subs WHERE endpoint=?", endpoint)


def send(user_id, title, body, url="/", tag=None):
    """Sends to every device the user turned notifications on for. Returns how many got it."""
    from pywebpush import WebPushException, webpush

    subs = db.all("SELECT * FROM push_subs WHERE user_id=?", user_id)
    if not subs:
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=payload,
                vapid_private_key=_vapid_key_b64(),
                vapid_claims={"sub": "mailto:" + config.SUPPORT_EMAIL},
                ttl=6 * 3600,
                timeout=10,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):  # the user unsubscribed or the browser dropped it
                remove(sub["endpoint"])
            else:
                current_app.logger.warning("Push to user %s failed: %s", user_id, exc)
        except Exception:
            current_app.logger.exception("Push to user %s failed", user_id)
    return sent
