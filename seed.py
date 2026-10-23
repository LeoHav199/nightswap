"""Sample data for local development only (`flask --app app seed`)."""
import hashlib
import os
import secrets
from datetime import date, timedelta

import config
import db

# email, first name, phone, student school (or None)
SELLERS = [
    ("sanne@example.com", "Sanne", "+31612345001", "Erasmus University Rotterdam"),
    ("daan@example.com", "Daan", "+31612345002", None),
    ("lucia@example.com", "Lucía", "+31612345003", "Hogeschool Rotterdam"),
    ("milan@example.com", "Milan", "+31612345004", "Erasmus University Rotterdam"),
]

# (days from today, venue, city, event name, category, start, face value, asking price, seller index, tickets)
LISTINGS = [
    (0, "Annabel", "Rotterdam", "Thursday Student Night", "student", "23:00", 1500, 1200, 0, 2),
    (1, "Annabel", "Rotterdam", "Thursday Student Night", "student", "23:00", 1500, 1500, 2, 1),
    (2, "BIRD", "Rotterdam", "Jazz & Broken Beat", "club", "22:00", 1800, 1800, 1, 1),
    (2, "Perron", "Rotterdam", "Techno Til Late", "club", "23:59", 2500, 2000, 3, 1),
    (8, "Maassilo", "Rotterdam", "Halloween Rave", "club", "22:00", 3200, 3000, 0, 1),
    (8, "Maassilo", "Rotterdam", "Halloween Rave", "club", "22:00", 3200, 3200, 1, 3),
    (9, "Toffler", "Rotterdam", "Toffler All Night", "club", None, 2200, 2200, 2, 1),
    (12, "Rotterdam Ahoy", "Rotterdam", "Indie Arena Night", "concert", "20:00", 5500, 5000, 3, 2),
    (15, "Rotown", "Rotterdam", "Indie Club", "club", None, 1000, 800, 3, 2),
    (16, "Club Vie", "Rotterdam", "Erasmus Welcome Party", "student", "22:30", 2000, 1750, 0, 1),
    (22, "Thalia Lounge", "Rotterdam", "Hip-Hop Fridays", "club", None, 1200, 1200, 1, 1),
    (30, "Strijp-S", "Eindhoven", "Winter Warehouse Festival", "festival", "13:00", 6900, 6500, 2, 1),
    (5, "TU Aula", "Delft", "Delft Student Gala", "student", "21:00", 3000, 2500, 1, 1),
]


def fake_pdf(label):
    body = ("Sample ticket %s %s" % (label, secrets.token_hex(4))).encode()
    return b"%PDF-1.4\n% Nightswap sample ticket\n" + body + b"\n%%EOF\n"


def run():
    ids = []
    for email, name, phone, school in SELLERS:
        row = db.one("SELECT id FROM users WHERE email=?", email)
        if row:
            ids.append(row["id"])
            continue
        ids.append(db.run(
            """INSERT INTO users (email, first_name, phone, phone_verified_at, payouts_ready,
                                  student_email, student_school, student_verified_at)
               VALUES (?, ?, ?, datetime('now'), 1, ?, ?, ?)""",
            email, name, phone, ("s-" + email) if school else None, school,
            "2026-09-01 12:00:00" if school else None))

    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    for days, venue, city, event, category, start, face, price, seller, count in LISTINGS:
        if not db.one("SELECT 1 FROM venues WHERE lower(name)=lower(?) AND lower(city)=lower(?)", venue, city):
            slug = (venue if city == "Rotterdam" else venue + " " + city).lower().replace(" ", "-").replace("&", "")
            db.run("INSERT OR IGNORE INTO venues (slug, name, city) VALUES (?, ?, ?)", slug, venue, city)
        listing_id = db.run(
            "INSERT INTO listings (seller_id, venue, city, event_name, category, start_time, event_date, "
            "face_value_cents, price_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ids[seller], venue, city, event, category, start, (date.today() + timedelta(days=days)).isoformat(),
            face, price,
        )
        for _ in range(count):
            data = fake_pdf(event)
            filename = secrets.token_hex(16) + ".pdf"
            with open(os.path.join(config.UPLOAD_DIR, filename), "wb") as fh:
                fh.write(data)
            db.run("INSERT INTO tickets (listing_id, file, file_hash) VALUES (?, ?, ?)",
                   listing_id, filename, hashlib.sha256(data).hexdigest())

    # People waiting for the Halloween Rave, so it shows as "Going fast".
    rave = (date.today() + timedelta(days=8)).isoformat()
    for uid in ids:
        db.run("INSERT OR IGNORE INTO alerts (user_id, venue, event_date, event_name) VALUES (?, 'Maassilo', ?, 'Halloween Rave')",
               uid, rave)

    # A track record for two sellers: past sales that were rated.
    past = (date.today() - timedelta(days=12)).isoformat()
    for seller, ratings in ((0, ["up"] * 6), (3, ["up", "up", "up", "down"])):
        listing_id = db.run(
            "INSERT INTO listings (seller_id, venue, event_name, event_date, face_value_cents, price_cents, status) "
            "VALUES (?, 'Maassilo', 'Past night', ?, 2000, 2000, 'sold')", ids[seller], past)
        for rating in ratings:
            db.run(
                """INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, price_cents, fee_cents, total_cents,
                                       status, rating, expires_at, paid_at, released_at)
                   VALUES (?, ?, ?, ?, 2000, 100, 2100, 'released', ?, datetime('now'), datetime('now'), datetime('now'))""",
                secrets.token_urlsafe(9), listing_id, ids[(seller + 1) % len(ids)], ids[seller], rating)
