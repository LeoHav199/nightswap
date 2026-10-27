# Spare (formerly Nightswap)

Ticket resale for students in Rotterdam, at sparetickets.eu: club nights, student parties, and some festivals and concerts. Owner: Leo. The site is in English and Dutch, plus French, German and Spanish.

## Working with Leo
- Leo is a student building this as a business, not a developer. Explain steps plainly, one at a time, with exactly where to click; give commands in separate code blocks he can paste.
- Leo makes the product calls. When he suggests an idea, give an honest opinion (including "don't do this") and a recommendation, then build what he picks.
- Leo signs up for outside services himself (GitHub, Stripe, Render, Brevo, all under his own email). Never ask him to paste keys, tokens or passwords into the chat; they go into `.env` locally or Render's Environment page.
- Run `sh tests/run_all.sh` after changes, then commit and `git push` (his Mac has the GitHub token saved; on slow Wi-Fi a push can time out, so just retry).
- To show the site on his phone or to family at home: start the `nightswap-phone` server and give http://<Mac's Wi-Fi IP>:5002. Home Wi-Fi only, since sign-in codes show on screen in development. If the preview tool can't reach the Desktop folder, start it with Bash instead: `../.venv/bin/flask --app app.py run --host 0.0.0.0 --port 5002`.
- The project was built in an earlier Claude account; this file is the handover. History lives in git (`git log`).

## Product decisions (already made, don't reopen without asking)
- Launch city is **Rotterdam only** (`ENABLED_CITIES`, `SHOW_ALL_NETHERLANDS=0`). The code supports more cities for later.
- Prices are capped at face value. The buyer pays an **8% fee, minimum €1**, set with `FEE_PERCENT`/`FEE_MIN_CENTS`. A flat €1 fee was tried and dropped because it didn't earn enough.
- Contact details are required for safety: an email code at sign-in, a phone code before selling, and sellers must be 18+.
- Sellers are paid out 48h after the event (`PAYOUT_DELAY_HOURS`). Buyers get refunds if an event is cancelled or a ticket is disputed.
- Most sellers sell once, so the track-record badge only appears after 2+ sales.
- Messages hide phone numbers, handles and links, and flag attempts to deal off the platform (`moderation.py`).
- Venue photos come from Wikimedia Commons, credited on `/credits` and not on the images. When no photo exists, a generated poster is shown (`art.py`).
- The homepage shows events with tickets first, then a compact "Coming up" list, so it never looks empty.
- Name: **Spare** (renamed from Nightswap on 27 Oct 2026; Leo and his partner chose it). Domain sparetickets.eu (chosen over sparetickets.nl so it can serve other countries later; spare.nl and sparetickets.com are taken).
- Logo: a tilted lime ticket with a dashed tear line plus a lowercase "spare" wordmark (Inter Tight 900), lime `#d4ff3a` on navy `#15132b`, off-white text `#f5f3ec`. Keep the brand on dark backgrounds; lime is unreadable on white. The PNG icons are drawn by `tools/make_icons.py`.
- Internal names keep "nightswap" on purpose: the repo, the Render service and disk, `NIGHTSWAP_*` settings, the database file and the preview server names. Renaming those would break the live site or make Render create a new empty service.
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

## Deploying (live at https://nightswap.onrender.com since 27 Oct 2026, Stripe test mode)
- `render.yaml` is ready: starter plan, Frankfurt region, 1 GB disk at `/var/data`, gunicorn without `--preload` (preloading crashes on macOS and isn't needed).
- Still needed:
  - GitHub repo: done, private at github.com/LeoHav199/nightswap (push with `git push`; Leo's Mac has the token saved)
  - Render: done, Blueprint `nightswap` on the Starter plan (needed for the disk, and the free plan blocks SMTP). Settings live on Render's Environment page.
  - email sending: done with iCloud SMTP (smtp.mail.me.com, leohavgarde@icloud.com, app-specific password). Check with `flask --app app test-email <address>`. Switch to Brevo once there's a domain to authenticate; Brevo with an @icloud.com sender lands in spam.
  - Twilio (optional: without it, phone codes are emailed, see `config.SMS_ENABLED`)
  - cron-job.org: done, job "Nightswap hourly" POSTs to `/tasks/hourly` with header `X-Task-Secret` (value = TASK_SECRET on Render). Test run gave 200 on 27 Oct 2026.
  - domain: sparetickets.eu bought at TransIP on 27 Oct 2026 (€9.67 first year, paid by iDEAL, no auto-debit). Renewal there is €26.99 + VAT, so **before Oct 2027 transfer it to a registrar with a cheaper .eu renewal**. Connect it in Render (Settings → Custom Domains) and set BASE_URL.
  - MAIL_FROM on Render still needs to say `Spare <leohavgarde@icloud.com>` after the rename.
  - Stripe webhook (STRIPE_WEBHOOK_SECRET): optional backup, orders are already confirmed when the buyer returns from Stripe
- Tested on the live site 27 Oct 2026: listing, Stripe test checkout, problem report and admin refund all work.
- Before real money:
  - KvK registration
  - Stripe live mode
  - terms and privacy reviewed
  - Apple Developer account for Wallet passes

## Ideas already weighed
- Spare buying early-bird tickets itself and reselling them: **rejected**. It breaks the face-value promise, organisers can void bulk-resold tickets, it competes with our own sellers, and we'd carry the inventory risk and legal grey zone.
- To fix the shortage of sellers instead (suggested, not built yet): first-hand ticket sales for student associations and small organisers (strongest), commission through official ticket-shop referral links, showing "X people are looking" to bring out sellers, reminders before the night, and official organiser allocations later.

## Still open
- Marketing plan: student associations, ambassadors, Instagram/TikTok, flyers at venues, organiser partnerships (Exchange Party, ESN), referral credit.
- The how-it-works, terms and privacy pages fall back to English for fr/de/es.
