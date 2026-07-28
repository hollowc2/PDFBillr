from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import event, select

from extensions import db
from models import Invoice, RecurringInvoice, User
from utils.financial_data_audit import audit_legacy_financial_data


VALID_ITEMS = (
    '[{"description":"Work","qty":1.25,"rate":8,"amount":10}]'
)


def _user() -> User:
    user = User(email="audit-owner@example.test")
    user.set_password("long test password")
    db.session.add(user)
    db.session.flush()
    return user


def test_financial_audit_accepts_matching_shadows_and_reports_pending(app):
    with app.app_context():
        user = _user()
        complete = Invoice(
            user_id=user.id,
            invoice_number="AUDIT-1",
            invoice_date="2026-07-28",
            due_date="",
            line_items_json=VALID_ITEMS,
            tax_rate=7.125,
            discount=1.25,
            subtotal=10,
            total=9.46,
            invoice_date_value=date(2026, 7, 28),
            due_date_value=None,
            tax_rate_decimal=Decimal("7.1250"),
            discount_decimal=Decimal("1.25"),
            subtotal_decimal=Decimal("10.00"),
            total_decimal=Decimal("9.46"),
        )
        pending = Invoice(
            user_id=user.id,
            invoice_number="AUDIT-2",
            invoice_date="2026-07-29",
            due_date=None,
            line_items_json=None,
            tax_rate=None,
            discount=None,
            subtotal=None,
            total=None,
        )
        recurring = RecurringInvoice(
            user_id=user.id,
            next_run_date=date(2026, 8, 1),
            line_items_json=VALID_ITEMS,
            tax_rate=5,
            discount=0,
            tax_rate_decimal=Decimal("5.0000"),
            discount_decimal=Decimal("0.00"),
        )
        db.session.add_all([complete, pending, recurring])
        db.session.commit()
        pending_id = pending.id

        audit = audit_legacy_financial_data(db.engine).as_dict()

    assert audit["blocker_count"] == 0
    assert audit["blockers"] == {}
    assert audit["invoices_scanned"] == 2
    assert audit["recurring_invoices_scanned"] == 1
    assert audit["pending_backfill"] == {
        "invoice_discount_decimal": {
            "count": 1,
            "row_ids": [pending_id],
        },
        "invoice_invoice_date_value": {
            "count": 1,
            "row_ids": [pending_id],
        },
        "invoice_subtotal_decimal": {
            "count": 1,
            "row_ids": [pending_id],
        },
        "invoice_tax_rate_decimal": {
            "count": 1,
            "row_ids": [pending_id],
        },
        "invoice_total_decimal": {
            "count": 1,
            "row_ids": [pending_id],
        },
    }


def test_financial_audit_cli_fails_for_blockers_without_writing(app):
    with app.app_context():
        user = _user()
        invoice = Invoice(
            user_id=user.id,
            invoice_number="PRIVATE-INVOICE-NUMBER",
            invoice_date="07/28/2026",
            due_date="2026-07-29",
            line_items_json='{"description":"not-a-list"}',
            tax_rate=float("inf"),
            discount=-1,
            subtotal=10_000_000_000_001,
            total=10,
            due_date_value=date(2026, 7, 30),
            total_decimal=Decimal("11.00"),
        )
        recurring = RecurringInvoice(
            user_id=user.id,
            next_run_date=date(2026, 8, 1),
            line_items_json="[NaN]",
            tax_rate=float("inf"),
            discount=2,
            discount_decimal=Decimal("3.00"),
        )
        db.session.add_all([invoice, recurring])
        db.session.commit()
        invoice_id = invoice.id
        recurring_id = recurring.id

        statements: list[str] = []

        def capture_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            runner = app.test_cli_runner()
            first = runner.invoke(args=["financial-data-audit"])
            second = runner.invoke(args=["financial-data-audit"])
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        invoice_count = db.session.scalar(select(db.func.count(Invoice.id)))
        recurring_count = db.session.scalar(
            select(db.func.count(RecurringInvoice.id))
        )

    assert first.exit_code == 1
    assert second.exit_code == 1
    assert first.output == second.output
    output = json.loads(first.output)
    assert output["blocker_count"] == 10
    assert output["blockers"] == {
        "invoice_due_date_shadow_mismatch": {
            "count": 1,
            "row_ids": [invoice_id],
        },
        "invoice_invalid_discount": {
            "count": 1,
            "row_ids": [invoice_id],
        },
        "invoice_invalid_invoice_date": {
            "count": 1,
            "row_ids": [invoice_id],
        },
        "invoice_invalid_subtotal": {
            "count": 1,
            "row_ids": [invoice_id],
        },
        "invoice_invalid_tax_rate": {
            "count": 1,
            "row_ids": [invoice_id],
        },
        "invoice_malformed_line_items_json": {
            "count": 1,
            "row_ids": [invoice_id],
        },
        "invoice_total_shadow_mismatch": {
            "count": 1,
            "row_ids": [invoice_id],
        },
        "recurring_invoice_discount_shadow_mismatch": {
            "count": 1,
            "row_ids": [recurring_id],
        },
        "recurring_invoice_invalid_tax_rate": {
            "count": 1,
            "row_ids": [recurring_id],
        },
        "recurring_invoice_malformed_line_items_json": {
            "count": 1,
            "row_ids": [recurring_id],
        },
    }
    assert invoice_count == 1
    assert recurring_count == 1
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert "PRIVATE-INVOICE-NUMBER" not in first.output
    assert "audit-owner@example.test" not in first.output


def test_financial_audit_blocks_internally_inconsistent_totals(app):
    with app.app_context():
        user = _user()
        invoice = Invoice(
            user_id=user.id,
            invoice_number="AUDIT-CALC",
            invoice_date="2026-07-28",
            line_items_json=(
                '[{"description":"Work","qty":2,"rate":5,"amount":9}]'
            ),
            tax_rate=10,
            discount=99,
            subtotal=9,
            total=1,
        )
        recurring = RecurringInvoice(
            user_id=user.id,
            next_run_date=date(2026, 8, 1),
            line_items_json=(
                '[{"description":"Work","qty":2,"rate":5,"amount":8}]'
            ),
            tax_rate=0,
            discount=99,
        )
        db.session.add_all([invoice, recurring])
        db.session.commit()
        invoice_id = invoice.id
        recurring_id = recurring.id

        audit = audit_legacy_financial_data(db.engine).as_dict()

    assert audit["blockers"]["invoice_line_amount_mismatch"] == {
        "count": 1,
        "row_ids": [invoice_id],
    }
    assert audit["blockers"]["invoice_discount_normalization_mismatch"] == {
        "count": 1,
        "row_ids": [invoice_id],
    }
    assert audit["blockers"]["invoice_subtotal_calculation_mismatch"] == {
        "count": 1,
        "row_ids": [invoice_id],
    }
    assert audit["blockers"]["invoice_total_calculation_mismatch"] == {
        "count": 1,
        "row_ids": [invoice_id],
    }
    assert audit["blockers"]["recurring_invoice_line_amount_mismatch"] == {
        "count": 1,
        "row_ids": [recurring_id],
    }
    assert audit["blockers"][
        "recurring_invoice_discount_normalization_mismatch"
    ] == {
        "count": 1,
        "row_ids": [recurring_id],
    }
