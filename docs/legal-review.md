# Points for a lawyer to check (terms and privacy)

Spare's terms (`templates/terms/`) and privacy policy (`templates/privacy/`) were written from what the code does, not by a lawyer. Have someone with legal knowledge check these points before real money flows. The English and Dutch versions are the reference; French, German and Spanish are translations.

## Company details
- Company name, address, KvK and VAT numbers come from Render settings (`COMPANY_NAME`, `COMPANY_ADDRESS`, `KVK_NUMBER`, `VAT_NUMBER`). Until they're set, the pages show `[PLACEHOLDERS]` and the footer has no company line.
- Is a home address acceptable to publish, or should a business address be used?

## Terms
1. **Right of withdrawal (§5).** We say there's no 14-day withdrawal right because tickets are for a specific date (art. 6:230p BW / art. 16(l) of Directive 2011/83). That exception is written for the trader who provides the leisure service. Does it cover Spare's own intermediation service and service fee?
2. **Private sellers (§2).** We tell buyers that sellers are private individuals and consumer law doesn't apply to them (art. 6a Directive 2011/83, as amended by the Omnibus directive). A short version is also shown next to the Buy button on every listing (`private_seller_note`). Is the wording enough?
3. **Price cap (§4).** At most double the face value (since 5 Nov 2026; it was face value + 20%). Is a markup this size a problem in the Netherlands? Check the Dutch rules on ticket resale (there's no general ban, but some events and organisers have their own rules) and the law in other countries before expanding (Belgium is believed to ban resale above the original price).
4. **Liability cap (§11).** Limited to the amount of the order, except intent or gross negligence. Reasonable towards consumers (art. 6:237 BW, grey list)?
5. **Clawback from sellers (§8).** "If the seller was paid already, we may claim the money back." Is this enforceable against private sellers as written?
6. **Dispute decisions (§8).** Spare decides disputes between buyer and seller. Is this an acceptable arrangement, and does it need more detail (deadlines, evidence)?
7. **DSA (§1, §10).** Single contact point, statements of reasons, 6-month internal complaint window, mention of out-of-court settlement bodies. Spare is likely a micro or small enterprise, which exempts it from some DSA duties: confirm which still apply (art. 16 notice-and-action, art. 17 statements of reasons, and for marketplaces art. 30 trader traceability, which shouldn't apply since sellers are private).
8. **Changes (§13).** We promise an email 15 days before changes that matter. Is that the right period?
9. **Minimum age for buyers.** Sellers must be 18+. Nothing is said about buyers. Decide whether buyers must be 16+ (needed if we rely on consent, see below) and add it.
10. **Holding the buyer's money (§6, "Swap").** The buyer's payment stays with Spare until they slide "Swap" at the door (the ticket worked), and at the latest 24 hours after the night (72 for new sellers); then it goes to the seller. The money sits in Stripe (Stripe Connect), not in Spare's own bank account. Confirm Spare doesn't need a payment-services licence (DNB) for this, and that the terms describe it clearly enough.

## Privacy
1. **Legal bases.** Check each one, in particular legitimate interest for fraud checks, message moderation (keeping original texts of blocked messages) and marketing-link tracking (`?from=` codes).
2. **Phone numbers without SMS.** Until Twilio is set up, phone numbers aren't verified. The policy says so; check that this is fine.
3. **Retention.** Orders 7 years (art. 52 AWR), ticket files 14 days after the event, backups: 3 on the server and the emailed copies deleted after 30 days. **Leo: actually delete backup emails older than 30 days** (an iCloud Mail rule can move them to the bin), or change the promise.
4. **Processors.** Data processing agreements are needed with Render, Brevo and (if used) Twilio; Stripe is partly an independent controller. Accept each provider's DPA in their dashboard. Apple iCloud is a consumer service: is it acceptable for receiving business email and backups?
5. **US transfers.** Render, Stripe and Twilio are US companies. Check they're certified under the EU-US Data Privacy Framework, or that their standard contractual clauses are in place.
6. **Age of consent.** In the Netherlands it's 16 for consent-based processing (push notifications, waitlist, student badge).
7. **Records of processing (art. 30 GDPR).** Small businesses are mostly exempt, but not when processing isn't occasional. A short register is cheap to keep.
8. **Data breach procedure.** Not in the policy (not required there), but Spare should know what to do within 72 hours.
