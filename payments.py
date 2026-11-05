"""Payments through Stripe Connect ("separate charges and transfers").

The buyer pays Spare. The money stays on the platform's Stripe balance
until the payout date, then the ticket price is transferred to the seller's
connected Stripe account. Spare never holds funds outside Stripe.

Without STRIPE_SECRET_KEY, every function falls back to a simulated flow so
the full site can be used locally.
"""
import time

import config


def live():
    """True when real Stripe calls are made (test or live keys)."""
    return bool(config.STRIPE_SECRET_KEY)


def is_test_mode():
    return not config.STRIPE_SECRET_KEY or config.STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_"))


def _stripe():
    import stripe

    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


def create_checkout(order, title, buyer_email, success_url, cancel_url, ideal_only=False, strong_card_check=False):
    """Returns (session_id, redirect_url). success_url must contain {CHECKOUT_SESSION_ID}.
    ideal_only (#27): first-time buyers pay by iDEAL, which can't be charged back like a card.
    strong_card_check (#38): always ask for 3D Secure on a card, which moves most fraud chargebacks to the card's bank."""
    stripe = _stripe()
    items = [{
        "price_data": {"currency": "eur", "unit_amount": order["price_cents"], "product_data": {"name": title}},
        "quantity": 1,
    }]
    if order["fee_cents"]:
        items.append({
            "price_data": {"currency": "eur", "unit_amount": order["fee_cents"],
                           "product_data": {"name": "Spare service fee"}},
            "quantity": 1,
        })
    extra = {"payment_method_types": ["ideal"]} if ideal_only else {}
    if strong_card_check and not ideal_only:
        extra["payment_method_options"] = {"card": {"request_three_d_secure": "any"}}
    session = stripe.checkout.Session.create(
        mode="payment",  # payment methods (iDEAL, card) are chosen in Dashboard → Settings → Payment methods
        **extra,
        customer_email=buyer_email,
        line_items=items,
        metadata={"order": order["public_id"]},
        payment_intent_data={"transfer_group": order["public_id"], "metadata": {"order": order["public_id"]}},
        expires_at=int(time.time()) + config.RESERVATION_MINUTES * 60 + 30,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.id, session.url


def checkout_status(session_id):
    """Returns (paid: bool, payment_intent_id or None)."""
    session = _stripe().checkout.Session.retrieve(session_id)
    return session.payment_status == "paid", session.payment_intent


def expire_checkout(session_id):
    try:
        _stripe().checkout.Session.expire(session_id)
    except Exception:
        pass  # already completed or expired


def refund(order):
    if not live() or not order["payment_intent"]:
        return "test-refund"
    # #26: the same key twice gives the same refund back instead of refunding again
    return _stripe().Refund.create(payment_intent=order["payment_intent"], idempotency_key="refund-" + order["public_id"]).id


def transfer_to_seller(order, seller):
    if not live():
        return "test-transfer"
    stripe = _stripe()
    intent = stripe.PaymentIntent.retrieve(order["payment_intent"])
    transfer = stripe.Transfer.create(
        amount=order["price_cents"] - (order["seller_fee_cents"] or 0),  # the seller fee stays with Spare
        currency="eur",
        destination=seller["stripe_account_id"],
        transfer_group=order["public_id"],
        source_transaction=intent.latest_charge,
        idempotency_key="payout-" + order["public_id"],  # #26: a retry can never pay the seller twice
    )
    return transfer.id


def payment_method_type(payment_intent_id):
    """How the buyer paid: 'ideal', 'card', ... or None when Stripe can't tell. The pretend checkout counts as iDEAL."""
    if not live():
        return "ideal"
    if not payment_intent_id:
        return None
    try:
        intent = _stripe().PaymentIntent.retrieve(payment_intent_id, expand=["latest_charge"])
        charge = intent.latest_charge
        details = getattr(charge, "payment_method_details", None) if charge else None
        return getattr(details, "type", None) if details else None
    except Exception:
        return None


def reverse_transfer(order):
    """#27: takes a seller's payout back (the money returns from their Stripe balance to Spare's)."""
    if not live():
        return "test-reversal"
    if not order["transfer_ref"] or not order["transfer_ref"].startswith("tr_"):
        return None  # #39: nothing to reverse (yet): take_back_payout tells the admins instead of claiming success
    return _stripe().Transfer.create_reversal(order["transfer_ref"], idempotency_key="reversal-" + order["public_id"]).id


def find_transfer(order):
    """#26: the transfer already sent for this order (by its transfer group), or None."""
    if not live():
        return None
    found = _stripe().Transfer.list(transfer_group=order["public_id"], limit=1)
    return found.data[0].id if found.data else None


def _v2(method, path, **params):
    """Stripe's Accounts v2 API, required for new Connect integrations."""
    import stripe

    client = stripe.StripeClient(config.STRIPE_SECRET_KEY)
    return client.deserialize(client.raw_request(method, path, **params), api_mode="V2")


def payout_onboarding_url(user, return_url, refresh_url):
    """Returns (stripe_account_id, url) for Stripe's hosted payout setup.

    Sellers are "recipient" accounts: they only receive transfers from
    Spare. Spare collects the fees and covers refunds and losses.
    """
    account_id = user["stripe_account_id"]
    if not account_id:
        account = _v2(
            "post", "/v2/core/accounts",
            contact_email=user["email"],
            display_name=user["first_name"] or user["email"],
            identity={"country": "NL", "entity_type": "individual"},
            dashboard="express",
            defaults={"currency": "eur",
                      "responsibilities": {"fees_collector": "application", "losses_collector": "application"},
                      # Filled in for them, so a student selling one ticket isn't asked for "your website".
                      "profile": {"product_description": "Resells personal event tickets they can no longer use, "
                                                         "to other students through Spare (sparetickets.eu)."}},
            configuration={"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}}},
        )
        account_id = account.id
    link = _v2(
        "post", "/v2/core/account_links",
        account=account_id,
        use_case={"type": "account_onboarding",
                  "account_onboarding": {"refresh_url": refresh_url, "return_url": return_url}},
    )
    return account_id, link.url


def payouts_ready(account_id):
    if not account_id:
        return False
    account = _v2("get", "/v2/core/accounts/%s" % account_id, include=["configuration.recipient"])
    recipient = account.configuration.recipient
    return bool(recipient) and recipient.capabilities.stripe_balance.stripe_transfers.status == "active"


def bank_fingerprint_check(account_id):
    """#40: what Stripe says about a seller's payout bank account, for the check-bank-fingerprint command."""
    if not live():
        return {"live": False, "note": "No Stripe key: nothing to check."}
    try:
        accounts = _stripe().Account.list_external_accounts(account_id, object="bank_account", limit=1)
        data = accounts.data[0] if accounts.data else None
        return {"live": True, "found": bool(data), "fingerprint": getattr(data, "fingerprint", None) if data else None,
                "last4": getattr(data, "last4", None) if data else None}
    except Exception as exc:
        return {"live": True, "error": "%s: %s" % (type(exc).__name__, exc)}


def bank_fingerprint(account_id):
    """#24: Stripe's fingerprint of the seller's payout bank account (the same for the same IBAN across all of
    Spare's sellers), or None when Stripe doesn't share it for this account."""
    if not live() or not account_id:
        return None
    try:
        accounts = _stripe().Account.list_external_accounts(account_id, object="bank_account", limit=1)
        return accounts.data[0].fingerprint if accounts.data else None
    except Exception:
        return None


def parse_webhook(payload, signature):
    return _stripe().Webhook.construct_event(payload, signature, config.STRIPE_WEBHOOK_SECRET)
