from datetime import datetime, timedelta, timezone

import stripe
from flask import (
    Blueprint, current_app, flash, jsonify, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required

from flask_mail import Message
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import csrf, db, limiter, mail
from models import (
    BillingNotificationDelivery,
    ProcessedStripeEvent,
    Subscription,
    User,
)
from utils.gating import is_pro
from utils.urls import external_url

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
    if is_pro():
        flash("Your Pro subscription is already active.", "info")
        return redirect(url_for("dashboard.index"))

    try:
        checkout_args = {
            "payment_method_types": ["card"],
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": str(current_user.id),
            "success_url": (
                external_url("billing.success")
                + "?session_id={CHECKOUT_SESSION_ID}"
            ),
            "cancel_url": external_url("billing.upgrade"),
        }
        if current_user.stripe_customer_id:
            checkout_args["customer"] = current_user.stripe_customer_id
        else:
            checkout_args["customer_email"] = current_user.email

        session = stripe.checkout.Session.create(
            **checkout_args,
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
@limiter.limit("10 per minute")
def portal():
    if not current_user.stripe_customer_id:
        flash("No billing account found.", "error")
        return redirect(url_for("dashboard.index"))
    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=external_url("dashboard.index"),
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

    try:
        event_id = str(event["id"])
        event_type = str(event["type"])
        event_created = int(event["created"])
        if event_created <= 0:
            raise ValueError
        data = event["data"]["object"]
    except (KeyError, TypeError, ValueError):
        current_app.logger.warning("Malformed signed Stripe event")
        return jsonify({"error": "malformed event"}), 400

    if db.session.get(ProcessedStripeEvent, event_id):
        _dispatch_billing_notifications(stripe_event_id=event_id)
        return jsonify({"received": True}), 200

    notification = None
    try:
        notification = _process_event(
            event_type,
            data,
            event_created=event_created,
        )
        # Effects and the processed marker become durable together. Email is
        # deliberately deferred until after this commit.
        db.session.add(ProcessedStripeEvent(stripe_event_id=event_id))
        db.session.flush()
        if notification:
            user_id, template = notification
            db.session.add(
                BillingNotificationDelivery(
                    stripe_event_id=event_id,
                    user_id=user_id,
                    template=template,
                )
            )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # A concurrent duplicate may have won the primary-key race.
        if db.session.get(ProcessedStripeEvent, event_id):
            return jsonify({"received": True}), 200
        current_app.logger.exception(
            "Stripe event idempotency conflict for event %s", event_id
        )
        return jsonify({"error": "temporary failure"}), 500
    except (SQLAlchemyError, stripe.StripeError, KeyError, TypeError, ValueError):
        db.session.rollback()
        current_app.logger.exception(
            "Stripe event processing failed: id=%s type=%s",
            event_id,
            event_type,
        )
        return jsonify({"error": "temporary failure"}), 500

    if notification:
        _dispatch_billing_notifications(stripe_event_id=event_id)

    return jsonify({"received": True}), 200


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------

def _process_event(event_type, data, *, event_created):
    if event_type == "checkout.session.completed":
        return _handle_checkout_completed(data, event_created=event_created)
    if event_type == "customer.subscription.updated":
        return _handle_subscription_updated(data, event_created=event_created)
    if event_type == "customer.subscription.deleted":
        return _handle_subscription_deleted(data, event_created=event_created)
    if event_type == "invoice.payment_failed":
        return _handle_payment_failed(data, event_created=event_created)
    if event_type == "invoice.paid":
        return _handle_invoice_paid(data, event_created=event_created)
    return None


def _handle_checkout_completed(session_obj, *, event_created):
    user_id = session_obj.get("client_reference_id")
    sub_id = session_obj.get("subscription")
    customer_id = session_obj.get("customer")
    if not user_id or not sub_id or not customer_id:
        return None
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    if not user:
        return None

    if user.stripe_customer_id and user.stripe_customer_id != customer_id:
        current_app.logger.warning(
            "Ignored checkout with conflicting customer for user %s",
            user.id,
        )
        return None

    sub_obj = stripe.Subscription.retrieve(sub_id)
    price_id = _price_id_from_sub(sub_obj)
    if not _is_configured_price(price_id):
        current_app.logger.warning(
            "Ignored checkout with unconfigured price for user %s",
            user.id,
        )
        return None
    if (
        user.subscription
        and user.subscription.stripe_sub_id
        and user.subscription.stripe_sub_id != sub_id
        and user.subscription.status in {"active", "trialing", "past_due"}
    ):
        current_app.logger.warning(
            "Ignored second subscription binding for user %s",
            user.id,
        )
        return None

    user.stripe_customer_id = customer_id
    _upsert_subscription(
        user,
        sub_id,
        status=sub_obj.get("status", "active"),
        period_end=_period_end_from_sub(sub_obj),
        price_id=price_id,
        event_created=event_created,
    )
    return user.id, "emails/payment_confirmed.txt"


def _handle_subscription_updated(sub_obj, *, event_created):
    user = _user_by_customer(sub_obj.get("customer"))
    if not user:
        return None
    sub_id = sub_obj.get("id")
    if not sub_id or _different_bound_subscription(user, sub_id):
        return None
    if user.subscription and _is_stale(user.subscription, event_created):
        return None

    status = sub_obj.get("status", "active")
    period_end = _period_end_from_sub(sub_obj)
    price_id = _price_id_from_sub(sub_obj)
    if not _is_configured_price(price_id):
        if user.subscription and user.subscription.stripe_sub_id == sub_id:
            user.subscription.plan = "free"
            user.subscription.status = status
            user.subscription.stripe_price_id = price_id
            _record_event_time(user.subscription, event_created)
        current_app.logger.warning(
            "Subscription %s uses an unconfigured price", sub_id
        )
        return None

    _upsert_subscription(
        user,
        sub_id,
        status=status,
        period_end=period_end,
        price_id=price_id,
        event_created=event_created,
    )
    return None


def _handle_subscription_deleted(sub_obj, *, event_created):
    user = _user_by_customer(sub_obj.get("customer"))
    if not user:
        return None
    sub = user.subscription
    if (
        not sub
        or _different_bound_subscription(user, sub_obj.get("id"))
        or _is_stale(sub, event_created)
    ):
        return None
    sub.plan = "free"
    sub.status = "canceled"
    sub.updated_at = datetime.now(timezone.utc)
    _record_event_time(sub, event_created)
    return user.id, "emails/subscription_canceled.txt"


def _handle_payment_failed(invoice_obj, *, event_created):
    user = _user_by_customer(invoice_obj.get("customer"))
    if not user:
        return None
    sub = user.subscription
    invoice_sub_id = _invoice_subscription_id(invoice_obj)
    if (
        not sub
        or not invoice_sub_id
        or sub.stripe_sub_id != invoice_sub_id
        or _is_stale(sub, event_created)
    ):
        return None
    sub.status = "past_due"
    sub.updated_at = datetime.now(timezone.utc)
    _record_event_time(sub, event_created)
    return user.id, "emails/payment_failed.txt"


def _handle_invoice_paid(invoice_obj, *, event_created):
    """Refresh period_end on successful renewal payment."""
    user = _user_by_customer(invoice_obj.get("customer"))
    if not user:
        return None
    sub_id = _invoice_subscription_id(invoice_obj)
    if (
        not sub_id
        or not user.subscription
        or user.subscription.stripe_sub_id != sub_id
        or _is_stale(user.subscription, event_created)
    ):
        return None
    sub_obj = stripe.Subscription.retrieve(sub_id)
    price_id = _price_id_from_sub(sub_obj)
    if not _is_configured_price(price_id):
        user.subscription.plan = "free"
        user.subscription.stripe_price_id = price_id
        _record_event_time(user.subscription, event_created)
        return None
    period_end = _period_end_from_sub(sub_obj)
    if period_end is not None:
        user.subscription.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
        user.subscription.plan = "pro"
        user.subscription.stripe_price_id = price_id
        user.subscription.status = sub_obj.get("status", "active")
        user.subscription.updated_at = datetime.now(timezone.utc)
        _record_event_time(user.subscription, event_created)
    return None


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


def _price_id_from_sub(sub_obj) -> str | None:
    items = sub_obj.get("items", {}).get("data", [])
    if not items:
        return None
    return items[0].get("price", {}).get("id")


def _invoice_subscription_id(invoice_obj) -> str | None:
    direct = invoice_obj.get("subscription")
    if direct:
        return direct
    return (
        invoice_obj.get("parent", {})
        .get("subscription_details", {})
        .get("subscription")
    )


def _is_configured_price(price_id: str | None) -> bool:
    configured = current_app.config.get("STRIPE_PRICE_ID_PRO")
    return bool(configured and price_id == configured)


def _different_bound_subscription(user: User, stripe_sub_id: str | None) -> bool:
    return bool(
        user.subscription
        and user.subscription.stripe_sub_id
        and user.subscription.stripe_sub_id != stripe_sub_id
    )


def _is_stale(sub: Subscription, event_created: int) -> bool:
    return bool(
        event_created
        and sub.last_stripe_event_created
        and event_created < sub.last_stripe_event_created
    )


def _record_event_time(sub: Subscription, event_created: int) -> None:
    if event_created:
        sub.last_stripe_event_created = event_created


_BILLING_EMAIL_SUBJECTS = {
    "emails/payment_confirmed.txt":      "Your PDFBillr Pro subscription is active",
    "emails/payment_failed.txt":         "Action required: PDFBillr payment failed",
    "emails/subscription_canceled.txt":  "Your PDFBillr Pro subscription has ended",
}
_NOTIFICATION_LEASE = timedelta(minutes=15)


def retry_billing_notifications(app) -> int:
    """Retry pending Stripe notification email from the scheduler process."""
    with app.app_context():
        return _dispatch_billing_notifications()


def _dispatch_billing_notifications(
    *,
    stripe_event_id: str | None = None,
    limit: int = 100,
) -> int:
    """Claim and deliver durable Stripe notification rows."""
    now = datetime.now(timezone.utc)
    stale_before = now - _NOTIFICATION_LEASE
    eligible = or_(
        BillingNotificationDelivery.status.in_(("pending", "failed")),
        (
            (BillingNotificationDelivery.status == "sending")
            & (BillingNotificationDelivery.last_attempt_at < stale_before)
        ),
    )
    query = BillingNotificationDelivery.query.filter(eligible)
    if stripe_event_id is not None:
        query = query.filter(
            BillingNotificationDelivery.stripe_event_id == stripe_event_id
        )
    delivery_ids = [
        row.id
        for row in (
            query.order_by(
                BillingNotificationDelivery.created_at,
                BillingNotificationDelivery.id,
            )
            .limit(limit)
            .all()
        )
    ]

    sent_count = 0
    for delivery_id in delivery_ids:
        claimed = (
            BillingNotificationDelivery.query.filter(
                BillingNotificationDelivery.id == delivery_id,
                eligible,
            )
            .update(
                {
                    BillingNotificationDelivery.status: "sending",
                    BillingNotificationDelivery.attempt_count: (
                        BillingNotificationDelivery.attempt_count + 1
                    ),
                    BillingNotificationDelivery.last_attempt_at: now,
                    BillingNotificationDelivery.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        db.session.commit()
        if claimed != 1:
            continue

        delivery = db.session.get(BillingNotificationDelivery, delivery_id)
        if delivery.user is None:
            delivery.status = "discarded"
            delivery.last_error = "UserUnavailable"
            delivery.updated_at = datetime.now(timezone.utc)
            db.session.commit()
            continue

        try:
            _send_billing_email(delivery.user, delivery.template)
        except Exception as exc:  # SMTP adapters expose varied exception types.
            db.session.rollback()
            delivery = db.session.get(BillingNotificationDelivery, delivery_id)
            delivery.status = "failed"
            delivery.last_error = type(exc).__name__[:500]
            delivery.updated_at = datetime.now(timezone.utc)
            db.session.commit()
            current_app.logger.warning(
                "Billing notification failed: delivery=%s event=%s error=%s",
                delivery.id,
                delivery.stripe_event_id,
                type(exc).__name__,
            )
            continue

        delivery.status = "sent"
        delivery.sent_at = datetime.now(timezone.utc)
        delivery.last_error = None
        delivery.updated_at = delivery.sent_at
        db.session.commit()
        sent_count += 1

    return sent_count


def _send_billing_email(user: User, template: str) -> None:
    subject = _BILLING_EMAIL_SUBJECTS.get(template, "PDFBillr account update")
    msg = Message(
        subject=subject,
        recipients=[user.email],
        body=render_template(
            template,
            user=user,
            portal_url=external_url("billing.portal"),
            upgrade_url=external_url("billing.upgrade"),
        ),
    )
    mail.send(msg)


def _upsert_subscription(
    user: User,
    stripe_sub_id: str | None,
    status: str = "active",
    period_end: int | None = None,
    price_id: str | None = None,
    event_created: int = 0,
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
    _record_event_time(sub, event_created)
