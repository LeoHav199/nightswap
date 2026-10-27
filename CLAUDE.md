# Nightswap

Ticket resale for students in Rotterdam: club nights, student parties, and some festivals and concerts. Owner: Leo. The site is in English and Dutch, plus French, German and Spanish.

## Product decisions (already made, don't reopen without asking)
- Launch city is **Rotterdam only** (`ENABLED_CITIES`, `SHOW_ALL_NETHERLANDS=0`). The code supports more cities for later.
- Prices are capped at face value. The buyer pays an **8% fee, minimum €1**, set with `FEE_PERCENT`/`FEE_MIN_CENTS`. A flat €1 fee was tried and dropped because it didn't earn enough.
- Contact details are required for safety: an email code at sign-in, a phone code before selling, and sellers must be 18+.
- Sellers are paid out 48h after the event (`PAYOUT_DELAY_HOURS`). Buyers get refunds if an event is cancelled or a ticket is disputed.
- Most sellers sell once, so the track-record badge only appears after 2+ sales.
- Messages hide phone numbers, handles and links, and flag attempts to deal off the platform (`moderation.py`).
- Venue photos come from Wikimedia Commons, credited on `/credits` and not on the images. When no photo exists, a generated poster is shown (`art.py`).
- The homepage shows events with tickets first, then a compact "Coming up" list, so it never looks empty.
- Logo: a ticket stub with two swap arrows, in pink `#ff5a8a` on dark `#0d0a13`. The PNG icons are drawn by `tools/make_icons.py`.
- Look and feel: professional nightlife style that shouldn't look AI-generated. Day tabs, poster cards, and a bottom tab bar on phones.

## Stack
- Flask 3, SQLite in WAL mode, Jinja templates, plain JS and CSS, no build step. Python 3.9 locally, 3.11 on Render.
- `app.py` holds all the routes. The rest: `db.py` (schema plus migrations in `ADDED_COLUMNS`), `config.py` (all settings come from env vars or `.env`), `payments.py` (Stripe Checkout and Connect Accounts **v2**), `notify.py` (SMTP email, Twilio SMS), `push.py` (web push), `wallet.py` (Apple Wallet), `ticketcodes.py` (duplicate QR check), `importer.py` (schema.org event import), `i18n.py` + `locales/` (translations).
- Installable web app: `static/js/sw.js` (bump `VERSION` when caching changes), the manifest is served by `app.py`, and static links carry `?v=hash`.
- Without a Stripe key it uses a pretend checkout. Without SMTP or Twilio, sign-in codes are printed and shown on screen (development only).

## Running
- The Python environment lives one level up, at `../.venv`. Install with `pip install -r requirements-dev.txt`.
- Preview servers are in `.claude/launch.json`:
  - `nightswap` (port 5001, debug)
  - `nightswap-phone` (Wi-Fi, port 5002)
  - `nightswap-demo` (gunicorn, port 5080, its own data in `demo-data/`, no admins)
- Tests: `sh tests/run_all.sh` runs all 209 checks against a throwaway database. Run it after every change.
- `starter_data/` holds the real events and venue photos a brand-new (live) database loads once (`starter.py`). Refresh it with `../.venv/bin/flask --app app export-starter`.
- `.env` holds secrets (Stripe test key, admin emails) and is never committed or printed. `.env.example` lists every setting.

## Deploying (not done yet)
- `render.yaml` is ready: starter plan, Frankfurt region, 1 GB disk at `/var/data`, gunicorn without `--preload` (preloading crashes on macOS and isn't needed).
- Still needed:
  - GitHub repo: done, private at github.com/LeoHav199/nightswap (push with `git push`; Leo's Mac has the token saved)
  - Render account: next step. New → Blueprint → pick the repo; it asks for ADMIN_EMAILS, STRIPE_SECRET_KEY, SMTP_HOST/USER/PASSWORD, MAIL_FROM
  - email sending: Brevo free plan (Brevo kept pushing paid plans; app.brevo.com → SMTP & API → SMTP for the key), or iCloud SMTP as a fallback (smtp.mail.me.com, port 587, app-specific password)
  - Twilio (optional: without it, phone codes are emailed, see `config.SMS_ENABLED`)
  - cron-job.org POSTing hourly to `/tasks/hourly` with header `X-Task-Secret`
  - a domain
- Before real money:
  - KvK registration
  - Stripe live mode
  - terms and privacy reviewed
  - Apple Developer account for Wallet passes

## Ideas already weighed
- Nightswap buying early-bird tickets itself and reselling them: **rejected**. It breaks the face-value promise, organisers can void bulk-resold tickets, it competes with our own sellers, and we'd carry the inventory risk and legal grey zone.
- To fix the shortage of sellers instead (suggested, not built yet): first-hand ticket sales for student associations and small organisers (strongest), commission through official ticket-shop referral links, showing "X people are looking" to bring out sellers, reminders before the night, and official organiser allocations later.

## Still open
- Marketing plan: student associations, ambassadors, Instagram/TikTok, flyers at venues, organiser partnerships (Exchange Party, ESN), referral credit.
- The how-it-works, terms and privacy pages fall back to English for fr/de/es.
