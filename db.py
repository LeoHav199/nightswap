import os
import sqlite3

from flask import g

import config


def get():
    if "db" not in g:
        os.makedirs(config.INSTANCE_DIR, exist_ok=True)
        conn = sqlite3.connect(config.DATABASE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        g.db = conn
    return g.db


def close(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _columns(table):
    return {r["name"] for r in get().execute("PRAGMA table_info(%s)" % table)}


ADDED_COLUMNS = [
    ("waitlist", "source", "TEXT"),       # which marketing link brought them (?from=)
    ("users", "source", "TEXT"),
    ("users", "avatar", "TEXT"),
    ("orders", "resell_reminded_at", "TEXT"),
    ("orders", "seller_fee_cents", "INTEGER NOT NULL DEFAULT 0"),  # fixed at purchase; older orders pay out in full  # the day-before "can't go? sell it" message          # profile icon key from avatars.ICONS, or NULL for the first letter
    ("users", "student_email", "TEXT"),
    ("users", "student_school", "TEXT"),
    ("users", "student_verified_at", "TEXT"),
    ("orders", "quantity", "INTEGER NOT NULL DEFAULT 1"),
    ("orders", "rating", "TEXT"),
    ("orders", "rated_at", "TEXT"),
    ("orders", "rating_asked_at", "TEXT"),
    ("tickets", "code_text", "TEXT"),
    ("tickets", "code_format", "TEXT"),
    ("orders", "offer_id", "INTEGER"),
    ("listings", "category", "TEXT NOT NULL DEFAULT 'club'"),
    ("listings", "city", "TEXT NOT NULL DEFAULT 'Rotterdam'"),
    ("listings", "start_time", "TEXT"),
    ("listings", "event_id", "INTEGER"),
    # Image credits: free-licence photos must show who made them and under which licence.
    ("venues", "photo_credit", "TEXT"),
    ("venues", "photo_license", "TEXT"),
    ("venues", "photo_license_url", "TEXT"),
    ("venues", "photo_source_url", "TEXT"),
    ("events", "poster_credit", "TEXT"),
    ("events", "cancelled_at", "TEXT"),
    ("events", "changed_at", "TEXT"),       # date or time changed after tickets were sold
    ("events", "change_note", "TEXT"),
    ("users", "blocked_at", "TEXT"),
    ("users", "blocked_reason", "TEXT"),
    ("users", "deleted_at", "TEXT"),
    ("users", "adult_confirmed_at", "TEXT"),
]

# Older databases stored one ticket file directly on each listing.
MOVE_TICKETS_OUT_OF_LISTINGS = """
PRAGMA foreign_keys = OFF;
BEGIN;
INSERT INTO tickets (listing_id, file, file_hash, status, order_id)
  SELECT l.id, l.ticket_file, l.ticket_hash,
         CASE l.status WHEN 'active' THEN 'available' WHEN 'sold' THEN 'sold' ELSE 'void' END,
         (SELECT o.id FROM orders o WHERE o.listing_id = l.id
            AND o.status IN ('paid', 'disputed', 'released', 'refunded') ORDER BY o.id DESC LIMIT 1)
  FROM listings l;
CREATE TABLE listings_new (
  id               INTEGER PRIMARY KEY,
  seller_id        INTEGER NOT NULL REFERENCES users (id),
  venue            TEXT NOT NULL,
  event_name       TEXT,
  event_date       TEXT NOT NULL,
  face_value_cents INTEGER NOT NULL,
  price_cents      INTEGER NOT NULL,
  poster_file      TEXT,
  sold_together    INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'active',
  nudged_at        TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO listings_new (id, seller_id, venue, event_name, event_date, face_value_cents, price_cents,
                          poster_file, status, created_at)
  SELECT id, seller_id, venue, event_name, event_date, face_value_cents, price_cents,
         poster_file, status, created_at FROM listings;
DROP TABLE listings;
ALTER TABLE listings_new RENAME TO listings;
CREATE INDEX IF NOT EXISTS listings_browse ON listings (status, event_date);
COMMIT;
PRAGMA foreign_keys = ON;
"""


def init():
    """Creates/updates the tables. Only one process at a time (server workers start together)."""
    import fcntl
    os.makedirs(config.INSTANCE_DIR, exist_ok=True)
    with open(os.path.join(config.INSTANCE_DIR, ".init.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _init()
            import starter
            starter.load_once()  # a brand-new database starts with the real events and venue photos
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _init():
    conn = get()
    conn.execute("PRAGMA journal_mode = WAL")  # lets several server workers read and write at once
    if "ticket_file" in _columns("listings") and "poster_file" not in _columns("listings"):
        conn.execute("ALTER TABLE listings ADD COLUMN poster_file TEXT")
        conn.commit()
    with open(os.path.join(config.BASE_DIR, "schema.sql"), encoding="utf-8") as fh:
        conn.executescript(fh.read())
    for table, column, ddl in ADDED_COLUMNS:
        if column not in _columns(table):
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))
    conn.commit()
    if "ticket_file" in _columns("listings"):
        conn.executescript(MOVE_TICKETS_OUT_OF_LISTINGS)
    # Listings from before events existed: create their event and link them.
    conn.execute("UPDATE listings SET event_name = venue WHERE event_name IS NULL OR event_name = ''")
    for row in conn.execute("""SELECT DISTINCT event_date, venue, COALESCE(event_name, '') AS name, city, category, start_time
                               FROM listings WHERE event_id IS NULL AND status != 'removed'""").fetchall():
        name = row["name"] or row["venue"]
        conn.execute("""INSERT OR IGNORE INTO events (name, venue, city, category, event_date, door_time)
                        VALUES (?, ?, ?, ?, ?, ?)""", (name, row["venue"], row["city"], row["category"], row["event_date"], row["start_time"]))
        ev = conn.execute("SELECT id FROM events WHERE event_date=? AND lower(venue)=lower(?) AND lower(name)=lower(?)",
                          (row["event_date"], row["venue"], name)).fetchone()
        conn.execute("""UPDATE listings SET event_id=? WHERE event_id IS NULL AND event_date=? AND lower(venue)=lower(?)
                        AND lower(COALESCE(event_name, ''))=lower(?)""", (ev["id"], row["event_date"], row["venue"], row["name"]))
    conn.commit()


def one(sql, *args):
    return get().execute(sql, args).fetchone()


def all(sql, *args):
    return get().execute(sql, args).fetchall()


def run(sql, *args):
    conn = get()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


def count(sql, *args):
    """Runs a write and returns how many rows it changed."""
    conn = get()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount
