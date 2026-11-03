# Plan: first-hand tickets for organisers (review item #33)

Nothing here is built yet. This is the plan for the only complete fix to double selling: tickets that Spare itself issues, so a resale inside Spare can cancel the old code at the door.

## Why

A PDF or screenshot is a copy. After a resale the seller still has the same QR code, and whoever scans first gets in. Spare's checks (#25 to #32) make this harder and refund the buyer, but they can't stop it, because Spare doesn't control the scanner at the door. When Spare issues the ticket and runs the scanner, a resale replaces the old code with a new one, and the old one stops working.

Who it's for: student associations, small organisers and venues that now sell through a Google Form and Tikkie, or through a ticket shop that charges them €1 to €2 per ticket. They get selling, a door scanner and a safe resale market in one place, and Spare earns on every first sale, not only on resales.

## How it works (for an organiser)

1. Sign up as an organiser, get verified (KvK or association registration, IBAN through Stripe).
2. Create an event with ticket types (early bird, regular, member price), each with a price, a capacity and a sales window.
3. Students buy on Spare. Each ticket is issued in the buyer's name and lives in their Spare account (and Apple Wallet).
4. At the door, the organiser's volunteers open the scanner page on their phones and scan. It works without signal and syncs between phones.
5. A buyer who can't go resells inside Spare, up to the price cap the organiser sets. The old code is cancelled and the new buyer gets a new one.
6. After the event, Stripe pays the organiser (ticket sales minus Spare's fee) and resellers as now.

## The parts, and what they reuse

### Organiser accounts and verification (medium)
- `organisers` table: name, type (association, company, venue), KvK or registration number, contact, `verified_at`, Stripe account. Members link users to an organiser (owner, staff, door).
- Verification by an admin (a queue on /admin, like the listing review queue from #25), with Stripe's own identity check for payouts.
- **Reuses:** users and sign-in, the admin review queue and statement-of-reasons emails (#12, #25), `payments.payout_onboarding_url` (Connect recipient accounts), `venues` and `events`.

### Ticket types and capacity (medium)
- `ticket_types` table: event, name, price, capacity, sold, `sale_starts_at`, `sale_ends_at`, `max_per_buyer`, `resale_cap_percent`.
- Capacity is claimed in the database before checkout (the same claim-first pattern as payouts in #26), so the last ticket can't be sold twice.
- **Reuses:** `events` (date, venue, door time, official price from #25), the buy and checkout flow, the 30-minute reservation with `release_unpaid`.

### Issued tickets with signed, rotating QR codes (large)
- `issued_tickets` table: ticket type, holder (user), holder name, `secret` (random, per ticket), `version` (raised on every resale), `status` (valid, used, cancelled, transferred), `scanned_at`, `scanned_by`.
- The QR code shown to the buyer is `ticket id . version . time step . signature`, where the signature is an HMAC of the rest with the ticket's secret. It changes every 15 seconds, like a TOTP code, so a screenshot is useless a minute later.
- Shown on the order page (which already works offline, #13) and in Apple Wallet (pass updates through the existing Wallet web service).
- **Reuses:** `totp.py` (time steps and HMAC from #23), `qr_svg`, `wallet.py`, the offline order page in `sw.js`.

### Door scanner page (large)
- `/door/<event>`: a page for the organiser's door staff using the phone camera (the only page that asks for camera access; the Permissions-Policy from #9 allows it there).
- Before doors, it downloads the list of valid tickets (ids, versions and secrets, encrypted for that device) so it can check signatures with no signal.
- Each scan is stored on the phone and synced whenever there's a connection; phones also sync with each other through the server, so a ticket scanned at door A is refused at door B within seconds when online, and the conflict is shown afterwards when offline.
- Clear results: green (welcome, name), red (already scanned at 23:41 by door B / cancelled / wrong event), and a manual search by name.
- **Reuses:** the service worker and offline cache (#13), the admin pages' style, rate limits and CSRF.

### Resale inside Spare that reissues the ticket (medium)
- A holder lists an issued ticket the same way as today, capped at the organiser's `resale_cap_percent` (default: face value).
- When it sells, the old ticket's `version` goes up (the old QR stops working at once), a new version is issued in the new buyer's name, and the seller is paid after the event as now. No file, no duplicate check, no dispute about copies.
- **Reuses:** listings, orders, offers, chat, payouts and refunds (`release_payout`, `refund_order`), "people looking" and alerts. Most of today's resale flow carries over; the ticket file is replaced by a reissue step.

### Payouts to organisers (medium)
- First-hand sales are paid to the organiser through Stripe Connect after the event (or in parts, for trusted organisers), minus Spare's fee. Refunds for cancelled events come out of the organiser's balance first.
- **Reuses:** `payments.transfer_to_seller` with idempotency keys (#26), chargeback handling (#27), the hourly task.

### Fees (small)
- First-hand: a fee per ticket paid by the buyer (e.g. 5%, min €0.75) and optionally a small organiser fee. Resale: as now (buyer 8% min €1.50, seller 5% min €1), possibly with a share for the organiser to make them like resale.
- **Reuses:** `fee_for`, `seller_fee_for`, `config` settings.

## Database changes (summary)
- New tables: `organisers`, `organiser_members`, `ticket_types`, `issued_tickets`, `scans`.
- New columns: `events.organiser_id`, `events.resale_cap_percent`; `listings.issued_ticket_id` (a resale of an issued ticket); `orders.ticket_type_id` (a first-hand sale).
- Everything goes through `db.ADDED_COLUMNS` and `schema.sql` as now.

## Order to build it

| # | Step | Reuses | Size |
|---|------|--------|------|
| 1 | Organiser accounts, verification queue, payout setup | users, admin queue, Stripe Connect | Medium |
| 2 | Ticket types, capacity claims, first-hand checkout | events, buy/checkout, reservations | Medium |
| 3 | Issued tickets with rotating signed QR codes on the order page | totp.py, qr_svg, offline order page | Large |
| 4 | Door scanner page: online first, then offline with sync | service worker, rate limits | Large |
| 5 | Resale of issued tickets with reissue | listings, orders, payouts, chat | Medium |
| 6 | Organiser payouts after the event, cancellations and refunds | payments, #26 claims, #27 chargebacks | Medium |
| 7 | Organiser dashboard: sales, check-ins, resales, export | admin pages | Medium |
| 8 | Apple Wallet passes that update on resale | wallet.py | Small |
| 9 | Fees and an organiser's share of resale fees | fee_for, config | Small |

Steps 1 to 4 make a usable pilot with one student association for one night (resale can wait). Before the pilot: test the scanner with 3 phones and 300 tickets in a basement with no signal.

## Open questions for Leo
- Which association or organiser would pilot this? Their current ticket costs and pain points decide the fee.
- Spare would then sell tickets itself (as the organiser's agent). Check VAT and consumer law (the organiser's terms apply to the event) with the lawyer from `docs/legal-review.md`.
- The other route is official resale with organisers whose ticket system supports it (TicketSwap's Secure Swap works with Weeztix, Paylogic and others). That needs partnerships rather than code, and could run alongside.
