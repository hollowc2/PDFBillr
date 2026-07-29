from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from extensions import db
from models import Invoice, InvoicePayment


def test_recording_partial_then_final_payment_updates_balance_and_status(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, total=100.0, total_decimal=Decimal("100.00"))
    login(owner.email)

    partial = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={
            "amount": "35.25",
            "method": "bank_transfer",
            "reference": "BANK-123",
            "note": "First installment",
        },
    )

    assert partial.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "partial"
        assert stored.amount_paid == Decimal("35.25")
        assert stored.balance_due == Decimal("64.75")
        payment = InvoicePayment.query.filter_by(invoice_id=invoice.id).one()
        assert payment.method == "bank_transfer"
        assert payment.reference == "BANK-123"

    final = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "64.75", "method": "card"},
    )

    assert final.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "paid"
        assert stored.paid_at is not None
        assert stored.amount_paid == Decimal("100.00")
        assert stored.balance_due == Decimal("0.00")
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 2


@pytest.mark.parametrize("amount", ["", "not-money", "0", "-1", "1.001", "NaN"])
def test_invalid_payment_amount_is_rejected(
    client, app, make_user, make_invoice, login, amount
):
    owner = make_user(f"owner-{amount}@example.test")
    invoice = make_invoice(owner.id, total=10.0)
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": amount, "method": "cash"},
    )

    assert response.status_code == 302
    with app.app_context():
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 0
        assert db.session.get(Invoice, invoice.id).status == "draft"


def test_overpayment_is_rejected_without_mutation(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, total=10.0)
    login(owner.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "10.01", "method": "cash"},
    )

    assert response.status_code == 302
    with app.app_context():
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 0
        assert db.session.get(Invoice, invoice.id).balance_due == Decimal("10.00")


def test_mark_paid_records_only_the_outstanding_balance_and_is_idempotent(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, total=80.0)
    login(owner.email)
    client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "30.00", "method": "check"},
    )

    first = client.post(f"/dashboard/invoice/{invoice.id}/mark-paid")
    second = client.post(f"/dashboard/invoice/{invoice.id}/mark-paid")

    assert first.status_code == 302
    assert second.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "paid"
        assert stored.amount_paid == Decimal("80.00")
        assert stored.balance_due == Decimal("0.00")
        payments = InvoicePayment.query.filter_by(invoice_id=invoice.id).all()
        assert sorted(payment.amount for payment in payments) == [
            Decimal("30.00"),
            Decimal("50.00"),
        ]


def test_void_stops_collection_and_rejects_new_payments(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, status="sent", total=10.0)
    login(owner.email)

    voided = client.post(f"/dashboard/invoice/{invoice.id}/void")
    payment = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "10.00", "method": "cash"},
    )

    assert voided.status_code == 302
    assert payment.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "void"
        assert stored.voided_at is not None
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 0


def test_invoice_with_payment_cannot_be_voided(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, total=10.0)
    login(owner.email)
    client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "5.00", "method": "cash"},
    )

    response = client.post(f"/dashboard/invoice/{invoice.id}/void")

    assert response.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "partial"
        assert stored.voided_at is None


def test_paid_invoice_rejects_additional_payment_and_void(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, total=10.0)
    login(owner.email)
    client.post(f"/dashboard/invoice/{invoice.id}/mark-paid")

    extra_payment = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "1.00", "method": "cash"},
    )
    voided = client.post(f"/dashboard/invoice/{invoice.id}/void")

    assert extra_payment.status_code == 302
    assert voided.status_code == 302
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "paid"
        assert stored.voided_at is None
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 1


def test_overdue_is_derived_without_rewriting_persisted_status(
    app, make_user, make_invoice
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(
        owner.id,
        status="sent",
        due_date=(date.today() - timedelta(days=1)).isoformat(),
        total=10.0,
    )

    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.display_status == "overdue"
        assert stored.status == "sent"
        stored.status = "paid"
        assert stored.display_status == "paid"


@pytest.mark.parametrize(
    ("suffix", "data"),
    [
        ("/payments", {"amount": "1.00", "method": "cash"}),
        ("/mark-paid", {}),
        ("/void", {}),
    ],
)
def test_payment_routes_deny_cross_user_access(
    client, app, make_user, make_invoice, login, suffix, data
):
    owner = make_user("owner@example.test")
    attacker = make_user("attacker@example.test")
    invoice = make_invoice(owner.id, total=10.0)
    login(attacker.email)

    response = client.post(
        f"/dashboard/invoice/{invoice.id}{suffix}",
        data=data,
    )

    assert response.status_code == 404
    with app.app_context():
        stored = db.session.get(Invoice, invoice.id)
        assert stored.status == "draft"
        assert stored.amount_paid == Decimal("0.00")


def test_deleting_invoice_cascades_payment_records(
    client, app, make_user, make_invoice, login
):
    owner = make_user("owner@example.test")
    invoice = make_invoice(owner.id, total=10.0)
    login(owner.email)
    client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "5.00", "method": "cash"},
    )

    client.post(f"/dashboard/invoice/{invoice.id}/delete")

    with app.app_context():
        assert db.session.get(Invoice, invoice.id) is None
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 0
