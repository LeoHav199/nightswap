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


def create_checkout(order, title, buyer_email, success_url, cancel_url):
    """Returns (session_id, redirect_url). success_url must contain {CHECKOUT_SESSION_ID}."""
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
    session = stripe.checkout.Session.create(
        mode="payment",  # payment methods (iDEAL, card) are chosen in Dashboard → Settings → Payment methods
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
    return _stripe().Refund.create(payment_intent=order["payment_intent"]).id


def transfer_to_seller(order, seller):
    if not live():
        return "test-transfer"
    stripe = _stripe()
    intent = stripe.PaymentIntent.retrieve(order["payment_intent"])
    transfer = stripe.Transfer.create(
        amount=order["price_cents"],
        currency="eur",
        destination=seller["stripe_account_id"],
        transfer_group=order["public_id"],
        source_transaction=intent.latest_charge,
    )
    return transfer.id


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
                      "responsibilities": {"fees_collector": "application", "losses_collector": "application"}},
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


def parse_webhook(payload, signature):
    return _stripe().Webhook.construct_event(payload, signature, config.STRIPE_WEBHOOK_SECRET)
