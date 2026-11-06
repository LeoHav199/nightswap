# Launch day checklist (Wednesday 11 November 2026)

Do these in order. If a step can't be finished today (Stripe or the bank takes longer), stop there and launch a day later: the site stays on the sign-up page until step 9.

Never put keys or passwords in a chat. They go on Render's Environment page only.

## 1. KVK appointment (Leo and Ilyas together)
- [ ] Both bring a valid ID (passport, ID card or residence permit) and a bank card for the fee (about €85).
- [ ] Write down the **KVK number** you get.
- [ ] The VAT number comes by post later; add it in step 3 when it arrives.

## 2. Business bank account
- [ ] Open an account in the name of **Spare vof** (bunq Business or Knab can often be done online the same day with the KVK number).
- [ ] Note the IBAN: Stripe needs it in step 4.

## 3. Company details on the site
- [ ] Render → the `nightswap` service → **Environment** → add `KVK_NUMBER` with the number → **Save changes**. Render redeploys (about 1 minute).
- [ ] Check https://sparetickets.eu/terms#company: it now shows "KvK" and the number.

## 4. Stripe live mode
In the Stripe dashboard, switch **off** "Test mode" (top right), then **Activate payments**:
- [ ] Country: Netherlands. Business type: the option for a **partnership (vof)**. KVK number, address Lombardkade 47A, 3011 ZC Rotterdam.
- [ ] Owners and representatives: **both of you, 50% each**.
- [ ] Website: `https://sparetickets.eu`. Product description: *"Online marketplace where students resell tickets for club nights, student parties, concerts and festivals in Rotterdam to each other. Spare takes payment, holds it until the buyer is in, and pays the seller through Stripe Connect."*
- [ ] Statement descriptor: `SPARETICKETS.EU`. Support email: `hello@sparetickets.eu`. Support phone: **not a personal mobile** (a separate number), because it's shown on receipts.
- [ ] Payout bank account: the business IBAN from step 2.
- [ ] Settings → Payment methods: **iDEAL** and **cards** on.
- [ ] Connect → finish the platform setup in live mode, the same way as in test mode (you handle pricing; sellers get payouts).

## 5. Live webhook
- [ ] Developers → Webhooks → **Add destination** → `https://sparetickets.eu/webhooks/stripe`, snapshot payload, events:
  `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.expired`, `charge.dispute.created`, `charge.dispute.closed`, `charge.refunded`.
- [ ] Copy its **signing secret** (starts with `whsec_`): you'll need it in step 7.

## 6. Clear the test data
On Render → the `nightswap` service → **Shell**:
- [ ] `flask --app app clear-test-data` shows what would be deleted. Check that the waitlist count is right and that the admins are listed.
- [ ] `flask --app app clear-test-data --yes` deletes it. A backup is made first (in `/var/data/backups`).

## 7. Live keys
Render → **Environment**:
- [ ] `STRIPE_SECRET_KEY` = the **live** secret key (starts with `sk_live_`, from Developers → API keys).
- [ ] `STRIPE_WEBHOOK_SECRET` = the live webhook's signing secret from step 5.
- [ ] Save changes; Render redeploys.
- [ ] The yellow "Test mode" bar is gone from the site.

## 8. One real-money test sale
- [ ] Ilyas lists a ticket (a fake one from `tools/make_test_tickets.py` is fine) with a face value and price of **€5**, and finishes the live Stripe payout setup when asked (including the ID check). If the listing waits for a face-value check, approve it yourself in /admin.
- [ ] Leo buys it with **iDEAL** (€6.99 in total: €5 plus the €1.99 minimum fee; Ilyas would receive €4).
- [ ] Check: the order says "paid", the webhook shows a 200 in Stripe, both of you got the emails.
- [ ] In /admin, refund the order, and check the money comes back.
- [ ] /admin → **Launch check**: everything green (fix what's red first).

## 9. Open the doors
- [ ] /admin → **Launch** → switch off coming-soon mode.
- [ ] /admin → **Waitlist** → send the launch email (it goes out in batches of 20).
- [ ] Message the friends who promised to list tickets: the link is `https://sparetickets.eu/sell`.
- [ ] Post on Instagram (@sparetickets.eu).

## 10. The first days
- [ ] Notifications on for hello@sparetickets.eu and Instagram DMs on both your phones: the site promises fast replies.
- [ ] Look at /admin every day: disputes, listings waiting for a check, payouts.
- [ ] No pushes (code changes) on Thursday to Saturday between 18:00 and 04:00: buyers open their tickets at the door.
