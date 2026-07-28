from __future__ import annotations

import json
from datetime import date

import pytest

from models import Invoice, RecurringInvoice
from utils.invoice_calculations import InvoiceCalculationError, calculate_invoice
from utils.scheduler import process_recurring_invoices


def item(qty, rate, *, amount=None, description="Work"):
    value = {"description": description, "qty": qty, "rate": rate}
    if amount is not None:
        value["amount"] = amount
    return value


@pytest.mark.parametrize(
    ("items", "tax", "discount", "expected"),
    [
        ([item(2, 10), item(0.5, 3)], 0, 0, (21.50, 0.00, 21.50)),
        ([item("1.5", "2.345")], 0, 0, (3.52, 0.00, 3.52)),
        ([item(1, 10)], "7.25", 1, (10.00, 0.73, 9.73)),
        ([item(1, 10)], 0, 100, (10.00, 0.00, 0.00)),
        ([item(0, 0)], 0, 0, (0.00, 0.00, 0.00)),
        ([], 0, 0, (0.00, 0.00, 0.00)),
    ],
)
def test_calculation_table(items, tax, discount, expected):
    result = calculate_invoice(items, tax_rate=tax, discount=discount)
    assert (
        float(result.subtotal),
        float(result.tax_amount),
        float(result.total),
    ) == expected


def test_rounds_each_line_before_subtotal():
    result = calculate_invoice([item(1, "0.005") for _ in range(3)])
    assert [line["amount"] for line in result.line_items] == [0.01, 0.01, 0.01]
    assert float(result.subtotal) == 0.03


def test_ignores_submitted_amount_and_recomputes_server_side():
    result = calculate_invoice([item(2, 5, amount=1_000_000)])
    assert result.line_items[0]["amount"] == 10.0
    assert float(result.total) == 10.0


@pytest.mark.parametrize(
    "bad_item",
    [
        item("not-a-number", 1),
        item(-1, 1),
        item("NaN", 1),
        item("Infinity", 1),
        item("1e100", "1e100"),
        "not-an-object",
    ],
)
def test_rejects_malformed_or_unsafe_line_items(bad_item):
    with pytest.raises(InvoiceCalculationError):
        calculate_invoice([bad_item])


@pytest.mark.parametrize("value", ["NaN", "Infinity", -1, "not-a-number"])
def test_rejects_malformed_tax_and_discount(value):
    with pytest.raises(InvoiceCalculationError):
        calculate_invoice([item(1, 1)], tax_rate=value)
    with pytest.raises(InvoiceCalculationError):
        calculate_invoice([item(1, 1)], discount=value)


def test_authenticated_generation_saves_server_calculated_totals(
    client, app, make_user, login, monkeypatch
):
    user = make_user("person@example.test")
    login(user.email)
    monkeypatch.setattr("blueprints.public.render_pdf", lambda *_args, **_kwargs: b"pdf")

    response = client.post(
        "/generate",
        data={
            "invoice_number": "INV-ROUND",
            "description[]": ["One", "Two", "Three"],
            "qty[]": ["1", "1", "1"],
            "rate[]": ["0.005", "0.005", "0.005"],
            "tax_rate": "0",
            "discount": "0",
        },
    )

    assert response.status_code == 200
    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=user.id).one()
        assert invoice.subtotal == 0.03
        assert invoice.total == 0.03


def test_malformed_generation_does_not_persist_invoice(
    client, app, make_user, login
):
    user = make_user("person@example.test")
    login(user.email)
    response = client.post(
        "/generate",
        data={
            "description[]": ["Bad"],
            "qty[]": ["NaN"],
            "rate[]": ["10"],
        },
    )

    assert response.status_code == 200
    assert b"must be finite" in response.data
    with app.app_context():
        assert Invoice.query.filter_by(user_id=user.id).count() == 0


def test_recurring_form_normalizes_tampered_amount_and_generated_invoice(
    client, app, make_user, login
):
    user = make_user("pro@example.test", pro=True)
    login(user.email)

    response = client.post(
        "/dashboard/recurring/new",
        data={
            "interval": "monthly",
            "next_run_date": date.today().isoformat(),
            "net_days": "0",
            "invoice_number_prefix": "SAFE",
            "line_items_json": json.dumps(
                [item(2, 5, amount=1_000_000)]
            ),
            "tax_rate": "0",
            "discount": "100",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        template = RecurringInvoice.query.filter_by(user_id=user.id).one()
        stored_item = json.loads(template.line_items_json)[0]
        assert stored_item["amount"] == 10.0
        assert template.discount == 10.0

    process_recurring_invoices(app)
    process_recurring_invoices(app)

    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=user.id).one()
        assert invoice.subtotal == 10.0
        assert invoice.discount == 10.0
        assert invoice.total == 0.0
        assert invoice.due_date == date.today().isoformat()
