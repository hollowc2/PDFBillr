from __future__ import annotations

import pytest

from extensions import db
from models import Invoice


@pytest.mark.parametrize(
    ("method", "suffix", "data"),
    [
        ("get", "", None),
        ("get", "/download", None),
        ("post", "/duplicate", {}),
        ("post", "/delete", {}),
        ("post", "/send", {"recipient_email": "client@example.test"}),
        ("post", "/public-link/rotate", {}),
        ("post", "/public-link/revoke", {}),
    ],
)
def test_invoice_routes_deny_cross_user_access(
    client, make_user, make_invoice, login, monkeypatch, method, suffix, data
):
    owner = make_user("owner@example.test")
    attacker = make_user("attacker@example.test", pro=True)
    invoice = make_invoice(owner.id)
    login(attacker.email)

    monkeypatch.setattr("blueprints.dashboard.render_pdf", lambda *_args, **_kwargs: b"pdf")
    monkeypatch.setattr("blueprints.dashboard.mail.send", lambda _message: None)

    response = getattr(client, method)(
        f"/dashboard/invoice/{invoice.id}{suffix}",
        data=data,
    )

    assert response.status_code == 404


def test_cross_user_denial_does_not_mutate_invoice(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    attacker = make_user("attacker@example.test", pro=True)
    invoice = make_invoice(owner.id)
    login(attacker.email)

    client.post(f"/dashboard/invoice/{invoice.id}/delete")
    with app.app_context():
        assert db.session.get(Invoice, invoice.id) is not None


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", "/edit"),
        ("post", "/edit"),
        ("post", "/toggle"),
        ("post", "/delete"),
    ],
)
def test_recurring_routes_deny_cross_user_access(
    client, make_user, make_recurring, login, method, suffix
):
    owner = make_user("owner@example.test")
    attacker = make_user("attacker@example.test", pro=True)
    recurring = make_recurring(owner.id)
    login(attacker.email)

    response = getattr(client, method)(
        f"/dashboard/recurring/{recurring.id}{suffix}",
        data={},
    )
    assert response.status_code == 404


def test_owner_can_delete_invoice(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id)
    login(owner.email)

    response = client.post(f"/dashboard/invoice/{invoice.id}/delete")

    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Invoice, invoice.id) is None


def test_repeated_invoice_duplicate_uses_distinct_numbers(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, invoice_number="CLIENT-42")
    login(owner.email)

    first = client.post(f"/dashboard/invoice/{invoice.id}/duplicate")
    second = client.post(f"/dashboard/invoice/{invoice.id}/duplicate")

    assert first.status_code == 302
    assert second.status_code == 302
    with app.app_context():
        numbers = {
            value
            for (value,) in (
                Invoice.query.filter_by(user_id=owner.id)
                .with_entities(Invoice.invoice_number)
                .all()
            )
        }
        assert numbers == {"CLIENT-42", "CLIENT-42-copy", "CLIENT-42-copy-2"}


def test_owner_can_send_invoice_with_mocked_mail_and_pdf(
    client, app, make_user, make_invoice, login, monkeypatch
):
    owner = make_user("owner@example.test", pro=True)
    invoice = make_invoice(owner.id, to_email="client@example.test")
    login(owner.email)
    messages = []
    monkeypatch.setattr(
        "blueprints.dashboard.render_pdf",
        lambda *_args, **_kwargs: b"%PDF-test",
    )
    monkeypatch.setattr(
        "blueprints.dashboard.mail.send",
        lambda message: messages.append(message),
    )

    response = client.post(f"/dashboard/invoice/{invoice.id}/send")

    assert response.status_code == 302
    assert len(messages) == 1
    assert messages[0].recipients == ["client@example.test"]
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "sent"
        assert stored.sent_at is not None


def test_invoice_send_rejects_header_injection_before_mail(
    client, make_user, make_invoice, login, monkeypatch
):
    owner = make_user("owner@example.test", pro=True)
    invoice = make_invoice(owner.id)
    login(owner.email)
    messages = []
    monkeypatch.setattr(
        "blueprints.dashboard.mail.send",
        lambda message: messages.append(message),
    )

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/send",
        data={"recipient_email": "victim@example.test\nBcc: attacker@example.test"},
    )

    assert response.status_code == 302
    assert messages == []
