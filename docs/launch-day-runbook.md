# Launch day runbook: Wednesday 11 November 2026

The short, ordered version for the technical steps. The long version, with the KVK appointment, the business bank account and the Stripe activation form, is `docs/launch-day.md`: finish those first, because step 4 needs a Stripe account that can take live payments.

Rules for the whole day:
- Keys and secrets go from Stripe straight into Render's **Environment** page. Never into a chat, an email or a note.
- Every **Save changes** on Render's Environment page redeploys the site (about a minute offline). Do all of them today.
- If a step fails, stop there. The site stays on the sign-up page until step 8, so launching a day later costs nothing, as long as it is not Thursday evening (step 9).

## 1. Check where you start
- [ ] /admin → **Launch**: coming-soon mode is **on**.
- [ ] /admin → **Launch check**: the **Stripe** row says "test mode". If it says "live", stop and ask Claude: the live key is already on Render, and real orders may exist.

## 2. Set BACKUP_KEY
- [ ] /admin → **Launch check**: if "BACKUP_KEY set" is green, skip to step 3.
- [ ] Render → the `nightswap` service → **Shell**: run `flask --app app backup-key`. It prints a new key.
- [ ] Copy it into Render → **Environment** as `BACKUP_KEY` and into your password manager (without it, no backup can be opened), then **Save changes**.
- [ ] /admin → **Launch check**: "BACKUP_KEY set" is green. Without the key the site neither sends nor stores backups.

## 3. Clear the test data (before the live key goes in)
In Render's **Shell**:
- [ ] `flask --app app clear-test-data` only lists what would go. Check that the waitlist count is what you expect and that your admin accounts are the ones listed as kept.
- [ ] `flask --app app clear-test-data --yes` deletes it. It makes a backup first and prints where it is.
- [ ] **No order may have been paid with live Stripe.** The command checks this itself: if it says "paid with real money (live Stripe)", nothing was deleted. Stop and ask Claude.
- [ ] Never run it again after step 7: the real-money test sale is a live order, so the command will refuse.

## 4. Live webhook in Stripe
Stripe dashboard, **Test mode switched off**:
- [ ] Developers → Webhooks → **Add destination** → `https://sparetickets.eu/webhooks/stripe`, snapshot payload, with these **6 events**:
  - `checkout.session.completed`
  - `checkout.session.async_payment_succeeded`
  - `checkout.session.expired`
  - `charge.dispute.created`
  - `charge.dispute.closed`
  - `charge.refunded`
- [ ] Open the destination and check that all 6 are listed. The last three are what put chargebacks and refunds from the Stripe dashboard into /admin, so a missing one is not a small thing.
- [ ] Keep the page open: the signing secret (starts with `whsec_`) is for step 5.

## 5. Live key and webhook secret on Render
Render → **Environment**:
- [ ] `STRIPE_SECRET_KEY`: the **live** secret key (Stripe → Developers → API keys, starts with `sk_live_`).
- [ ] `STRIPE_WEBHOOK_SECRET`: the signing secret of the live destination from step 4.
- [ ] **Save changes**, and wait until Render says "Live".
- [ ] The yellow "Test mode" bar is gone from the site, and the **Stripe** row in /admin → Launch check says "live".
- [ ] Stripe's test-mode destination ("dynamic-celebration") now gets nothing. That is fine; leave it.

## 6. Checks in /admin
- [ ] **Launch check**: every row green. Fix red ones first (for example "Company details in the terms" needs `KVK_NUMBER`, "Listings waiting for a check" needs you to approve them).
- [ ] Be careful with two rows: "A Stripe webhook arrived" and "Chargeback/refund events reach Spare" were already green from test mode, because step 3 keeps the settings. They prove nothing about the live webhook until step 7 shows a fresh time next to them.
- [ ] **Network check**: the IP address Spare shows must be the same as on whatismyipaddress.com (opened on the same Wi-Fi or phone). Render is set to `TRUST_PROXY=1` and `TRUST_PROXY_HOPS=2`. If it shows another address, stop and ask Claude: sign-in limits would be shared between everybody.

## 7. One real-money test sale
Follow step 8 of `docs/launch-day.md` (a €5 ticket, bought with iDEAL, then refunded in /admin). Then:
- [ ] In Stripe, the live destination shows deliveries with **200** for `checkout.session.completed` and `charge.refunded`.
- [ ] /admin → Launch check: the two Stripe webhook rows now show today's time.

## 8. Open the doors
- [ ] /admin → **Launch** → **Open the site to everyone**. Check in a private window that the full site shows.
- [ ] /admin → **Waitlist** → **Send launch email (next 20)**, once. That is the first batch of 20; the page says how many are still to go.
- [ ] Check that the first emails arrive (not in spam) and that the links work, then press the button again for the next 20 whenever you are happy. Each email has a remove-me link.

## 9. The deploy ban starts tomorrow evening
From the evening after launch, three nights in a row, **nothing goes to production**:
- Thursday 12 Nov, 18:00 to Friday 13 Nov, 04:00
- Friday 13 Nov, 18:00 to Saturday 14 Nov, 04:00
- Saturday 14 Nov, 18:00 to Sunday 15 Nov, 04:00

Why: a deploy takes the site down for about a minute, and buyers open their tickets at the door.
- [ ] That covers `git push` **and** every Save on Render's Environment page. Finish all keys and settings on Wednesday.
- [ ] Only a live outage is a reason to break the ban. For anything else, wait for Sunday morning.
- [ ] Switching on **Pause new sales** (/admin → Launch) needs no deploy and is allowed in the ban. It stops new sales and listings and keeps order pages, Swap, reports and payouts working.
