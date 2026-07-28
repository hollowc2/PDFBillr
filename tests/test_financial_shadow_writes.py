from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from extensions import db
from models import Invoice, RecurringInvoice
from utils.scheduler import process_recurring_invoices


def _invoice_form(**overrides):
    values = {
        "invoice_number": "SHADOW-001",
        "invoice_date": "2026-07-28",
        "due_date": "",
        "description[]": ["Precision work"],
        "qty[]": ["3"],
        "rate[]": ["3.335"],
        "tax_rate": "7.12555",
        "discount": "0.005",
        # Browser-computed totals are deliberately hostile and must be ignored.
        "subtotal": "999999999.99",
        "total": "0.01",
    }
    values.update(overrides)
    return values


def test_authenticated_invoice_dual_write_uses_server_calculation_and_typed_dates(
    client, app, make_user, login, monkeypatch
):
    user = make_user("shadow-new@example.test")
    login(user.email)
    monkeypatch.setattr("blueprints.public.render_pdf", lambda *_args, **_kwargs: b"pdf")

    response = client.post("/generate", data=_invoice_form())

    assert response.status_code == 200
    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=user.id).one()
        assert invoice.subtotal == 10.01
        assert invoice.total == 10.71
        assert invoice.invoice_date_value == date(2026, 7, 28)
        assert invoice.due_date_value is None
        assert invoice.tax_rate_decimal == Decimal("7.1256")
        assert invoice.discount_decimal == Decimal("0.01")
        assert invoice.subtotal_decimal == Decimal("10.01")
        assert invoice.total_decimal == Decimal("10.71")


def test_invoice_duplicate_populates_shadows_consistently(
    client, app, make_user, login, monkeypatch
):
    user = make_user("shadow-duplicate@example.test")
    login(user.email)
    monkeypatch.setattr("blueprints.public.render_pdf", lambda *_args, **_kwargs: b"pdf")
    client.post("/generate", data=_invoice_form())

    with app.app_context():
        original = Invoice.query.filter_by(user_id=user.id).one()
        original_id = original.id

    response = client.post(f"/dashboard/invoice/{original_id}/duplicate")

    assert response.status_code == 302
    with app.app_context():
        invoices = (
            Invoice.query.filter_by(user_id=user.id)
            .order_by(Invoice.id)
            .all()
        )
        assert len(invoices) == 2
        original, duplicate = invoices
        assert duplicate.invoice_date == original.invoice_date
        assert duplicate.due_date == original.due_date
        assert duplicate.invoice_date_value == original.invoice_date_value
        assert duplicate.due_date_value is None
        assert duplicate.tax_rate_decimal == original.tax_rate_decimal
        assert duplicate.discount_decimal == original.discount_decimal
        assert duplicate.subtotal_decimal == original.subtotal_decimal
        assert duplicate.total_decimal == original.total_decimal


def test_invoice_duplicate_rejects_out_of_range_legacy_financials(
    client, app, make_user, make_invoice, login
):
    user = make_user("shadow-invalid-legacy@example.test")
    original = make_invoice(
        user.id,
        invoice_number="INVALID-LEGACY",
        tax_rate=101.0,
    )
    login(user.email)

    response = client.post(f"/dashboard/invoice/{original.id}/duplicate")

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/dashboard/invoice/{original.id}"
    )
    with app.app_context():
        assert Invoice.query.filter_by(user_id=user.id).count() == 1


def test_recurring_template_and_generated_invoice_dual_write_consistently(
    client, app, make_user, login
):
    user = make_user("shadow-recurring@example.test", pro=True)
    login(user.email)

    response = client.post(
        "/dashboard/recurring/new",
        data={
            "interval": "monthly",
            "next_run_date": date.today().isoformat(),
            "net_days": "0",
            "invoice_number_prefix": "SHADOW",
            "line_items_json": json.dumps(
                [
                    {
                        "description": "Precision work",
                        "qty": "3",
                        "rate": "3.335",
                        "amount": "999999999.99",
                    }
                ]
            ),
            "tax_rate": "7.12555",
            "discount": "0.005",
            "subtotal": "999999999.99",
            "total": "0.01",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        template = RecurringInvoice.query.filter_by(user_id=user.id).one()
        assert template.tax_rate_decimal == Decimal("7.1256")
        assert template.discount_decimal == Decimal("0.01")
        assert json.loads(template.line_items_json)[0]["amount"] == 10.01
        template.net_days = None
        db.session.commit()

    process_recurring_invoices(app)

    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=user.id).one()
        assert invoice.invoice_date_value == date.today()
        assert invoice.due_date is None
        assert invoice.due_date_value is None
        assert invoice.tax_rate_decimal == Decimal("7.1256")
        assert invoice.discount_decimal == Decimal("0.01")
        assert invoice.subtotal_decimal == Decimal("10.01")
        assert invoice.total_decimal == Decimal("10.71")


def test_malformed_invoice_date_is_rejected_before_render_or_persistence(
    client, app, make_user, login, monkeypatch
):
    user = make_user("shadow-invalid-date@example.test")
    login(user.email)
    render_calls = []
    monkeypatch.setattr(
        "blueprints.public.render_pdf",
        lambda *_args, **_kwargs: render_calls.append(True) or b"pdf",
    )

    response = client.post(
        "/generate",
        data=_invoice_form(invoice_date="not-a-date"),
    )

    assert response.status_code == 200
    assert b"Invoice date must be a valid date" in response.data
    assert render_calls == []
    with app.app_context():
        assert Invoice.query.filter_by(user_id=user.id).count() == 0
