from datetime import datetime, timezone

import stripe
from flask import (
    Blueprint, current_app, flash, jsonify, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required

from flask_mail import Message

from extensions import csrf, db, mail
from models import ProcessedStripeEvent, Subscription, User

bp = Blueprint("billing", __name__, url_prefix="/billing")


# ---------------------------------------------------------------------------
# Upgrade page (public — shown to any non-Pro user)
# ---------------------------------------------------------------------------

@bp.route("/upgrade")
def upgrade():
    return render_template("billing/upgrade.html")


# ---------------------------------------------------------------------------
# Create Stripe Checkout Session
# ---------------------------------------------------------------------------

@bp.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    price_id = current_app.config["STRIPE_PRICE_ID_PRO"]
    if not price_id:
        flash("Billing is not configured yet.", "error")
        return redirect(url_for("billing.upgrade"))

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=str(current_user.id),
            customer_email=current_user.email,
            success_url=url_for("billing.success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("billing.upgrade", _external=True),
        )
    except stripe.StripeError as e:
        flash(f"Stripe error: {e.user_message}", "error")
        return redirect(url_for("billing.upgrade"))

    return redirect(session.url, code=303)


# ---------------------------------------------------------------------------
# Post-checkout success landing
# ---------------------------------------------------------------------------

@bp.route("/success")
@login_required
def success():
    flash("Payment received! Your Pro subscription will be active within a few seconds.", "success")
    return redirect(url_for("dashboard.index"))


# ---------------------------------------------------------------------------
# Stripe Customer Portal
# ---------------------------------------------------------------------------

@bp.route("/portal")
@login_required
def portal():
    if not current_user.stripe_customer_id:
        flash("No billing account found.", "error")
        return redirect(url_for("dashboard.index"))
    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=url_for("dashboard.index", _external=True),
        )
    except stripe.StripeError as e:
        flash(f"Stripe error: {e.user_message}", "error")
        return redirect(url_for("dashboard.index"))
    return redirect(session.url, code=303)


# ---------------------------------------------------------------------------
# Webhook — no @login_required, verified via Stripe signature
# ---------------------------------------------------------------------------

@bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret     = current_app.config["STRIPE_WEBHOOK_SECRET"]

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        current_app.logger.warning("Webhook signature failure: %s", exc)
        return jsonify({"error": "invalid signature"}), 400

    # Idempotency: skip events already processed
    event_id = event["id"]
    if db.session.get(ProcessedStripeEvent, event_id):
        return jsonify({"received": True}), 200
    db.session.add(ProcessedStripeEvent(stripe_event_id=event_id))

    etype = event["type"]
    data  = event["data"]["object"]

    if etype == "checkout.session.completed":
        _handle_checkout_completed(data)
    elif etype == "customer.subscription.updated":
        _handle_subscription_updated(data)
    elif etype == "customer.subscription.deleted":
        _handle_subscription_deleted(data)
    elif etype == "invoice.payment_failed":
        _handle_payment_failed(data)
    elif etype == "invoice.paid":
        _handle_invoice_paid(data)
    else:
        db.session.commit()

    return jsonify({"received": True}), 200


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------

def _handle_checkout_completed(session_obj) -> None:
    user_id = session_obj.get("client_reference_id")
    if not user_id:
        return
    user = db.session.get(User, int(user_id))
    if not user:
        return

    # Store Stripe customer ID
    customer_id = session_obj.get("customer")
    if customer_id and not user.stripe_customer_id:
        user.stripe_customer_id = customer_id

    sub_id = session_obj.get("subscription")
    if sub_id:
        # Retrieve full subscription to capture period_end and price_id immediately
        sub_obj = stripe.Subscription.retrieve(sub_id)
        price_id = None
        items_data = sub_obj.get("items", {}).get("data", [])
        if items_data:
            price_id = items_data[0].get("price", {}).get("id")
        _upsert_subscription(
            user, sub_id,
            status=sub_obj.get("status", "active"),
            period_end=_period_end_from_sub(sub_obj),
            price_id=price_id,
        )
    else:
        _upsert_subscription(user, sub_id, status="active")

    _send_billing_email(user, "emails/payment_confirmed.txt")
    db.session.commit()


def _handle_subscription_updated(sub_obj) -> None:
    user = _user_by_customer(sub_obj.get("customer"))
    if not user:
        return
    status = sub_obj.get("status", "active")
    period_end = _period_end_from_sub(sub_obj)
    _upsert_subscription(
        user,
        sub_obj["id"],
        status=status,
        period_end=period_end,
        price_id=sub_obj.get("items", {}).get("data", [{}])[0].get("price", {}).get("id"),
    )
    db.session.commit()


def _handle_subscription_deleted(sub_obj) -> None:
    user = _user_by_customer(sub_obj.get("customer"))
    if not user:
        return
    sub = user.subscription
    if sub:
        sub.status = "canceled"
        sub.updated_at = datetime.now(timezone.utc)
        _send_billing_email(user, "emails/subscription_canceled.txt")
        db.session.commit()


def _handle_payment_failed(invoice_obj) -> None:
    user = _user_by_customer(invoice_obj.get("customer"))
    if not user:
        return
    sub = user.subscription
    if sub:
        sub.status = "past_due"
        sub.updated_at = datetime.now(timezone.utc)
        _send_billing_email(user, "emails/payment_failed.txt")
        db.session.commit()


def _handle_invoice_paid(invoice_obj) -> None:
    """Refresh period_end on successful renewal payment."""
    user = _user_by_customer(invoice_obj.get("customer"))
    if not user:
        return
    sub_id = invoice_obj.get("subscription")
    if not sub_id or not user.subscription:
        return
    sub_obj = stripe.Subscription.retrieve(sub_id)
    period_end = _period_end_from_sub(sub_obj)
    if period_end is not None:
        user.subscription.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
        user.subscription.status = sub_obj.get("status", "active")
        user.subscription.updated_at = datetime.now(timezone.utc)
        db.session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _period_end_from_sub(sub_obj) -> int | None:
    """Extract current_period_end from a Stripe subscription object.

    Stripe API >= 2024-09-30 moved current_period_end from the top-level
    subscription onto each SubscriptionItem.
    """
    period_end = sub_obj.get("current_period_end")
    if period_end is not None:
        return period_end
    items = sub_obj.get("items", {}).get("data", [])
    if items:
        return items[0].get("current_period_end")
    return None


def _user_by_customer(customer_id: str | None) -> User | None:
    if not customer_id:
        return None
    return User.query.filter_by(stripe_customer_id=customer_id).first()


_BILLING_EMAIL_SUBJECTS = {
    "emails/payment_confirmed.txt":      "Your PDFBillr Pro subscription is active",
    "emails/payment_failed.txt":         "Action required: PDFBillr payment failed",
    "emails/subscription_canceled.txt":  "Your PDFBillr Pro subscription has ended",
}


def _send_billing_email(user: User, template: str) -> None:
    subject = _BILLING_EMAIL_SUBJECTS.get(template, "PDFBillr account update")
    msg = Message(subject=subject, recipients=[user.email],
                  body=render_template(template, user=user))
    try:
        mail.send(msg)
    except Exception as exc:
        current_app.logger.warning("Billing email failed for user %s (%s): %s", user.id, template, exc)


def _upsert_subscription(
    user: User,
    stripe_sub_id: str | None,
    status: str = "active",
    period_end: int | None = None,
    price_id: str | None = None,
) -> None:
    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id, plan="pro")
        db.session.add(sub)

    sub.plan           = "pro"
    sub.stripe_sub_id  = stripe_sub_id
    sub.status         = status
    sub.updated_at     = datetime.now(timezone.utc)
    if price_id:
        sub.stripe_price_id = price_id
    if period_end is not None:
        sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
