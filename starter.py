"""Starter data: the real events and venue photos a brand-new database begins with.

The live site's database isn't in git, so a fresh server would open empty. `export()` saves the
published events and the venues (with their free-licence photos and credits) into starter_data/,
which is committed. `load_once()` runs at startup and copies them into a new database one time only,
so events an admin later edits or deletes don't come back.

Refresh the files after adding events locally:  ../.venv/bin/flask --app app export-starter
"""
import json
import os
import shutil
from datetime import date

import config
import db

DIR = os.path.join(config.BASE_DIR, "starter_data")
PHOTOS = os.path.join(DIR, "photos")
EVENT_FIELDS = ("name", "venue", "city", "category", "event_date", "door_time", "end_time", "min_age",
                "ticket_url", "description", "source_url")
VENUE_FIELDS = ("slug", "name", "city", "address", "website", "description", "photo_file", "photo_credit",
                "photo_license", "photo_license_url", "photo_source_url", "colour")


def export():
    os.makedirs(PHOTOS, exist_ok=True)
    events = db.all("""SELECT %s FROM events WHERE status = 'published' AND cancelled_at IS NULL
                       AND created_by IS NULL AND source_url IS NOT NULL AND event_date >= ?
                       ORDER BY event_date, door_time""" % ", ".join(EVENT_FIELDS), date.today().isoformat())
    venues = db.all("SELECT %s FROM venues ORDER BY slug" % ", ".join(VENUE_FIELDS))
    for v in venues:
        if v["photo_file"]:
            shutil.copy2(os.path.join(config.UPLOAD_DIR, "posters", v["photo_file"]), os.path.join(PHOTOS, v["photo_file"]))
    with open(os.path.join(DIR, "events.json"), "w", encoding="utf-8") as fh:
        json.dump([dict(e) for e in events], fh, ensure_ascii=False, indent=1)
    with open(os.path.join(DIR, "venues.json"), "w", encoding="utf-8") as fh:
        json.dump([dict(v) for v in venues], fh, ensure_ascii=False, indent=1)
    return len(events), len(venues)


def load_once():
    """Copies the starter data into the database the first time it starts. Returns True if it loaded."""
    marker = os.path.join(config.INSTANCE_DIR, ".starter_loaded")
    if not config.LOAD_STARTER_DATA or os.path.exists(marker) or not os.path.exists(os.path.join(DIR, "events.json")):
        return False
    if db.one("SELECT COUNT(*) FROM events")[0] == 0:
        with open(os.path.join(DIR, "venues.json"), encoding="utf-8") as fh:
            venues = json.load(fh)
        with open(os.path.join(DIR, "events.json"), encoding="utf-8") as fh:
            events = json.load(fh)
        os.makedirs(os.path.join(config.UPLOAD_DIR, "posters"), exist_ok=True)
        for v in venues:
            if v["photo_file"]:
                shutil.copy2(os.path.join(PHOTOS, v["photo_file"]), os.path.join(config.UPLOAD_DIR, "posters", v["photo_file"]))
            db.run("INSERT OR IGNORE INTO venues (%s) VALUES (%s)" % (", ".join(VENUE_FIELDS), ", ".join("?" * len(VENUE_FIELDS))),
                   *[v[f] for f in VENUE_FIELDS])
        today = date.today().isoformat()
        for e in events:
            if e["event_date"] >= today:
                db.run("INSERT OR IGNORE INTO events (%s) VALUES (%s)" % (", ".join(EVENT_FIELDS), ", ".join("?" * len(EVENT_FIELDS))),
                       *[e[f] for f in EVENT_FIELDS])
    with open(marker, "w") as fh:
        fh.write(date.today().isoformat())
    return True
