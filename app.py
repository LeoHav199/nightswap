import csv
import hashlib
import hmac
import ipaddress
import io
import json
import os
import re
import secrets
import shutil
import unicodedata
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote, urlencode, urlparse

import click
from flask import (Flask, abort, flash, g, redirect, render_template, request, send_file, session,
                   url_for)
from markupsafe import escape

import art
import avatars
import backup
import config
import db
import i18n
import importer
import moderation
import notify
import payments
import push
import totp
import ticketcodes
import wallet

app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.secret_key(),
    MAX_CONTENT_LENGTH=(config.MAX_TICKETS_PER_LISTING + 1) * config.MAX_UPLOAD_BYTES + 512 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not config.DEV,
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),  # #11: signed in for 90 days, renewed on every visit
)
app.teardown_appcontext(db.close)
if config.TRUST_PROXY:
    # Behind a host's proxy or a tunnel: take the visitor's https address from the forwarded headers.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.PROXY_HOPS, x_proto=1, x_host=1)
config.check_production()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")  # ?from=<marketing link>
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
FILE_TYPES = {  # extension -> (magic bytes, mimetype)
    "pdf": (b"%PDF", "application/pdf"),
    "png": (b"\x89PNG", "image/png"),
    "jpg": (b"\xff\xd8\xff", "image/jpeg"),
    "jpeg": (b"\xff\xd8\xff", "image/jpeg"),
}
POSTER_TYPES = {"jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "png": b"\x89PNG", "webp": b"RIFF"}
PAID_STATES = ("paid", "disputed", "released", "releasing")  # releasing: payout being sent (#26)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def utcnow():
    return datetime.utcnow().replace(microsecond=0)


def sqltime(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_sqltime(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S") if value else None


def today():
    return date.today()


def parse_money(raw):
    """'20', '20.5', '20,50', '€ 20' -> cents, or None."""
    raw = (raw or "").replace("€", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d{1,4}(\.\d{1,2})?", raw):
        return None
    euros, _, cents = raw.partition(".")
    return int(euros) * 100 + int((cents + "00")[:2])


def fee_for(price_cents):
    """Buyer service fee for one ticket at this price."""
    fee = max(config.FEE_MIN_CENTS, int(price_cents * config.FEE_PERCENT / 100 + 0.5))
    return min(fee, config.FEE_MAX_CENTS) if config.FEE_MAX_CENTS else fee


def with_fee(price_cents):
    return price_cents + fee_for(price_cents)


def with_extra(body, extra):
    """Adds a paragraph to an email just above the "Spare" sign-off."""
    if not extra:
        return body
    head, sep, sign = body.rpartition("\n\n")
    return head + "\n\n" + extra + sep + sign if sep else body + "\n\n" + extra


def app_name(key):
    """Display name of a ticket app, e.g. 'Ticketmaster', or 'another app' in the visitor's language."""
    return config.TRANSFER_APPS.get(key) or t("app_other")


def seller_fee_for(price_cents):
    """Seller fee for one ticket at this price (never more than the price itself)."""
    if not config.SELLER_FEE_PERCENT and not config.SELLER_FEE_MIN_CENTS:
        return 0
    return min(price_cents, max(config.SELLER_FEE_MIN_CENTS, int(price_cents * config.SELLER_FEE_PERCENT / 100 + 0.5)))


def payout_for(order):
    """What the seller receives for an order: the price minus the seller fee fixed when it was bought."""
    return order["price_cents"] - (order["seller_fee_cents"] or 0)


def seller_fee_label():
    pct = ("%g" % config.SELLER_FEE_PERCENT).replace(".", "," if g.get("lang") == "nl" else ".")
    return t("fee_label", pct=pct, min=money(config.SELLER_FEE_MIN_CENTS))


def fee_label():
    pct = ("%g" % config.FEE_PERCENT).replace(".", "," if g.get("lang") == "nl" else ".")
    return t("fee_label", pct=pct, min=money(config.FEE_MIN_CENTS))


def max_price(face_value_cents):
    return face_value_cents * config.PRICE_CAP_PERCENT // 100


def t(key, **kwargs):
    return i18n.t(g.get("lang", "en"), key, **kwargs)


def money(cents):
    return i18n.money(g.get("lang", "en"), cents)


def fmt_date(value, style="short"):
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return i18n.fmt_date(g.get("lang", "en"), value, style)


def base_url():
    """Absolute site address for emails and payment redirects: BASE_URL, or the address the visitor used."""
    if config.BASE_URL:
        return config.BASE_URL
    return request.host_url.rstrip("/")


def safe_next(target, default="/"):
    if not target:
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return default
    return target


def code_hash(code):
    return hmac.new(app.config["SECRET_KEY"].encode(), code.encode(), hashlib.sha256).hexdigest()


def issue_code(purpose, target):
    """Creates a 6-digit one-time code. Returns None if one was sent less than 45s ago."""
    recent = db.one(
        "SELECT 1 FROM codes WHERE purpose=? AND target=? AND created_at > ?",
        purpose, target, sqltime(utcnow() - timedelta(seconds=45)),
    )
    if recent:
        return None
    code = "%06d" % secrets.randbelow(1000000)
    db.run("DELETE FROM codes WHERE purpose=? AND target=?", purpose, target)
    db.run(
        "INSERT INTO codes (purpose, target, code_hash, expires_at) VALUES (?, ?, ?, ?)",
        purpose, target, code_hash(code), sqltime(utcnow() + timedelta(minutes=10)),
    )
    return code


def check_code(purpose, target, code):
    row = db.one(
        "SELECT * FROM codes WHERE purpose=? AND target=? AND expires_at > ? ORDER BY id DESC LIMIT 1",
        purpose, target, sqltime(utcnow()),
    )
    if not row or row["attempts"] >= 5:
        return False
    if not hmac.compare_digest(row["code_hash"], code_hash(code)):
        db.run("UPDATE codes SET attempts = attempts + 1 WHERE id=?", row["id"])
        return False
    db.run("DELETE FROM codes WHERE id=?", row["id"])
    return True


def normalise_phone(raw):
    phone = re.sub(r"[\s\-().]", "", raw or "")
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    elif phone.startswith("06"):
        phone = "+31" + phone[1:]
    return phone if PHONE_RE.match(phone) else None


def student_school(email):
    domain = email.rsplit("@", 1)[-1]
    for allowed, school in config.STUDENT_DOMAINS.items():
        if domain == allowed or domain.endswith("." + allowed):
            return school
    return None


def client_ip():
    """#10: the visitor's IP for rate limits. ProxyFix reads it from the right-hand end of X-Forwarded-For, which only
    our host's proxies write; the left-hand end can be faked by the visitor, so it's never trusted."""
    ip = request.remote_addr or "?"
    if config.TRUST_PROXY and proxy_address(ip):
        # TRUST_PROXY_HOPS is too low and this is one of the host's own proxies. Sharing its address would lock
        # everyone out of signing in, so fall back to the next entry the proxies wrote (logged, see Network check).
        chain = [p.strip() for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
        for candidate in reversed(chain[:-config.PROXY_HOPS] if len(chain) > config.PROXY_HOPS else chain[:1]):
            if not proxy_address(candidate):
                return candidate
    return ip


def proxy_address(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a in ipaddress.ip_network("100.64.0.0/10")


def rate_limited(action, limit, minutes, who=None):
    """Counts an attempt; True when there were already `limit` attempts in the last `minutes`."""
    key = "%s:%s" % (action, who if who is not None else client_ip())
    since = sqltime(utcnow() - timedelta(minutes=minutes))
    if db.one("SELECT COUNT(*) AS n FROM hits WHERE key=? AND created_at > ?", key, since)["n"] >= limit:
        return True
    db.run("INSERT INTO hits (key) VALUES (?)", key)
    if secrets.randbelow(50) == 0:
        db.run("DELETE FROM hits WHERE created_at < ?", sqltime(utcnow() - timedelta(days=1)))
    return False


def get_setting(key, default=None):
    row = db.one("SELECT value FROM settings WHERE key=?", key)
    return row["value"] if row else default


def set_setting(key, value):
    db.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, value)


# Coming-soon mode: visitors see a sign-up page; admins and people with the preview link see the full site.
# These pages stay reachable for everyone (sign-in for admins, legal pages, payments and cron).
OPEN_WHILE_COMING_SOON = {
    "static", "index", "waitlist_join", "waitlist_leave", "preview", "set_lang", "login", "login_verify", "logout",
    "admin_verify", "robots", "sitemap", "manifest", "service_worker", "offline", "how", "terms", "privacy", "credits",
    "stripe_webhook", "hourly_tasks",
}


def track_source():
    """Remembers which marketing link (?from=esn) brought this visitor, and counts the visit once per session."""
    src = request.args.get("from", "").strip().lower()
    if request.method != "GET" or not SOURCE_RE.match(src):
        return
    seen = session.get("seen_sources", [])
    if src not in seen and len(seen) < 10:
        db.run("""INSERT INTO source_visits (source, day, visits) VALUES (?, ?, 1)
                  ON CONFLICT(source, day) DO UPDATE SET visits = visits + 1""", src, today().isoformat())
        session["seen_sources"] = seen + [src]
    session.setdefault("source", src)  # the first link wins


def coming_soon_on():
    return get_setting("coming_soon") == "1"


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.user or not g.is_admin:
            abort(404)
        ok_until = session.get("admin_ok_until", 0)
        if config.ADMIN_SECOND_FACTOR and ok_until < utcnow().timestamp():
            return redirect(url_for("admin_verify", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


def release_unpaid():
    """Expire checkouts that ran out of time and put their tickets back on sale."""
    db.run("UPDATE orders SET status='expired' WHERE status='pending' AND expires_at < ?", sqltime(utcnow()))
    db.run("""UPDATE tickets SET status='available', order_id=NULL WHERE status='held'
              AND order_id IN (SELECT id FROM orders WHERE status IN ('expired', 'cancelled'))""")


def browse(where="", *args):
    """Active listings with at least one ticket for sale, plus seller details."""
    sql = """
    SELECT * FROM (
      SELECT l.*, u.first_name AS seller_name, u.avatar AS seller_avatar, u.phone_verified_at, u.student_verified_at, u.student_school,
             u.created_at AS seller_since,
             (SELECT COUNT(*) FROM tickets t WHERE t.listing_id = l.id AND t.status = 'available') AS available
      FROM listings l JOIN users u ON u.id = l.seller_id
      WHERE l.status = 'active' AND l.event_date >= ? %s
    ) WHERE available > 0
    ORDER BY event_date, price_cents, id""" % where
    return db.all(sql, today().isoformat(), *args)


def listing_with_seller(listing_id):
    return db.one(
        """SELECT l.*, u.first_name AS seller_name, u.avatar AS seller_avatar, u.created_at AS seller_since, u.phone_verified_at,
                  u.student_verified_at, u.student_school,
                  (SELECT COUNT(*) FROM tickets t WHERE t.listing_id = l.id AND t.status = 'available') AS available,
                  (SELECT COUNT(*) FROM tickets t WHERE t.listing_id = l.id AND t.status = 'held') AS held
           FROM listings l JOIN users u ON u.id = l.seller_id WHERE l.id = ?""",
        listing_id,
    )


def seller_stats(seller_ids):
    """{seller_id: {"sales": completed sales, "up": tickets that worked, "rated": rated sales}}"""
    ids = list(set(seller_ids))
    if not ids:
        return {}
    rows = db.all(
        """SELECT seller_id,
                  SUM(CASE WHEN status = 'released' THEN 1 ELSE 0 END) AS sales,
                  SUM(CASE WHEN rating = 'up' THEN 1 ELSE 0 END) AS up,
                  SUM(CASE WHEN rating IS NOT NULL THEN 1 ELSE 0 END) AS rated
           FROM orders WHERE seller_id IN (%s) GROUP BY seller_id""" % ",".join("?" * len(ids)),
        *ids,
    )
    stats = {i: {"sales": 0, "up": 0, "rated": 0} for i in ids}
    for r in rows:
        stats[r["seller_id"]] = {"sales": r["sales"] or 0, "up": r["up"] or 0, "rated": r["rated"] or 0}
    return stats


def payout_due_for(event_date, seller_id=None):
    # The night ends the next morning; count the delay from then. New sellers wait longer (#28).
    night_end = datetime.combine(date.fromisoformat(event_date) + timedelta(days=1), datetime.min.time())
    hours = config.NEW_SELLER_PAYOUT_HOURS if seller_id and is_new_seller(seller_id) else config.PAYOUT_DELAY_HOURS
    return night_end + timedelta(hours=hours)


RATE_AFTER_DOORS_HOURS = 2   # #30: "did it work?" opens this long after the doors
PAYOUT_AFTER_DOORS_HOURS = 3  # a 👍 brings the payout forward to this long after the doors (never sooner)


def doors_open_at(listing):
    """The doors (UTC): the event's door time, else the listing's start time, else 22:00, on the event date,
    Amsterdam time. Times before 06:00 belong to the night after the event date."""
    e = db.one("SELECT door_time FROM events WHERE id=?", listing["event_id"]) if listing["event_id"] else None
    door = (e and e["door_time"]) or listing["start_time"] or "22:00"
    day = date.fromisoformat(listing["event_date"])
    hh, mm = (int(x) for x in door.split(":")[:2])
    if hh < 6:
        day += timedelta(days=1)
    local = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
    return local.astimezone(timezone.utc).replace(tzinfo=None)  # the server runs on Amsterdam time (TZ in render.yaml)


def can_rate_now(listing):
    return utcnow() >= doors_open_at(listing) + timedelta(hours=RATE_AFTER_DOORS_HOURS)


def is_new_seller(user_id):
    """#28: fewer than 2 paid-out sales. New sellers get limits that make selling a ticket twice not worth trying."""
    if not config.NEW_SELLER_LIMITS:
        return False
    done = db.one("SELECT COUNT(*) AS n FROM orders WHERE seller_id=? AND status='released'", user_id)["n"]
    return done < config.NEW_SELLER_SALES


def set_payouts_ready(user_id):
    """Payout setup (with Stripe's identity check) is done: listings that waited for it go on sale (#28)."""
    db.run("UPDATE users SET payouts_ready=1 WHERE id=?", user_id)
    for l in db.all("SELECT * FROM listings WHERE seller_id=? AND status='draft'", user_id):
        db.run("UPDATE listings SET status=? WHERE id=?", "review" if l["review_reason"] else "active", l["id"])
        if l["review_reason"]:
            notify.notify_admins("Spare: listing to check (%s)" % l["review_reason"],
                                 "A listing for %s needs a check before it goes live:\n\n%s" % (listing_title(l), base_url() + url_for("admin") + "#review"))
        else:
            notify_alerts(db.one("SELECT * FROM listings WHERE id=?", l["id"]))


def qr_svg(text):
    """Inline SVG of a QR code, for showing the ticket code on the order page."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    svg = img.to_string(encoding="unicode")
    return re.sub(r"<\?xml[^>]*>", "", svg)


def listing_title(listing):
    if listing["event_name"]:
        return "%s · %s" % (listing["event_name"], listing["venue"])
    return listing["venue"]


def event_url(venue, event_date, name, external=False):
    path = url_for("event_page") + "?" + urlencode({"venue": venue, "date": event_date, "name": name or ""})
    return base_url() + path if external else path


def update_listing_status(listing_id):
    """A listing is sold once none of its tickets are for sale or being paid for."""
    left = db.one("SELECT COUNT(*) AS n FROM tickets WHERE listing_id=? AND status IN ('available', 'held')",
                  listing_id)["n"]
    if left == 0:
        db.run("UPDATE listings SET status='sold' WHERE id=? AND status='active'", listing_id)


def mark_paid(order, payment_intent=None):
    """Idempotent. Called from the success page, the webhook and the test checkout."""
    order = db.one("SELECT * FROM orders WHERE id=?", order["id"])
    if order["status"] not in ("pending", "expired", "cancelled"):
        return order

    # Normally the tickets are still held for this order. If the checkout ran out of time first,
    # try to claim them again; if someone else bought them in the meantime, refund this buyer.
    held = db.one("SELECT COUNT(*) AS n FROM tickets WHERE order_id=? AND status='held'", order["id"])["n"]
    if held < order["quantity"]:
        need = order["quantity"] - held
        free = db.all("SELECT id FROM tickets WHERE listing_id=? AND status='available' ORDER BY id LIMIT ?",
                      order["listing_id"], need)
        for row in free:
            db.run("UPDATE tickets SET status='held', order_id=? WHERE id=? AND status='available'", order["id"], row["id"])
        held = db.one("SELECT COUNT(*) AS n FROM tickets WHERE order_id=? AND status='held'", order["id"])["n"]
        if held < order["quantity"]:
            db.run("UPDATE tickets SET status='available', order_id=NULL WHERE order_id=? AND status='held'", order["id"])
            db.run("UPDATE orders SET status='refunded', payment_intent=? WHERE id=?", payment_intent, order["id"])
            payments.refund(dict(order, payment_intent=payment_intent))
            return db.one("SELECT * FROM orders WHERE id=?", order["id"])

    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    due = payout_due_for(listing["event_date"], order["seller_id"])
    db.run(
        "UPDATE orders SET status='paid', paid_at=?, payout_due_at=?, payment_intent=COALESCE(?, payment_intent) WHERE id=?",
        sqltime(utcnow()), sqltime(due), payment_intent, order["id"],
    )
    db.run("UPDATE tickets SET status='sold' WHERE order_id=? AND status='held'", order["id"])
    update_listing_status(listing["id"])
    if order["offer_id"]:
        db.run("UPDATE offers SET status='used' WHERE id=?", order["offer_id"])
    th = get_thread(listing, order["buyer_id"])
    db.run("UPDATE threads SET order_id=? WHERE id=?", order["id"], th["id"])
    add_message(th, None, "system", "sys_paid")

    buyer = db.one("SELECT * FROM users WHERE id=?", order["buyer_id"])
    seller = db.one("SELECT * FROM users WHERE id=?", order["seller_id"])
    order_url = base_url() + url_for("order_page", public_id=order["public_id"])
    title = listing_title(listing)
    if order["quantity"] > 1:
        title = "%d× %s" % (order["quantity"], title)
    transfer = listing["delivery"] == "transfer"
    app_label = app_name(listing["transfer_app"]) if transfer else ""
    notify.send_email(buyer["email"], t("mail_bought_subject", title=title),
                      with_extra(t("mail_bought_body", name=buyer["first_name"], title=title, url=order_url),
                                 t("mail_bought_transfer", email=order["transfer_email"], app=app_label) if transfer else ""))
    notify.send_email(seller["email"], t("mail_sold_subject", title=title),
                      with_extra(t("mail_sold_body", name=seller["first_name"], title=title, url=order_url,
                                   amount=money(payout_for(order)), date=fmt_date(due.date(), "long")),
                                 t("mail_sold_transfer", tname=order["transfer_name"], temail=order["transfer_email"], app=app_label)
                                 if transfer else ""))
    order_path = url_for("order_page", public_id=order["public_id"])
    push.send(seller["id"], t("push_sold_title"), t("push_sold_body", title=title, amount=money(payout_for(order))),
              order_path, tag="sold-" + order["public_id"])
    push.send(buyer["id"], t("push_bought_title"), t("push_bought_body", title=title), order_path,
              tag="bought-" + order["public_id"])
    return db.one("SELECT * FROM orders WHERE id=?", order["id"])


def release_due_payouts():
    """Pays sellers whose payout date has passed. Returns (released, waiting_for_seller_setup)."""
    due = db.all("SELECT * FROM orders WHERE status='paid' AND payout_due_at <= ?", sqltime(utcnow()))
    released = waiting = 0
    for order in due:
        if release_payout(order):
            released += 1
        else:
            waiting += 1
    return released, waiting


def bank_matches_blocked(seller):
    """#24: a blocked seller who comes back with a new email and phone still gets paid to the same IBAN. Hold
    payouts to a bank account that a blocked user also used, and tell the admins (once)."""
    if seller["bank_flagged_at"]:
        return True
    fingerprint = payments.bank_fingerprint(seller["stripe_account_id"])
    if fingerprint and fingerprint != seller["bank_fingerprint"]:
        db.run("UPDATE users SET bank_fingerprint=? WHERE id=?", fingerprint, seller["id"])
    if not fingerprint:
        return False
    twin = db.one("SELECT email FROM users WHERE bank_fingerprint=? AND blocked_at IS NOT NULL AND id != ?", fingerprint, seller["id"])
    if not twin:
        return False
    db.run("UPDATE users SET bank_flagged_at=? WHERE id=?", sqltime(utcnow()), seller["id"])
    notify.notify_admins("Spare: payouts held for %s" % seller["email"],
                         "%s is paid out to the same bank account as blocked user %s. Their payouts are on hold.\n\n"
                         "Check them at %s and block them, or clear the hold there." % (seller["email"], twin["email"], base_url() + url_for("admin_users", q=seller["email"])))
    return True


def release_payout(order):
    """Sends one order's money to its seller. False when it has to wait (seller blocked or payouts not set up)."""
    seller = db.one("SELECT * FROM users WHERE id=?", order["seller_id"])
    if seller["blocked_at"]:
        return False  # held until an admin refunds the buyer or unblocks the seller
    listing = db.one("SELECT delivery FROM listings WHERE id=?", order["listing_id"])
    if listing["delivery"] == "transfer" and not (order["transferred_at"] or order["received_at"] or order["rating"] == "up"):
        return False  # an app ticket that was never sent: no money until it is (the buyer can report it and get a refund)
    if listing["delivery"] == "transfer" and not order["received_at"] and order["rating"] != "up" and not order["buyer_reminders"]:
        return False  # #31: the buyer hasn't confirmed and hasn't even been asked yet
    ready = seller["payouts_ready"] and (seller["stripe_account_id"] or not payments.live())
    if payments.live() and seller["stripe_account_id"] and not ready:
        ready = payments.payouts_ready(seller["stripe_account_id"])
        if ready:
            set_payouts_ready(seller["id"])
    if not ready:
        return False
    if bank_matches_blocked(seller):
        return False  # #24: same bank account as a blocked user; the admins were told
    # #26: claim the order before calling Stripe, so two runs at once (👍, the hourly task, the admin button) can't both
    # pay. If Stripe fails, it goes back to 'paid'; if we crash in between, release_stuck_payouts() sorts it out.
    if not db.count("UPDATE orders SET status='releasing', releasing_at=? WHERE id=? AND status='paid'", sqltime(utcnow()), order["id"]):
        return False
    try:
        ref = payments.transfer_to_seller(order, seller)
    except Exception:
        db.run("UPDATE orders SET status='paid', releasing_at=NULL WHERE id=? AND status='releasing'", order["id"])
        raise
    db.run("UPDATE orders SET status='released', released_at=?, transfer_ref=? WHERE id=?",
           sqltime(utcnow()), ref, order["id"])
    return True


def release_stuck_payouts():
    """#26: payouts claimed more than 15 minutes ago that never finished: finished if Stripe has the transfer, else retried later."""
    fixed = 0
    for order in db.all("SELECT * FROM orders WHERE status='releasing' AND releasing_at < ?", sqltime(utcnow() - timedelta(minutes=15))):
        ref = payments.find_transfer(order)
        if ref:
            db.run("UPDATE orders SET status='released', released_at=?, transfer_ref=? WHERE id=?", sqltime(utcnow()), ref, order["id"])
        else:
            db.run("UPDATE orders SET status='paid', releasing_at=NULL WHERE id=?", order["id"])
        fixed += 1
    return fixed


def notify_alerts(listing):
    """Email everyone waiting for this event that tickets are available (at most once an hour each)."""
    rows = db.all(
        """SELECT a.id, a.user_id, u.email, u.first_name FROM alerts a JOIN users u ON u.id = a.user_id
           WHERE a.event_date = ? AND lower(a.venue) = lower(?) AND lower(a.event_name) = lower(?)
             AND a.user_id != ? AND (a.last_notified_at IS NULL OR a.last_notified_at < ?)""",
        listing["event_date"], listing["venue"], listing["event_name"] or "", listing["seller_id"],
        sqltime(utcnow() - timedelta(hours=1)),
    )
    title = listing_title(listing)
    url = event_url(listing["venue"], listing["event_date"], listing["event_name"], external=True)
    for row in rows:
        notify.send_email(row["email"], t("mail_alert_subject", title=title),
                          t("mail_alert_body", name=row["first_name"], title=title,
                            date=fmt_date(listing["event_date"], "long"), price=money(with_fee(listing["price_cents"])),
                            url=url))
        push.send(row["user_id"], t("push_alert_title", title=title),
                  t("push_alert_body", date=fmt_date(listing["event_date"]), price=money(with_fee(listing["price_cents"]))),
                  event_url(listing["venue"], listing["event_date"], listing["event_name"]), tag="alert-%s" % listing["id"])
        db.run("UPDATE alerts SET last_notified_at=? WHERE id=?", sqltime(utcnow()), row["id"])
    return len(rows)


def send_reminders():
    """Day-of-event price nudges for unsold tickets, and 'did it work?' requests after the event."""
    nudged = asked = 0
    if datetime.now().hour >= config.NUDGE_HOUR:
        for l in browse("AND l.event_date = ? AND l.nudged_at IS NULL", today().isoformat()):
            seller = db.one("SELECT * FROM users WHERE id=?", l["seller_id"])
            notify.send_email(seller["email"], t("mail_nudge_subject", title=listing_title(l)),
                              t("mail_nudge_body", name=seller["first_name"], title=listing_title(l),
                                n=l["available"], price=money(l["price_cents"]),
                                url=base_url() + url_for("listing_page", listing_id=l["id"])))
            push.send(seller["id"], t("push_nudge_title"), t("push_nudge_body", n=l["available"], title=listing_title(l)),
                      url_for("listing_page", listing_id=l["id"]), tag="nudge-%s" % l["id"])
            db.run("UPDATE listings SET nudged_at=? WHERE id=?", sqltime(utcnow()), l["id"])
            nudged += 1
    for o in db.all(
        """SELECT o.*, l.venue, l.event_name, l.event_date FROM orders o JOIN listings l ON l.id = o.listing_id
           WHERE o.status IN ('paid', 'released') AND o.rating IS NULL AND o.rating_asked_at IS NULL
             AND l.event_date < ?""",
        today().isoformat(),
    ):
        buyer = db.one("SELECT * FROM users WHERE id=?", o["buyer_id"])
        notify.send_email(buyer["email"], t("mail_rate_subject", title=listing_title(o)),
                          t("mail_rate_body", name=buyer["first_name"], title=listing_title(o),
                            url=base_url() + url_for("order_page", public_id=o["public_id"])))
        push.send(buyer["id"], t("push_rate_title"), t("push_rate_body", title=listing_title(o)),
                  url_for("order_page", public_id=o["public_id"]), tag="rate-" + o["public_id"])
        db.run("UPDATE orders SET rating_asked_at=? WHERE id=?", sqltime(utcnow()), o["id"])
        asked += 1
    return nudged, asked


def send_transfer_reminders():
    """App tickets sold a while ago but not marked as transferred: one reminder email to the seller."""
    cutoff = sqltime(utcnow() - timedelta(hours=config.TRANSFER_REMIND_HOURS))
    sent = 0
    for o in db.all("""SELECT o.*, l.venue, l.event_name, l.event_date, l.transfer_app FROM orders o JOIN listings l ON l.id = o.listing_id
                       WHERE l.delivery = 'transfer' AND o.status = 'paid' AND o.transferred_at IS NULL AND o.received_at IS NULL
                         AND o.transfer_reminded_at IS NULL AND o.paid_at < ?""", cutoff):
        db.run("UPDATE orders SET transfer_reminded_at=? WHERE id=?", sqltime(utcnow()), o["id"])
        seller = db.one("SELECT * FROM users WHERE id=?", o["seller_id"])
        path = url_for("order_page", public_id=o["public_id"])
        notify.send_email(seller["email"], t("mail_transfer_reminder_subject", title=listing_title(o)),
                          t("mail_transfer_reminder_body", name=seller["first_name"], title=listing_title(o), app=app_name(o["transfer_app"]),
                            tname=o["transfer_name"], temail=o["transfer_email"], url=base_url() + path))
        push.send(seller["id"], t("mail_transfer_reminder_subject", title=listing_title(o)), t("seller_transfer_title"), path,
                  tag="transfer-remind-" + o["public_id"])
        sent += 1
    return sent


def send_buyer_transfer_reminders():
    """#31: "Did your ticket arrive in the app?" to the buyer, 2 hours after the seller marked it as transferred and
    again from 12:00 on the event day, while they haven't confirmed. One tap answers either way."""
    sent = 0
    now = utcnow()
    for o in db.all("""SELECT o.*, l.venue, l.event_name, l.event_date, l.transfer_app FROM orders o JOIN listings l ON l.id = o.listing_id
                       WHERE l.delivery = 'transfer' AND o.status = 'paid' AND o.transferred_at IS NOT NULL
                         AND o.received_at IS NULL AND o.buyer_reminders < 2""", ):
        first_due = parse_sqltime(o["transferred_at"]) + timedelta(hours=config.BUYER_REMIND_HOURS)
        day_of = o["event_date"] == today().isoformat() and datetime.now().hour >= 12
        if o["buyer_reminders"] == 0 and now < first_due and not day_of:
            continue
        if o["buyer_reminders"] == 1 and not (day_of and o["buyer_reminded_at"] and o["buyer_reminded_at"][:10] != now.strftime("%Y-%m-%d")):
            continue
        db.run("UPDATE orders SET buyer_reminders = buyer_reminders + 1, buyer_reminded_at=? WHERE id=?", sqltime(now), o["id"])
        path = url_for("order_page", public_id=o["public_id"])
        tell(o["buyer_id"], t("mail_arrived_subject", title=listing_title(o)),
             t("mail_arrived_body", title=listing_title(o), app=app_name(o["transfer_app"]), email=o["transfer_email"]), path)
        sent += 1
    return sent


def nightly_backup(force=False):
    """Once a day around 03:00: zip the database (and uploads when they fit) and email it to the admins."""
    day = today().isoformat()
    if not force and (datetime.now().hour != 3 or get_setting("last_backup_day") == day):
        return False
    try:
        data, name, full = backup.for_email()
    except backup.NoKey:
        notify.notify_admins("Spare: backup NOT made", "Tonight's backup wasn't made because BACKUP_KEY isn't set on Render, and "
                             "Spare never sends or stores backups unencrypted. Ask Claude for the steps to set it.")
        set_setting("last_backup_day", day)
        return False
    size = "%.1f MB" % (len(data) / 1024 / 1024)
    note = ("Database and uploaded tickets/posters." if full else
            "Database only: the uploaded files were too big for email. Render's daily disk snapshots cover them.")
    body = ("Spare's nightly backup (%s, %s).\n\n%s\n\nIt's encrypted: it opens only with BACKUP_KEY (from your password manager), "
            "using: flask --app app decrypt-backup <file>\n\nKeep these emails. To restore, ask Claude to put this file back on the server." % (name, size, note))
    for email in config.ADMIN_EMAILS:
        notify.send_email(email, "Spare backup %s" % day, body, attachments=[(name, data)])
    set_setting("last_backup_day", day)
    return True


TICKET_FILE_DAYS = 14   # #15: ticket files are deleted this long after the event, once the money is settled
DISK_ALERT = 0.70       # warn the admins when the data disk is this full


def cleanup_ticket_files():
    """#15: deletes ticket files 14 days after the event when nothing can still change: the order was paid out or
    refunded, or the ticket never sold. The hashes stay, so the duplicate check still recognises the ticket."""
    cutoff = (today() - timedelta(days=TICKET_FILE_DAYS)).isoformat()
    rows = db.all("""SELECT t.id, t.file, o.status AS order_status FROM tickets t JOIN listings l ON l.id = t.listing_id
                     LEFT JOIN orders o ON o.id = t.order_id
                     WHERE t.file != '' AND l.event_date < ?""", cutoff)
    by_file = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    deleted = 0
    for name, tickets in by_file.items():
        settled = all(r["order_status"] in (None, "released", "refunded", "expired", "cancelled") for r in tickets)
        # A file can be shared by several tickets (one PDF with several codes): only delete it when all are done.
        if not settled or db.one("SELECT COUNT(*) AS n FROM tickets WHERE file=?", name)["n"] != len(tickets):
            continue
        db.run("UPDATE tickets SET file='' WHERE file=?", name)
        try:
            os.remove(os.path.join(config.UPLOAD_DIR, name))
        except FileNotFoundError:
            pass
        deleted += 1
    # Proof files (screenshots, order confirmations) go on the same schedule, unless an order is still open.
    delete_proofs(db.all("""SELECT p.* FROM proofs p LEFT JOIN listings l ON l.id = p.listing_id
                            LEFT JOIN orders o ON o.id = p.order_id LEFT JOIN listings ol ON ol.id = o.listing_id
                            WHERE COALESCE(l.event_date, ol.event_date) < ?
                              AND NOT EXISTS (SELECT 1 FROM orders x WHERE (x.listing_id = p.listing_id OR x.id = p.order_id)
                                              AND x.status IN ('pending', 'paid', 'disputed', 'releasing'))""", cutoff))
    return deleted


def delete_proofs(rows):
    for p in rows:
        db.run("DELETE FROM proofs WHERE id=?", p["id"])
        try:
            os.remove(os.path.join(config.UPLOAD_DIR, "proofs", p["file"]))
        except FileNotFoundError:
            pass


def company():
    """Who runs Spare (#19, #20), with visible placeholders until the KvK details are set on Render."""
    return {"name": config.COMPANY_NAME or "[COMPANY NAME]", "address": config.COMPANY_ADDRESS or "[ADDRESS]",
            "kvk": config.KVK_NUMBER or "[KVK NUMBER]", "vat": config.VAT_NUMBER or "[VAT NUMBER]",
            "complete": bool(config.COMPANY_NAME and config.COMPANY_ADDRESS and config.KVK_NUMBER)}


def disk_usage():
    """(used fraction, used bytes, total bytes) of the disk holding the data."""
    usage = shutil.disk_usage(config.DATA_DIR)
    return usage.used / usage.total, usage.used, usage.total


def check_disk():
    """#15: email the admins (at most once a day) when the disk passes 70%, before a full disk stops all writes."""
    used, _, total = disk_usage()
    day = today().isoformat()
    if used < DISK_ALERT or get_setting("disk_alert_day") == day:
        return False
    notify.notify_admins("Spare: the disk is %d%% full" % round(used * 100),
                         "Spare's data disk is %d%% full (%.1f GB). When it's full, nothing can be saved: no sign-ins, sales or payouts.\n\n"
                         "Ask Claude to look at what's using the space, or make the disk bigger on Render (Disks)." % (round(used * 100), total / 1e9))
    set_setting("disk_alert_day", day)
    return True


def send_resell_reminders():
    """The day before an event: 'Can't make it any more? Sell your ticket.' Once per order, to buyers who still hold it."""
    if datetime.now().hour < config.RESELL_HOUR:
        return 0
    tomorrow = (today() + timedelta(days=1)).isoformat()
    sent = 0
    for o in db.all("""SELECT o.*, l.venue, l.event_name, l.event_date, l.event_id FROM orders o JOIN listings l ON l.id = o.listing_id
                       WHERE o.status = 'paid' AND o.resell_reminded_at IS NULL AND l.event_date = ?""", tomorrow):
        db.run("UPDATE orders SET resell_reminded_at=? WHERE id=?", sqltime(utcnow()), o["id"])
        relisted = db.one("""SELECT 1 FROM listings WHERE seller_id=? AND event_date=? AND lower(venue)=lower(?)
                             AND lower(COALESCE(event_name, ''))=lower(?) AND status='active'""",
                          o["buyer_id"], o["event_date"], o["venue"], o["event_name"] or "")
        if relisted:
            continue
        buyer = db.one("SELECT * FROM users WHERE id=?", o["buyer_id"])
        title = listing_title(o)
        looking = looking_count(o["event_date"], o["venue"], o["event_name"])
        path = url_for("sell", event=o["event_id"]) if o["event_id"] else url_for("sell")
        notify.send_email(buyer["email"], t("mail_resell_subject", title=title),
                          t("mail_resell_body", name=buyer["first_name"], title=title,
                            looking=t("mail_resell_looking", n=looking) if looking > 1 else "", url=base_url() + path))
        push.send(buyer["id"], t("push_resell_title"), t("push_resell_body", title=title), path, tag="resell-" + o["public_id"])
        sent += 1
    return sent


# --------------------------------------------------------------------------
# Request setup
# --------------------------------------------------------------------------

# Stripe, the cron job and Render's health check must keep working on any address.
HOST_REDIRECT_EXEMPT = {"/webhooks/stripe", "/tasks/hourly", "/robots.txt"}


@app.before_request
def canonical_host():
    """#1: serve Spare only at BASE_URL (sparetickets.eu); old addresses like nightswap.onrender.com redirect there."""
    if config.DEV or not config.BASE_URL or request.path in HOST_REDIRECT_EXEMPT:
        return None
    wanted = urlparse(config.BASE_URL).netloc.lower()
    if wanted and request.host.lower() != wanted:
        return redirect(config.BASE_URL + request.full_path.rstrip("?"), code=301)
    return None


@app.before_request
def before():
    lang = request.cookies.get("lang")
    if lang not in i18n.LANGS:
        lang = request.accept_languages.best_match(list(i18n.LANGS)) or "en"
    g.lang = lang

    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = db.one("SELECT * FROM users WHERE id=?", uid)
        if not g.user:
            session.pop("uid", None)
    if g.user and (g.user["blocked_at"] or g.user["deleted_at"] or session.get("sv", 0) != g.user["session_version"]):
        fresh_session()  # blocked, deleted, or signed out everywhere (#11)
        g.user = None
    g.is_admin = bool(g.user and g.user["email"] in config.ADMIN_EMAILS)

    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    if request.method == "POST" and request.endpoint not in ("stripe_webhook", "hourly_tasks", "waitlist_leave"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(sent, session["csrf"]):
            abort(400)

    track_source()

    # #22: on the real site with Stripe still in test mode, nobody may trade real tickets: anyone could pay with
    # Stripe's public test card. Behave as if coming-soon mode is on for everyone except admins and preview links.
    g.test_lock = not config.DEV and payments.is_test_mode()
    g.coming_soon = coming_soon_on() or g.test_lock
    preview = session.get("preview")
    g.sees_site = not g.coming_soon or g.is_admin or bool(preview and preview == get_setting("preview_code"))
    if not g.sees_site and request.endpoint not in OPEN_WHILE_COMING_SOON:
        return redirect(url_for("index"))


CSP = "; ".join([
    "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
    "font-src 'self'", "connect-src 'self'", "worker-src 'self'", "manifest-src 'self'", "object-src 'none'",
    "base-uri 'self'", "frame-ancestors 'none'", "form-action 'self' https://*.stripe.com",
])


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # #9: scripts only from our own files (no inline scripts or onclick=...); forms may also lead on to Stripe.
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    if not config.DEV:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


_asset_versions = {}


@app.url_defaults
def version_static_files(endpoint, values):
    """Adds ?v=<content hash> to /static URLs so browsers and the offline cache pick up new versions at once."""
    if endpoint != "static" or "filename" not in values or "v" in values:
        return
    path = os.path.join(app.static_folder, values["filename"])
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return
    cached = _asset_versions.get(path)
    if not cached or cached[0] != mtime:
        with open(path, "rb") as fh:
            cached = (mtime, hashlib.md5(fh.read()).hexdigest()[:10])
        _asset_versions[path] = cached
    values["v"] = cached[1]


TONIGHT_POP_PAGES = {"index", "event_page", "venue_page", "how", "account", "inbox", "settings", "credits"}


def tonight_pop():
    """Tonight's event with the most tickets for sale, for the small pop-up. None on checkout, chat, selling and so on."""
    if request.endpoint not in TONIGHT_POP_PAGES or not g.get("sees_site", True) or datetime.now().hour < 12:
        return None
    day = today().isoformat()
    row = db.one("""SELECT e.name, e.venue, COUNT(t.id) AS n, MIN(l.price_cents) AS price
                    FROM events e JOIN listings l ON l.event_id = e.id AND l.status = 'active'
                    JOIN tickets t ON t.listing_id = l.id AND t.status = 'available'
                    WHERE e.event_date = ? AND e.status IN ('published', 'review') AND e.cancelled_at IS NULL
                    GROUP BY e.id ORDER BY n DESC, price LIMIT 1""", day)
    if not row:
        return None
    price = money(with_fee(row["price"]))
    line = t("pop_line_one", price=price) if row["n"] == 1 else t("pop_line", n=row["n"], price=price)
    return {"day": day, "name": row["name"], "venue": row["venue"], "line": line,
            "url": event_url(row["venue"], day, row["name"])}


@app.context_processor
def template_globals():
    return {
        "t": t,
        "money": money,
        "fmt_date": fmt_date,
        "event_url": event_url,
        "art_url": art_url,
        "lang": g.get("lang", "en"),
        "lang_names": i18n.LANG_NAMES,
        "user": g.get("user"),
        "is_admin": g.get("is_admin"),
        "csrf_token": session.get("csrf", ""),
        "test_mode": payments.is_test_mode(),
        "sms_enabled": config.SMS_ENABLED,
        "fee_for": fee_for,
        "with_fee": with_fee,
        "fee_label": fee_label(),
        "fee_percent": config.FEE_PERCENT,
        "fee_min": config.FEE_MIN_CENTS,
        "seller_fee_for": seller_fee_for, "payout_for": payout_for, "seller_fee_label": seller_fee_label(),
        "seller_fee_pct": config.SELLER_FEE_PERCENT, "seller_fee_min": config.SELLER_FEE_MIN_CENTS,
        "fee_max": config.FEE_MAX_CENTS,
        "price_cap": config.PRICE_CAP_PERCENT,
        "price_markup": config.PRICE_CAP_PERCENT - 100,  # e.g. 20 for "up to 20% above face value"
        "payout_hours": config.PAYOUT_DELAY_HOURS,
        "new_seller_hours": config.NEW_SELLER_PAYOUT_HOURS, "new_seller_sales": config.NEW_SELLER_SALES,
        "base_url": base_url(),
        "unread": unread_count(g.user["id"]) if g.get("user") else 0,
        "coming_soon": g.get("coming_soon"), "test_lock": g.get("test_lock"),
        "transfer_apps": config.TRANSFER_APPS, "app_name": app_name,
        "avatar_icons": avatars.ICONS, "avatar_choices": avatars.CHOICES, "avatar_viewbox": avatars.VIEWBOX,
        "tonight_pop": tonight_pop(),
        "company": company(),
    }


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def error_page(err):
    return render_template("error.html", code=err.code), err.code


@app.errorhandler(500)
def server_error(err):
    """Something crashed: tell the admins by email (at most 5 an hour), and show a friendly page."""
    original = getattr(err, "original_exception", None) or err
    try:
        if not app.debug and not app.testing and not rate_limited("crash-email", 5, 60, who="site"):
            import traceback
            trace = "".join(traceback.format_exception(type(original), original, original.__traceback__))[-6000:]
            who = g.get("user")["email"] if g.get("user") else "not signed in"
            notify.notify_admins("Spare error: %s on %s" % (type(original).__name__, request.path),
                                 "Something went wrong for a visitor.\n\nPage: %s %s\nUser: %s\nTime: %s\n\n%s"
                                 % (request.method, request.full_path, who, utcnow(), trace))
    except Exception:
        app.logger.exception("Could not send the error email")
    try:
        return render_template("error.html", code=500), 500
    except Exception:
        return "Something went wrong. Please try again in a minute.", 500


# --------------------------------------------------------------------------
# Browsing
# --------------------------------------------------------------------------

VENUE_COLOURS = ["#5b2a86", "#1f4e8c", "#0f6b5c", "#a8322d", "#7a2f5f", "#2c5d3a", "#8a4b12", "#3a3f9e"]


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "venue"


def get_venue(name, city=None, create=False):
    """The venue record for a venue name (and city). Created on first use when create=True."""
    cache = g.setdefault("_venues", {})
    key = (name.lower(), (city or "").lower())
    if cache.get(key) or (key in cache and not create):
        return cache[key]
    if city:
        row = db.one("SELECT * FROM venues WHERE lower(name)=lower(?) AND lower(city)=lower(?)", name, city)
    else:
        row = db.one("SELECT * FROM venues WHERE lower(name)=lower(?) ORDER BY city != ?, id LIMIT 1",
                     name, config.DEFAULT_CITY)
    if not row and create and city:
        base = slugify(name) if city.lower() == config.DEFAULT_CITY.lower() else slugify(name + " " + city)
        slug, n = base, 2
        while db.one("SELECT 1 FROM venues WHERE slug=?", slug):
            slug, n = "%s-%d" % (base, n), n + 1
        db.run("INSERT INTO venues (slug, name, city) VALUES (?, ?, ?)", slug, name, city)
        row = db.one("SELECT * FROM venues WHERE slug=?", slug)
    cache[key] = row
    return row


def venue_colour(venue):
    v = get_venue(venue)
    if v and v["colour"]:
        return v["colour"]
    return VENUE_COLOURS[int(hashlib.md5(venue.lower().encode()).hexdigest(), 16) % len(VENUE_COLOURS)]


def event_key(row):
    return (row["event_date"], row["venue"].lower(), (row["event_name"] or "").lower())


def photo_credit(venue):
    """{'text': 'Photo: Name · CC BY-SA 4.0', 'url': source page} for a credited venue photo, else None."""
    if not venue or not venue["photo_file"] or not (venue["photo_credit"] or venue["photo_license"]):
        return None
    return {"url": url_for("credits") + "#v-" + venue["slug"]}


def event_rows(where="", *args):
    """Upcoming real events that are visible on the site."""
    return db.all("SELECT * FROM events WHERE status IN ('published', 'review') AND cancelled_at IS NULL AND event_date >= ? %s ORDER BY event_date" % where,
                  today().isoformat(), *args)


def find_event(event_date, venue, name):
    return db.one("""SELECT * FROM events WHERE event_date=? AND lower(venue)=lower(?) AND lower(name)=lower(?)
                     AND status != 'hidden'""", event_date, venue, name or "")


def group_events(rows, events=()):
    """One card per event: listings grouped by (date, venue, name), plus real events nobody is selling yet."""
    out = {}

    def blank(day, venue_name, name, city, category):
        venue = get_venue(venue_name, city)
        return {"date": day, "venue": venue_name, "name": name, "city": city, "category": category,
                "start_time": None, "poster": None, "photo": venue["photo_file"] if venue else None,
                "photo_credit": photo_credit(venue), "poster_credit": None,
                "venue_slug": venue["slug"] if venue else None, "count": 0, "min_price": None,
                "colour": venue_colour(venue_name), "newest": "", "hot": False, "new": False, "event": None}

    for row in rows:
        key = event_key(row)
        ev = out.get(key)
        if not ev:
            ev = out[key] = blank(row["event_date"], row["venue"], row["event_name"], row["city"], row["category"])
        ev["count"] += row["available"]
        ev["min_price"] = row["price_cents"] if ev["min_price"] is None else min(ev["min_price"], row["price_cents"])
        ev["poster"] = ev["poster"] or row["poster_file"]
        ev["start_time"] = ev["start_time"] or row["start_time"]
        ev["newest"] = max(ev["newest"], row["created_at"])
    for e in events:
        key = (e["event_date"], e["venue"].lower(), (e["name"] or "").lower())
        ev = out.get(key)
        if not ev:
            ev = out[key] = blank(e["event_date"], e["venue"], e["name"], e["city"], e["category"])
        ev.update(event=e, category=e["category"], city=e["city"], name=e["name"],
                  start_time=e["door_time"] or ev["start_time"], poster=e["poster_file"] or ev["poster"],
                  poster_credit=e["poster_credit"] if e["poster_file"] else None)
    for ev in out.values():
        if ev["poster"]:
            ev["credit"] = {"url": url_for("credits") + "#e-%s" % ev["event"]["id"]} if ev["poster_credit"] and ev["event"] else None
        else:
            ev["credit"] = ev["photo_credit"]
    order = {c: i for i, c in enumerate(config.CATEGORIES)}
    return sorted(out.values(), key=lambda e: (e["date"], 0 if e["count"] else 1, order.get(e["category"], 9),
                                               e["min_price"] or 0))


def already_listed(column, value):
    """True when this ticket file or code is (or was) on Spare, unless the person selling bought it here:
    someone who can't go any more may resell a ticket they bought on Spare."""
    rows = db.all("""SELECT t.status, o.buyer_id, o.status AS order_status FROM tickets t LEFT JOIN orders o ON o.id = t.order_id
                     WHERE t.%s = ? AND t.status != 'void' ORDER BY t.id""" % column, value)
    if not rows:
        return False
    if any(r["status"] != "sold" for r in rows):  # on sale or in someone's checkout right now
        return True
    last = rows[-1]  # only whoever bought it most recently may sell it on
    return not (last["buyer_id"] == g.user["id"] and last["order_status"] in ("paid", "released"))


def similar_picture_listed(phash, codes):
    """#29: True when a picture that looks the same (perceptual hash) is on Spare, as a ticket or an app-ticket
    screenshot. Two pictures whose codes could both be read and differ are different tickets, however alike they look."""
    if not phash:
        return False
    own_codes = {c[0] for c in codes}
    candidates = db.all("""SELECT t.phash, t.code_hash FROM tickets t WHERE t.phash IS NOT NULL AND t.status != 'void'
                           UNION ALL SELECT p.phash, NULL FROM proofs p WHERE p.kind = 'app_screenshot' AND p.phash IS NOT NULL""")
    for row in candidates:
        if ticketcodes.phash_distance(phash, row["phash"]) > ticketcodes.PHASH_MATCH:
            continue
        if own_codes and row["code_hash"] and row["code_hash"] not in own_codes:
            continue  # both readable, different codes: a different ticket from the same template
        if row["code_hash"] and row["code_hash"] in own_codes and not already_listed("code_hash", row["code_hash"]):
            continue  # the same ticket, but this person may sell it on (they bought it here)
        return True
    return False


def looking_count(event_date, venue, name):
    """How many people asked to be told when a ticket for this night appears ("Notify me")."""
    return db.one("""SELECT COUNT(*) AS n FROM alerts WHERE event_date=? AND lower(venue)=lower(?) AND lower(event_name)=lower(?)""",
                  event_date, venue, name or "")["n"]


def annotate_activity(events):
    """'Going fast' (people waiting or recent sales) and 'Just listed' signals for event cards."""
    if not events:
        return events
    waiting = {(r["event_date"], r["v"], r["n"]): r["c"] for r in db.all(
        """SELECT event_date, lower(venue) AS v, lower(event_name) AS n, COUNT(*) AS c
           FROM alerts WHERE event_date >= ? GROUP BY 1, 2, 3""", today().isoformat())}
    sold = {(r["event_date"], r["v"], r["n"]): r["c"] for r in db.all(
        """SELECT l.event_date, lower(l.venue) AS v, lower(COALESCE(l.event_name, '')) AS n, SUM(o.quantity) AS c
           FROM orders o JOIN listings l ON l.id = o.listing_id
           WHERE o.status IN ('paid', 'released', 'disputed') AND o.paid_at > ? GROUP BY 1, 2, 3""",
        sqltime(utcnow() - timedelta(hours=24)))}
    fresh = sqltime(utcnow() - timedelta(hours=6))
    for ev in events:
        key = (ev["date"], ev["venue"].lower(), (ev["name"] or "").lower())
        ev["hot"] = ev["count"] > 0 and (waiting.get(key, 0) >= 3 or sold.get(key, 0) >= 2)
        ev["waiting"] = waiting.get(key, 0)
        ev["new"] = False
    # "Just listed" only means something if it's rare: the three most recent events, listed in the last 6 hours.
    recent = sorted((ev for ev in events if ev["newest"] >= fresh and not ev["hot"]), key=lambda e: e["newest"], reverse=True)
    for ev in recent[:3]:
        ev["new"] = True
    return events


def starts_at(ev):
    """ISO start datetime for the countdown, when the seller gave a start time."""
    if not ev.get("start_time"):
        return None
    return "%sT%s:00" % (ev["date"], ev["start_time"])


def in_city_view(row, city):
    if city == "all":
        return True
    return row["city"].lower() == city.lower() or row["category"] in config.NATIONAL_CATEGORIES


def art_url(event_date, venue, name, category):
    """Generated artwork for an event without a poster (see art.py)."""
    return url_for("event_art", k="%s|%s|%s" % (event_date, venue.lower(), (name or "").lower()),
                   c=category or "club", b=venue_colour(venue).lstrip("#"))


@app.route("/art.svg")
def event_art():
    key = request.args.get("k", "")[:200]
    category = request.args.get("c", "club")
    colour = request.args.get("b", "")
    colour = "#" + colour if re.fullmatch(r"[0-9a-fA-F]{6}", colour) else None
    resp = app.response_class(art.svg(key, category, colour), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    return resp


@app.template_filter("vt")
def view_transition_name(ev):
    """A CSS-safe name so the poster animates from the card into the event page."""
    return "ev-" + slugify("%s %s %s" % (ev["date"], ev["venue"], ev["name"] or ""))[:60]


@app.route("/")
def index():
    if not g.sees_site:
        return coming_soon_page()
    release_unpaid()
    q = request.args.get("q", "").strip()[:60]
    day = request.args.get("day", "").strip()
    city = request.args.get("city") or request.cookies.get("city") or config.DEFAULT_CITY
    if (city == "all" and not config.SHOW_ALL_NETHERLANDS) or (city != "all" and city not in config.ENABLED_CITIES):
        city = config.DEFAULT_CITY
    cat = request.args.get("cat", "")
    if cat not in config.CATEGORIES:
        cat = ""

    evs = [e for e in group_events(browse(), event_rows()) if in_city_view(e, city)]
    if q:
        needle = q.lower()
        evs = [e for e in evs if needle in e["venue"].lower() or needle in (e["name"] or "").lower()]
    cat_counts = {c: sum(1 for e in evs if e["category"] == c) for c in config.CATEGORIES}
    if cat:
        evs = [e for e in evs if e["category"] == cat]

    # Day tabs: the next 14 nights, then "later".
    horizon = (today() + timedelta(days=13)).isoformat()
    tickets_per_day, events_per_day = {}, {}
    for e in evs:
        tickets_per_day[e["date"]] = tickets_per_day.get(e["date"], 0) + e["count"]
        events_per_day[e["date"]] = events_per_day.get(e["date"], 0) + 1
    tabs = [{"key": d, "date": d, "count": tickets_per_day.get(d, 0), "events": events_per_day.get(d, 0)}
            for d in ((today() + timedelta(days=i)).isoformat() for i in range(14))]
    later = sum(n for d, n in events_per_day.items() if d > horizon)

    if day == "later":
        shown = [e for e in evs if e["date"] > horizon]
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        shown = [e for e in evs if e["date"] == day]
    else:
        day, shown = "", evs

    # "Tonight" strip in the evening, on the main view.
    hour = datetime.now().hour
    tonight = []
    if not day and not q and (hour >= 17 or hour < 4):
        tonight = [e for e in evs if e["date"] == today().isoformat() and e["count"]]

    shown = annotate_activity(shown)
    resp = app.make_response(render_template(
        "index.html", events=[e for e in shown if e["count"]], upcoming=[e for e in shown if not e["count"]],
        count=sum(e["count"] for e in shown),
        total=sum(tickets_per_day.values()), q=q, day=day, tabs=tabs, later=later, city=city, cat=cat,
        cat_counts=cat_counts, cities=config.ENABLED_CITIES, show_all_nl=config.SHOW_ALL_NETHERLANDS, tonight=annotate_activity(tonight), starts_at=starts_at))
    if request.args.get("city"):
        resp.set_cookie("city", city, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


def search_rank(needle, *fields):
    """0 = a word starts with the search, 1 = contains it, None = no match."""
    best = None
    for field in fields:
        text = (field or "").lower()
        if any(word.startswith(needle) for word in re.split(r"[\s\-&·/]+", text)) or text.startswith(needle):
            return 0
        if needle in text:
            best = 1
    return best


@app.route("/api/search")
def search_suggest():
    """Live suggestions for the search bar: matching events (with tickets) and venues."""
    release_unpaid()
    needle = " ".join(request.args.get("q", "").lower().split())[:60]
    if not needle:
        return {"events": [], "venues": []}
    events = []
    for ev in group_events(browse(), event_rows()):
        rank = search_rank(needle, ev["name"], ev["venue"], ev["city"])
        if rank is not None:
            events.append((rank, ev))
    events.sort(key=lambda pair: (pair[0], 0 if pair[1]["count"] else 1, pair[1]["date"]))
    venues = db.all("SELECT * FROM venues ORDER BY name")
    venues = [v for v in venues if search_rank(needle, v["name"], v["city"]) is not None]
    return {
        "events": [{
            "name": ev["name"] or ev["venue"], "venue": ev["venue"] + ("" if ev["city"] == config.DEFAULT_CITY else ", " + ev["city"]),
            "date": fmt_date(ev["date"]),
            "price": ((t("from") + " " if ev["count"] > 1 else "") + money(with_fee(ev["min_price"])))
                     if ev["count"] else "",
            "count": (t("n_left", n=ev["count"]) if ev["count"] != 1 else t("one_left")) if ev["count"] else t("no_tickets_yet"),
            "colour": ev["colour"], "day": int(ev["date"][8:10]), "dow": fmt_date(ev["date"], "dow"),
            "url": event_url(ev["venue"], ev["date"], ev["name"]),
        } for _, ev in events[:6]],
        "venues": [{"name": v["name"] + ("" if v["city"] == config.DEFAULT_CITY else ", " + v["city"]),
                    "url": url_for("venue_page", slug=v["slug"])} for v in venues[:3]],
    }


def directions_url(venue):
    query = ", ".join(x for x in (venue["name"], venue["address"], venue["city"]) if x)
    return "https://www.google.com/maps/search/?api=1&query=" + quote(query)


@app.route("/event")
def event_page():
    release_unpaid()
    venue = request.args.get("venue", "")
    day = request.args.get("date", "")
    name = request.args.get("name", "")
    rows = [r for r in browse("AND l.event_date = ? AND lower(l.venue) = lower(?)", day, venue)
            if (r["event_name"] or "").lower() == name.lower()]
    e = find_event(day, venue, name)
    cancelled = e if e and e["cancelled_at"] else None
    known = rows or e or db.one(
        """SELECT * FROM listings
           WHERE event_date = ? AND lower(venue) = lower(?) AND lower(COALESCE(event_name, '')) = lower(?)
           AND status != 'removed' LIMIT 1""",
        day, venue, name,
    )
    if not known:
        abort(404)
    if rows or e:
        ev = annotate_activity(group_events(rows, [e] if e else []))[0]
    else:  # an old listing-only event that's sold out: keep the page so people can ask to be notified
        v = get_venue(known["venue"], known["city"])
        ev = {"date": known["event_date"], "venue": known["venue"], "name": known["event_name"], "city": known["city"],
              "category": known["category"], "start_time": known["start_time"], "poster": known["poster_file"],
              "photo": v["photo_file"] if v else None, "venue_slug": v["slug"] if v else None, "event": None,
              "count": 0, "min_price": None, "colour": venue_colour(known["venue"]), "hot": False, "new": False,
              "credit": photo_credit(v), "poster_credit": None, "photo_credit": photo_credit(v)}
    alert = g.user and db.one(
        "SELECT 1 FROM alerts WHERE user_id=? AND event_date=? AND lower(venue)=lower(?) AND lower(event_name)=lower(?)",
        g.user["id"], ev["date"], ev["venue"], ev["name"] or "",
    )
    venue_row = get_venue(ev["venue"], ev["city"])
    return render_template("event.html", ev=ev, rows=rows, stats=seller_stats(r["seller_id"] for r in rows),
                           alert=alert, looking=looking_count(ev["date"], ev["venue"], ev["name"]), past=ev["date"] < today().isoformat(), venue=venue_row, e=ev.get("event"), cancelled=cancelled,
                           directions=directions_url(venue_row) if venue_row else None, starts_at=starts_at(ev))


@app.route("/venues/<slug>")
def venue_page(slug):
    release_unpaid()
    venue = db.one("SELECT * FROM venues WHERE slug=?", slug)
    if not venue:
        abort(404)
    rows = browse("AND lower(l.venue) = lower(?) AND lower(l.city) = lower(?)", venue["name"], venue["city"])
    evs = group_events(rows, event_rows("AND lower(venue) = lower(?) AND lower(city) = lower(?)", venue["name"], venue["city"]))
    return render_template("venue.html", venue=venue, events=annotate_activity(evs), credit=photo_credit(venue),
                           colour=venue_colour(venue["name"]), directions=directions_url(venue))


@app.route("/sitemap.xml")
def sitemap():
    urls = [base_url() + url_for("index"), base_url() + url_for("how")]
    urls += [base_url() + url_for("venue_page", slug=v["slug"]) for v in db.all("SELECT slug FROM venues")]
    urls += [event_url(ev["venue"], ev["date"], ev["name"], external=True) for ev in group_events(browse(), event_rows())]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    body += "".join("  <url><loc>%s</loc></url>\n" % escape(u) for u in urls) + "</urlset>\n"
    return app.response_class(body, mimetype="application/xml")


@app.route("/robots.txt")
def robots():
    body = "User-agent: *\nDisallow: /account\nDisallow: /orders\nDisallow: /threads\nDisallow: /admin\n"
    body += "Sitemap: %s\n" % (base_url() + url_for("sitemap"))
    return app.response_class(body, mimetype="text/plain")


@app.route("/alerts", methods=["POST"])
@login_required
def toggle_alert():
    venue = request.form.get("venue", "")[:60]
    day = request.form.get("date", "")
    name = request.form.get("name", "")[:80]
    if not venue or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        abort(400)
    if request.form.get("action") == "remove":
        db.run("DELETE FROM alerts WHERE user_id=? AND event_date=? AND lower(venue)=lower(?) AND lower(event_name)=lower(?)",
               g.user["id"], day, venue, name)
        flash(t("alert_removed"), "ok")
    else:
        db.run("INSERT OR IGNORE INTO alerts (user_id, venue, event_date, event_name) VALUES (?, ?, ?, ?)",
               g.user["id"], venue, day, name)
        flash(t("alert_added"), "ok")
    return redirect(safe_next(request.form.get("next"), event_url(venue, day, name)))


@app.route("/posters/<name>")
def poster(name):
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp)", name):
        abort(404)
    return send_file(os.path.join(config.UPLOAD_DIR, "posters", name), max_age=60 * 60 * 24 * 30)


@app.route("/t/<int:listing_id>")
def listing_page(listing_id):
    release_unpaid()
    l = listing_with_seller(listing_id)
    if not l or l["status"] == "removed":
        abort(404)
    own = bool(g.user and g.user["id"] == l["seller_id"])
    if l["status"] in ("review", "draft") and not (own or g.is_admin):
        abort(404)
    past = l["event_date"] < today().isoformat()
    suggestions = []
    if own and l["event_date"] == today().isoformat() and l["available"]:
        # Day-of-event nudge: a couple of lower prices that tend to sell.
        for drop in (200, 500):
            if l["price_cents"] - drop >= 100:
                suggestions.append(l["price_cents"] - drop)
    return render_template(
        "listing.html", l=l, own=own, past=past, stats=seller_stats([l["seller_id"]])[l["seller_id"]],
        available=(l["status"] == "active" and not past and l["available"] > 0),
        report_until=payout_due_for(l["event_date"], l["seller_id"]), colour=venue_colour(l["venue"]),
        max_price=max_price(l["face_value_cents"]), suggestions=suggestions,
        just_listed=request.args.get("new") == "1", questions=QUICK_QUESTIONS,
        venue=get_venue(l["venue"], l["city"]),
        event=db.one("SELECT * FROM events WHERE id=?", l["event_id"]) if l["event_id"] else None,
    )


# --------------------------------------------------------------------------
# Buying
# --------------------------------------------------------------------------

@app.route("/t/<int:listing_id>/buy", methods=["POST"])
@login_required
def buy(listing_id):
    release_unpaid()
    l = listing_with_seller(listing_id)
    back = safe_next(request.form.get("back"), url_for("listing_page", listing_id=listing_id))
    if not l:
        abort(404)
    if l["seller_id"] == g.user["id"]:
        flash(t("err_own_ticket"), "error")
        return redirect(back)
    if l["status"] != "active" or l["event_date"] < today().isoformat() or not l["available"]:
        flash(t("err_unavailable"), "error")
        return redirect(back)

    try:
        qty = int(request.form.get("qty", "1"))
    except ValueError:
        qty = 1
    unit_price = l["price_cents"]
    offer = None
    if request.form.get("offer_id"):
        offer = db.one("SELECT * FROM offers WHERE id=? AND listing_id=? AND buyer_id=? AND status='accepted'",
                       request.form.get("offer_id"), listing_id, g.user["id"])
        if not offer:
            flash(t("err_offer_closed"), "error")
            return redirect(back)
        unit_price, qty = min(offer["amount_cents"], l["price_cents"]), offer["quantity"]
    if l["sold_together"]:
        qty = l["available"]
    qty = max(1, min(qty, l["available"]))
    transfer_name = transfer_email = None
    if l["delivery"] == "transfer":
        # Where the seller sends the ticket in the organiser's app. Only the seller sees this.
        transfer_name = " ".join(request.form.get("transfer_name", "").split())[:80]
        transfer_email = request.form.get("transfer_email", "").strip().lower()[:254]
        if len(transfer_name) < 2 or not EMAIL_RE.match(transfer_email):
            flash(t("err_transfer_details"), "error")
            return redirect(back)

    public_id = secrets.token_urlsafe(9)
    price = unit_price * qty
    fee = fee_for(unit_price) * qty
    order_id = db.run(
        """INSERT INTO orders (public_id, listing_id, buyer_id, seller_id, quantity, price_cents, fee_cents,
                               total_cents, offer_id, expires_at, seller_fee_cents, transfer_name, transfer_email)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        public_id, listing_id, g.user["id"], l["seller_id"], qty, price, fee, price + fee, offer["id"] if offer else None,
        sqltime(utcnow() + timedelta(minutes=config.RESERVATION_MINUTES + 2)), seller_fee_for(unit_price) * qty,
        transfer_name, transfer_email,
    )
    held = 0
    for row in db.all("SELECT id FROM tickets WHERE listing_id=? AND status='available' ORDER BY id LIMIT ?",
                      listing_id, qty):
        held += db.count("UPDATE tickets SET status='held', order_id=? WHERE id=? AND status='available'",
                         order_id, row["id"])
    if held < qty:  # someone else got there first
        db.run("UPDATE orders SET status='cancelled' WHERE id=?", order_id)
        db.run("UPDATE tickets SET status='available', order_id=NULL WHERE order_id=? AND status='held'", order_id)
        flash(t("err_unavailable"), "error")
        return redirect(back)
    order = db.one("SELECT * FROM orders WHERE id=?", order_id)

    if not payments.live():
        return redirect(url_for("test_checkout", public_id=public_id))

    success = base_url() + url_for("order_page", public_id=public_id) + "?session_id={CHECKOUT_SESSION_ID}"
    cancel = base_url() + url_for("cancel_order", public_id=public_id)
    title = listing_title(l) + " – " + fmt_date(l["event_date"], "long")
    # #27: a first purchase is paid by iDEAL only (a card payment can be charged back after the seller is paid)
    first_time = not db.one("SELECT 1 FROM orders WHERE buyer_id=? AND status IN ('paid', 'released', 'releasing') AND id != ?",
                            g.user["id"], order_id)
    try:
        session_id, pay_url = payments.create_checkout(order, title, g.user["email"], success, cancel, ideal_only=first_time)
    except Exception:
        app.logger.exception("Stripe checkout failed")
        db.run("UPDATE orders SET status='cancelled' WHERE id=?", order_id)
        db.run("UPDATE tickets SET status='available', order_id=NULL WHERE order_id=? AND status='held'", order_id)
        flash(t("err_payment_start"), "error")
        return redirect(back)
    db.run("UPDATE orders SET payment_ref=? WHERE id=?", session_id, order_id)
    return redirect(pay_url, code=303)


def get_order_for_user(public_id):
    order = db.one("SELECT * FROM orders WHERE public_id=?", public_id)
    if not order:
        abort(404)
    if g.user["id"] not in (order["buyer_id"], order["seller_id"]) and not g.is_admin:
        abort(404)
    return order


@app.route("/checkout/<public_id>/cancel")
@login_required
def cancel_order(public_id):
    order = get_order_for_user(public_id)
    if order["status"] == "pending" and order["buyer_id"] == g.user["id"]:
        db.run("UPDATE orders SET status='cancelled' WHERE id=?", order["id"])
        db.run("UPDATE tickets SET status='available', order_id=NULL WHERE order_id=? AND status='held'", order["id"])
        if order["payment_ref"]:
            payments.expire_checkout(order["payment_ref"])
    return redirect(url_for("listing_page", listing_id=order["listing_id"]))


@app.route("/checkout/<public_id>/test", methods=["GET", "POST"])
@login_required
def test_checkout(public_id):
    if payments.live():
        abort(404)
    order = get_order_for_user(public_id)
    if order["buyer_id"] != g.user["id"]:
        abort(404)
    if order["status"] != "pending":
        return redirect(url_for("order_page", public_id=public_id))
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    if request.method == "POST":
        mark_paid(order, payment_intent="test_" + public_id)
        return redirect(url_for("order_page", public_id=public_id, paid=1))
    return render_template("test_checkout.html", order=order, l=listing)


@app.route("/orders/<public_id>")
@login_required
def order_page(public_id):
    order = get_order_for_user(public_id)
    session_id = request.args.get("session_id")
    just_paid = request.args.get("paid") == "1"
    if order["status"] in ("pending", "expired") and payments.live() and session_id == order["payment_ref"]:
        try:
            paid, intent = payments.checkout_status(session_id)
            if paid:
                order = mark_paid(order, intent)
                just_paid = True
        except Exception:
            app.logger.exception("Could not confirm Stripe session")
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    buyer = db.one("SELECT * FROM users WHERE id=?", order["buyer_id"])
    seller = db.one("SELECT * FROM users WHERE id=?", order["seller_id"])
    files = db.all("SELECT MIN(id) AS id, file FROM tickets WHERE order_id=? AND file != '' GROUP BY file ORDER BY MIN(id)", order["id"])
    wallet_tickets = [tk for tk in db.all("SELECT * FROM tickets WHERE order_id=? ORDER BY id", order["id"])
                      if wallet.enabled() and wallet.supports(tk)]
    due = parse_sqltime(order["payout_due_at"])
    is_buyer = g.user["id"] == order["buyer_id"]
    # #13: the buyer's paid order page (with the QR code) may be kept on their phone for the door, where there's
    # often no signal. The service worker only stores pages with this header, until 3 days after the event.
    offline = is_buyer and order["status"] in PAID_STATES and not (g.get("test_lock") and not g.is_admin)
    resp = app.make_response(render_template(
        "order.html", offline=offline,
        transfer_proofs=db.all("SELECT * FROM proofs WHERE order_id=? AND kind='transfer'", order["id"]), file_gone=listing["delivery"] == "file" and not files and order["status"] in PAID_STATES,
        file_days=TICKET_FILE_DAYS, order=order, l=listing, buyer=buyer, seller=seller, payout_due=due, files=files,
        wallet_tickets=wallet_tickets, just_paid=just_paid and order["status"] in PAID_STATES,
        qr_codes=[(tk["id"], qr_svg(tk["code_text"])) for tk in db.all("SELECT * FROM tickets WHERE order_id=? ORDER BY id", order["id"])
                  if tk["code_text"] and tk["code_format"] == "QR"] if order["status"] in PAID_STATES and g.user["id"] == order["buyer_id"] else [],
        changed=(lambda e: e if e and e["changed_at"] and order["paid_at"] and e["changed_at"] >= order["paid_at"] else None)(
            db.one("SELECT * FROM events WHERE id=?", listing["event_id"]) if listing["event_id"] else None),
        venue=get_venue(listing["venue"], listing["city"]),
        directions=directions_url(get_venue(listing["venue"], listing["city"])) if get_venue(listing["venue"], listing["city"]) else None,
        colour=venue_colour(listing["venue"]), is_buyer=is_buyer, is_seller=g.user["id"] == order["seller_id"],
        can_report=order["status"] == "paid" and is_buyer,
        can_rate=is_buyer and order["status"] in PAID_STATES and not order["rating"] and can_rate_now(listing),
    ))
    if offline:
        resp.headers["X-Spare-Offline"] = (date.fromisoformat(listing["event_date"]) + timedelta(days=3)).isoformat()
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp


@app.route("/orders/<public_id>/tickets/<int:ticket_id>")
@login_required
def download_ticket(public_id, ticket_id):
    order = get_order_for_user(public_id)
    if order["status"] not in PAID_STATES or (g.get("test_lock") and not g.is_admin):
        abort(404)
    ticket = db.one("SELECT * FROM tickets WHERE id=? AND order_id=?", ticket_id, order["id"])
    if not ticket:
        abort(404)
    if not ticket["file"] or not os.path.exists(os.path.join(config.UPLOAD_DIR, ticket["file"])):
        flash(t("ticket_file_gone", days=TICKET_FILE_DAYS), "error")  # #15: deleted after the event
        return redirect(url_for("order_page", public_id=public_id))
    ext = ticket["file"].rsplit(".", 1)[-1]
    return send_file(os.path.join(config.UPLOAD_DIR, ticket["file"]), mimetype=FILE_TYPES[ext][1],
                     as_attachment=True, download_name="spare-ticket-%s-%s.%s" % (order["public_id"], ticket_id, ext))


@app.route("/orders/<public_id>/tickets/<int:ticket_id>/wallet")
@login_required
def wallet_pass(public_id, ticket_id):
    order = get_order_for_user(public_id)
    if order["status"] not in PAID_STATES or order["buyer_id"] != g.user["id"] or not wallet.enabled():
        abort(404)
    tickets = db.all("SELECT * FROM tickets WHERE order_id=? ORDER BY id", order["id"])
    ticket = next((tk for tk in tickets if tk["id"] == ticket_id), None)
    if not ticket or not wallet.supports(ticket):
        abort(404)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    seller = db.one("SELECT first_name FROM users WHERE id=?", order["seller_id"])
    labels = {"event": t("event"), "venue": t("venue"), "date": t("date"), "ticket": t("ticket"), "order": t("order"),
              "date_value": fmt_date(listing["event_date"]), "seller": t("seller_label"), "help_label": t("footer_contact"),
              "help": t("wallet_help", email=config.SUPPORT_EMAIL)}
    data = wallet.pass_json(ticket, listing, order, tickets.index(ticket) + 1, len(tickets), venue_colour(listing["venue"]),
                            seller["first_name"], labels)
    try:
        pkpass = wallet.build(data)
    except Exception:
        app.logger.exception("Could not build Wallet pass")
        abort(404)
    resp = app.response_class(pkpass, mimetype="application/vnd.apple.pkpass")
    resp.headers["Content-Disposition"] = 'attachment; filename="spare-%s.pkpass"' % ticket_id
    return resp


@app.route("/orders/<public_id>/calendar.ics")
@login_required
def order_calendar(public_id):
    order = get_order_for_user(public_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    start = datetime.fromisoformat("%sT%s" % (listing["event_date"], listing["start_time"] or "22:00"))
    end = start + timedelta(hours=5)
    fmt = lambda d: d.strftime("%Y%m%dT%H%M%S")  # noqa: E731
    esc = lambda v: str(v).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")  # noqa: E731
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Spare//EN", "BEGIN:VEVENT",
        "UID:%s@sparetickets.eu" % order["public_id"], "DTSTAMP:%sZ" % fmt(utcnow()),
        "DTSTART;TZID=Europe/Amsterdam:%s" % fmt(start), "DTEND;TZID=Europe/Amsterdam:%s" % fmt(end),
        "SUMMARY:%s" % esc(listing["event_name"] or listing["venue"]),
        "LOCATION:%s" % esc("%s, %s" % (listing["venue"], listing["city"])),
        "URL:%s" % (base_url() + url_for("order_page", public_id=public_id)),
        "END:VEVENT", "END:VCALENDAR",
    ]
    resp = app.response_class("\r\n".join(lines) + "\r\n", mimetype="text/calendar")
    resp.headers["Content-Disposition"] = 'attachment; filename="spare-%s.ics"' % public_id
    return resp


DISPUTE_REPLY_HOURS = 24  # the seller's time to answer a problem report in the order chat


def open_dispute(order, reason):
    """Holds the payout and tells the admins and the seller (#12), who can answer in the order chat."""
    if not db.count("UPDATE orders SET status='disputed', dispute_reason=? WHERE id=? AND status='paid'", reason, order["id"]):
        return False
    notify.notify_admins("Spare: problem reported (%s)" % order["public_id"],
                         "%s\n\n%s" % (reason, base_url() + url_for("admin")))
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    add_message(get_thread(listing, order["buyer_id"]), None, "system", "sys_disputed")
    tell(order["seller_id"], t("mail_dispute_seller_subject", title=listing_title(listing)),
         t("mail_dispute_seller_body", title=listing_title(listing), hours=DISPUTE_REPLY_HOURS), url_for("order_page", public_id=order["public_id"]))
    return True


@app.route("/orders/<public_id>/report", methods=["POST"])
@login_required
def report_problem(public_id):
    order = get_order_for_user(public_id)
    if order["buyer_id"] != g.user["id"] or order["status"] != "paid":
        abort(400)  # #30: possible until the money has actually gone to the seller, even after a 👍
    reason = request.form.get("reason", "").strip()[:1000]
    if len(reason) < 10:
        flash(t("err_report_short"), "error")
        return redirect(url_for("order_page", public_id=public_id))
    open_dispute(order, reason)
    flash(t("report_sent"), "ok")
    return redirect(url_for("order_page", public_id=public_id))


@app.route("/orders/<public_id>/rate", methods=["POST"])
@login_required
def rate_order(public_id):
    order = get_order_for_user(public_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    value = request.form.get("rating")
    if (order["buyer_id"] != g.user["id"] or value not in ("up", "down") or order["rating"]
            or order["status"] not in PAID_STATES or not can_rate_now(listing)):
        abort(400)  # #30: only once the doors have been open a while, so nobody confirms before they're inside
    db.run("UPDATE orders SET rating=?, rated_at=? WHERE id=?", value, sqltime(utcnow()), order["id"])
    if value == "up" and order["status"] == "paid" and not is_new_seller(order["seller_id"]):
        # The buyer confirms the ticket worked: no need to keep the seller waiting for the full 48 hours. The hourly
        # task pays it, no sooner than 3 hours after the doors, and until then the buyer can still report a problem.
        # (New sellers are always paid 72 hours after the night, #28.)
        sooner = max(utcnow(), doors_open_at(listing) + timedelta(hours=PAYOUT_AFTER_DOORS_HOURS))
        db.run("UPDATE orders SET payout_due_at=? WHERE id=? AND payout_due_at > ?", sqltime(sooner), order["id"], sqltime(sooner))
    if value == "down" and order["status"] == "paid":
        flash(t("rated_down"), "error")
        return redirect(url_for("order_page", public_id=public_id, report=1) + "#report")
    flash(t("rated_up") if value == "up" else t("rated_down_late"), "ok")
    return redirect(url_for("order_page", public_id=public_id))


@app.route("/orders/<public_id>/transferred", methods=["POST"])
@login_required
def mark_transferred(public_id):
    """The seller has sent the ticket with the organiser's app."""
    order = get_order_for_user(public_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    if order["seller_id"] != g.user["id"] or listing["delivery"] != "transfer" or order["status"] not in PAID_STATES:
        abort(400)
    if not order["transferred_at"]:
        # #31: proof of the transfer, a screenshot of the app's confirmation (the buyer and Spare's team can see it)
        errors = {}
        proof = read_proof(request.files.get("transfer_proof"), errors, "transfer_proof") if config.TICKET_CHECKS else None
        if errors:
            flash(errors["transfer_proof"], "error")
            return redirect(url_for("order_page", public_id=public_id))
        if proof:
            picture = ticketcodes.phash(*proof)
            reused = picture and any(ticketcodes.phash_distance(picture, p["phash"]) <= ticketcodes.PHASH_MATCH for p in db.all(
                "SELECT phash FROM proofs WHERE kind='transfer' AND phash IS NOT NULL AND order_id != ?", order["id"]))
            if reused:
                flash(t("err_transfer_proof_reused"), "error")
                return redirect(url_for("order_page", public_id=public_id))
            save_proof(proof, "transfer", order_id=order["id"], phash=picture)
        db.run("UPDATE orders SET transferred_at=? WHERE id=?", sqltime(utcnow()), order["id"])
        th = get_thread(listing, order["buyer_id"])
        add_message(th, None, "system", "sys_transferred")
        buyer = db.one("SELECT * FROM users WHERE id=?", order["buyer_id"])
        app_label = app_name(listing["transfer_app"])
        path = url_for("order_page", public_id=public_id)
        notify.send_email(buyer["email"], t("mail_transfer_sent_subject", title=listing_title(listing)),
                          t("mail_transfer_sent_body", name=buyer["first_name"], title=listing_title(listing),
                            email=order["transfer_email"], app=app_label, url=base_url() + path))
        push.send(buyer["id"], t("mail_transfer_sent_subject", title=listing_title(listing)),
                  t("buyer_transfer_sent", email=order["transfer_email"], app=app_label), path, tag="transfer-" + public_id)
    flash(t("seller_transfer_done", app=app_name(listing["transfer_app"])), "ok")
    return redirect(url_for("order_page", public_id=public_id))


@app.route("/orders/<public_id>/received", methods=["POST"])
@login_required
def mark_received(public_id):
    """The buyer confirms the transferred ticket is in their app."""
    order = get_order_for_user(public_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    if order["buyer_id"] != g.user["id"] or listing["delivery"] != "transfer" or order["status"] not in PAID_STATES:
        abort(400)
    if not order["received_at"]:
        db.run("UPDATE orders SET received_at=?, transferred_at=COALESCE(transferred_at, ?) WHERE id=?",
               sqltime(utcnow()), sqltime(utcnow()), order["id"])
        add_message(get_thread(listing, order["buyer_id"]), None, "system", "sys_received")
    flash(t("buyer_received_done"), "ok")
    return redirect(url_for("order_page", public_id=public_id))


@app.route("/orders/<public_id>/not-received", methods=["POST"])
@login_required
def mark_not_received(public_id):
    """#31: the app ticket never arrived: a dispute straight away, the seller isn't paid."""
    order = get_order_for_user(public_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    if order["buyer_id"] != g.user["id"] or listing["delivery"] != "transfer" or order["status"] != "paid" or order["received_at"]:
        abort(400)
    open_dispute(order, "The ticket never arrived in %s." % app_name(listing["transfer_app"]))
    flash(t("report_sent"), "ok")
    return redirect(url_for("order_page", public_id=public_id))


@app.route("/orders/<public_id>/proofs/<int:proof_id>")
@login_required
def order_proof(public_id, proof_id):
    """Evidence attached to an order, for its buyer, its seller and the admins."""
    order = get_order_for_user(public_id)
    p = db.one("SELECT * FROM proofs WHERE id=? AND order_id=?", proof_id, order["id"])
    if not p:
        abort(404)
    ext = p["file"].rsplit(".", 1)[-1]
    return send_file(os.path.join(config.UPLOAD_DIR, "proofs", p["file"]), mimetype=FILE_TYPES[ext][1])


@app.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    if not payments.live():
        abort(404)
    try:
        event = payments.parse_webhook(request.get_data(), request.headers.get("Stripe-Signature", ""))
    except Exception:
        abort(400)
    obj = event["data"]["object"]
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()  # stripe-python 8+ objects aren't dicts any more: .get() raises AttributeError
    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        order = db.one("SELECT * FROM orders WHERE payment_ref=?", obj["id"])
        if order and obj.get("payment_status") == "paid":
            mark_paid(order, obj.get("payment_intent"))
    elif event["type"] == "checkout.session.expired":
        db.run("UPDATE orders SET status='expired' WHERE payment_ref=? AND status='pending'", obj["id"])
        release_unpaid()
    elif event["type"] in ("charge.dispute.created", "charge.dispute.closed", "charge.refunded"):
        order = db.one("SELECT * FROM orders WHERE payment_intent=?", obj.get("payment_intent")) if obj.get("payment_intent") else None
        if order:
            handle_stripe_money_event(event["type"], order, obj)
    return "", 200


def take_back_payout(order):
    """#27: reverse a seller's payout. Returns the reversal id, or None (and tells the admins) if Stripe refused."""
    try:
        ref = payments.reverse_transfer(order)
    except Exception as exc:
        app.logger.exception("Transfer reversal failed")
        notify.notify_admins("Spare: couldn't take back a payout (%s)" % order["public_id"],
                             "Stripe refused to reverse the seller's payout for order %s: %s\n\nCheck it in the Stripe dashboard." % (order["public_id"], exc))
        return None
    db.run("UPDATE orders SET reversal_ref=? WHERE id=?", ref, order["id"])
    return ref


def handle_stripe_money_event(kind, order, obj):
    """#27: chargebacks and refunds made outside Spare. Spare is liable for losses on its sellers' accounts, so a
    chargeback holds the payout, or takes it back if it was already sent."""
    admin_link = base_url() + url_for("admin") + "#chargebacks"
    if kind == "charge.dispute.created":
        db.run("UPDATE orders SET chargeback_at=? WHERE id=?", sqltime(utcnow()), order["id"])
        if order["status"] == "paid":
            db.run("UPDATE orders SET status='disputed', dispute_reason=? WHERE id=?", "Chargeback by the buyer's bank", order["id"])
            what = "The payout is on hold."
        elif order["status"] in ("released", "releasing"):
            what = "The seller was already paid; " + ("the payout was taken back." if take_back_payout(order) else "taking it back FAILED.")
        else:
            what = "Order status: %s." % order["status"]
        notify.notify_admins("Spare: chargeback on order %s" % order["public_id"],
                             "The buyer's bank opened a dispute (%s, %s). %s\n\nRespond with evidence in the Stripe dashboard (Payments → Disputes).\n%s"
                             % (obj.get("reason", "?"), money(obj.get("amount") or 0), what, admin_link))
    elif kind == "charge.dispute.closed":
        db.run("UPDATE orders SET chargeback_status=? WHERE id=?", obj.get("status"), order["id"])
        notify.notify_admins("Spare: chargeback closed (%s): %s" % (order["public_id"], obj.get("status")),
                             "The bank's dispute on order %s closed with status '%s'.\n%s" % (order["public_id"], obj.get("status"), admin_link))
    elif kind == "charge.refunded" and order["status"] not in ("refunded", "refunding"):
        # Refunded in the Stripe dashboard rather than on Spare: bring Spare in line.
        was = order["status"]
        db.run("UPDATE orders SET status='refunded' WHERE id=?", order["id"])
        db.run("UPDATE tickets SET status='void' WHERE order_id=?", order["id"])
        note = ""
        if was in ("released", "releasing"):
            note = " The seller was already paid; " + ("the payout was taken back." if take_back_payout(order) else "taking it back FAILED.")
        notify.notify_admins("Spare: order %s refunded in Stripe" % order["public_id"],
                             "Order %s was refunded from the Stripe dashboard; Spare marked it refunded.%s\n%s" % (order["public_id"], note, admin_link))


# --------------------------------------------------------------------------
# Messages, quick questions and offers
# --------------------------------------------------------------------------

QUICK_QUESTIONS = ["q_name", "q_valid", "q_type", "q_delivery", "q_entry"]
FLAG_LIMIT = 4  # messages with contact details per 24 hours before sending is paused


def get_thread(listing, buyer_id, create=True):
    th = db.one("SELECT * FROM threads WHERE listing_id=? AND buyer_id=?", listing["id"], buyer_id)
    if not th and create:
        db.run("INSERT OR IGNORE INTO threads (listing_id, buyer_id, seller_id) VALUES (?, ?, ?)",
               listing["id"], buyer_id, listing["seller_id"])
        th = db.one("SELECT * FROM threads WHERE listing_id=? AND buyer_id=?", listing["id"], buyer_id)
    return th


def thread_for_user(thread_id):
    th = db.one("SELECT * FROM threads WHERE id=?", thread_id)
    if not th or (g.user["id"] not in (th["buyer_id"], th["seller_id"]) and not g.is_admin):
        abort(404)
    return th


def chat_open(th):
    """Free chat only after the buyer has paid: before that, only questions and offers."""
    if not th["order_id"]:
        return False
    order = db.one("SELECT status FROM orders WHERE id=?", th["order_id"])
    return bool(order) and order["status"] in PAID_STATES + ("refunded",)


def thread_url(th):
    return url_for("thread_page", thread_id=th["id"])


def add_message(th, sender_id, kind, body, raw=None, flags=None, offer_id=None, notify_other=True):
    blocked = bool(flags and "blocked" in flags)  # kept for the admin page, never shown to the other person
    db.run("""INSERT INTO messages (thread_id, sender_id, kind, body, raw_body, flags, offer_id, read_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", th["id"], sender_id, kind, body, raw, flags, offer_id,
           sqltime(utcnow()) if blocked else None)
    if blocked:
        return
    db.run("UPDATE threads SET updated_at=? WHERE id=?", sqltime(utcnow()), th["id"])
    if not notify_other or sender_id is None:
        return
    recipient_id = th["seller_id"] if sender_id == th["buyer_id"] else th["buyer_id"]
    sender = db.one("SELECT first_name FROM users WHERE id=?", sender_id)
    recipient = db.one("SELECT email, first_name FROM users WHERE id=?", recipient_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", th["listing_id"])
    preview = message_preview(kind, body, offer_id)
    pushed = push.send(recipient_id, t("push_message_title", name=sender["first_name"]), preview,
                       thread_url(th), tag="thread-%s" % th["id"])
    # Questions and offers always get an email; chat messages only when there's no phone to push to.
    if kind != "text" or not pushed:
        notify.send_email(recipient["email"], t("mail_message_subject", name=sender["first_name"], title=listing_title(listing)),
                          t("mail_message_body", name=recipient["first_name"], sender=sender["first_name"],
                            title=listing_title(listing), preview=preview, url=base_url() + thread_url(th)))


def message_preview(kind, body, offer_id=None):
    if kind == "question":
        return t(body)
    if kind in ("offer", "offer_accepted", "offer_declined") and offer_id:
        offer = db.one("SELECT * FROM offers WHERE id=?", offer_id)
        key = {"offer": "msg_offer", "offer_accepted": "msg_offer_accepted", "offer_declined": "msg_offer_declined"}[kind]
        return t(key, amount=money(offer["amount_cents"]), n=offer["quantity"])
    return body if kind != "system" else t(body)


def unread_count(user_id):
    return db.one(
        """SELECT COUNT(*) AS n FROM messages m JOIN threads th ON th.id = m.thread_id
           WHERE (th.buyer_id = ? OR th.seller_id = ?) AND m.read_at IS NULL
             AND m.sender_id IS NOT NULL AND m.sender_id != ?""", user_id, user_id, user_id)["n"]


def recent_flags(user_id):
    return db.one("SELECT COUNT(*) AS n FROM messages WHERE sender_id=? AND flags IS NOT NULL AND flags != 'rate_early' AND created_at > ?",
                  user_id, sqltime(utcnow() - timedelta(hours=24)))["n"]


@app.route("/messages")
@login_required
def inbox():
    uid = g.user["id"]
    threads = db.all(
        """SELECT th.*, l.venue, l.event_name, l.event_date, l.poster_file,
                  b.first_name AS buyer_name, s.first_name AS seller_name, b.avatar AS buyer_avatar, s.avatar AS seller_avatar,
                  (SELECT COUNT(*) FROM messages m WHERE m.thread_id = th.id AND m.read_at IS NULL
                     AND m.sender_id IS NOT NULL AND m.sender_id != ?) AS unread,
                  (SELECT m.id FROM messages m WHERE m.thread_id = th.id
                     AND (m.flags IS NULL OR m.flags NOT LIKE '%blocked%' OR m.sender_id = ?) ORDER BY m.id DESC LIMIT 1) AS last_id
           FROM threads th JOIN listings l ON l.id = th.listing_id
           JOIN users b ON b.id = th.buyer_id JOIN users s ON s.id = th.seller_id
           WHERE th.buyer_id = ? OR th.seller_id = ? ORDER BY th.updated_at DESC""", uid, uid, uid, uid)
    previews = {}
    for th in threads:
        last = db.one("SELECT * FROM messages WHERE id=?", th["last_id"]) if th["last_id"] else None
        previews[th["id"]] = message_preview(last["kind"], last["body"], last["offer_id"]) if last else ""
    return render_template("inbox.html", threads=threads, previews=previews)


@app.route("/threads/<int:thread_id>")
@login_required
def thread_page(thread_id):
    th = thread_for_user(thread_id)
    uid = g.user["id"]
    db.run("""UPDATE messages SET read_at=? WHERE thread_id=? AND read_at IS NULL
              AND sender_id IS NOT NULL AND sender_id != ?""", sqltime(utcnow()), th["id"], uid)
    l = listing_with_seller(th["listing_id"])
    msgs = db.all("SELECT * FROM messages WHERE thread_id=? ORDER BY id", th["id"])
    offers = {o["id"]: o for o in db.all("SELECT * FROM offers WHERE thread_id=?", th["id"])}
    names = {th["buyer_id"]: db.one("SELECT first_name FROM users WHERE id=?", th["buyer_id"])["first_name"],
             th["seller_id"]: l["seller_name"]}
    is_buyer = uid == th["buyer_id"]
    order = db.one("SELECT * FROM orders WHERE id=?", th["order_id"]) if th["order_id"] else None
    return render_template(
        "thread.html", **thread_context(th, l, msgs, offers, names, is_buyer), order=order,
        open=chat_open(th),
        flagged=any(m["flags"] for m in msgs), questions=QUICK_QUESTIONS, colour=venue_colour(l["venue"]),
        other_name=names[th["seller_id"] if is_buyer else th["buyer_id"]], paused=recent_flags(uid) >= FLAG_LIMIT,
    )


def wants_json():
    return request.headers.get("X-Requested-With") == "fetch"


def chat_error(th, message):
    if wants_json():
        return {"error": message}, 400
    flash(message, "error")
    return redirect(thread_url(th))


def thread_context(th, l, msgs, offers, names, is_buyer):
    return {"th": th, "l": l, "msgs": msgs, "offers": offers, "names": names, "is_buyer": is_buyer,
            "can_buy": l["status"] == "active" and l["available"] > 0 and l["event_date"] >= today().isoformat()}


def render_new_messages(th, after_id):
    """HTML for messages after `after_id` (live chat updates), and the newest id."""
    l = listing_with_seller(th["listing_id"])
    msgs = db.all("SELECT * FROM messages WHERE thread_id=? AND id > ? ORDER BY id", th["id"], after_id)
    offers = {o["id"]: o for o in db.all("SELECT * FROM offers WHERE thread_id=?", th["id"])}
    names = {th["buyer_id"]: db.one("SELECT first_name FROM users WHERE id=?", th["buyer_id"])["first_name"],
             th["seller_id"]: l["seller_name"]}
    ctx = thread_context(th, l, msgs, offers, names, g.user["id"] == th["buyer_id"])
    html_out = "".join(render_template("_msg.html", msg=msg, **ctx) for msg in msgs)
    return html_out, (msgs[-1]["id"] if msgs else after_id)


@app.route("/threads/<int:thread_id>/updates")
@login_required
def thread_updates(thread_id):
    th = thread_for_user(thread_id)
    try:
        after = int(request.args.get("after", "0"))
    except ValueError:
        after = 0
    db.run("""UPDATE messages SET read_at=? WHERE thread_id=? AND id > ? AND read_at IS NULL
              AND sender_id IS NOT NULL AND sender_id != ?""", sqltime(utcnow()), th["id"], after, g.user["id"])
    html_out, last = render_new_messages(th, after)
    return {"html": html_out, "last": last}


@app.route("/threads/<int:thread_id>/send", methods=["POST"])
@login_required
def send_message(thread_id):
    th = thread_for_user(thread_id)
    uid = g.user["id"]
    if uid == th["buyer_id"] and not chat_open(th):
        return chat_error(th, t("err_chat_closed"))
    if recent_flags(uid) >= FLAG_LIMIT:
        return chat_error(th, t("err_chat_paused"))
    text = request.form.get("body", "").strip()[:1000]
    if not text:
        return redirect(thread_url(th))
    if rate_limited("msg", 60, 60, uid):
        return chat_error(th, t("err_too_many_tries"))
    clean, flags = moderation.check(text)
    if "off_platform" in flags:
        flags.append("blocked")  # deals and payments outside Spare aren't delivered
    add_message(th, uid, "text", clean, raw=text if flags else None, flags=",".join(flags) or None)
    shown = [f for f in flags if f != "rate_early"]  # rate_early is only for the admins (#30)
    warning = (t("msg_blocked") if "blocked" in flags else t("msg_masked")) if shown else None
    if wants_json():
        try:
            after = int(request.form.get("after", "0"))
        except ValueError:
            after = 0
        html_out, last = render_new_messages(th, after)
        return {"html": html_out, "last": last, "warning": warning}
    if warning:
        flash(warning, "error")
    return redirect(thread_url(th) + "#end")


@app.route("/t/<int:listing_id>/ask", methods=["POST"])
@login_required
def ask_question(listing_id):
    l = listing_with_seller(listing_id)
    key = request.form.get("question")
    if not l or l["seller_id"] == g.user["id"] or key not in QUICK_QUESTIONS:
        abort(400)
    th = get_thread(l, g.user["id"])
    already = db.one("SELECT 1 FROM messages WHERE thread_id=? AND kind='question' AND body=?", th["id"], key)
    if not already:
        add_message(th, g.user["id"], "question", key)
    return redirect(thread_url(th) + "#end")


@app.route("/t/<int:listing_id>/offer", methods=["POST"])
@login_required
def make_offer(listing_id):
    release_unpaid()
    l = listing_with_seller(listing_id)
    if not l or l["seller_id"] == g.user["id"]:
        abort(400)
    back = url_for("listing_page", listing_id=listing_id)
    amount = parse_money(request.form.get("amount"))
    try:
        qty = int(request.form.get("qty", "1"))
    except ValueError:
        qty = 1
    if l["status"] != "active" or not l["available"] or l["event_date"] < today().isoformat():
        flash(t("err_unavailable"), "error")
        return redirect(back)
    if rate_limited("offer", 20, 60, g.user["id"]):
        flash(t("err_too_many_tries"), "error")
        return redirect(back)
    qty = l["available"] if l["sold_together"] else max(1, min(qty, l["available"]))
    if amount is None or amount < 100:
        flash(t("err_offer_amount"), "error")
        return redirect(back)
    if amount >= l["price_cents"]:
        flash(t("err_offer_too_high", price=money(l["price_cents"])), "error")
        return redirect(back)
    th = get_thread(l, g.user["id"])
    db.run("UPDATE offers SET status='withdrawn', responded_at=? WHERE thread_id=? AND status='pending'",
           sqltime(utcnow()), th["id"])
    offer_id = db.run("INSERT INTO offers (thread_id, listing_id, buyer_id, amount_cents, quantity) VALUES (?, ?, ?, ?, ?)",
                      th["id"], listing_id, g.user["id"], amount, qty)
    add_message(th, g.user["id"], "offer", "", offer_id=offer_id)
    flash(t("offer_sent"), "ok")
    return redirect(thread_url(th) + "#end")


@app.route("/offers/<int:offer_id>/<action>", methods=["POST"])
@login_required
def respond_offer(offer_id, action):
    offer = db.one("SELECT * FROM offers WHERE id=?", offer_id)
    if not offer:
        abort(404)
    th = thread_for_user(offer["thread_id"])
    uid = g.user["id"]
    if offer["status"] != "pending":
        flash(t("err_offer_closed"), "error")
    elif action in ("accept", "decline") and uid == th["seller_id"]:
        status = "accepted" if action == "accept" else "declined"
        db.run("UPDATE offers SET status=?, responded_at=? WHERE id=?", status, sqltime(utcnow()), offer_id)
        add_message(th, uid, "offer_" + status, "", offer_id=offer_id)
    elif action == "withdraw" and uid == th["buyer_id"]:
        db.run("UPDATE offers SET status='withdrawn', responded_at=? WHERE id=?", sqltime(utcnow()), offer_id)
    else:
        abort(400)
    return redirect(thread_url(th) + "#end")


@app.route("/orders/<public_id>/chat")
@login_required
def order_chat(public_id):
    order = get_order_for_user(public_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    th = get_thread(listing, order["buyer_id"])
    if not th["order_id"] and order["status"] in PAID_STATES:
        db.run("UPDATE threads SET order_id=? WHERE id=?", order["id"], th["id"])
    return redirect(thread_url(th))


# --------------------------------------------------------------------------
# Selling
# --------------------------------------------------------------------------

def read_proof(upload, errors, field, required=True):
    """A private evidence file (screenshot or PDF): returns (data, ext) or None, with the same checks as tickets."""
    if not upload or not upload.filename:
        if required:
            errors[field] = t("err_" + field)
        return None
    ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    ext = "jpg" if ext == "jpeg" else ext
    data = upload.read()
    if ext not in FILE_TYPES or not data.startswith(FILE_TYPES[ext][0]):
        errors[field] = t("err_ticket_type")
    elif len(data) > config.MAX_UPLOAD_BYTES:
        errors[field] = t("err_ticket_size")
    elif ticketcodes.check(data, ext):
        errors[field] = t("err_ticket_" + ticketcodes.check(data, ext), pages=ticketcodes.MAX_PDF_PAGES)
    else:
        return data, ext
    return None


def save_proof(proof, kind, listing_id=None, order_id=None, phash=None):
    data, ext = proof
    folder = os.path.join(config.UPLOAD_DIR, "proofs")
    os.makedirs(folder, exist_ok=True)
    name = "%s.%s" % (secrets.token_hex(16), ext)
    with open(os.path.join(folder, name), "wb") as fh:
        fh.write(data)
    return db.run("INSERT INTO proofs (listing_id, order_id, user_id, kind, file, phash) VALUES (?, ?, ?, ?, ?, ?)",
                  listing_id, order_id, g.user["id"], kind, name, phash)


def read_ticket_files(uploads, errors):
    """Validates the uploaded ticket files. Returns a list of (data, ext, [(code hash, text, format)]) or None."""
    files = [u for u in uploads if u and u.filename]
    if not files:
        errors["ticket"] = t("err_ticket_missing")
        return None
    if len(files) > config.MAX_TICKETS_PER_LISTING:
        errors["ticket"] = t("err_too_many", n=config.MAX_TICKETS_PER_LISTING)
        return None
    result = []
    for upload in files:
        ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
        data = upload.read()
        if ext not in FILE_TYPES or not data.startswith(FILE_TYPES[ext][0]):
            errors["ticket"] = t("err_ticket_type")
            return None
        if len(data) > config.MAX_UPLOAD_BYTES:
            errors["ticket"] = t("err_ticket_size")
            return None
        problem = ticketcodes.check(data, "jpg" if ext == "jpeg" else ext)
        if problem:  # #14: refuse files that would take more memory to read than the server has
            errors["ticket"] = t("err_ticket_" + problem, pages=ticketcodes.MAX_PDF_PAGES)
            return None
        ext = "jpg" if ext == "jpeg" else ext
        codes = [(ticketcodes.code_hash(text), text, kind) for text, kind in ticketcodes.codes(data, ext)]
        result.append((data, ext, codes))
    return result


NAME_ANSWERS = ("no", "changeable", "unsure")  # "fixed" (a name that can't be changed) is refused


@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    if g.get("test_lock") and not g.is_admin:
        flash(t("err_test_lock"), "error")
        return redirect(url_for("index"))
    if not g.user["phone_verified_at"]:
        return redirect(url_for("phone", next=url_for("sell")))

    form = request.form
    errors = {}
    if request.method == "POST":
        if not g.user["adult_confirmed_at"]:
            if form.get("adult") != "yes":
                errors["adult"] = t("err_adult")
            else:
                db.run("UPDATE users SET adult_confirmed_at=? WHERE id=?", sqltime(utcnow()), g.user["id"])
        chosen = None
        if form.get("event_id") and form.get("event_id") != "new":
            chosen = db.one("SELECT * FROM events WHERE id=? AND status != 'hidden' AND cancelled_at IS NULL", form.get("event_id"))
            if not chosen or chosen["event_date"] < today().isoformat():
                errors["event"] = t("err_event_gone")
                chosen = None
        if chosen:
            venue, event_name, city, category = chosen["venue"], chosen["name"], chosen["city"], chosen["category"]
            start_time = chosen["door_time"] or ""
            event_date = date.fromisoformat(chosen["event_date"])
        else:
            venue = form.get("venue", "")
            if venue == "__other":
                venue = form.get("other_venue", "").strip()[:60]
            if not venue:
                errors["venue"] = t("err_venue")
            event_name = form.get("event_name", "").strip()[:80] or venue

            category = form.get("category", "club")
            if category not in config.CATEGORIES:
                errors["category"] = t("err_category")
            if venue in config.VENUES:
                city = config.DEFAULT_CITY
            else:
                city = " ".join(form.get("city", "").split())[:60]
                if not city:
                    errors["city"] = t("err_city")
            start_time = form.get("start_time", "").strip()
            if start_time and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", start_time):
                errors["start_time"] = t("err_start_time")

            event_date = None
            try:
                event_date = date.fromisoformat(form.get("event_date", ""))
                if event_date < today() or event_date > today() + timedelta(days=365):
                    errors["event_date"] = t("err_date_range")
            except ValueError:
                errors["event_date"] = t("err_date")
            if event_date and venue:
                existing = find_event(event_date.isoformat(), venue, event_name)
                if existing and existing["cancelled_at"]:
                    errors["event"] = t("err_event_gone")

        face = parse_money(form.get("face_value"))
        if face is None or face < 100 or face > config.MAX_FACE_VALUE_CENTS:
            errors["face_value"] = t("err_face_value")
            face = None

        # #25: the price cap only means something if the face value is real. Known official price: enforce it.
        # Unknown: the seller shows their original order confirmation and an admin checks it before the listing goes live.
        official = chosen["face_value_max_cents"] if chosen else None
        face_proof = None
        if face is not None and official and face > official:
            errors["face_value"] = t("err_face_official", max=money(official))
        elif not official and config.FACE_PROOF_REQUIRED:
            face_proof = read_proof(request.files.get("face_proof"), errors, "face_proof")

        price = parse_money(form.get("price"))
        if price is None or price < 100:
            errors["price"] = t("err_price")
        elif face is not None and price > max_price(face):
            errors["price"] = t("err_price_cap", max=money(max_price(face)))

        # #28: the seller promises the ticket isn't for sale anywhere else.
        if form.get("no_double_sell") != "yes":
            errors["no_double_sell"] = t("err_no_double_sell")

        # #21: names on tickets are why buyers get turned away at the door. A name that can't be changed: don't list.
        name_on_ticket = form.get("name_on_ticket")
        if name_on_ticket == "fixed":
            errors["name_on_ticket"] = t("err_name_fixed")
        elif name_on_ticket not in NAME_ANSWERS:
            errors["name_on_ticket"] = t("err_name_question")

        delivery = "transfer" if form.get("delivery") == "transfer" else "file"
        transfer_app = transfer_qty = None
        if delivery == "transfer":
            # The ticket lives in an app with a changing QR code: nothing to upload, it's sent with the app's transfer button.
            files = []
            transfer_app = form.get("transfer_app")
            if transfer_app not in config.TRANSFER_APPS:
                errors["transfer_app"] = t("err_transfer_app")
            try:
                transfer_qty = int(form.get("transfer_qty", "1"))
            except ValueError:
                transfer_qty = 0
            if not 1 <= transfer_qty <= config.MAX_TICKETS_PER_LISTING:
                errors["transfer_qty"] = t("err_too_many", n=config.MAX_TICKETS_PER_LISTING)
        else:
            files = read_ticket_files(request.files.getlist("ticket"), errors)

        # #29: app tickets have nothing to check automatically, so the seller shows them: a screenshot from the
        # ticket app (event, date, ticket holder) and the order confirmation. Kept private for Spare's team.
        app_shot = app_order = None
        if delivery == "transfer" and config.TICKET_CHECKS:
            app_shot = read_proof(request.files.get("app_screenshot"), errors, "app_screenshot")
            app_order = read_proof(request.files.get("app_order"), errors, "app_order")
            if app_shot and similar_picture_listed(ticketcodes.phash(*app_shot), []):
                errors["app_screenshot"] = t("err_ticket_duplicate")

        poster_upload = request.files.get("poster")
        poster_data = poster_ext = None
        if poster_upload and poster_upload.filename:
            poster_ext = poster_upload.filename.rsplit(".", 1)[-1].lower() if "." in poster_upload.filename else ""
            poster_data = poster_upload.read()
            if poster_ext not in POSTER_TYPES or not poster_data.startswith(POSTER_TYPES[poster_ext]):
                errors["poster"] = t("err_poster_type")
            elif len(poster_data) > config.MAX_UPLOAD_BYTES:
                errors["poster"] = t("err_ticket_size")

        if files and not errors:
            # Duplicate checks: the exact same file, or the same QR/barcode in a different file.
            file_hashes = [hashlib.sha256(data).hexdigest() for data, _, _ in files]
            all_codes = [c[0] for _, _, codes in files for c in codes]
            if len(set(file_hashes)) < len(file_hashes) or len(set(all_codes)) < len(all_codes):
                errors["ticket"] = t("err_ticket_twice")
            elif any(already_listed("file_hash", h) for h in file_hashes):
                errors["ticket"] = t("err_ticket_duplicate")
            elif any(already_listed("code_hash", c) for c in all_codes):
                errors["ticket"] = t("err_code_duplicate")
            elif sum(max(1, len(codes)) for _, _, codes in files) > config.MAX_TICKETS_PER_LISTING:
                errors["ticket"] = t("err_too_many", n=config.MAX_TICKETS_PER_LISTING)
            elif config.TICKET_CHECKS and any(similar_picture_listed(ticketcodes.phash(data, ext), codes) for data, ext, codes in files):
                errors["ticket"] = t("err_ticket_duplicate")  # #29: a re-saved, resized or cropped copy of a listed ticket
            elif config.TICKET_CHECKS and any(not codes for _, _, codes in files) and form.get("code_review") != "yes":
                # #29: without a readable code, the duplicate check only sees the file. Ask for a clearer file,
                # or let an admin look at it before it goes on sale.
                errors["ticket"] = t("err_no_code")
                errors["code_review_offer"] = True

        new_seller = is_new_seller(g.user["id"])
        if not errors and new_seller:
            # #28: new sellers have at most 2 tickets on sale at once (waiting for payout setup or a check counts too).
            on_sale = db.one("""SELECT COUNT(*) AS n FROM tickets t JOIN listings l ON l.id = t.listing_id
                                WHERE l.seller_id=? AND l.status IN ('active', 'draft', 'review') AND t.status='available'""",
                             g.user["id"])["n"]
            adding = transfer_qty if delivery == "transfer" else sum(max(1, len(codes)) for _, _, codes in (files or []))
            if on_sale + (adding or 0) > config.NEW_SELLER_MAX_TICKETS:
                errors["ticket"] = t("err_new_seller_max", n=config.NEW_SELLER_MAX_TICKETS, sales=config.NEW_SELLER_SALES)

        if not errors:
            poster_file = None
            if poster_data:
                poster_file = "%s.%s" % (secrets.token_hex(16), "jpg" if poster_ext == "jpeg" else poster_ext)
                os.makedirs(os.path.join(config.UPLOAD_DIR, "posters"), exist_ok=True)
                with open(os.path.join(config.UPLOAD_DIR, "posters", poster_file), "wb") as fh:
                    fh.write(poster_data)
            # One file holding several tickets can only be sold as a set.
            sold_together = 1 if any(len(codes) > 1 for _, _, codes in files) else 0
            known = get_venue(venue, city)
            venue = known["name"] if known else venue  # reuse the existing spelling
            get_venue(venue, city, create=True)
            if not chosen:
                # A seller-added event: visible straight away, flagged for an admin to check.
                chosen = find_event(event_date.isoformat(), venue, event_name)
                if not chosen:
                    db.run("""INSERT OR IGNORE INTO events (name, venue, city, category, event_date, door_time, status, created_by)
                              VALUES (?, ?, ?, ?, ?, ?, 'review', ?)""",
                           event_name, venue, city, category, event_date.isoformat(), start_time or None, g.user["id"])
                    chosen = find_event(event_date.isoformat(), venue, event_name)
            review = ["face"] if face_proof else []
            if config.TICKET_CHECKS and files and any(not codes for _, _, codes in files):
                review.append("code")
            # #28: a seller's listings only go on sale once Stripe's payout setup (with its identity check) is done.
            draft = config.NEW_SELLER_LIMITS and not g.user["payouts_ready"]
            listing_id = db.run(
                """INSERT INTO listings (seller_id, venue, event_name, event_date, face_value_cents, price_cents,
                   poster_file, sold_together, category, city, start_time, event_id, delivery, transfer_app, name_on_ticket,
                   status, review_reason, face_checked)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                g.user["id"], venue, event_name, event_date.isoformat(), face, price, poster_file, sold_together,
                category, city, start_time or None, chosen["id"] if chosen else None, delivery, transfer_app, name_on_ticket,
                "draft" if draft else "review" if review else "active", ",".join(review) or None, 0 if face_proof else 1,
            )
            db.run("UPDATE listings SET no_double_sell_at=? WHERE id=?", sqltime(utcnow()), listing_id)
            if face_proof:
                save_proof(face_proof, "face", listing_id=listing_id)
            if app_shot:
                save_proof(app_shot, "app_screenshot", listing_id=listing_id, phash=ticketcodes.phash(*app_shot))
                save_proof(app_order, "app_order", listing_id=listing_id)
            for _ in range(transfer_qty or 0):  # app tickets: one row per ticket, no file
                db.run("INSERT INTO tickets (listing_id, file, file_hash) VALUES (?, '', ?)",
                       listing_id, "transfer:" + secrets.token_hex(12))
            os.makedirs(config.UPLOAD_DIR, exist_ok=True)
            for (data, ext, codes), digest in zip(files, file_hashes if files else []):
                filename = "%s.%s" % (secrets.token_hex(16), ext)
                with open(os.path.join(config.UPLOAD_DIR, filename), "wb") as fh:
                    fh.write(data)
                picture = ticketcodes.phash(data, ext)
                for code_hash_, code_text, code_format in (codes or [(None, None, None)]):
                    db.run("""INSERT INTO tickets (listing_id, file, file_hash, code_hash, code_text, code_format, phash)
                              VALUES (?, ?, ?, ?, ?, ?, ?)""", listing_id, filename, digest, code_hash_, code_text, code_format, picture)
            if draft:
                flash(t("listed_draft"), "ok")
                return redirect(url_for("payouts_setup"))
            if review:
                notify.notify_admins("Spare: listing to check (%s)" % ", ".join(review),
                                     "%s listed a ticket for %s that needs a check before it goes live:\n\n%s" % (
                                         g.user["email"], event_name or venue, base_url() + url_for("admin") + "#review"))
                flash(t("listed_review"), "ok")
                return redirect(url_for("listing_page", listing_id=listing_id))
            notify_alerts(db.one("SELECT * FROM listings WHERE id=?", listing_id))
            flash(t("listed_ok"), "ok")
            return redirect(url_for("listing_page", listing_id=listing_id, new=1))

    preset = None
    if request.values.get("event"):
        preset = db.one("SELECT * FROM events WHERE id=? AND status != 'hidden' AND cancelled_at IS NULL AND event_date >= ?",
                        request.values.get("event"), today().isoformat())
    return render_template("sell.html", form=form, errors=errors, venues=config.VENUES, categories=config.CATEGORIES,
                           city_suggestions=config.CITY_SUGGESTIONS, preset=preset,
                           preset_looking=looking_count(preset["event_date"], preset["venue"], preset["name"]) if preset else 0,
                           today=today().isoformat(), max_date=(today() + timedelta(days=365)).isoformat(),
                           max_tickets=config.MAX_TICKETS_PER_LISTING, new_seller=is_new_seller(g.user["id"]),
                           new_seller_max=config.NEW_SELLER_MAX_TICKETS, new_seller_hours=config.NEW_SELLER_PAYOUT_HOURS,
                           new_seller_sales=config.NEW_SELLER_SALES)


@app.route("/api/events")
def events_on_date():
    """Events on a given date, for the 'which event?' picker when selling."""
    day = request.args.get("date", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return {"events": []}
    rows = db.all("""SELECT * FROM events WHERE event_date=? AND status IN ('published', 'review') AND cancelled_at IS NULL
                     ORDER BY city != ?, venue, name""", day, config.DEFAULT_CITY)
    return {"looking_label": t("picker_looking"),
            "events": [{"id": e["id"], "name": e["name"], "venue": e["venue"], "city": e["city"],
                        "category": t("cat_" + e["category"]), "time": e["door_time"] or "",
                        "looking": looking_count(day, e["venue"], e["name"]),
                        "face_max": money(e["face_value_max_cents"]) if e["face_value_max_cents"] else ""} for e in rows]}


@app.route("/t/<int:listing_id>/price", methods=["POST"])
@login_required
def change_price(listing_id):
    l = db.one("SELECT * FROM listings WHERE id=?", listing_id)
    if not l or l["seller_id"] != g.user["id"]:
        abort(404)
    price = parse_money(request.form.get("price"))
    if l["status"] != "active":
        flash(t("err_cannot_edit"), "error")
    elif price is None or price < 100:
        flash(t("err_price"), "error")
    elif price > max_price(l["face_value_cents"]):
        flash(t("err_price_cap", max=money(max_price(l["face_value_cents"]))), "error")
    else:
        db.run("UPDATE listings SET price_cents=? WHERE id=?", price, listing_id)
        flash(t("price_changed", price=money(price)), "ok")
        if price < l["price_cents"]:
            notify_alerts(db.one("SELECT * FROM listings WHERE id=?", listing_id))
    return redirect(url_for("listing_page", listing_id=listing_id))


@app.route("/t/<int:listing_id>/remove", methods=["POST"])
@login_required
def remove_listing(listing_id):
    listing = db.one("SELECT * FROM listings WHERE id=?", listing_id)
    if not listing or listing["seller_id"] != g.user["id"]:
        abort(404)
    release_unpaid()
    checking_out = db.one("SELECT 1 FROM tickets WHERE listing_id=? AND status='held'", listing_id)
    if listing["status"] != "active" or checking_out:
        flash(t("err_cannot_remove"), "error")
        return redirect(url_for("listing_page", listing_id=listing_id))
    unsold = db.all("SELECT DISTINCT file FROM tickets WHERE listing_id=? AND status='available'", listing_id)
    db.run("UPDATE tickets SET status='void' WHERE listing_id=? AND status='available'", listing_id)
    sold = db.one("SELECT 1 FROM tickets WHERE listing_id=? AND status='sold'", listing_id)
    db.run("UPDATE listings SET status=? WHERE id=?", "sold" if sold else "removed", listing_id)
    for row in unsold:
        if not db.one("SELECT 1 FROM tickets WHERE file=? AND status != 'void'", row["file"]):
            try:
                os.remove(os.path.join(config.UPLOAD_DIR, row["file"]))
            except OSError:
                pass
    flash(t("removed_ok"), "ok")
    return redirect(url_for("account"))


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"))
    if g.user:
        return redirect(nxt)
    error = None
    email = request.values.get("email", "").strip().lower()
    if request.method == "POST":
        if not EMAIL_RE.match(email) or len(email) > 254:
            error = t("err_email")
        elif rate_limited("login-ip", 10, 60) or rate_limited("login-email", 5, 60, email):
            error = t("err_too_many_tries")
        else:
            code = issue_code("login", email)
            session["login_email"] = email
            session["login_next"] = nxt
            if code:
                sent = notify.send_email(email, t("mail_code_subject"), t("mail_code_body", code=code))
                if not sent and config.DEV:
                    flash(t("dev_code", code=code), "dev")
            return redirect(url_for("login_verify"))
    return render_template("login.html", email=email, next=nxt, error=error)


@app.route("/login/verify", methods=["GET", "POST"])
def login_verify():
    email = session.get("login_email")
    if not email:
        return redirect(url_for("login"))
    user = db.one("SELECT * FROM users WHERE email=?", email)
    needs_name = not user or not user["first_name"]
    error = None
    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        name = " ".join(request.form.get("first_name", "").split())[:40]
        if user and user["blocked_at"]:
            error = t("err_blocked", email=config.SUPPORT_EMAIL)
        elif needs_name and not name:
            error = t("err_first_name")
        elif rate_limited("verify-ip", 20, 60):
            error = t("err_too_many_tries")
        elif not check_code("login", email, code):
            error = t("err_code")
        else:
            if not user:
                uid = db.run("INSERT INTO users (email, first_name, source) VALUES (?, ?, ?)", email, name, session.get("source"))
                school = student_school(email)
                if school:  # signed up with a university address: already verified
                    db.run("UPDATE users SET student_email=?, student_school=?, student_verified_at=? WHERE id=?",
                           email, school, sqltime(utcnow()), uid)
            else:
                uid = user["id"]
                if needs_name:
                    db.run("UPDATE users SET first_name=? WHERE id=?", name, uid)
            nxt = session.get("login_next", "/")
            fresh_session(db.one("SELECT * FROM users WHERE id=?", uid))
            return redirect(safe_next(nxt))
    return render_template("login_verify.html", email=email, needs_name=needs_name, error=error)


# Kept across sign-in and sign-out: which marketing link someone came from, and a friend's preview access.
SESSION_KEEPS = ("source", "seen_sources", "preview")


def fresh_session(user=None):
    """#11: start a clean session, so nothing from before carries over; signs `user` in when given."""
    keep = {k: session[k] for k in SESSION_KEEPS if k in session}
    session.clear()
    session.update(keep)
    session["csrf"] = secrets.token_urlsafe(24)  # a new form token too
    if user:
        session["uid"], session["sv"] = user["id"], user["session_version"]
        session.permanent = True


def end_all_sessions(user_id):
    """#11: signs the user out on every device (each session remembers the version it started with)."""
    db.run("UPDATE users SET session_version = session_version + 1 WHERE id=?", user_id)


@app.route("/logout", methods=["POST"])
def logout():
    fresh_session()
    return redirect(url_for("index"))


@app.route("/account/sign-out-everywhere", methods=["POST"])
@login_required
def sign_out_everywhere():
    end_all_sessions(g.user["id"])
    fresh_session()
    flash(t("signed_out_everywhere"), "ok")
    return redirect(url_for("login"))


def phone_problem(number):
    """#24: one verified number per account, and never the number of a blocked user."""
    if db.one("SELECT 1 FROM blocked_phones WHERE phone=?", number):
        return t("err_phone_blocked", email=config.SUPPORT_EMAIL)
    if db.one("SELECT 1 FROM users WHERE phone=? AND phone_verified_at IS NOT NULL AND id != ? AND deleted_at IS NULL", number, g.user["id"]):
        return t("err_phone_taken", email=config.SUPPORT_EMAIL)
    return None


def send_phone_code(number, text):
    """Texts the code. Without SMS (development only, see phone()) it's emailed to the signed-in user."""
    if config.SMS_ENABLED:
        return notify.send_sms(number, text)
    return notify.send_email(g.user["email"], text, text)


@app.route("/account/phone", methods=["GET", "POST"])
@login_required
def phone():
    nxt = safe_next(request.values.get("next"), url_for("account"))
    error = None
    raw = request.values.get("phone", g.user["phone"] or "")
    if not config.DEV and not config.SMS_ENABLED:
        # #24: on the real site a phone number only counts when it's checked by text message.
        return render_template("phone.html", phone=raw, next=nxt, error=None, sms_needed=True)
    if request.method == "POST":
        number = normalise_phone(raw)
        problem = phone_problem(number) if number else None
        if not number:
            error = t("err_phone")
        elif problem:
            error = problem
        elif rate_limited("sms-user", 5, 60, g.user["id"]) or rate_limited("sms-ip", 10, 60):
            error = t("err_too_many_tries")
        else:
            code = issue_code("phone", number)
            session["phone_pending"] = number
            session["phone_next"] = nxt
            if code:
                sent = send_phone_code(number, t("sms_code", code=code))
                if not sent and config.DEV:
                    flash(t("dev_code", code=code), "dev")
            return redirect(url_for("phone_verify"))
    return render_template("phone.html", phone=raw, next=nxt, error=error)


@app.route("/account/phone/verify", methods=["GET", "POST"])
@login_required
def phone_verify():
    number = session.get("phone_pending")
    if not number:
        return redirect(url_for("phone"))
    error = None
    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        problem = phone_problem(number)
        if problem:
            error = problem
        elif check_code("phone", number, code):
            db.run("UPDATE users SET phone=?, phone_verified_at=? WHERE id=?", number, sqltime(utcnow()), g.user["id"])
            session.pop("phone_pending", None)
            flash(t("phone_ok"), "ok")
            return redirect(safe_next(session.pop("phone_next", None), url_for("account")))
        error = t("err_code")
    return render_template("phone_verify.html", phone=number, error=error)


@app.route("/account/student", methods=["GET", "POST"])
@login_required
def student():
    error = None
    email = request.values.get("email", "").strip().lower()
    if request.method == "POST":
        school = student_school(email) if EMAIL_RE.match(email) else None
        if not school:
            error = t("err_student_domain")
        elif db.one("SELECT 1 FROM users WHERE student_email=? AND id != ?", email, g.user["id"]):
            error = t("err_student_taken")
        elif rate_limited("student-mail", 5, 60, g.user["id"]):
            error = t("err_too_many_tries")
        else:
            code = issue_code("student", email)
            session["student_pending"] = email
            if code:
                sent = notify.send_email(email, t("mail_student_subject"), t("mail_code_body", code=code))
                if not sent and config.DEV:
                    flash(t("dev_code", code=code), "dev")
            return redirect(url_for("student_verify"))
    return render_template("student.html", email=email, error=error, schools=sorted(set(config.STUDENT_DOMAINS.values())))


@app.route("/account/student/verify", methods=["GET", "POST"])
@login_required
def student_verify():
    email = session.get("student_pending")
    if not email:
        return redirect(url_for("student"))
    error = None
    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        if check_code("student", email, code):
            db.run("UPDATE users SET student_email=?, student_school=?, student_verified_at=? WHERE id=?",
                   email, student_school(email), sqltime(utcnow()), g.user["id"])
            session.pop("student_pending", None)
            flash(t("student_ok"), "ok")
            return redirect(url_for("account"))
        error = t("err_code")
    return render_template("student_verify.html", email=email, error=error)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Profile icon (earned by selling, saved on the account) and tap sounds (saved on this device by the page's script)."""
    sales = sales_count(g.user["id"]) if g.user else 0
    if request.method == "POST":
        if not g.user:
            return redirect(url_for("login", next=url_for("settings")))
        key = request.form.get("avatar", "")
        db.run("UPDATE users SET avatar=? WHERE id=?", key if key in avatars.unlocked(sales) else None, g.user["id"])
        flash(t("avatar_saved"), "ok")
        return redirect(url_for("settings"))
    return render_template("settings.html", sales=sales, unlocked=avatars.unlocked(sales), next_at=avatars.next_unlock(sales),
                           unlock_at=avatars.UNLOCK_AT)


def sales_count(user_id):
    """Tickets someone has sold (paid or paid out; refunds don't count). Unlocks profile icons."""
    return db.one("SELECT COALESCE(SUM(quantity), 0) AS n FROM orders WHERE seller_id=? AND status IN ('paid', 'released')", user_id)["n"]


@app.route("/account")
@login_required
def account():
    release_unpaid()
    uid = g.user["id"]
    if payments.live() and g.user["stripe_account_id"] and not g.user["payouts_ready"]:
        # Stripe verifies details for a little while after onboarding; check again until it's done.
        try:
            if payments.payouts_ready(g.user["stripe_account_id"]):
                set_payouts_ready(uid)
                g.user = db.one("SELECT * FROM users WHERE id=?", uid)
        except Exception:
            app.logger.exception("Could not check payout status")
    listings = db.all(
        """SELECT l.*, (SELECT COUNT(*) FROM tickets t WHERE t.listing_id = l.id AND t.status = 'available') AS available
           FROM listings l WHERE seller_id=? AND status='active' AND event_date >= ? ORDER BY event_date""",
        uid, today().isoformat(),
    )
    sales = db.all(
        """SELECT o.*, l.venue, l.event_name, l.event_date FROM orders o JOIN listings l ON l.id=o.listing_id
           WHERE o.seller_id=? AND o.status IN ('paid','disputed','released','refunded') ORDER BY o.id DESC""",
        uid,
    )
    purchases = db.all(
        """SELECT o.*, l.venue, l.event_name, l.event_date FROM orders o JOIN listings l ON l.id=o.listing_id
           WHERE o.buyer_id=? AND o.status IN ('paid','disputed','released','refunded') ORDER BY l.event_date DESC""",
        uid,
    )
    alerts = db.all("SELECT * FROM alerts WHERE user_id=? AND event_date >= ? ORDER BY event_date",
                    uid, today().isoformat())
    return render_template("account.html", listings=listings, sales=sales, purchases=purchases, alerts=alerts)


@app.route("/account/payouts")
@login_required
def payouts_setup():
    if not payments.live():
        set_payouts_ready(g.user["id"])
        flash(t("payouts_test_ok"), "ok")
        return redirect(url_for("account"))
    try:
        account_id, url = payments.payout_onboarding_url(
            g.user, base_url() + url_for("payouts_return"), base_url() + url_for("payouts_setup"),
        )
    except Exception:
        app.logger.exception("Stripe onboarding failed")
        flash(t("err_payouts"), "error")
        return redirect(url_for("account"))
    db.run("UPDATE users SET stripe_account_id=? WHERE id=?", account_id, g.user["id"])
    return redirect(url, code=303)


@app.route("/account/payouts/return")
@login_required
def payouts_return():
    try:
        ready = payments.payouts_ready(g.user["stripe_account_id"])
    except Exception:
        ready = False
    if ready:
        set_payouts_ready(g.user["id"])
    else:
        db.run("UPDATE users SET payouts_ready=0 WHERE id=?", g.user["id"])
    flash(t("payouts_ok") if ready else t("payouts_pending"), "ok" if ready else "error")
    return redirect(url_for("account"))


@app.route("/lang/<code>")
def set_lang(code):
    resp = redirect(safe_next(request.args.get("next")))
    if code in i18n.LANGS:
        resp.set_cookie("lang", code, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


# --------------------------------------------------------------------------
# Installable app (PWA) and push notifications
# --------------------------------------------------------------------------

@app.route("/api/push/key")
def push_key():
    return {"key": push.public_key()}


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    if not push.save(g.user["id"], request.get_json(silent=True) or {}):
        abort(400)
    push.send(g.user["id"], t("push_welcome_title"), t("push_welcome_body"), url_for("account"), tag="welcome")
    return {"ok": True}


@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    endpoint = (request.get_json(silent=True) or {}).get("endpoint", "")
    db.run("DELETE FROM push_subs WHERE endpoint=? AND user_id=?", endpoint, g.user["id"])
    return {"ok": True}


@app.route("/manifest.webmanifest")
def manifest():
    icon = lambda name, size, purpose="any": {  # noqa: E731
        "src": url_for("static", filename="icons/" + name), "sizes": size, "type": "image/png", "purpose": purpose}
    data = {
        "name": "Spare",
        "short_name": "Spare",
        "description": t("meta_description"),
        "lang": g.lang,
        "start_url": "/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#15132b",
        "theme_color": "#15132b",
        "icons": [icon("icon-192.png", "192x192"), icon("icon-512.png", "512x512"),
                  icon("icon-maskable-512.png", "512x512", "maskable")],
        "shortcuts": [
            {"name": t("nav_sell"), "url": url_for("sell"), "icons": [icon("icon-192.png", "192x192")]},
            {"name": t("your_tickets"), "url": url_for("account"), "icons": [icon("icon-192.png", "192x192")]},
        ],
    }
    resp = app.response_class(json.dumps(data), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/sw.js")
def service_worker():
    # Served from the root so it can handle every page.
    resp = send_file(os.path.join(app.static_folder, "js", "sw.js"), mimetype="text/javascript", max_age=0)
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/favicon.ico")
def favicon():
    return send_file(os.path.join(app.static_folder, "icons", "favicon-48.png"), mimetype="image/png", max_age=86400)


@app.route("/offline")
def offline():
    return render_template("offline.html")


# --------------------------------------------------------------------------
# Static pages
# --------------------------------------------------------------------------

@app.route("/credits")
def credits():
    """Every credited image on the site: free-licence venue photos and organiser posters."""
    venues = db.all("""SELECT * FROM venues WHERE photo_file IS NOT NULL AND (photo_credit IS NOT NULL OR photo_license IS NOT NULL)
                       ORDER BY name""")
    posters = db.all("""SELECT * FROM events WHERE poster_file IS NOT NULL AND poster_credit IS NOT NULL
                        AND status != 'hidden' AND event_date >= ? ORDER BY event_date""", today().isoformat())
    return render_template("credits.html", venues=venues, posters=posters)


@app.route("/how-it-works")
def how():
    return render_template("how.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# --------------------------------------------------------------------------
# Coming soon: waitlist and preview link
# --------------------------------------------------------------------------

WAITLIST_INTERESTS = ("buy", "sell", "organise")


def coming_soon_page(error=None, email="", interest="buy"):
    count = db.one("SELECT COUNT(*) AS n FROM waitlist")["n"]
    joined = session.get("waitlisted")
    return render_template("coming_soon.html", bare=True, error=error, email=email, interest=interest,
                           joined=joined, waiting=count)


@app.route("/waitlist", methods=["POST"])
def waitlist_join():
    email = request.form.get("email", "").strip().lower()
    interest = request.form.get("interest", "buy")
    if interest not in WAITLIST_INTERESTS:
        interest = "buy"
    if not EMAIL_RE.match(email) or len(email) > 254:
        return coming_soon_page(t("err_email"), email, interest), 400
    if rate_limited("waitlist", 10, 60):
        return coming_soon_page(t("err_too_many_tries"), email, interest), 429
    db.run("""INSERT INTO waitlist (email, interest, lang, token, source) VALUES (?, ?, ?, ?, ?)
              ON CONFLICT(email) DO UPDATE SET interest=excluded.interest, lang=excluded.lang""",
           email, interest, g.lang, secrets.token_urlsafe(16), session.get("source"))
    session["waitlisted"] = email
    return redirect(url_for("index") + "#joined")


@app.route("/waitlist/leave/<token>", methods=["GET", "POST"])
def waitlist_leave(token):
    db.run("DELETE FROM waitlist WHERE token=?", token)
    if request.method == "POST":  # one-click unsubscribe from a mail app (#17): no page, no session
        return "Removed.", 200
    session.pop("waitlisted", None)
    flash(t("cs_left"), "ok")
    return redirect(url_for("index"))


@app.route("/preview/<code>")
def preview(code):
    """The link Leo shares with friends so they can try the full site before launch."""
    current = get_setting("preview_code")
    if not current or not hmac.compare_digest(code, current):
        abort(404)
    session["preview"] = code
    flash(t("cs_preview_on"), "ok")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    q = """SELECT o.*, l.venue, l.event_name, l.event_date, b.email AS buyer_email, b.phone AS buyer_phone,
                  s.email AS seller_email, s.phone AS seller_phone
           FROM orders o JOIN listings l ON l.id=o.listing_id
           JOIN users b ON b.id=o.buyer_id JOIN users s ON s.id=o.seller_id """
    disputed = db.all(q + "WHERE o.status='disputed' ORDER BY o.id")
    recent = db.all(q + "WHERE o.status IN ('paid','released','refunded') ORDER BY o.id DESC LIMIT 50")
    chargebacks = db.all(q + "WHERE o.chargeback_at IS NOT NULL ORDER BY o.chargeback_at DESC LIMIT 50")
    stats = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(fee_cents + seller_fee_cents),0) AS fees, COALESCE(SUM(total_cents),0) AS volume
           FROM orders WHERE status IN ('paid','released')"""
    )
    flagged = db.all(
        """SELECT m.*, u.email AS sender_email, u.first_name AS sender_name,
                  (SELECT COUNT(*) FROM messages m2 WHERE m2.sender_id = m.sender_id AND m2.flags IS NOT NULL) AS strikes
           FROM messages m JOIN users u ON u.id = m.sender_id
           WHERE m.flags IS NOT NULL ORDER BY m.id DESC LIMIT 50""")
    reports = db.all("""SELECT r.*, l.event_name, l.venue, l.event_date, l.status AS listing_status, s.email AS seller_email,
                               s.id AS seller_id, u.email AS reporter_email
                        FROM reports r JOIN listings l ON l.id = r.listing_id JOIN users s ON s.id = l.seller_id
                        LEFT JOIN users u ON u.id = r.reporter_id WHERE r.status='open' ORDER BY r.id""")
    waitlist = db.one("""SELECT COUNT(*) AS n, COALESCE(SUM(interest='sell'),0) AS sell, COALESCE(SUM(interest='organise'),0) AS organise,
                                COALESCE(SUM(notified_at IS NULL),0) AS waiting FROM waitlist""")
    review = db.all("""SELECT l.*, u.email AS seller_email, e.face_value_max_cents FROM listings l JOIN users u ON u.id = l.seller_id
                       LEFT JOIN events e ON e.id = l.event_id WHERE l.status='review' ORDER BY l.id""")
    proofs = {}
    for p in db.all("SELECT * FROM proofs WHERE listing_id IN (SELECT id FROM listings WHERE status='review')"):
        proofs.setdefault(p["listing_id"], []).append(p)
    review_files = {}
    for tk in db.all("""SELECT MIN(id) AS id, listing_id FROM tickets WHERE file != ''
                        AND listing_id IN (SELECT id FROM listings WHERE status='review') GROUP BY listing_id, file"""):
        review_files.setdefault(tk["listing_id"], []).append(tk["id"])
    preview_code = get_setting("preview_code")
    preview_link = base_url() + url_for("preview", code=preview_code) if preview_code else None
    chain = [p for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
    network = {"ip": client_ip(), "entries": len(chain), "hops": config.PROXY_HOPS, "proxy": config.TRUST_PROXY}
    used, used_bytes, total = disk_usage()
    disk = {"pct": round(used * 100), "used": used_bytes / 1e9, "total": total / 1e9, "warn": used >= DISK_ALERT}
    return render_template("admin.html", disputed=disputed, recent=recent, stats=stats, flagged=flagged, reports=reports,
                           waitlist=waitlist, preview_link=preview_link, network=network, disk=disk,
                           review=review, proofs=proofs, chargebacks=chargebacks, review_files=review_files)


@app.route("/admin/backup")
@admin_required
def admin_backup():
    """Download a full backup now (database plus uploaded tickets and posters), encrypted with BACKUP_KEY."""
    try:
        data, name = backup.seal(*backup.make_zip()[:2])
    except backup.NoKey:
        flash("Set BACKUP_KEY on Render first: backups are never downloaded unencrypted.", "error")
        return redirect(url_for("admin"))
    resp = app.response_class(data, mimetype="application/octet-stream" if name.endswith(".enc") else "application/zip")
    resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % name
    return resp


@app.route("/admin/launch", methods=["POST"])
@admin_required
def admin_launch():
    """Coming-soon switch and the friends' preview link."""
    action = request.form.get("action")
    if action == "coming_soon":
        set_setting("coming_soon", "1")
        if not get_setting("preview_code"):
            set_setting("preview_code", secrets.token_urlsafe(8))
        flash("Coming-soon mode is on: visitors see the sign-up page.", "ok")
    elif action == "open":
        set_setting("coming_soon", "0")
        flash("The site is open to everyone.", "ok")
    elif action == "new_link":
        set_setting("preview_code", secrets.token_urlsafe(8))
        flash("New preview link made. The old one no longer works.", "ok")
    return redirect(url_for("admin") + "#launch")


@app.route("/admin/waitlist")
@admin_required
def admin_waitlist():
    rows = db.all("SELECT * FROM waitlist ORDER BY id DESC")
    if request.args.get("format") == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["email", "interest", "language", "signed_up", "launch_email_sent"])
        for r in rows:
            writer.writerow([r["email"], r["interest"], r["lang"], r["created_at"], r["notified_at"] or ""])
        resp = app.response_class(out.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = 'attachment; filename="spare-waitlist.csv"'
        return resp
    return render_template("admin_waitlist.html", rows=rows)


@app.route("/admin/waitlist/notify", methods=["POST"])
@admin_required
def admin_waitlist_notify():
    """Sends the launch email in small batches, so one click never runs into the server's time limit."""
    sent = 0
    for r in db.all("SELECT * FROM waitlist WHERE notified_at IS NULL ORDER BY id LIMIT 20"):
        lang = r["lang"] if r["lang"] in i18n.LANGS else "en"
        leave = base_url() + url_for("waitlist_leave", token=r["token"])
        body = i18n.t(lang, "mail_launch_body", url=base_url() + url_for("index"), leave=leave)
        # #17: mail apps show an "unsubscribe" button for this; one click (RFC 8058) removes the address.
        unsubscribe = {"List-Unsubscribe": "<%s>, <mailto:%s?subject=unsubscribe>" % (leave, config.SUPPORT_EMAIL),
                       "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if notify.send_email(r["email"], i18n.t(lang, "mail_launch_subject"), body, headers=unsubscribe):
            db.run("UPDATE waitlist SET notified_at=? WHERE id=?", sqltime(utcnow()), r["id"])
            sent += 1
    left = db.one("SELECT COUNT(*) AS n FROM waitlist WHERE notified_at IS NULL")["n"]
    flash("Launch email sent to %d people. %d still to go%s." % (sent, left, ": click again" if left else ""), "ok")
    return redirect(url_for("admin_waitlist"))


def source_link(slug):
    return base_url() + url_for("index", **{"from": slug})


@app.route("/admin/sources", methods=["GET", "POST"])
@admin_required
def admin_sources():
    """Marketing links with a QR code each, and how many visits and sign-ups every link brought in."""
    error = None
    if request.method == "POST":
        label = request.form.get("label", "").strip()[:80]
        slug = request.form.get("slug", "").strip().lower() or label.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:32]
        if not label or not SOURCE_RE.match(slug):
            error = "Give the link a name (letters or numbers)."
        elif db.one("SELECT 1 FROM sources WHERE slug=?", slug):
            error = "There is already a link called '%s'. Pick another code." % slug
        else:
            db.run("INSERT INTO sources (slug, label) VALUES (?, ?)", slug, label)
            flash("Link made: %s" % source_link(slug), "ok")
            return redirect(url_for("admin_sources") + "#src-" + slug)

    labels = {r["slug"]: r for r in db.all("SELECT * FROM sources")}
    visits = {r["source"]: r["n"] for r in db.all("SELECT source, SUM(visits) AS n FROM source_visits GROUP BY source")}
    signups = {r["source"]: r for r in db.all("""SELECT source, COUNT(*) AS n, SUM(interest='sell') AS sell, SUM(interest='organise') AS organise
                                                  FROM waitlist WHERE source IS NOT NULL GROUP BY source""")}
    accounts = {r["source"]: r["n"] for r in db.all("SELECT source, COUNT(*) AS n FROM users WHERE source IS NOT NULL GROUP BY source")}
    rows = []
    for slug in set(labels) | set(visits) | set(signups) | set(accounts):
        w = signups.get(slug)
        n_visits, n_signups = visits.get(slug, 0), (w["n"] if w else 0)
        rows.append({
            "slug": slug, "label": labels[slug]["label"] if slug in labels else None, "link": source_link(slug),
            "visits": n_visits, "signups": n_signups, "sell": (w["sell"] if w else 0), "organise": (w["organise"] if w else 0),
            "accounts": accounts.get(slug, 0), "rate": round(100 * n_signups / n_visits) if n_visits else None,
            "qr": qr_svg(source_link(slug)) if slug in labels else None,
        })
    rows.sort(key=lambda r: (r["label"] is None, -r["signups"], -r["visits"], r["slug"]))
    direct = db.one("SELECT COUNT(*) AS n FROM waitlist WHERE source IS NULL")["n"]
    return render_template("admin_sources.html", rows=rows, direct=direct, error=error,
                           label=request.form.get("label", ""), slug=request.form.get("slug", ""))


@app.route("/admin/sources/<slug>/qr.svg")
@admin_required
def admin_source_qr(slug):
    """A print-ready QR code (black on white, with a quiet zone) for a flyer or poster."""
    import qrcode
    import qrcode.image.svg

    if not db.one("SELECT 1 FROM sources WHERE slug=?", slug):
        abort(404)
    img = qrcode.make(source_link(slug), image_factory=qrcode.image.svg.SvgFillImage, box_size=20, border=4)
    resp = app.response_class(img.to_string(encoding="unicode"), mimetype="image/svg+xml")
    resp.headers["Content-Disposition"] = 'attachment; filename="spare-qr-%s.svg"' % slug
    return resp


@app.route("/admin/sources/<slug>/delete", methods=["POST"])
@admin_required
def admin_source_delete(slug):
    db.run("DELETE FROM sources WHERE slug=?", slug)
    flash("Link removed. Its numbers stay in the list as an unnamed code.", "ok")
    return redirect(url_for("admin_sources"))


@app.route("/admin/waitlist/<int:entry_id>/delete", methods=["POST"])
@admin_required
def admin_waitlist_delete(entry_id):
    db.run("DELETE FROM waitlist WHERE id=?", entry_id)
    flash("Removed from the waitlist.", "ok")
    return redirect(url_for("admin_waitlist"))


def save_image(upload):
    """Stores an uploaded poster/photo and returns its file name, or None if it isn't a valid image."""
    if not upload or not upload.filename:
        return None
    ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    data = upload.read()
    if ext not in POSTER_TYPES or not data.startswith(POSTER_TYPES[ext]) or len(data) > config.MAX_UPLOAD_BYTES:
        return None
    name = "%s.%s" % (secrets.token_hex(16), "jpg" if ext == "jpeg" else ext)
    os.makedirs(os.path.join(config.UPLOAD_DIR, "posters"), exist_ok=True)
    with open(os.path.join(config.UPLOAD_DIR, "posters", name), "wb") as fh:
        fh.write(data)
    return name


@app.route("/admin/venues", methods=["GET", "POST"])
@admin_required
def admin_venues():
    if request.method == "POST":
        v = db.one("SELECT * FROM venues WHERE id=?", request.form.get("id"))
        if not v:
            abort(404)
        colour = request.form.get("colour", "").strip()
        if colour and not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
            colour = v["colour"]
        photo = save_image(request.files.get("photo")) or v["photo_file"]
        website = request.form.get("website", "").strip()[:200]
        if website and not website.startswith(("http://", "https://")):
            website = "https://" + website
        clean = lambda key, n=200: " ".join(request.form.get(key, "").split())[:n] or None  # noqa: E731
        db.run("""UPDATE venues SET address=?, website=?, description=?, colour=?, photo_file=?, photo_credit=?,
                  photo_license=?, photo_license_url=?, photo_source_url=? WHERE id=?""",
               clean("address"), website or None, request.form.get("description", "").strip()[:600] or None,
               colour or None, photo, clean("photo_credit", 120), clean("photo_license", 60),
               clean("photo_license_url", 300), clean("photo_source_url", 300), v["id"])
        flash("Saved %s." % v["name"], "ok")
        return redirect(url_for("admin_venues") + "#v%s" % v["id"])
    venues = db.all("SELECT * FROM venues ORDER BY city != ?, city, name", config.DEFAULT_CITY)
    events = group_events(browse())
    return render_template("admin_venues.html", venues=venues, events=events)


@app.route("/admin/posters", methods=["POST"])
@admin_required
def admin_poster():
    name = save_image(request.files.get("poster"))
    if not name:
        flash("Upload a JPG, PNG or WebP image.", "error")
    else:
        db.run("""UPDATE listings SET poster_file=? WHERE event_date=? AND lower(venue)=lower(?)
                  AND lower(COALESCE(event_name, ''))=lower(?)""",
               name, request.form.get("date"), request.form.get("venue"), request.form.get("name", ""))
        flash("Poster updated.", "ok")
    return redirect(url_for("admin_venues") + "#events")


TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def event_from_form(form):
    """Validates the admin event form. Returns (values dict, error message or None)."""
    v = {
        "name": " ".join(form.get("name", "").split())[:80],
        "venue": " ".join(form.get("venue", "").split())[:60],
        "city": " ".join(form.get("city", "").split())[:60] or config.DEFAULT_CITY,
        "category": form.get("category", "club"),
        "event_date": form.get("event_date", ""),
        "door_time": form.get("door_time", "").strip() or None,
        "end_time": form.get("end_time", "").strip() or None,
        "min_age": form.get("min_age", "").strip() or None,
        "ticket_url": form.get("ticket_url", "").strip()[:500] or None,
        "description": form.get("description", "").strip()[:600] or None,
        "source_url": form.get("source_url", "").strip()[:500] or None,
        "face_value_max_cents": parse_money(form.get("face_value_max", "")) if form.get("face_value_max", "").strip() else None,
    }
    if not v["name"] or not v["venue"]:
        return v, "Name and venue are required."
    if v["category"] not in config.CATEGORIES:
        v["category"] = "club"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v["event_date"]):
        return v, "Date is required."
    for key in ("door_time", "end_time"):
        if v[key] and not TIME_RE.fullmatch(v[key]):
            return v, "Times look like 23:00."
    if v["min_age"] and not v["min_age"].isdigit():
        return v, "Minimum age is a number, like 18."
    if form.get("face_value_max", "").strip() and not v["face_value_max_cents"]:
        return v, "The official ticket price looks like 24.50."
    for key in ("ticket_url", "source_url"):
        if v[key] and not v[key].startswith(("http://", "https://")):
            v[key] = "https://" + v[key]
    return v, None


def update_event(event_id, v):
    """Saves an edited event and keeps its listings and alerts pointing at it."""
    old = db.one("SELECT * FROM events WHERE id=?", event_id)
    db.run("""UPDATE events SET name=?, venue=?, city=?, category=?, event_date=?, door_time=?, end_time=?, min_age=?,
              ticket_url=?, description=?, source_url=COALESCE(?, source_url), face_value_max_cents=? WHERE id=?""",
           v["name"], v["venue"], v["city"], v["category"], v["event_date"], v["door_time"], v["end_time"], v["min_age"],
           v["ticket_url"], v["description"], v["source_url"], v["face_value_max_cents"], event_id)
    db.run("""UPDATE listings SET venue=?, event_name=?, event_date=?, city=?, category=?, start_time=? WHERE event_id=?""",
           v["venue"], v["name"], v["event_date"], v["city"], v["category"], v["door_time"], event_id)
    db.run("""UPDATE alerts SET venue=?, event_date=?, event_name=? WHERE event_date=? AND lower(venue)=lower(?)
              AND lower(event_name)=lower(?)""", v["venue"], v["event_date"], v["name"], old["event_date"], old["venue"], old["name"])
    changes = []
    if v["event_date"] != old["event_date"]:
        changes.append(t("change_date", old=fmt_date(old["event_date"], "long"), new=fmt_date(v["event_date"], "long")))
    if (v["door_time"] or "") != (old["door_time"] or "") and old["door_time"]:
        changes.append(t("change_time", old=old["door_time"], new=v["door_time"] or "?"))
    if changes:
        notify_event_change(event_id, " ".join(changes))


@app.route("/admin/events")
@admin_required
def admin_events():
    review = db.all("SELECT e.*, u.email AS by_email FROM events e LEFT JOIN users u ON u.id = e.created_by "
                    "WHERE e.status='review' AND e.event_date >= ? ORDER BY e.event_date", today().isoformat())
    upcoming = db.all(
        """SELECT e.*, (SELECT COUNT(*) FROM tickets t JOIN listings l ON l.id = t.listing_id
                        WHERE l.event_id = e.id AND t.status = 'available') AS tickets,
                       (SELECT COUNT(*) FROM alerts a WHERE a.event_date = e.event_date AND lower(a.venue) = lower(e.venue)
                        AND lower(a.event_name) = lower(e.name)) AS waiting
           FROM events e WHERE e.event_date >= ? ORDER BY e.event_date, e.venue""", today().isoformat())
    edit = db.one("SELECT * FROM events WHERE id=?", request.args.get("edit")) if request.args.get("edit") else None
    posters = db.all("""SELECT p.*, e.name AS event_name, e.venue, e.event_date, u.email FROM poster_submissions p
                        JOIN events e ON e.id = p.event_id JOIN users u ON u.id = p.user_id
                        WHERE p.status='pending' ORDER BY p.id""")
    return render_template("admin_events.html", review=review, upcoming=upcoming, edit=edit, posters=posters,
                           categories=config.CATEGORIES, venues=db.all("SELECT name, city FROM venues ORDER BY name"))


@app.route("/admin/events/save", methods=["POST"])
@admin_required
def admin_event_save():
    v, error = event_from_form(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("admin_events", edit=request.form.get("id") or None) + "#form")
    get_venue(v["venue"], v["city"], create=True)
    event_id = request.form.get("id")
    if event_id:
        update_event(event_id, v)
    else:
        if find_event(v["event_date"], v["venue"], v["name"]):
            flash("That event already exists.", "error")
            return redirect(url_for("admin_events") + "#form")
        event_id = db.run("""INSERT INTO events (name, venue, city, category, event_date, door_time, end_time, min_age,
                             ticket_url, description, source_url, created_by, face_value_max_cents)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          v["name"], v["venue"], v["city"], v["category"], v["event_date"], v["door_time"], v["end_time"],
                          v["min_age"], v["ticket_url"], v["description"], v["source_url"], g.user["id"], v["face_value_max_cents"])
    poster_name = save_image(request.files.get("poster"))
    if poster_name:
        db.run("UPDATE events SET poster_file=? WHERE id=?", poster_name, event_id)
    flash("Saved %s." % v["name"], "ok")
    return redirect(url_for("admin_events") + "#e%s" % event_id)


@app.route("/admin/events/<int:event_id>/<action>", methods=["POST"])
@admin_required
def admin_event_status(event_id, action):
    if action == "cancel":
        refunded = cancel_event(event_id)
        flash("Event cancelled. %d orders refunded in full; all its tickets are off sale." % refunded, "ok")
        return redirect(url_for("admin_events"))
    status = {"approve": "published", "publish": "published", "hide": "hidden"}.get(action)
    if not status:
        abort(400)
    db.run("UPDATE events SET status=? WHERE id=?", status, event_id)
    return redirect(request.referrer or url_for("admin_events"))


@app.route("/admin/events/import", methods=["POST"])
@admin_required
def admin_event_import():
    url = request.form.get("url", "").strip()
    try:
        found = importer.extract(importer.fetch(url), url)
    except importer.ImportError_ as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_events"))
    found = [e for e in found if e["event_date"] >= today().isoformat()]
    for e in found:
        e["exists"] = bool(find_event(e["event_date"], e["venue"], e["name"]))
        e["city"] = e["city"] or config.DEFAULT_CITY
    if not found:
        flash("No upcoming events found on that page. Some sites don't publish event data; add those by hand.", "error")
        return redirect(url_for("admin_events"))
    return render_template("admin_import.html", found=found, url=url, payload=json.dumps(found), categories=config.CATEGORIES)


@app.route("/admin/events/import/confirm", methods=["POST"])
@admin_required
def admin_event_import_confirm():
    try:
        found = json.loads(request.form.get("payload", "[]"))
    except ValueError:
        abort(400)
    picked = set(request.form.getlist("pick"))
    added = 0
    for i, e in enumerate(found):
        if str(i) not in picked:
            continue
        form = dict(e, category=request.form.get("category_%d" % i, e.get("category")),
                    venue=request.form.get("venue_%d" % i, e.get("venue")) or "",
                    source_url=request.form.get("url"), min_age="", door_time=e.get("door_time") or "",
                    end_time=e.get("end_time") or "", ticket_url=e.get("ticket_url") or "", description=e.get("description") or "",
                    face_value_max=e.get("face_value_max") or "")
        v, error = event_from_form(form)
        if error or find_event(v["event_date"], v["venue"], v["name"]):
            continue
        venue = get_venue(v["venue"], v["city"], create=True)
        if e.get("address") and venue and not venue["address"]:
            db.run("UPDATE venues SET address=? WHERE id=?", e["address"], venue["id"])
        db.run("""INSERT INTO events (name, venue, city, category, event_date, door_time, end_time, ticket_url, description,
                  source_url, created_by, face_value_max_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
               v["name"], v["venue"], v["city"], v["category"], v["event_date"], v["door_time"], v["end_time"],
               v["ticket_url"], v["description"], v["source_url"], g.user["id"], v["face_value_max_cents"])
        added += 1
    flash("Added %d events." % added, "ok")
    return redirect(url_for("admin_events"))


@app.route("/events/<int:event_id>/poster", methods=["GET", "POST"])
@login_required
def submit_poster(event_id):
    e = db.one("SELECT * FROM events WHERE id=? AND status != 'hidden'", event_id)
    if not e:
        abort(404)
    errors = {}
    form = request.form
    if request.method == "POST":
        organisation = " ".join(form.get("organisation", "").split())[:80]
        if not organisation:
            errors["organisation"] = t("err_organisation")
        if form.get("consent") != "yes":
            errors["consent"] = t("err_consent")
        if not errors and rate_limited("poster", 5, 60, g.user["id"]):
            errors["poster"] = t("err_too_many_tries")
        upload = request.files.get("poster")
        if not upload or not upload.filename:
            errors["poster"] = t("err_poster_missing")
        if not errors:
            name = save_image(upload)
            if not name:
                errors["poster"] = t("err_poster_type")
            else:
                db.run("""INSERT INTO poster_submissions (event_id, user_id, file, organisation, role, credit)
                          VALUES (?, ?, ?, ?, ?, ?)""", event_id, g.user["id"], name, organisation,
                       " ".join(form.get("role", "").split())[:60] or None,
                       " ".join(form.get("credit", "").split())[:80] or None)
                notify.notify_admins("Spare: poster sent in for %s" % e["name"],
                                     "%s (%s) sent a poster.\n\n%s" % (organisation, g.user["email"], base_url() + url_for("admin_events")))
                flash(t("poster_thanks"), "ok")
                return redirect(event_url(e["venue"], e["event_date"], e["name"]))
    return render_template("submit_poster.html", e=e, errors=errors, form=form)


@app.route("/admin/posters/<int:submission_id>/<action>", methods=["POST"])
@admin_required
def admin_poster_review(submission_id, action):
    sub = db.one("SELECT * FROM poster_submissions WHERE id=?", submission_id)
    if not sub or action not in ("approve", "reject"):
        abort(404)
    if action == "approve":
        db.run("UPDATE events SET poster_file=?, poster_credit=? WHERE id=?",
               sub["file"], sub["credit"] or sub["organisation"], sub["event_id"])
        db.run("UPDATE poster_submissions SET status='rejected' WHERE event_id=? AND status='pending' AND id != ?",
               sub["event_id"], sub["id"])
    db.run("UPDATE poster_submissions SET status=? WHERE id=?", "approved" if action == "approve" else "rejected", sub["id"])
    flash("Poster %s." % ("approved" if action == "approve" else "rejected"), "ok")
    return redirect(url_for("admin_events") + "#posters")


# --------------------------------------------------------------------------
# Safety: blocking, reports, cancelled and changed events, admin second factor
# --------------------------------------------------------------------------

def tell(user_id, subject, body, url, push_title=None):
    """Email + push to one user (skips deleted accounts)."""
    u = db.one("SELECT * FROM users WHERE id=?", user_id)
    if not u or u["deleted_at"]:
        return
    notify.send_email(u["email"], subject, body.replace("{name}", u["first_name"] or "") + "\n\n" + base_url() + url + "\n\nSpare")
    push.send(user_id, push_title or subject, body.split("\n")[0].replace("{name}", u["first_name"] or "")[:120], url)


def take_off_sale(listing_ids):
    for lid in listing_ids:
        db.run("UPDATE tickets SET status='void' WHERE listing_id=? AND status='available'", lid)
        db.run("UPDATE listings SET status='removed' WHERE id=? AND status='active'", lid)


def send_statement_of_reasons(user, what, why):
    """#12, EU Digital Services Act art. 17: tell someone what we removed or blocked, why, and how to object.
    Never names who reported them."""
    if not user or user["deleted_at"]:
        return
    notify.send_email(user["email"], t("mail_dsa_subject"),
                      t("mail_dsa_body", name=user["first_name"] or "", what=what, why=why, email=config.SUPPORT_EMAIL,
                        terms=base_url() + url_for("terms")))


def block_user(user_id, reason):
    db.run("UPDATE users SET blocked_at=?, blocked_reason=? WHERE id=?", sqltime(utcnow()), reason[:300] or None, user_id)
    end_all_sessions(user_id)
    blocked = db.one("SELECT phone, phone_verified_at FROM users WHERE id=?", user_id)
    if blocked and blocked["phone"] and blocked["phone_verified_at"]:  # #24
        db.run("INSERT OR IGNORE INTO blocked_phones (phone, user_id) VALUES (?, ?)", blocked["phone"], user_id)
    take_off_sale([r["id"] for r in db.all("SELECT id FROM listings WHERE seller_id=? AND status='active'", user_id)])
    for o in db.all("SELECT id FROM orders WHERE buyer_id=? AND status='pending'", user_id):
        db.run("UPDATE orders SET status='cancelled' WHERE id=?", o["id"])
        db.run("UPDATE tickets SET status='available', order_id=NULL WHERE order_id=? AND status='held'", o["id"])
    db.run("DELETE FROM push_subs WHERE user_id=?", user_id)


def claim_refund(order):
    """#26: marks the order as being refunded; False if another request got there first (or it's not refundable)."""
    return bool(db.count("UPDATE orders SET status='refunding' WHERE id=? AND status IN ('paid', 'disputed')", order["id"]))


def refund_payment(order):
    """Refunds through Stripe after claim_refund(); puts the order back as it was if Stripe fails."""
    try:
        payments.refund(order)
    except Exception:
        db.run("UPDATE orders SET status=? WHERE id=? AND status='refunding'", order["status"], order["id"])
        raise
    db.run("UPDATE orders SET status='refunded' WHERE id=?", order["id"])


def refund_order(order, reason_key, ticket_status="void"):
    """Refunds an order in full (ticket + fee) and tells both sides. False if it was already being refunded or paid out."""
    if not claim_refund(order):
        return False
    refund_payment(order)
    db.run("UPDATE tickets SET status=?, order_id=CASE WHEN ?='available' THEN NULL ELSE order_id END WHERE order_id=?",
           ticket_status, ticket_status, order["id"])
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    title = listing_title(listing)
    path = url_for("order_page", public_id=order["public_id"])
    tell(order["buyer_id"], t("mail_refund_subject", title=title),
         t(reason_key + "_buyer", title=title, amount=money(order["total_cents"])), path)
    tell(order["seller_id"], t("mail_refund_subject_seller", title=title), t(reason_key + "_seller", title=title), path)
    return True


def cancel_event(event_id):
    e = db.one("SELECT * FROM events WHERE id=?", event_id)
    listing_ids = [r["id"] for r in db.all("SELECT id FROM listings WHERE event_id=?", event_id)]
    refunded = 0
    for lid in listing_ids:
        for o in db.all("SELECT * FROM orders WHERE listing_id=? AND status IN ('paid', 'disputed')", lid):
            if refund_order(o, "refund_cancelled"):
                refunded += 1
        for o in db.all("SELECT * FROM orders WHERE listing_id=? AND status='pending'", lid):
            db.run("UPDATE orders SET status='cancelled' WHERE id=?", o["id"])
            if o["payment_ref"]:
                payments.expire_checkout(o["payment_ref"])
        db.run("UPDATE tickets SET status='void' WHERE listing_id=? AND status IN ('available', 'held')", lid)
    db.run("UPDATE listings SET status='removed' WHERE event_id=? AND status='active'", event_id)
    db.run("UPDATE events SET cancelled_at=? WHERE id=?", sqltime(utcnow()), event_id)
    return refunded


def notify_event_change(event_id, note):
    """After a date/time change: tell ticket holders and sellers; buyers may ask for a refund."""
    e = db.one("SELECT * FROM events WHERE id=?", event_id)
    db.run("UPDATE events SET changed_at=?, change_note=? WHERE id=?", sqltime(utcnow()), note, event_id)
    for o in db.all("""SELECT o.* FROM orders o JOIN listings l ON l.id = o.listing_id
                       WHERE l.event_id=? AND o.status IN ('paid', 'disputed')""", event_id):
        db.run("UPDATE orders SET payout_due_at=? WHERE id=?", sqltime(payout_due_for(e["event_date"], o["seller_id"])), o["id"])
        path = url_for("order_page", public_id=o["public_id"])
        tell(o["buyer_id"], t("mail_changed_subject", title=e["name"]), t("mail_changed_buyer", title=e["name"], note=note), path)
        tell(o["seller_id"], t("mail_changed_subject", title=e["name"]), t("mail_changed_seller", title=e["name"], note=note), path)


@app.route("/orders/<public_id>/change-refund", methods=["POST"])
@login_required
def change_refund(public_id):
    order = get_order_for_user(public_id)
    listing = db.one("SELECT * FROM listings WHERE id=?", order["listing_id"])
    e = db.one("SELECT * FROM events WHERE id=?", listing["event_id"]) if listing["event_id"] else None
    if (order["buyer_id"] != g.user["id"] or order["status"] != "paid" or not e or not e["changed_at"]
            or not order["paid_at"] or e["changed_at"] < order["paid_at"] or e["event_date"] < today().isoformat()):
        abort(400)
    # The ticket is still valid for the new date, so it goes back on sale for the seller.
    if not refund_order(order, "refund_changed", ticket_status="available"):
        abort(400)  # already being refunded or paid out
    db.run("UPDATE listings SET status='active' WHERE id=? AND status='sold'", listing["id"])
    flash(t("change_refund_done", amount=money(order["total_cents"])), "ok")
    return redirect(url_for("order_page", public_id=public_id))


@app.route("/t/<int:listing_id>/report", methods=["GET", "POST"])
def report_listing(listing_id):
    l = listing_with_seller(listing_id)
    if not l or l["status"] == "removed":
        abort(404)
    reasons = ["fake", "wrong_event", "price", "abuse", "other"]
    error = None
    if request.method == "POST":
        reason = request.form.get("reason")
        email = request.form.get("email", "").strip().lower()
        details = request.form.get("details", "").strip()[:1000]
        if reason not in reasons:
            error = t("err_report_reason")
        elif not g.user and not EMAIL_RE.match(email):
            error = t("err_email")
        elif rate_limited("report", 10, 60):
            error = t("err_too_many_tries")
        else:
            db.run("INSERT INTO reports (listing_id, reporter_id, email, reason, details) VALUES (?, ?, ?, ?, ?)",
                   listing_id, g.user["id"] if g.user else None, None if g.user else email, reason, details or None)
            notify.notify_admins("Spare: listing reported (%s)" % reason,
                                 "%s\n\n%s" % (details, base_url() + url_for("admin")))
            flash(t("report_listing_thanks"), "ok")
            return redirect(url_for("listing_page", listing_id=listing_id))
    return render_template("report_listing.html", l=l, reasons=reasons, error=error)


ADMIN_OK_HOURS = 12  # how long the authenticator check lasts


def admin_ok():
    session["admin_ok_until"] = (utcnow() + timedelta(hours=ADMIN_OK_HOURS)).timestamp()


@app.route("/admin/verify", methods=["GET", "POST"])
@login_required
def admin_verify():
    """#23: admins confirm a code from their authenticator app every 12 hours (or use a one-time recovery code)."""
    if not g.is_admin:
        abort(404)
    nxt = safe_next(request.values.get("next"), url_for("admin"))
    if not g.user["totp_secret"]:
        return redirect(url_for("admin_totp_setup", next=nxt))
    error = None
    if request.method == "POST":
        entered = request.form.get("code", "").strip()
        if rate_limited("admin-verify", 10, 60, g.user["id"]):
            error = t("err_too_many_tries")
        elif "-" in entered or re.search(r"[a-z]", entered.lower()):
            hashes = json.loads(g.user["totp_recovery"] or "[]")
            h = totp.hash_recovery(entered)
            if h in hashes:  # a recovery code works once
                hashes.remove(h)
                db.run("UPDATE users SET totp_recovery=? WHERE id=?", json.dumps(hashes), g.user["id"])
                notify.notify_admins("Spare: an admin recovery code was used",
                                     "%s used a recovery code to open /admin. %d left. If this wasn't you, block the account "
                                     "and reset its authenticator: flask --app app reset-admin-2fa <email>" % (g.user["email"], len(hashes)))
                admin_ok()
                flash("Recovery code used. %d left: set up your authenticator again if you lost your phone." % len(hashes), "ok")
                return redirect(nxt)
            error = t("err_code")
        else:
            step = totp.verify(totp.unseal(g.user["totp_secret"], app.config["SECRET_KEY"]), entered, g.user["totp_last_step"])
            if step is not None:
                db.run("UPDATE users SET totp_last_step=? WHERE id=?", step, g.user["id"])
                admin_ok()
                return redirect(nxt)
            error = t("err_code")
    return render_template("admin_verify.html", next=nxt, error=error)


@app.route("/admin/authenticator", methods=["GET", "POST"])
@login_required
def admin_totp_setup():
    """#23: link an authenticator app. Replacing an existing one needs a verified admin session."""
    if not g.is_admin:
        abort(404)
    nxt = safe_next(request.values.get("next"), url_for("admin"))
    if g.user["totp_secret"] and session.get("admin_ok_until", 0) < utcnow().timestamp():
        return redirect(url_for("admin_verify", next=url_for("admin_totp_setup")))
    if request.method == "GET" or "totp_pending" not in session:
        session["totp_pending"] = totp.seal(totp.new_secret(), app.config["SECRET_KEY"])
    secret = totp.unseal(session["totp_pending"], app.config["SECRET_KEY"])
    error = None
    if request.method == "POST":
        step = totp.verify(secret, request.form.get("code", ""))
        if rate_limited("admin-verify", 10, 60, g.user["id"]):
            error = t("err_too_many_tries")
        elif step is None:
            error = t("err_code")
        else:
            codes = totp.recovery_codes()
            db.run("UPDATE users SET totp_secret=?, totp_last_step=?, totp_recovery=? WHERE id=?",
                   session.pop("totp_pending"), step, json.dumps([totp.hash_recovery(c) for c in codes]), g.user["id"])
            notify.notify_admins("Spare: authenticator set up for %s" % g.user["email"],
                                 "An authenticator app was just linked to %s for the admin area. If this wasn't you, "
                                 "reset it straight away: flask --app app reset-admin-2fa <email>" % g.user["email"])
            admin_ok()
            return render_template("admin_totp_codes.html", codes=codes, next=nxt)
    return render_template("admin_totp_setup.html", secret=secret, qr=qr_svg(totp.uri(secret, g.user["email"])),
                           next=nxt, error=error, replacing=bool(g.user["totp_secret"]))


@app.route("/admin/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip().lower()[:80]
    rows = []
    if q:
        like = "%" + q + "%"
        rows = db.all("""SELECT u.*,
                           (SELECT COUNT(*) FROM listings l WHERE l.seller_id = u.id) AS listings,
                           (SELECT COUNT(*) FROM orders o WHERE o.seller_id = u.id AND o.status IN ('paid','released','disputed')) AS sales,
                           (SELECT COUNT(*) FROM orders o WHERE o.buyer_id = u.id AND o.status IN ('paid','released','disputed')) AS buys,
                           (SELECT COUNT(*) FROM messages m WHERE m.sender_id = u.id AND m.flags IS NOT NULL) AS flagged,
                           (SELECT COUNT(*) FROM reports r JOIN listings l ON l.id = r.listing_id WHERE l.seller_id = u.id) AS reported
                         FROM users u WHERE lower(u.email) LIKE ? OR lower(COALESCE(u.first_name, '')) LIKE ?
                           OR COALESCE(u.phone, '') LIKE ? ORDER BY u.id DESC LIMIT 50""", like, like, like)
    blocked = db.all("SELECT * FROM users WHERE blocked_at IS NOT NULL ORDER BY blocked_at DESC LIMIT 50")
    phones = db.all("SELECT * FROM blocked_phones ORDER BY created_at DESC LIMIT 100")
    held = db.all("SELECT * FROM users WHERE bank_flagged_at IS NOT NULL ORDER BY bank_flagged_at DESC")
    return render_template("admin_users.html", rows=rows, q=q, blocked=blocked, phones=phones, held=held)


@app.route("/admin/listings/<int:listing_id>/<action>", methods=["POST"])
@admin_required
def admin_listing_review(listing_id, action):
    """Approve or reject a listing that waited for a check (#25 face value, #29 unreadable code or app ticket)."""
    l = db.one("SELECT * FROM listings WHERE id=? AND status='review'", listing_id)
    if not l:
        abort(404)
    seller = db.one("SELECT * FROM users WHERE id=?", l["seller_id"])
    if action == "approve":
        db.run("UPDATE listings SET status='active', review_reason=NULL, face_checked=1 WHERE id=?", listing_id)
        tell(seller["id"], t("mail_listing_live_subject", title=listing_title(l)), t("mail_listing_live_body", title=listing_title(l)),
             url_for("listing_page", listing_id=listing_id))
        notify_alerts(db.one("SELECT * FROM listings WHERE id=?", listing_id))
        flash("Approved: the listing is live.", "ok")
    elif action == "reject":
        why = request.form.get("why", "").strip()[:500]
        if not why:
            flash("Write the reason: the seller gets it by email (EU rules).", "error")
            return redirect(url_for("admin") + "#review")
        db.run("UPDATE tickets SET status='void' WHERE listing_id=? AND status='available'", listing_id)
        db.run("UPDATE listings SET status='removed' WHERE id=?", listing_id)
        send_statement_of_reasons(seller, t("dsa_what_listing_refused", title=listing_title(l)), why)
        flash("Rejected; the seller was told why.", "ok")
    else:
        abort(400)
    return redirect(url_for("admin") + "#review")


@app.route("/admin/tickets/<int:ticket_id>/file")
@admin_required
def admin_ticket_file(ticket_id):
    tk = db.one("SELECT * FROM tickets WHERE id=?", ticket_id)
    if not tk or not tk["file"]:
        abort(404)
    ext = tk["file"].rsplit(".", 1)[-1]
    return send_file(os.path.join(config.UPLOAD_DIR, tk["file"]), mimetype=FILE_TYPES[ext][1])


@app.route("/admin/proofs/<int:proof_id>")
@admin_required
def admin_proof(proof_id):
    p = db.one("SELECT * FROM proofs WHERE id=?", proof_id)
    if not p:
        abort(404)
    ext = p["file"].rsplit(".", 1)[-1]
    return send_file(os.path.join(config.UPLOAD_DIR, "proofs", p["file"]), mimetype=FILE_TYPES[ext][1])


@app.route("/admin/blocked-phones/remove", methods=["POST"])
@admin_required
def admin_unblock_phone():
    db.run("DELETE FROM blocked_phones WHERE phone=?", request.form.get("phone", ""))
    flash("That number can be verified again.", "ok")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/<action>", methods=["POST"])
@admin_required
def admin_user_action(user_id, action):
    u = db.one("SELECT * FROM users WHERE id=?", user_id)
    if not u or u["email"] in config.ADMIN_EMAILS:
        abort(404)
    if action == "block":
        reason = request.form.get("reason", "").strip()
        if not reason:
            flash("Write the reason: the user gets it by email (EU rules).", "error")
            return redirect(request.referrer or url_for("admin_users"))
        block_user(user_id, reason)
        send_statement_of_reasons(u, t("dsa_what_account"), reason)
        flash("Blocked %s. Their listings are off sale and their payouts are on hold." % u["email"], "ok")
    elif action == "unblock":
        db.run("UPDATE users SET blocked_at=NULL, blocked_reason=NULL WHERE id=?", user_id)
        flash("Unblocked %s. Their phone number stays blocked until you remove it below." % u["email"], "ok")
    elif action == "clear_bank_flag":
        db.run("UPDATE users SET bank_flagged_at=NULL WHERE id=?", user_id)
        flash("Payouts to %s are no longer held." % u["email"], "ok")
    else:
        abort(400)
    return redirect(request.referrer or url_for("admin_users"))


@app.route("/admin/reports/<int:report_id>/<action>", methods=["POST"])
@admin_required
def admin_report_action(report_id, action):
    r = db.one("SELECT r.*, l.seller_id FROM reports r JOIN listings l ON l.id = r.listing_id WHERE r.id=?", report_id)
    if not r:
        abort(404)
    why = request.form.get("why", "").strip()[:500]
    if action in ("remove", "block") and not why:
        flash("Write the reason: the seller gets it by email (EU rules).", "error")
        return redirect(url_for("admin") + "#reports")
    seller = db.one("SELECT * FROM users WHERE id=?", r["seller_id"])
    listing = db.one("SELECT * FROM listings WHERE id=?", r["listing_id"])
    if action == "remove":
        take_off_sale([r["listing_id"]])
        send_statement_of_reasons(seller, t("dsa_what_listing", title=listing_title(listing)), why)
    elif action == "block":
        block_user(r["seller_id"], "Reported listing: %s. %s" % (r["reason"], why))
        send_statement_of_reasons(seller, t("dsa_what_account_listing", title=listing_title(listing)), why)
    elif action != "close":
        abort(400)
    db.run("UPDATE reports SET status='closed' WHERE id=? OR (listing_id=? AND ? != 'close')", report_id, r["listing_id"], action)
    flash("Done.", "ok")
    return redirect(url_for("admin"))


# --------------------------------------------------------------------------
# Your data (GDPR): export and delete
# --------------------------------------------------------------------------

@app.route("/account/data")
@login_required
def account_data():
    open_sales = db.one("SELECT COUNT(*) AS n FROM orders WHERE seller_id=? AND status IN ('paid', 'disputed')", g.user["id"])["n"]
    upcoming = db.one("""SELECT COUNT(*) AS n FROM orders o JOIN listings l ON l.id = o.listing_id
                         WHERE o.buyer_id=? AND o.status IN ('paid', 'disputed') AND l.event_date >= ?""",
                      g.user["id"], today().isoformat())["n"]
    return render_template("account_data.html", open_sales=open_sales, upcoming=upcoming)


@app.route("/account/export")
@login_required
def account_export():
    uid = g.user["id"]
    rows = lambda sql, *a: [dict(r) for r in db.all(sql, *a)]  # noqa: E731
    data = {
        "exported_at": sqltime(utcnow()),
        "account": {k: g.user[k] for k in ("email", "first_name", "phone", "phone_verified_at", "student_email",
                                          "student_school", "student_verified_at", "created_at")},
        "listings": rows("""SELECT id, venue, city, event_name, event_date, face_value_cents, price_cents, status, created_at
                            FROM listings WHERE seller_id=?""", uid),
        "purchases": rows("""SELECT public_id, listing_id, quantity, price_cents, fee_cents, total_cents, status, paid_at, rating
                             FROM orders WHERE buyer_id=?""", uid),
        "sales": rows("""SELECT public_id, listing_id, quantity, price_cents, status, paid_at, released_at
                         FROM orders WHERE seller_id=?""", uid),
        "messages_sent": rows("SELECT thread_id, kind, body, created_at FROM messages WHERE sender_id=?", uid),
        "alerts": rows("SELECT venue, event_date, event_name, created_at FROM alerts WHERE user_id=?", uid),
        "reports_made": rows("SELECT listing_id, reason, details, created_at FROM reports WHERE reporter_id=?", uid),
    }
    resp = app.response_class(json.dumps(data, indent=2, ensure_ascii=False), mimetype="application/json")
    resp.headers["Content-Disposition"] = 'attachment; filename="spare-my-data.json"'
    return resp


@app.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    uid = g.user["id"]
    if request.form.get("confirm", "").strip().lower() != g.user["email"]:
        flash(t("err_delete_confirm"), "error")
        return redirect(url_for("account_data"))
    if db.one("SELECT 1 FROM orders WHERE seller_id=? AND status IN ('paid', 'disputed', 'releasing', 'refunding')", uid):
        flash(t("err_delete_open_sales"), "error")
        return redirect(url_for("account_data"))
    take_off_sale([r["id"] for r in db.all("SELECT id FROM listings WHERE seller_id=? AND status='active'", uid)])
    for o in db.all("SELECT id FROM orders WHERE buyer_id=? AND status='pending'", uid):
        db.run("UPDATE orders SET status='cancelled' WHERE id=?", o["id"])
        db.run("UPDATE tickets SET status='available', order_id=NULL WHERE order_id=? AND status='held'", o["id"])
    # Orders stay (bookkeeping law requires keeping them for 7 years) but are no longer linked to a person:
    # only amounts, dates and order numbers are kept. Everything else personal goes (#18).
    u = g.user
    db.run("""UPDATE users SET email=?, first_name=?, phone=NULL, phone_verified_at=NULL, student_email=NULL,
              student_school=NULL, student_verified_at=NULL, avatar=NULL, source=NULL, deleted_at=? WHERE id=?""",
           "deleted-%d@deleted.invalid" % uid, t("deleted_user"), sqltime(utcnow()), uid)
    db.run("DELETE FROM push_subs WHERE user_id=?", uid)
    db.run("DELETE FROM alerts WHERE user_id=?", uid)
    db.run("DELETE FROM codes WHERE target IN (?, ?, ?)", u["email"], u["phone"] or "", u["student_email"] or "")
    db.run("DELETE FROM waitlist WHERE email=?", u["email"])
    db.run("DELETE FROM hits WHERE key LIKE ?", "%:" + u["email"])  # rate-limit counters keyed by email
    db.run("UPDATE reports SET email=NULL, details=NULL WHERE reporter_id=? OR email=?", uid, u["email"])
    # Chat messages they wrote (including the original text of blocked ones) become a "message removed" note.
    db.run("""UPDATE messages SET kind='system', body='sys_message_removed', raw_body=NULL, flags=NULL
              WHERE sender_id=? AND kind='text'""", uid)
    db.run("UPDATE orders SET transfer_name=NULL, transfer_email=NULL, dispute_reason=NULL WHERE buyer_id=?", uid)
    delete_proofs(db.all("""SELECT p.* FROM proofs p WHERE p.user_id=? AND NOT EXISTS (
                              SELECT 1 FROM orders x WHERE (x.listing_id = p.listing_id OR x.id = p.order_id)
                              AND x.status IN ('pending', 'paid', 'disputed', 'releasing'))""", uid))
    # Their ticket files (which often show their full name), for sales that are finished and tickets that never sold.
    for row in db.all("""SELECT DISTINCT t.file FROM tickets t JOIN listings l ON l.id = t.listing_id
                         LEFT JOIN orders o ON o.id = t.order_id
                         WHERE l.seller_id=? AND t.file != ''
                           AND (o.id IS NULL OR o.status IN ('released', 'refunded', 'expired', 'cancelled'))""", uid):
        if not db.one("""SELECT 1 FROM tickets t JOIN orders o ON o.id = t.order_id
                         WHERE t.file=? AND o.status IN ('pending', 'paid', 'disputed')""", row["file"]):
            db.run("UPDATE tickets SET file='' WHERE file=?", row["file"])
            try:
                os.remove(os.path.join(config.UPLOAD_DIR, row["file"]))
            except FileNotFoundError:
                pass
    end_all_sessions(uid)
    fresh_session()
    flash(t("account_deleted"), "ok")
    return redirect(url_for("index"))


@app.route("/admin/orders/<public_id>/<action>", methods=["POST"])
@admin_required
def admin_order(public_id, action):
    if action == "payouts":
        released, waiting = release_due_payouts()
        flash("Released %d payouts, %d waiting for seller payout setup." % (released, waiting), "ok")
        return redirect(url_for("admin"))
    order = db.one("SELECT * FROM orders WHERE public_id=?", public_id)
    if not order:
        abort(404)
    if action == "refund" and claim_refund(order):
        refund_payment(order)
        # Treat the seller's other tickets for this listing as suspect too.
        db.run("UPDATE tickets SET status='void' WHERE order_id=? OR (listing_id=? AND status='available')",
               order["id"], order["listing_id"])
        db.run("UPDATE listings SET status='removed' WHERE id=?", order["listing_id"])
        flash("Refunded %s" % money(order["total_cents"]), "ok")
    elif action == "refund_after_payout" and order["status"] == "released":
        # #27: the ticket turned out to be fake after the seller was paid: take the payout back, refund the buyer,
        # void the tickets and block the seller.
        why = request.form.get("why", "").strip()[:500]
        if not why:
            flash("Write the reason: the seller gets it by email (EU rules).", "error")
            return redirect(url_for("admin") + "#recent")
        if not db.count("UPDATE orders SET status='refunding' WHERE id=? AND status='released'", order["id"]):
            return redirect(url_for("admin"))
        reversed_ok = take_back_payout(order)
        try:
            payments.refund(order)
        except Exception:
            db.run("UPDATE orders SET status='released' WHERE id=?", order["id"])
            raise
        db.run("UPDATE orders SET status='refunded' WHERE id=?", order["id"])
        db.run("UPDATE tickets SET status='void' WHERE order_id=? OR (listing_id=? AND status='available')", order["id"], order["listing_id"])
        db.run("UPDATE listings SET status='removed' WHERE id=?", order["listing_id"])
        seller = db.one("SELECT * FROM users WHERE id=?", order["seller_id"])
        if not seller["blocked_at"]:
            block_user(seller["id"], "Refunded after payout: %s" % why)
            send_statement_of_reasons(seller, t("dsa_what_account"), why)
        flash("Refunded %s to the buyer%s; seller blocked." % (money(order["total_cents"]),
              "" if reversed_ok else " (taking back the payout FAILED: check Stripe)"), "ok")
    elif action == "keep" and order["status"] == "disputed":
        db.run("UPDATE orders SET status='paid' WHERE id=?", order["id"])
        flash("Dispute closed, seller will be paid on the payout date.", "ok")
    return redirect(url_for("admin"))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

@app.route("/tasks/hourly", methods=["POST"])
def hourly_tasks():
    """Payouts and reminders. Called every hour by a cron service with the TASK_SECRET."""
    sent = request.headers.get("X-Task-Secret", "")
    if not config.TASK_SECRET or not hmac.compare_digest(sent, config.TASK_SECRET):
        abort(404)
    release_stuck_payouts()
    released, waiting = release_due_payouts()
    nudged, asked = send_reminders()
    resell = send_resell_reminders()
    transfer_reminders = send_transfer_reminders()
    send_buyer_transfer_reminders()
    backed_up = nightly_backup()
    files_deleted = cleanup_ticket_files()
    check_disk()
    db.run("DELETE FROM hits WHERE created_at < ?", sqltime(utcnow() - timedelta(days=1)))
    db.run("DELETE FROM codes WHERE expires_at < ?", sqltime(utcnow()))  # expired sign-in codes (privacy policy)
    return {"payouts_released": released, "payouts_waiting": waiting, "nudges": nudged, "rating_requests": asked,
            "resell_reminders": resell, "transfer_reminders": transfer_reminders, "backup": backed_up,
            "ticket_files_deleted": files_deleted}


@app.cli.command("init-db")
def init_db_command():
    """Create the database tables."""
    db.init()
    click.echo("Database ready at %s" % config.DATABASE)


@app.cli.command("export-starter")
def export_starter_command():
    """Save the current events and venue photos as starter data for a new (live) database."""
    import starter
    events, venues = starter.export()
    click.echo("Saved %d events and %d venues to starter_data/" % (events, venues))


@app.cli.command("release-payouts")
def release_payouts_command():
    """Pay sellers whose payout date has passed. Run this hourly (cron)."""
    with app.test_request_context(base_url=config.BASE_URL or "http://localhost:5001"):
        released, waiting = release_due_payouts()
    click.echo("Released %d, waiting for seller setup %d" % (released, waiting))


@app.cli.command("send-reminders")
def send_reminders_command():
    """Day-of-event price nudges and post-event rating requests. Run this hourly (cron)."""
    with app.test_request_context(base_url=config.BASE_URL or "http://localhost:5001"):
        nudged, asked = send_reminders()
        resell = send_resell_reminders()
    click.echo("Price nudges %d, rating requests %d, resell reminders %d" % (nudged, asked, resell))


@app.cli.command("test-email")
@click.argument("to")
def test_email_command(to):
    """Send one test email, to check the SMTP settings."""
    if not config.SMTP_HOST:
        raise click.ClickException("SMTP_HOST is not set.")
    with app.test_request_context(base_url=config.BASE_URL or "http://localhost:5001"):
        ok = notify.send_email(to, "Spare test email", "If you can read this, email sending works.")
    if not ok:
        raise click.ClickException("Sending failed, see the error above.")
    click.echo("Sent to %s from %s" % (to, config.MAIL_FROM))


@app.cli.command("backup-key")
def backup_key_command():
    """Print a new BACKUP_KEY. Put it on Render's Environment page and in a password manager."""
    click.echo(backup.new_key())


@app.cli.command("decrypt-backup")
@click.argument("path")
@click.option("--key", envvar="BACKUP_KEY", prompt="BACKUP_KEY", hide_input=True, help="defaults to BACKUP_KEY from .env")
def decrypt_backup_command(path, key):
    """Turn an encrypted backup (.zip.enc) back into a normal zip next to it."""
    with open(path, "rb") as fh:
        data = backup.unseal(fh.read(), key)
    out = path[:-4] if path.endswith(".enc") else path + ".zip"
    with open(out, "wb") as fh:
        fh.write(data)
    click.echo("Saved " + out)


@app.cli.command("reset-admin-2fa")
@click.argument("email")
def reset_admin_2fa_command(email):
    """Unlink an admin's authenticator app (lost phone and recovery codes). They set it up again at the next sign-in."""
    n = db.count("UPDATE users SET totp_secret=NULL, totp_last_step=NULL, totp_recovery=NULL, "
                 "session_version=session_version+1 WHERE email=?", email.strip().lower())
    click.echo("Reset." if n else "No account with that email.")


@app.cli.command("seed")
def seed_command():
    """Fill a development database with sample listings."""
    if not config.DEV:
        raise click.ClickException("Refusing to seed in production.")
    import seed
    seed.run()
    click.echo("Sample data added.")


with app.app_context():
    db.init()
    for _venue in config.VENUES:
        get_venue(_venue, config.DEFAULT_CITY, create=True)


if __name__ == "__main__":
    app.run(debug=config.DEV)
