from datetime import datetime, timezone

import stripe
from flask import (
    Blueprint, current_app, flash, jsonify, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required

from extensions import db
from models import Subscription, User

bp = Blueprint("billing", __name__, url_prefix="/billing")


def _stripe():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe


# ---------------------------------------------------------------------------
# Upgrade page (public — shown to any non-Pro user)
# ---------------------------------------------------------------------------

@bp.route("/upgrade")
def upgrade():
    return render_template(
        "billing/upgrade.html",
        publishable_key=current_app.config["STRIPE_PUBLISHABLE_KEY"],
    )


# ---------------------------------------------------------------------------
# Create Stripe Checkout Session
# ---------------------------------------------------------------------------

@bp.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    s = _stripe()
    price_id = current_app.config["STRIPE_PRICE_ID_PRO"]
    if not price_id:
        flash("Billing is not configured yet.", "error")
        return redirect(url_for("billing.upgrade"))

    try:
        session = s.checkout.Session.create(
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
    flash("Subscription activated! Welcome to Pro.", "success")
    return redirect(url_for("dashboard.index"))


# ---------------------------------------------------------------------------
# Stripe Customer Portal
# ---------------------------------------------------------------------------

@bp.route("/portal")
@login_required
def portal():
    s = _stripe()
    if not current_user.stripe_customer_id:
        flash("No billing account found.", "error")
        return redirect(url_for("dashboard.index"))
    try:
        session = s.billing_portal.Session.create(
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
def webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret     = current_app.config["STRIPE_WEBHOOK_SECRET"]

    try:
        event = _stripe().Webhook.construct_event(payload, sig_header, secret)
    except (ValueError, stripe.SignatureVerificationError):
        return jsonify({"error": "invalid signature"}), 400

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
    _upsert_subscription(user, sub_id, status="active")
    db.session.commit()


def _handle_subscription_updated(sub_obj) -> None:
    user = _user_by_customer(sub_obj.get("customer"))
    if not user:
        return
    status = sub_obj.get("status", "active")
    period_end = sub_obj.get("current_period_end")
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
        db.session.commit()


def _handle_payment_failed(invoice_obj) -> None:
    user = _user_by_customer(invoice_obj.get("customer"))
    if not user:
        return
    sub = user.subscription
    if sub:
        sub.status = "past_due"
        sub.updated_at = datetime.now(timezone.utc)
        db.session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_by_customer(customer_id: str | None) -> User | None:
    if not customer_id:
        return None
    return User.query.filter_by(stripe_customer_id=customer_id).first()


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
