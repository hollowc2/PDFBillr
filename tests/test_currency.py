from __future__ import annotations

import csv
import io
import json
from datetime import date

import pytest
from sqlalchemy import inspect, text

from app import create_app
from extensions import db
from models import Invoice, InvoicePayment, RecurringInvoice
from utils.currency import format_currency, normalize_currency_code
from utils.pdf import build_invoice_context, render_pdf
from utils.scheduler import process_recurring_invoices


def _invoice_payload(**overrides):
    payload = {
        "invoice_number": "FX-001",
        "invoice_date": "2026-07-28",
        "due_date": "2026-08-28",
        "from_company": "Studio",
        "to_name": "Client",
        "description[]": ["Consulting"],
        "qty[]": ["2"],
        "rate[]": ["1000"],
        "tax_rate": "10",
        "discount": "50",
        "currency_code": "EUR",
        "theme": "default",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("code", "value", "expected"),
    (
        ("USD", "1234.5", "$1,234.50"),
        ("CAD", "1234.5", "CA$1,234.50"),
        ("EUR", "1234.5", "€1,234.50"),
        ("GBP", "1234.5", "£1,234.50"),
        ("AUD", "1234.5", "A$1,234.50"),
        ("JPY", "1234.5", "¥1,235"),
    ),
)
def test_supported_currency_formatting(code, value, expected):
    assert format_currency(value, code) == expected


def test_unknown_currency_defaults_safely_to_usd():
    assert normalize_currency_code("btc") == "USD"
    assert format_currency("10", "btc") == "$10.00"


def test_creation_snapshots_allowlisted_currency(client, app, make_user, login, monkeypatch):
    owner = make_user("currency-owner@example.test")
    login(owner.email)
    rendered_contexts = []
    monkeypatch.setattr(
        "blueprints.public.render_pdf",
        lambda context, **_kwargs: rendered_contexts.append(context) or b"%PDF-test",
    )

    response = client.post("/generate", data=_invoice_payload())

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert rendered_contexts[0]["currency_code"] == "EUR"
    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=owner.id).one()
        assert invoice.currency_code == "EUR"
        assert invoice.total == 2150


def test_tampered_currency_is_not_persisted(client, app, make_user, login, monkeypatch):
    owner = make_user("allowlist@example.test")
    login(owner.email)
    monkeypatch.setattr(
        "blueprints.public.render_pdf",
        lambda *_args, **_kwargs: b"%PDF-test",
    )

    response = client.post(
        "/generate",
        data=_invoice_payload(currency_code="BTC"),
    )

    assert response.status_code == 200
    with app.app_context():
        invoice = Invoice.query.filter_by(user_id=owner.id).one()
        assert invoice.currency_code == "USD"


def test_edit_and_duplicate_preserve_currency_snapshot(client, app, make_user, make_invoice, login):
    owner = make_user("edit-currency@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="GBP-ORIGINAL",
        currency_code="GBP",
    )
    login(owner.email)

    edit_response = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_invoice_payload(
            invoice_number="GBP-EDITED",
            currency_code="AUD",
        ),
    )
    assert edit_response.status_code == 302

    duplicate_response = client.post(f"/dashboard/invoice/{invoice.id}/duplicate")
    assert duplicate_response.status_code == 302

    with app.app_context():
        edited = db.session.get(Invoice, invoice.id)
        duplicate = Invoice.query.filter(
            Invoice.user_id == owner.id,
            Invoice.id != invoice.id,
        ).one()
        assert edited.currency_code == "AUD"
        assert duplicate.currency_code == "AUD"


def test_pdf_html_uses_invoice_currency_and_minor_units(app, monkeypatch):
    captured = {}

    class FakeHTML:
        def __init__(self, *, string, url_fetcher):
            captured["html"] = string
            captured["url_fetcher"] = url_fetcher

        def write_pdf(self):
            return b"%PDF-test"

    monkeypatch.setattr("utils.pdf.HTML", FakeHTML)
    # Build from the same multi-value interface Flask's request.form exposes.
    from werkzeug.datastructures import MultiDict

    form = MultiDict(
        [
            ("invoice_number", "JPY-PDF"),
            ("description[]", "Work"),
            ("qty[]", "1"),
            ("rate[]", "1234.5"),
            ("currency_code", "JPY"),
        ]
    )
    with app.app_context(), app.test_request_context():
        context = build_invoice_context(form)
        pdf = render_pdf(context)

    assert pdf == b"%PDF-test"
    assert "¥1,235" in captured["html"]
    assert "$1,234.50" not in captured["html"]


def test_csv_export_includes_iso_currency(client, make_user, make_invoice, login):
    owner = make_user("export-currency@example.test")
    make_invoice(owner.id, currency_code="CAD")
    login(owner.email)

    response = client.get("/dashboard/invoices.csv")

    rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8-sig"))))
    assert rows[0]["Currency"] == "CAD"


def test_jpy_payments_require_whole_minor_units(client, app, make_user, make_invoice, login):
    owner = make_user("jpy-payments@example.test")
    invoice = make_invoice(
        owner.id,
        currency_code="JPY",
        total=100,
    )
    login(owner.email)

    rejected = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "1.5", "method": "cash"},
    )
    assert rejected.status_code == 302
    with app.app_context():
        assert InvoicePayment.query.filter_by(invoice_id=invoice.id).count() == 0

    accepted = client.post(
        f"/dashboard/invoice/{invoice.id}/payments",
        data={"amount": "1", "method": "cash"},
    )
    assert accepted.status_code == 302
    with app.app_context():
        payment = InvoicePayment.query.filter_by(invoice_id=invoice.id).one()
        assert payment.amount == 1


def test_recurring_currency_and_jpy_rounding_are_snapshotted(client, app, make_user, login):
    owner = make_user("recurring-currency@example.test", pro=True)
    login(owner.email)
    response = client.post(
        "/dashboard/recurring/new",
        data={
            "interval": "monthly",
            "next_run_date": date.today().isoformat(),
            "net_days": "0",
            "invoice_number_prefix": "JPY",
            "currency_code": "JPY",
            "line_items_json": json.dumps(
                [
                    {
                        "description": "Localized service",
                        "qty": "1",
                        "rate": "100.5",
                    }
                ]
            ),
            "tax_rate": "10",
            "discount": "0",
        },
    )
    assert response.status_code == 302

    process_recurring_invoices(app)

    with app.app_context():
        template = RecurringInvoice.query.filter_by(user_id=owner.id).one()
        invoice = Invoice.query.filter_by(user_id=owner.id).one()
        assert template.currency_code == "JPY"
        assert json.loads(template.line_items_json)[0]["amount"] == 101
        assert invoice.currency_code == "JPY"
        assert invoice.subtotal == 101
        assert invoice.total == 111


def test_currency_migration_defaults_existing_rows_and_is_reversible(tmp_path):
    database_path = tmp_path / "currency-migration.db"

    class MigrationConfig:
        TESTING = True
        APP_ENV = "test"
        SECRET_KEY = "tests-only-deterministic-secret-key-000000"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_CREATE_DB = False
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        WTF_CSRF_ENABLED = False
        PUBLIC_BASE_URL = "https://billing.example.test/pdfbillr"
        TRUST_PROXY_HEADERS = False
        TRUSTED_HOSTS = ["localhost"]
        STRIPE_WEBHOOK_SECRET = "whsec_test"

    application = create_app(MigrationConfig)
    runner = application.test_cli_runner()
    before = runner.invoke(args=["db", "upgrade", "20260728_08"])
    assert before.exit_code == 0, before.output

    with application.app_context():
        db.session.execute(
            text(
                "INSERT INTO users "
                "(id, email, password_hash, auth_session_version, is_active) "
                "VALUES (1, 'legacy@example.test', 'hash', 1, 1)"
            )
        )
        db.session.execute(
            text("INSERT INTO invoices (id, user_id, invoice_number) VALUES (1, 1, 'LEGACY')")
        )
        db.session.execute(
            text(
                "INSERT INTO recurring_invoices "
                "(id, user_id, interval, next_run_date, is_active) "
                "VALUES (1, 1, 'monthly', '2026-08-01', 1)"
            )
        )
        db.session.commit()

    upgraded = runner.invoke(args=["db", "upgrade", "20260728_09"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "currency_code" in {column["name"] for column in inspector.get_columns("invoices")}
        assert "currency_code" in {
            column["name"] for column in inspector.get_columns("recurring_invoices")
        }
        assert (
            db.session.execute(text("SELECT currency_code FROM invoices WHERE id = 1")).scalar_one()
            == "USD"
        )
        assert (
            db.session.execute(
                text("SELECT currency_code FROM recurring_invoices WHERE id = 1")
            ).scalar_one()
            == "USD"
        )

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_08"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "currency_code" not in {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "currency_code" not in {
            column["name"] for column in inspector.get_columns("recurring_invoices")
        }
