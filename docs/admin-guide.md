# Admin guide: what to do when...

For Leo and Ilyas, for the first weeks after launch. Everything here is done in **/admin** (https://sparetickets.eu/admin, you sign in with the authenticator-app code), in the Stripe dashboard, or on Render. It describes what the code does today.

The rule behind almost everything: **Spare holds the buyer's money until the buyer is in (Swap) or 24 hours after the night.** While an order is "disputed", nothing is paid to the seller. So you almost never have to hurry; you need to decide correctly.

## Every day (5 minutes)

Open /admin and look from the top:

1. **Hourly task** (the pill at the top, under "Admin"): green says "last ran 12 min ago". Red means it hasn't run for 3 hours or more: see "The hourly task is late" in case 5.
2. **Launch check**: everything should be green. Red means something is not set up or not running (see the cases below).
3. **Payouts that need a manual look** (appears under the Launch check only when there is one): see case 2.
4. **Reported problems**: see case 1.
5. **Listings to check**: see case 4.
6. **Reported listings**, **Flagged messages** and **Chargebacks**: see case 3 for chargebacks. Reported listings have three buttons: *Remove listing* and *Block seller* both need a reason (the seller gets it by email), *Nothing wrong* closes the report.

You also get an email for most of these (problem reported, seller replied, listing to check, chargeback, payout needs a look, crash). Keep those emails and hello@sparetickets.eu where you will see them.

When you block or reject something, you always have to write a reason. The person gets it by email (an EU rule), and it never says who reported them.

---

## 1. A buyer reports a problem

**What already happened by itself:** the order went to "disputed" and the seller's payout is on hold. You got the email "problem reported". The seller got an email too, has **48 hours** to reply with text and up to 3 photos, and gets a reminder after 12 hours.

What the buyer could choose: the ticket was rejected at the door, was already scanned, is for the wrong event, never arrived (app tickets), or something else.

Two rules the code enforces on the buyer's side:
- Door problems (rejected, already scanned) need **a photo**, can only be reported until **06:00 after the doors opened**, and never after the buyer pressed Swap.
- A buyer can report a problem until the seller has actually been paid. After that, use "Refund after payout" (below).

**What you do:**

1. /admin → **Reported problems** → **Open the dispute**.
2. Read the page from the top: what the buyer said and their photos, the seller's reply, the evidence, the chat, and the numbers about both people (sales, purchases, earlier disputes, thumbs up and down).
3. **Was this ticket on Spare before?** shows other tickets with the same code or file. Two different sellers with the same code means someone sold it twice.
4. **Ask the organiser**: if the ticket has a code (or is an app ticket with the buyer's name), there is a ready Dutch/English email. Click it, send it to the organiser, and ask whether the ticket was scanned and when. Their scan log settles most cases.
5. If the page or the order shows a **chargeback** (the buyer's bank also opened a dispute), do **not** press either refund button: the buyer would get the money from you and from the bank. See case 3.
6. Decide with one of four buttons. Each needs a short reason, and **both people get it by email**:

| Button | Use it when | What it does |
|---|---|---|
| Refund the buyer | The ticket really didn't work, or the seller can't show it did | Full refund (ticket and fee). The ticket is voided. |
| Refund and block the seller | The ticket was fake, a double sale, or the seller lied | Same refund, plus the seller's other tickets are voided, the listing is removed and the seller is blocked. |
| Keep: pay the seller | The buyer's claim doesn't hold up | The order goes back to "paid" and the seller is paid on the normal date. |
| False report: pay the seller and block the buyer | The organiser says the ticket was scanned and the buyer got in | Same as Keep, and the buyer is blocked. |

Use the last one carefully: it blocks someone from the site. The terms (section 8) say false reports are checked with the organiser.

**If nobody decides:** door problems ("rejected at the door", "already scanned") are refunded automatically when the seller is silent for 48 hours, but **only if** the buyer attached a photo, has no other dispute in the last 90 days, and there is no chargeback. If any of those is missing, you get the email "dispute needs a decision". The other kinds (wrong event, never arrived, other) are never refunded automatically: they wait for you.

**If the seller was already paid** (order status "released"): there is no dispute button. In /admin → **Recent orders** (only the last 50 orders are listed), open **Refund after payout...** on that order, write a reason, and press *Refund, take payout back, block seller*. This takes the money back from the seller's Stripe account, refunds the buyer in full and blocks the seller. If Stripe refuses to take the payout back, the confirmation says so ("taking back the payout FAILED") and you get an email: check the Stripe dashboard.

---

## 2. A payout fails

First, know the difference between **waiting** and **failing**. A payout waits quietly, with no error, when:
- it isn't due yet (24 hours after the night, or earlier if the buyer swapped and the seller has 2+ finished sales and the buyer paid by iDEAL),
- the order is disputed,
- the seller is blocked, or hasn't finished Stripe's payout setup (their listings stay in "draft" until they have),
- the seller's bank account matches a blocked user's (you got an email "payouts held"; see /admin → Users → *held* list; clear the hold there if it is a mistake),
- it is an app ticket that the seller never marked as transferred.

"Run due payouts now" (top of /admin) pays everything that is due and not on hold. It says how many it paid and how many are waiting.

A payout **fails** when Stripe refuses the transfer. What happens:
1. The hourly task tries again every hour. Each failure is counted and the error text is saved.
2. After **3 failures** the order is no longer retried. You get the email "payout needs a manual look", and the order shows up in /admin under **Payouts that need a manual look**, with the seller's email and Stripe's error.

**What you do:**

1. Read the error on /admin. It is Stripe's own message.
2. Look at the seller's account and your own balance in the Stripe dashboard (Connect → the seller's account; Balances). Typical reasons are a seller account that Stripe has restricted or that is missing details, or not enough available balance on Spare's side. Ask the seller to finish whatever Stripe asks for.
3. **Be honest about the gap:** once an order has failed 3 times, there is no button in /admin to try it again, and "Run due payouts now" skips it. Today the counter has to be reset in the database (ask Claude to do it with you in a normal session, or to build a "Try again" button). Don't leave it for weeks: the seller is waiting for their money.
4. **Do not pay the seller by hand in Stripe** and leave the order as it is. Spare would still think the order is unpaid and could pay it a second time later. If you ever do it by hand, write down the order number and have Claude mark the order as paid out.
5. If the seller can't be paid at all (for example they cannot finish Stripe's setup), the buyer's money is still safe with Spare. Decide with Leo whether to refund the buyer.

Also good to know:
- An order that shows "releasing" for more than 15 minutes (the server stopped in the middle of a payout) is sorted out by the hourly task: if Stripe has the transfer it is marked paid out, otherwise it goes back to "paid" and is retried.
- If a whole task in the hourly run fails (not just one order), you get an email "hourly task '...' failed", at most once a day per task. The other tasks keep going.

---

## 3. A chargeback (the buyer's bank takes the money back)

Stripe tells Spare automatically (the webhook), you get the email "chargeback on order ..." and the order appears in /admin → **Chargebacks**.

What Spare did by itself:
- Seller **not paid yet**: the order is set to "disputed" with the reason "Chargeback by the buyer's bank". The payout is on hold.
- Seller **already paid**: Spare tried to take the payout back. The email says whether that worked. If it says "taking it back FAILED", go to the Stripe dashboard and reverse the transfer by hand (Connect → Transfers).
- If a chargeback arrives while a payout is being sent, the payout is taken back straight away.

**What you do:**

1. In the Stripe dashboard go to **Payments → Disputes** and open it. Stripe shows the **deadline** for your answer. Don't miss it: a missed deadline is an automatic loss.
2. Send evidence: the order number, the date and time, the ticket details, the buyer's chat messages, the seller's reply, and the organiser's answer if you asked (ask them, see case 1). The more concrete, the better.
3. When the bank decides, you get "chargeback closed" with the result. It is also shown on the order in /admin → Chargebacks.
4. Don't pay the seller while it is open. If you press *Keep: pay the seller* on a chargeback order, the seller is paid on the normal date and Spare then takes the payout straight back because of the chargeback, so it only creates noise.
5. **Never refund the buyer yourself while a chargeback is open** (not with the dispute buttons, not "Refund after payout", not in Stripe): the buyer would be paid twice. The bank's decision settles it.
6. If you lose, the money is gone either way. Think about whether the seller was at fault (fake or double-sold ticket): then block them. Don't try to refund the buyer on top of a lost chargeback; the money was already taken back from Spare by the bank.

Two things that lower the risk: the first purchase by a new buyer can be card with 3D Secure (set by `FIRST_BUY_CARD`), and a Swap by iDEAL pays instantly only for iDEAL, because card payments can be reversed weeks later.

If you refund an order **in the Stripe dashboard** instead of in /admin, Spare notices (webhook `charge.refunded`), marks the order refunded, voids the tickets, and emails you. If the seller was already paid, it tries to take the payout back.

---

## 4. A listing waits for a face-value check

**Why it waits.** Prices are capped at double the face value, so Spare must know the face value. If the event has an **official price** (set in /admin → Events), the seller can't enter a higher face value and the listing goes live straight away. If the event has no official price, the seller must upload **proof of the face value** (a screenshot of the purchase, a receipt) and the listing waits. You get the email "listing to check (face)".

The same queue is used for two other reasons, shown in "To check":
- **code**: the code in the ticket file couldn't be read, so the duplicate check can't see it. Look at the file yourself.
- **picture**: the ticket looks like one that is already on Spare (the same picture re-saved, or the same template). Compare the two.

A listing that says **draft** (not in your queue) is something else: the seller hasn't finished Stripe's payout setup yet. Nothing for you to do. When they finish, the listing goes on sale, or into your queue if it also needs a check.

**What you do:**

1. /admin → **Listings to check**. Each case shows the event, the seller, "To check", the face value, the price and (if known) the official price.
2. Open the **ticket file** and the **proof** links.
3. Check:
   - The proof shows the same event and a price that matches the face value the seller typed.
   - The price is not more than **double** the face value. (The form blocks higher prices, but check the face value itself, since the cap is built on it.)
   - The ticket file belongs to this event and looks real.
4. **Approve: put it on sale** if it's fine. The seller gets an email and people with an alert for the event are notified.
5. **Reject** if not. Type a reason; the seller gets it by email and the ticket is voided. Say what they can do ("upload the order confirmation that shows the price").

**Tip:** every time you approve a face value for an event, fill in the official price in /admin → **Events** (field "Official ticket price (highest)"). Future sellers for that event then skip the check, and nobody can enter a higher face value. It does not change listings that are already waiting; those still need your Approve.

Don't leave listings waiting: someone is trying to sell a ticket for a night that is getting closer.

---

## 5. The site is down or something doesn't run

**First, check from outside** (phone on mobile data): open https://sparetickets.eu and, if that fails, https://nightswap.onrender.com. Both failing means Render. Only one failing means the domain or Cloudflare.

**Then, in Render** (dashboard → the `nightswap` service):
1. Look at the status at the top and at **Events** (was there a deploy just before?) and **Logs** (the last red error).
2. A deploy always takes the site down for about a minute: the disk can only be attached to one server. If it's been less than 2 or 3 minutes after a deploy, wait.
3. If a new deploy broke the site, go to Events and roll back to the previous good deploy. That is allowed even on a Friday evening, because it fixes an outage. Otherwise **no pushes Thursday to Saturday between 18:00 and 04:00**, since buyers open their tickets at the door.
4. If the service is up but pages show "Something went wrong" (a 500 error), you get an email "Spare error: ..." with the page and the traceback (at most 5 an hour). Send that email to Claude in a normal session.
5. If it says the disk is full, see below.

**Things that look like "the site is down" but aren't:**

| What you see | Check | Fix |
|---|---|---|
| Visitors only see the sign-up page | /admin → Launch | The site is in coming-soon mode: press *Open the site to everyone*. |
| Nobody gets sign-in codes or emails | Launch check: *Email sending (SMTP)*. In Render's Shell: `flask --app app test-email you@example.com` | Check the iCloud app-specific password on Render's Environment page. |
| Payments go through but orders stay unpaid | Launch check: *A Stripe webhook arrived* | In Stripe → Developers → Webhooks: failed deliveries? The signing secret on Render must match the live webhook. |
| Payouts and reminders stopped | The red *Hourly task* pill on /admin, or an UptimeRobot email | See "The hourly task is late" below. |
| Everyone seems to come from one address, rate limits hit real users | /admin → Network check | `TRUST_PROXY_HOPS` should be 2. |
| Disk above 70% (you get an email) | /admin shows the disk usage | Ticket files are deleted 14 days after an event once the order is done, so it should shrink by itself; if it keeps growing, ask Claude to look, or raise the disk size on Render. |

**The hourly task is late.** Payouts, the "Inside? Slide Swap" reminder, resell reminders, the nightly backup and the disk warning all run from one job on cron-job.org that calls Spare every hour. If it stops, nothing looks broken: sellers just aren't paid. So Spare has a page, https://sparetickets.eu/health, that says `ok` while the job ran in the last 3 hours and `late` (error 503) otherwise. It shows nothing else. The red pill on /admin means the same thing.

When it is late:
1. Log in to cron-job.org and open the job "Nightswap hourly". Is it switched on? Look at its history: what did the last runs get back?
2. **404** means the secret doesn't match: the header `X-Task-Secret` in the job must equal TASK_SECRET on Render's Environment page. Copy it again from Render into the job (don't paste it into a chat).
3. **Timeouts or 5xx** mean the site itself is down: see the steps at the top of this case.
4. No runs at all: the job is off, or cron-job.org has an outage (check their status page). Run it by hand once: in cron-job.org open the job and press *Test run*. If cron-job.org itself is down, Render's Shell can pay sellers and send reminders with `flask --app app release-payouts` and `flask --app app send-reminders` (the pill stays red until the real job runs again).
5. Refresh /admin: the pill turns green as soon as a full run finishes. Nothing is lost: payouts and reminders catch up on the next run.

**Set up the outside monitor once** (free, so you get an email even if you don't open /admin):
1. Sign up at uptimerobot.com with your own email.
2. *Add New Monitor* → type **HTTP(s)**, name "Spare hourly task", URL `https://sparetickets.eu/health`, interval 5 minutes.
3. Under alert contacts tick your email (and the app's push, if you install it). Save.
4. It should say "Up". It turns "Down" and emails you when the page answers `late` or the whole site is down.

Leave Render's own health check on `/robots.txt`: if it pointed at /health, Render would treat a late cron job as a broken server.

**While the site is down on a party night:** buyers who already opened their order page may still see it, because paid order pages are kept on their phone for offline use. Post on Instagram (@sparetickets.eu) that you know and are fixing it, and answer hello@sparetickets.eu.

**If data has been lost:** a backup of the database and uploads is emailed to the admins every night (around 03:00), encrypted with BACKUP_KEY. The last 3 database copies are also kept on the disk in `/var/data/backups`. You can also press *Download backup* on /admin at any time. To open an encrypted backup on your Mac:

```
flask --app app decrypt-backup path/to/backup.zip.enc
```

It asks for BACKUP_KEY (keep it in a password manager as well as on Render). Putting a backup back on the live disk is not automated: don't improvise, do it with Claude and make a fresh backup first.

---

## Also useful

- **Locked out of /admin** (lost phone, lost recovery codes): in Render's Shell run the command below. At the next sign-in you set up the authenticator app again.

```
flask --app app reset-admin-2fa you@example.com
```

- **Block or unblock someone:** /admin → Users, search by email, name or phone. Blocking takes their listings off sale, holds their payouts and blocks their verified phone number. Unblocking does not release the phone number: remove it in the same page.
- **A seller says their money is late:** check the order in Recent orders. Payout date = 24 hours after the night (also for new sellers); a swap by iDEAL from a seller with 2+ finished sales pays immediately. Check if the order is disputed, if the seller finished Stripe setup, and if it's listed under "Payouts that need a manual look".
- **An event is cancelled or changed:** /admin → Events. Cancelling an event refunds every paid order and removes the listings. A date change emails ticket holders, and buyers may then ask for a refund themselves.
- **Never** paste keys, tokens or passwords into a chat. They go on Render's Environment page.
