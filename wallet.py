"""Apple Wallet (.pkpass) tickets.

A pass is a zip of pass.json + images + manifest.json (SHA-1 of every file)
+ signature (a detached PKCS#7 signature of the manifest made with the Pass
Type ID certificate, including Apple's WWDR certificate). Wallet refuses
passes that aren't signed with a real Apple-issued certificate.
"""
import hashlib
import io
import json
import os
import zipfile
from datetime import datetime

import config

IMAGE_DIR = os.path.join(config.BASE_DIR, "static", "wallet")
BARCODE_FORMATS = {"QR": "PKBarcodeFormatQR", "CODE_128": "PKBarcodeFormatCode128"}


def enabled():
    return all([config.APPLE_PASS_TYPE_ID, config.APPLE_TEAM_ID, config.APPLE_PASS_CERT,
                config.APPLE_PASS_KEY, config.APPLE_WWDR_CERT])


def supports(ticket):
    """Wallet can only show the ticket if we know its code and the format is one Wallet can draw."""
    return bool(ticket["code_text"]) and ticket["code_format"] in BARCODE_FORMATS


def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def pass_json(ticket, listing, order, number, total, colour, seller_name, labels):
    event_name = listing["event_name"] or listing["venue"]
    starts = datetime.fromisoformat(listing["event_date"] + "T22:00:00").astimezone()
    return {
        "formatVersion": 1,
        "passTypeIdentifier": config.APPLE_PASS_TYPE_ID,
        "teamIdentifier": config.APPLE_TEAM_ID,
        "serialNumber": "%s-%s" % (order["public_id"], ticket["id"]),
        "organizationName": "Spare",
        "description": "%s · %s" % (event_name, listing["venue"]),
        "logoText": "spare",
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(255, 216, 77)",
        "backgroundColor": _rgb(colour),
        "relevantDate": starts.isoformat(),
        "barcodes": [{
            "format": BARCODE_FORMATS[ticket["code_format"]],
            "message": ticket["code_text"],
            "messageEncoding": "iso-8859-1",
        }],
        "eventTicket": {
            "primaryFields": [{"key": "event", "label": labels["event"], "value": event_name}],
            "secondaryFields": [
                {"key": "venue", "label": labels["venue"], "value": listing["venue"]},
                {"key": "date", "label": labels["date"], "value": labels["date_value"]},
            ],
            "auxiliaryFields": [
                {"key": "ticket", "label": labels["ticket"], "value": "%d / %d" % (number, total)},
                {"key": "order", "label": labels["order"], "value": order["public_id"]},
            ],
            "backFields": [
                {"key": "seller", "label": labels["seller"], "value": seller_name},
                {"key": "help", "label": labels["help_label"], "value": labels["help"]},
            ],
        },
    }


def _sign(manifest):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7

    with open(config.APPLE_PASS_CERT, "rb") as fh:
        cert = x509.load_pem_x509_certificate(fh.read())
    with open(config.APPLE_PASS_KEY, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=(config.APPLE_PASS_KEY_PASSWORD or "").encode() or None)
    with open(config.APPLE_WWDR_CERT, "rb") as fh:
        wwdr = x509.load_pem_x509_certificate(fh.read())
    return (pkcs7.PKCS7SignatureBuilder().set_data(manifest)
            .add_signer(cert, key, hashes.SHA256()).add_certificate(wwdr)
            .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary]))


def build(pass_data):
    """Returns the .pkpass file as bytes."""
    files = {"pass.json": json.dumps(pass_data, ensure_ascii=False, indent=1).encode()}
    for name in sorted(os.listdir(IMAGE_DIR)):
        if name.endswith(".png"):
            with open(os.path.join(IMAGE_DIR, name), "rb") as fh:
                files[name] = fh.read()
    manifest = json.dumps({name: hashlib.sha1(data).hexdigest() for name, data in files.items()}).encode()
    files["manifest.json"] = manifest
    files["signature"] = _sign(manifest)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()
