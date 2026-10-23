# Nightswap

Student-to-student club ticket resale for Rotterdam. Flask + SQLite, Stripe Connect for payments, English and Dutch.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/flask --app app seed        # optional: sample listings
.venv/bin/flask --app app run --debug
```

Open http://localhost:5000. Without email/SMS/Stripe configured, sign-in codes appear on screen and checkout is simulated (a yellow bar says so).
Add your email to `ADMIN_EMAILS` in `.env` to see `/admin`.

## How money moves

1. The buyer pays ticket price + service fee (default 8%, at least €1) through Stripe Checkout (iDEAL or card).
2. The money stays on Nightswap's Stripe balance. The buyer can download the ticket right away.
3. Until `PAYOUT_DELAY_HOURS` (default 48) after the event night, the buyer can report a problem, which puts the payout on hold for review in `/admin`.
4. `flask --app app release-payouts` (run it hourly via cron) transfers the ticket price to the seller's Stripe Express account. Nightswap keeps the fee.

Sellers set up payouts (IBAN + ID check, hosted by Stripe) only after their first sale, so posting a ticket stays quick.

## Events

Events are real records (`events` table): name, venue, city, category, date, door/end time, age, official ticket link, description and a source link. Events without tickets still get a page with "notify me".

- **Admin → Events** (`/admin/events`): import events from a link (reads the schema.org Event data many venue and ticket-shop pages publish), add or edit events by hand, approve or hide events sellers added.
- **Selling:** sellers pick the date, then the event. "Can't find it? Add it" creates the event straight away with status `review`, so it shows in the admin review queue.
- Weekly routine: paste your main venues' agenda links into the importer, add the rest by hand, check the review queue.

## Safety and admin tools

- **Admin second check:** admins confirm a text code every 12 hours (needs a verified phone). `ADMIN_SECOND_FACTOR=0` turns it off for local testing only.
- **Users** (`/admin/users`): search by email/name/phone; blocking takes listings off sale, cancels checkouts, holds payouts and signs the user out.
- **Reported listings:** anyone can report a listing (EU Digital Services Act); reports appear on `/admin` with remove / block / dismiss.
- **Cancel & refund all:** on Admin → Events, refunds every buyer in full and takes all tickets off sale.
- **Event changes:** changing an event's date or time emails buyers and sellers, moves payout dates, and lets buyers ask for a full refund (the ticket goes back on sale).
- **Your data** (`/account/data`): users can download their data or delete their account (orders are kept anonymously for the 7-year bookkeeping obligation).
- **Rate limits** on sign-in codes, code guesses, texts, messages, offers, reports and poster uploads. Behind a proxy (Render, Fly) set `TRUST_PROXY=1`.
- **Sellers confirm they're 18+** the first time they post.

## Trust features

- **Verified students:** sign up with (or later add) a university email from `STUDENT_DOMAINS` in `config.py` to get a badge.
- **Duplicate tickets:** every upload is checked by file hash *and* by the QR code/barcode inside it (`ticketcodes.py`), so a re-saved PDF or screenshot of an already-listed ticket is refused.
- **Ratings:** after the event, buyers answer "did your ticket work?"; a thumbs-down before the payout date opens the problem report and holds the payout.
- **Several tickets per listing:** one file per ticket. One file containing several QR codes becomes a set that's sold together.

## Rules you can change in `.env`

| Setting | Default | Meaning |
|---|---|---|
| `NIGHTSWAP_FEE_PERCENT` | `8` | Buyer fee per ticket, % of the price |
| `NIGHTSWAP_FEE_MIN_CENTS` | `100` | Minimum fee per ticket (EUR 1.00) |
| `NIGHTSWAP_FEE_MAX_CENTS` | `0` | Optional maximum fee per ticket (0 = none) |
| `NIGHTSWAP_PRICE_CAP_PERCENT` | `100` | Max price as % of face value |
| `NIGHTSWAP_PAYOUT_DELAY_HOURS` | `48` | Hold after the event night |

The venue list is in `config.py` (`VENUES`). Sellers can also type another venue.

## Going live checklist

- [ ] Register with the KVK and open a business bank account.
- [ ] Create a Stripe account, enable **Connect** (Express) and iDEAL. Set `STRIPE_SECRET_KEY` (test key first).
- [ ] Add a webhook in Stripe to `https://<your-domain>/webhooks/stripe` for `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.expired`; set `STRIPE_WEBHOOK_SECRET`.
- [ ] Email: any SMTP provider (Postmark, Mailgun, Brevo…) → `SMTP_*`, `MAIL_FROM`.
- [ ] SMS: Twilio → `TWILIO_*` (or swap `notify.send_sms` for another provider).
- [ ] Set `NIGHTSWAP_ENV=production`, a long random `SECRET_KEY`, `BASE_URL=https://your-domain`.
- [ ] Host it (Fly.io, Render, Railway, a VPS) behind HTTPS with `gunicorn app:app`; keep `instance/` and `uploads/` on a persistent disk and back them up.
- [ ] Hourly cron: `flask --app app release-payouts` and `flask --app app send-reminders` (day-of-event price nudges, "did your ticket work?" emails).
- [ ] Check the university email domains in `config.py` (`STUDENT_DOMAINS`) against what each school issues.
- [ ] Have the Terms and Privacy pages checked by someone with legal knowledge.

## Files

- `app.py`: routes, order logic, CLI commands
- `payments.py`: all Stripe calls (and the simulated fallback)
- `ticketcodes.py`: reads QR codes and barcodes from uploaded PDFs and images
- `importer.py`: reads event data from venue and ticket-shop pages
- `art.py`: generated artwork for events without a poster
- `moderation.py`: hides contact and payment details in messages
- `push.py`, `wallet.py`: push notifications and Apple Wallet passes
- `notify.py`: email and SMS
- `i18n.py`: every English and Dutch string, date and money formatting
- `schema.sql`: database tables
- `templates/`, `static/`: pages, CSS, a small amount of JS

## Logo and icons

The logo is a ticket stub with two swap arrows. The SVG version is in `templates/base.html` and `static/favicon.svg`; the PNG app, Wallet and email icons are drawn by `tools/make_icons.py` (run `../.venv/bin/python tools/make_icons.py` after changing the shape or colours).
