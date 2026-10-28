"""Backups: a consistent copy of the database plus the uploaded tickets and posters, zipped.

Every night the hourly task emails the zip to the admins (off Render's disk) and keeps the last
copies in DATA_DIR/backups. Render also snapshots the disk daily; this is the copy that survives
even if the whole Render service is lost.
"""
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime

import config

KEEP = 7                         # copies kept on the disk
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
    """The zip for the nightly email: with uploads if it fits, otherwise the database alone."""
    data, name, full = make_zip(with_uploads=True)
    keep_on_disk(data, name)
    if len(data) > EMAIL_LIMIT:
        data, name, full = make_zip(with_uploads=False)
    return data, name, full
