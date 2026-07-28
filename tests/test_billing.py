from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.exc import OperationalError

from extensions import db
from models import ProcessedStripeEvent, Subscription, User


def stripe_event(event_id, event_type, data, *, created=100):
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {"object": data},
    }


def subscription_object(
    *,
    subscription_id="sub_1",
    customer="cus_1",
    price="price_pro",
    status="active",
):
    return {
        "id": subscription_id,
        "customer": customer,
        "status": status,
        "items": {
            "data": [
                {
                    "price": {"id": price},
                    "current_period_end": 2_000_000_000,
                }
            ]
        },
    }


def bind_subscription(app, user_id, *, created=0):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.stripe_customer_id = "cus_1"
        subscription = user.subscription
        if subscription is None:
            subscription = Subscription(user_id=user.id)
            db.session.add(subscription)
        subscription.plan = "pro"
        subscription.status = "active"
        subscription.stripe_sub_id = "sub_1"
        subscription.stripe_price_id = "price_pro"
        subscription.last_stripe_event_created = created or None
        db.session.commit()


def test_webhook_rejects_invalid_signature(client, monkeypatch):
    def reject(*_args, **_kwargs):
        raise ValueError("bad signature")

    monkeypatch.setattr("blueprints.billing.stripe.Webhook.construct_event", reject)
    response = client.post(
        "/billing/webhook",
        data=b"{}",
        headers={"Stripe-Signature": "invalid"},
    )
    assert response.status_code == 400


def test_duplicate_webhook_is_harmless_and_unknown_customer_is_recorded(
    client, app, monkeypatch
):
    event = stripe_event(
        "evt_unknown",
        "customer.subscription.updated",
        subscription_object(customer="cus_missing"),
    )
    monkeypatch.setattr(
        "blueprints.billing.stripe.Webhook.construct_event",
        lambda *_args, **_kwargs: event,
    )

    assert client.post("/billing/webhook").status_code == 200
    assert client.post("/billing/webhook").status_code == 200

    with app.app_context():
        assert db.session.get(ProcessedStripeEvent, "evt_unknown") is not None
        assert ProcessedStripeEvent.query.count() == 1


def test_wrong_price_fails_closed_and_older_update_cannot_reactivate(
    client, app, make_user, monkeypatch
):
    user = make_user("person@example.test")
    bind_subscription(app, user.id, created=100)
    events = [
        stripe_event(
            "evt_wrong_price",
            "customer.subscription.updated",
            subscription_object(price="price_unapproved"),
            created=200,
        ),
        stripe_event(
            "evt_deleted",
            "customer.subscription.deleted",
            subscription_object(status="canceled"),
            created=300,
        ),
        stripe_event(
            "evt_stale_active",
            "customer.subscription.updated",
            subscription_object(status="active"),
            created=250,
        ),
    ]
    monkeypatch.setattr(
        "blueprints.billing.stripe.Webhook.construct_event",
        lambda *_args, **_kwargs: events.pop(0),
    )
    monkeypatch.setattr("blueprints.billing._send_billing_email", lambda *_args: None)

    assert client.post("/billing/webhook").status_code == 200
    with app.app_context():
        sub = db.session.get(User, user.id).subscription
        assert sub.plan == "free"
        assert sub.stripe_price_id == "price_unapproved"

        # Restore an approved binding to exercise cancellation ordering.
        sub.plan = "pro"
        sub.stripe_price_id = "price_pro"
        db.session.commit()

    assert client.post("/billing/webhook").status_code == 200
    assert client.post("/billing/webhook").status_code == 200

    with app.app_context():
        sub = db.session.get(User, user.id).subscription
        assert sub.plan == "free"
        assert sub.status == "canceled"
        assert sub.last_stripe_event_created == 300


def test_checkout_completion_grants_only_configured_price_after_commit(
    client, app, make_user, monkeypatch
):
    user = make_user("person@example.test")
    event = stripe_event(
        "evt_checkout",
        "checkout.session.completed",
        {
            "client_reference_id": str(user.id),
            "customer": "cus_1",
            "subscription": "sub_1",
        },
    )
    notifications = []
    retrievals = []
    monkeypatch.setattr(
        "blueprints.billing.stripe.Webhook.construct_event",
        lambda *_args, **_kwargs: event,
    )

    def retrieve(subscription_id):
        retrievals.append(subscription_id)
        return subscription_object()

    monkeypatch.setattr(
        "blueprints.billing.stripe.Subscription.retrieve",
        retrieve,
    )
    monkeypatch.setattr(
        "blueprints.billing._send_billing_email",
        lambda stored_user, template: notifications.append((stored_user.id, template)),
    )

    assert client.post("/billing/webhook").status_code == 200
    assert client.post("/billing/webhook").status_code == 200

    with app.app_context():
        stored = db.session.get(User, user.id)
        assert stored.stripe_customer_id == "cus_1"
        assert stored.subscription.plan == "pro"
        assert stored.subscription.stripe_price_id == "price_pro"

    assert retrievals == ["sub_1"]
    assert notifications == [(user.id, "emails/payment_confirmed.txt")]


def test_failed_commit_keeps_event_retryable_without_notification(
    client, app, monkeypatch
):
    event = stripe_event("evt_commit_fail", "unhandled.event", {})
    monkeypatch.setattr(
        "blueprints.billing.stripe.Webhook.construct_event",
        lambda *_args, **_kwargs: event,
    )
    notifications = []
    monkeypatch.setattr(
        "blueprints.billing._send_billing_email",
        lambda *_args: notifications.append(True),
    )
    original_commit = db.session.commit

    def fail_commit():
        raise OperationalError("forced", {}, Exception("forced"))

    monkeypatch.setattr(db.session, "commit", fail_commit)
    response = client.post("/billing/webhook")
    monkeypatch.setattr(db.session, "commit", original_commit)

    assert response.status_code == 500
    with app.app_context():
        assert db.session.get(ProcessedStripeEvent, "evt_commit_fail") is None
    assert notifications == []


def test_checkout_reuses_existing_customer(
    client, app, make_user, login, monkeypatch
):
    user = make_user("person@example.test")
    with app.app_context():
        stored = db.session.get(User, user.id)
        stored.stripe_customer_id = "cus_existing"
        db.session.commit()
    login(user.email)

    captured = {}

    def create_session(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.test/session")

    monkeypatch.setattr(
        "blueprints.billing.stripe.checkout.Session.create",
        create_session,
    )
    response = client.post("/billing/create-checkout-session")

    assert response.status_code == 303
    assert captured["customer"] == "cus_existing"
    assert "customer_email" not in captured
    assert captured["line_items"] == [{"price": "price_pro", "quantity": 1}]
