PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id                  INTEGER PRIMARY KEY,
  email               TEXT NOT NULL UNIQUE,
  first_name          TEXT,
  phone               TEXT,
  phone_verified_at   TEXT,
  student_email       TEXT,
  student_school      TEXT,
  student_verified_at TEXT,
  stripe_account_id   TEXT,
  payouts_ready       INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One-time codes for email sign-in, phone and student email verification.
CREATE TABLE IF NOT EXISTS codes (
  id         INTEGER PRIMARY KEY,
  purpose    TEXT NOT NULL,            -- 'login' | 'phone' | 'student'
  target     TEXT NOT NULL,            -- email address or phone number
  code_hash  TEXT NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS codes_target ON codes (purpose, target);

-- A listing is one seller's offer for one event: one or more tickets at the same price.
CREATE TABLE IF NOT EXISTS listings (
  id               INTEGER PRIMARY KEY,
  seller_id        INTEGER NOT NULL REFERENCES users (id),
  venue            TEXT NOT NULL,
  event_name       TEXT,
  event_date       TEXT NOT NULL,      -- YYYY-MM-DD, the night of the event
  face_value_cents INTEGER NOT NULL,   -- per ticket
  price_cents      INTEGER NOT NULL,   -- per ticket, what the seller receives
  poster_file      TEXT,               -- optional public event poster inside uploads/posters/
  sold_together    INTEGER NOT NULL DEFAULT 0,  -- 1 when several tickets share one file and can't be split
  category         TEXT NOT NULL DEFAULT 'club',    -- student | club | festival | concert
  city             TEXT NOT NULL DEFAULT 'Rotterdam',
  start_time       TEXT,               -- optional HH:MM, used for the "starts in" countdown
  status           TEXT NOT NULL DEFAULT 'active',  -- active | sold | removed
  nudged_at        TEXT,               -- day-of-event price reminder sent
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS listings_browse ON listings (status, event_date);

CREATE TABLE IF NOT EXISTS tickets (
  id         INTEGER PRIMARY KEY,
  listing_id INTEGER NOT NULL REFERENCES listings (id),
  file       TEXT NOT NULL,            -- file name inside uploads/
  file_hash  TEXT NOT NULL,            -- sha256 of the file
  code_hash  TEXT,                     -- sha256 of the QR/barcode on the ticket, when it could be read
  code_text  TEXT,                     -- the code itself, for Apple Wallet passes
  code_format TEXT,                    -- 'QR', 'CODE_128', ...
  status     TEXT NOT NULL DEFAULT 'available',  -- available | held | sold | void
  order_id   INTEGER REFERENCES orders (id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS tickets_listing ON tickets (listing_id, status);
CREATE INDEX IF NOT EXISTS tickets_file_hash ON tickets (file_hash);
CREATE INDEX IF NOT EXISTS tickets_code_hash ON tickets (code_hash);
CREATE INDEX IF NOT EXISTS tickets_order ON tickets (order_id);

CREATE TABLE IF NOT EXISTS orders (
  id              INTEGER PRIMARY KEY,
  public_id       TEXT NOT NULL UNIQUE,
  listing_id      INTEGER NOT NULL REFERENCES listings (id),
  buyer_id        INTEGER NOT NULL REFERENCES users (id),
  seller_id       INTEGER NOT NULL REFERENCES users (id),
  quantity        INTEGER NOT NULL DEFAULT 1,
  offer_id        INTEGER,             -- set when bought at an accepted offer price
  price_cents     INTEGER NOT NULL,    -- all tickets in the order
  fee_cents       INTEGER NOT NULL,
  total_cents     INTEGER NOT NULL,
  -- pending -> paid -> released          (normal)
  -- pending -> expired | cancelled       (not paid in time / buyer cancelled)
  -- paid    -> disputed -> refunded | paid
  status          TEXT NOT NULL DEFAULT 'pending',
  payment_ref     TEXT,                -- Stripe Checkout Session id
  payment_intent  TEXT,
  transfer_ref    TEXT,
  dispute_reason  TEXT,
  rating          TEXT,                -- 'up' | 'down': did the ticket work?
  rated_at        TEXT,
  rating_asked_at TEXT,
  expires_at      TEXT NOT NULL,
  paid_at         TEXT,
  payout_due_at   TEXT,
  released_at     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS orders_listing ON orders (listing_id, status);
CREATE INDEX IF NOT EXISTS orders_buyer ON orders (buyer_id);
CREATE INDEX IF NOT EXISTS orders_seller ON orders (seller_id);

-- "Notify me when tickets come up" for one event.
CREATE TABLE IF NOT EXISTS alerts (
  id               INTEGER PRIMARY KEY,
  user_id          INTEGER NOT NULL REFERENCES users (id),
  venue            TEXT NOT NULL,
  event_date       TEXT NOT NULL,
  event_name       TEXT NOT NULL DEFAULT '',
  last_notified_at TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, venue, event_date, event_name)
);
CREATE INDEX IF NOT EXISTS alerts_event ON alerts (event_date, venue);

-- Browser push subscriptions (one per device).
CREATE TABLE IF NOT EXISTS push_subs (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users (id),
  endpoint   TEXT NOT NULL UNIQUE,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS push_subs_user ON push_subs (user_id);

-- Conversations: one per (listing, buyer). Before payment the buyer can only send quick questions
-- and offers; after payment (order_id set) both sides can chat freely. Contact details are masked.
CREATE TABLE IF NOT EXISTS threads (
  id         INTEGER PRIMARY KEY,
  listing_id INTEGER NOT NULL REFERENCES listings (id),
  buyer_id   INTEGER NOT NULL REFERENCES users (id),
  seller_id  INTEGER NOT NULL REFERENCES users (id),
  order_id   INTEGER REFERENCES orders (id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (listing_id, buyer_id)
);
CREATE INDEX IF NOT EXISTS threads_seller ON threads (seller_id, updated_at);
CREATE INDEX IF NOT EXISTS threads_buyer ON threads (buyer_id, updated_at);

CREATE TABLE IF NOT EXISTS offers (
  id           INTEGER PRIMARY KEY,
  thread_id    INTEGER NOT NULL REFERENCES threads (id),
  listing_id   INTEGER NOT NULL REFERENCES listings (id),
  buyer_id     INTEGER NOT NULL REFERENCES users (id),
  amount_cents INTEGER NOT NULL,       -- per ticket, what the seller would receive
  quantity     INTEGER NOT NULL DEFAULT 1,
  status       TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | declined | withdrawn | used
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  responded_at TEXT
);
CREATE INDEX IF NOT EXISTS offers_thread ON offers (thread_id, status);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY,
  thread_id  INTEGER NOT NULL REFERENCES threads (id),
  sender_id  INTEGER REFERENCES users (id),   -- NULL for system notes
  kind       TEXT NOT NULL,                   -- text | question | offer | offer_accepted | offer_declined | system
  body       TEXT NOT NULL,                   -- what people see (contact details masked)
  raw_body   TEXT,                            -- original text, only kept when something was masked or flagged
  flags      TEXT,                            -- e.g. "phone,off_platform"
  offer_id   INTEGER REFERENCES offers (id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  read_at    TEXT
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages (thread_id, id);
CREATE INDEX IF NOT EXISTS messages_flagged ON messages (sender_id, flags);

-- Venue pages: photo, address and colour. Created automatically when a ticket for a new venue is posted.
CREATE TABLE IF NOT EXISTS venues (
  id          INTEGER PRIMARY KEY,
  slug        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  city        TEXT NOT NULL,
  address     TEXT,
  website     TEXT,
  description TEXT,
  photo_file  TEXT,                   -- inside uploads/posters/
  photo_credit TEXT,                  -- "Photographer name"
  photo_license TEXT,                 -- "CC BY-SA 4.0", "Own photo", ...
  photo_license_url TEXT,
  photo_source_url TEXT,              -- e.g. the Wikimedia Commons file page
  colour      TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Real events. Sellers pick one when posting; events without tickets still get a page ("notify me").
-- status: published (visible) | review (added by a seller, visible but waiting for an admin check) | hidden
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  venue       TEXT NOT NULL,
  city        TEXT NOT NULL,
  category    TEXT NOT NULL DEFAULT 'club',
  event_date  TEXT NOT NULL,           -- YYYY-MM-DD, the night it starts
  door_time   TEXT,                    -- HH:MM
  end_time    TEXT,                    -- HH:MM
  min_age     INTEGER,
  ticket_url  TEXT,                    -- official ticket shop
  description TEXT,
  poster_file TEXT,
  poster_credit TEXT,                  -- who provided the poster (organiser), shown with it
  source_url  TEXT,                    -- where the info came from (import or research)
  status      TEXT NOT NULL DEFAULT 'published',
  created_by  INTEGER REFERENCES users (id),
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS events_date ON events (event_date, status);
CREATE UNIQUE INDEX IF NOT EXISTS events_unique ON events (event_date, lower(venue), lower(name));

-- Posters sent in by organisers: shown only after an admin approves them.
CREATE TABLE IF NOT EXISTS poster_submissions (
  id           INTEGER PRIMARY KEY,
  event_id     INTEGER NOT NULL REFERENCES events (id),
  user_id      INTEGER NOT NULL REFERENCES users (id),
  file         TEXT NOT NULL,           -- inside uploads/posters/
  organisation TEXT NOT NULL,
  role         TEXT,
  credit       TEXT,                    -- e.g. "Poster: Exchange Party" or a designer's name
  status       TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- "Report this listing" (EU Digital Services Act: anyone can flag illegal or fraudulent content).
CREATE TABLE IF NOT EXISTS reports (
  id          INTEGER PRIMARY KEY,
  listing_id  INTEGER REFERENCES listings (id),
  reporter_id INTEGER REFERENCES users (id),
  email       TEXT,                    -- for reporters who aren't signed in
  reason      TEXT NOT NULL,           -- fake | wrong_event | price | abuse | other
  details     TEXT,
  status      TEXT NOT NULL DEFAULT 'open',   -- open | closed
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Rate limiting: one row per attempt, cleaned up as it goes.
CREATE TABLE IF NOT EXISTS hits (
  id         INTEGER PRIMARY KEY,
  key        TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS hits_key ON hits (key, created_at);
