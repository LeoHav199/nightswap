"""Backups: a consistent copy of the database plus the uploaded tickets and posters, zipped.

Every night the hourly task emails the zip to the admins (off Render's disk) and keeps the last
few database-only copies in DATA_DIR/backups (#15: uploads aren't copied on the same disk, which would fill it). Render also snapshots the disk daily; this is the copy that survives
even if the whole Render service is lost.
"""
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime

import config


class NoKey(Exception):
    """On the real site, backups are never stored or sent unencrypted."""


def seal(data, name):
    """#16: encrypts a backup zip with BACKUP_KEY (Fernet: AES with a check that it wasn't changed).
    Without a key this only works in development, where the zip stays plain."""
    if not config.BACKUP_KEY:
        if config.DEV:
            return data, name
        raise NoKey()
    from cryptography.fernet import Fernet
    return Fernet(config.BACKUP_KEY.encode()).encrypt(data), name + ".enc"


def unseal(data, key):
    from cryptography.fernet import Fernet
    return Fernet(key.encode()).decrypt(data)


def new_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


KEEP = 3                         # database-only copies kept on the disk
EMAIL_LIMIT = 15 * 1024 * 1024   # bigger than this and the uploads stay out of the email (the database always goes)


def make_zip(with_uploads=True):
    """Returns (zip bytes, file name, whether uploads are included)."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "spare.sqlite3")
        src, dst = sqlite3.connect(config.DATABASE), sqlite3.connect(copy)
        with dst:
            src.backup(dst)  # safe while the site is running
        src.close(); dst.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copy, "spare.sqlite3")
            if with_uploads and os.path.isdir(config.UPLOAD_DIR):
                for root, _dirs, files in os.walk(config.UPLOAD_DIR):
                    for name in files:
                        path = os.path.join(root, name)
                        z.write(path, os.path.join("uploads", os.path.relpath(path, config.UPLOAD_DIR)))
    name = "spare-backup-%s.zip" % datetime.now().strftime("%Y-%m-%d-%H%M")
    return buf.getvalue(), name, with_uploads


def keep_on_disk(data, name):
    folder = os.path.join(config.DATA_DIR, "backups")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as fh:
        fh.write(data)
    old = sorted(f for f in os.listdir(folder) if f.startswith("spare-backup-"))
    for f in old[:-KEEP]:
        os.remove(os.path.join(folder, f))


def for_email():
    """The encrypted zip for the nightly email: with uploads if it fits, otherwise the database alone.
    Raises NoKey on the real site when BACKUP_KEY isn't set."""
    disk_copy, disk_name, _ = make_zip(with_uploads=False)
    keep_on_disk(*seal(disk_copy, disk_name))
    data, name, full = make_zip(with_uploads=True)
    if len(data) > EMAIL_LIMIT:
        data, name, full = make_zip(with_uploads=False)
    data, name = seal(data, name)
    return data, name, full
