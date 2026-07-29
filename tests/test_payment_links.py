from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import inspect, text

from app import create_app
from extensions import db
from models import BusinessDefaults, Invoice, RecurringInvoice
from utils.scheduler import process_recurring_invoices
from utils.validation import PaymentURLValidationError, normalize_payment_url


PAYMENT_URL = "https://pay.example.com/checkout/invoice-123?source=pdfbillr"


def _invoice_payload(**overrides):
    payload = {
        "invoice_number": "PAY-001",
        "invoice_date": "2026-07-28",
        "due_date": "2026-08-28",
        "from_company": "Studio",
        "to_name": "Client",
        "description[]": ["Consulting"],
        "qty[]": ["1"],
        "rate[]": ["100"],
        "tax_rate": "0",
        "discount": "0",
        "currency_code": "USD",
        "payment_url": PAYMENT_URL,
        "theme": "default",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "value",
    (
        "http://pay.example.com/invoice",
        "javascript:alert(1)",
        "data:text/html,pay",
        "//pay.example.com/invoice",
        "https://user:secret@pay.example.com/invoice",
        "https://localhost/invoice",
        "https://127.0.0.1/invoice",
        "https://10.0.0.1/invoice",
        "https://bad_host.example/invoice",
        "https://example/invoice",
        "https://pay.example.com\\@attacker.example/invoice",
        "https://pay.example.com/in voice",
    ),
)
def test_payment_url_rejects_unsafe_or_invalid_destinations(value):
    with pytest.raises(PaymentURLValidationError):
        normalize_payment_url(value)


def test_payment_url_accepts_https_public_host_and_blank():
    assert normalize_payment_url(f"  {PAYMENT_URL}  ") == PAYMENT_URL
    assert normalize_payment_url("") is None


def test_business_default_prefills_and_creation_snapshots_payment_url(
    client, app, make_user, login, monkeypatch
):
    owner = make_user("payment-default@example.test")
    login(owner.email)

    defaults_response = client.post(
        "/clients/defaults",
        data={
            "default_tax_rate": "0",
            "default_payment_terms_days": "30",
            "default_payment_url": PAYMENT_URL,
        },
    )
    assert defaults_response.status_code == 302

    form_response = client.get("/app")
    assert form_response.status_code == 200
    assert PAYMENT_URL.encode() in form_response.data

    monkeypatch.setattr(
        "blueprints.public.render_pdf",
        lambda *_args, **_kwargs: b"%PDF-test",
    )
    create_response = client.post("/generate", data=_invoice_payload())
    assert create_response.status_code == 200

    with app.app_context():
        defaults = BusinessDefaults.query.filter_by(user_id=owner.id).one()
        invoice = Invoice.query.filter_by(user_id=owner.id).one()
        assert defaults.default_payment_url == PAYMENT_URL
        assert invoice.payment_url == PAYMENT_URL


def test_invalid_default_link_does_not_create_partial_record(
    client, app, make_user, login
):
    owner = make_user("invalid-default-link@example.test")
    login(owner.email)

    response = client.post(
        "/clients/defaults",
        data={
            "default_tax_rate": "0",
            "default_payment_terms_days": "30",
            "default_payment_url": "http://pay.example.com",
        },
    )
    assert response.status_code == 200
    assert b"must use HTTPS" in response.data
    with app.app_context():
        assert BusinessDefaults.query.filter_by(user_id=owner.id).first() is None


def test_edit_duplicate_and_cross_owner_protection(
    client, app, make_user, make_invoice, login
):
    owner = make_user("payment-owner@example.test")
    attacker = make_user("payment-attacker@example.test")
    invoice = make_invoice(
        owner.id,
        invoice_number="PAY-ORIGINAL",
        payment_url="https://pay.example.com/original",
    )

    login(attacker.email)
    denied = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_invoice_payload(payment_url="https://evil.example.com/pay"),
    )
    assert denied.status_code == 404

    client.get("/auth/logout")
    login(owner.email)
    updated_url = "https://checkout.example.com/revised"
    edited = client.post(
        f"/dashboard/invoice/{invoice.id}/edit",
        data=_invoice_payload(
            invoice_number="PAY-REVISED",
            payment_url=updated_url,
        ),
    )
    assert edited.status_code == 302
    duplicated = client.post(f"/dashboard/invoice/{invoice.id}/duplicate")
    assert duplicated.status_code == 302

    with app.app_context():
        original = db.session.get(Invoice, invoice.id)
        copy = Invoice.query.filter(
            Invoice.user_id == owner.id,
            Invoice.id != invoice.id,
        ).one()
        assert original.payment_url == updated_url
        assert copy.payment_url == updated_url


@pytest.mark.parametrize(
    ("status", "total", "visible"),
    (
        ("draft", 100, True),
        ("sent", 100, True),
        ("partial", 100, True),
        ("paid", 100, False),
        ("void", 100, False),
        ("sent", 0, False),
    ),
)
def test_public_pay_now_visibility_tracks_invoice_state(
    client, make_user, make_invoice, status, total, visible
):
    owner = make_user(f"public-{status}-{total}@example.test")
    token = f"payment-link-{status}-{total}"
    make_invoice(
        owner.id,
        invoice_number=f"STATE-{status}-{total}",
        payment_url=PAYMENT_URL,
        view_token=token,
        status=status,
        total=total,
    )

    response = client.get(f"/invoice/view/{token}")

    assert response.status_code == 200
    assert (b"Pay now" in response.data) is visible
    assert (PAYMENT_URL.encode() in response.data) is visible


def test_marking_invoice_paid_removes_public_payment_link(
    client, make_user, make_invoice, login
):
    owner = make_user("paid-link@example.test")
    invoice = make_invoice(
        owner.id,
        payment_url=PAYMENT_URL,
        view_token="pay-then-hide",
        status="sent",
        total=100,
    )
    assert b"Pay now" in client.get("/invoice/view/pay-then-hide").data

    login(owner.email)
    paid = client.post(f"/dashboard/invoice/{invoice.id}/mark-paid")
    assert paid.status_code == 302
    assert b"Pay now" not in client.get("/invoice/view/pay-then-hide").data


def test_recurring_payment_link_is_validated_and_snapshotted(
    client, app, make_user, login
):
    owner = make_user("recurring-payment-link@example.test", pro=True)
    login(owner.email)
    response = client.post(
        "/dashboard/recurring/new",
        data={
            "interval": "monthly",
            "next_run_date": date.today().isoformat(),
            "net_days": "0",
            "invoice_number_prefix": "PAYLINK",
            "currency_code": "EUR",
            "line_items_json": json.dumps(
                [{"description": "Service", "qty": 1, "rate": 25}]
            ),
            "tax_rate": "0",
            "discount": "0",
            "payment_url": PAYMENT_URL,
        },
    )
    assert response.status_code == 302

    process_recurring_invoices(app)

    with app.app_context():
        template = RecurringInvoice.query.filter_by(user_id=owner.id).one()
        invoice = Invoice.query.filter_by(user_id=owner.id).one()
        assert template.payment_url == PAYMENT_URL
        assert invoice.payment_url == PAYMENT_URL
        assert invoice.currency_code == "EUR"


def test_recurring_rejects_non_https_payment_link(
    client, app, make_user, login
):
    owner = make_user("invalid-recurring-link@example.test", pro=True)
    login(owner.email)
    response = client.post(
        "/dashboard/recurring/new",
        data={
            "interval": "monthly",
            "next_run_date": date.today().isoformat(),
            "net_days": "30",
            "line_items_json": json.dumps(
                [{"description": "Service", "qty": 1, "rate": 25}]
            ),
            "payment_url": "data:text/html,bad",
        },
    )

    assert response.status_code == 200
    assert b"must use HTTPS" in response.data
    with app.app_context():
        assert RecurringInvoice.query.filter_by(user_id=owner.id).first() is None


def test_payment_link_migration_is_additive_and_reversible(tmp_path):
    database_path = tmp_path / "payment-links.db"

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
    before = runner.invoke(args=["db", "upgrade", "20260728_10"])
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
            text(
                "INSERT INTO business_defaults "
                "(id, user_id, default_tax_rate, default_payment_terms_days, "
                "created_at, updated_at) "
                "VALUES (1, 1, 0, 30, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO invoices "
                "(id, user_id, invoice_number, currency_code) "
                "VALUES (1, 1, 'LEGACY', 'USD')"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO recurring_invoices "
                "(id, user_id, currency_code, interval, next_run_date, "
                "is_active, created_at) "
                "VALUES (1, 1, 'USD', 'monthly', '2026-08-01', 1, "
                "CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()

    upgraded = runner.invoke(args=["db", "upgrade", "20260728_11"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "default_payment_url" in {
            column["name"]
            for column in inspector.get_columns("business_defaults")
        }
        assert "payment_url" in {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "payment_url" in {
            column["name"]
            for column in inspector.get_columns("recurring_invoices")
        }
        assert db.session.execute(
            text("SELECT payment_url FROM invoices WHERE id = 1")
        ).scalar_one_or_none() is None

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_10"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "default_payment_url" not in {
            column["name"]
            for column in inspector.get_columns("business_defaults")
        }
        assert "payment_url" not in {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "payment_url" not in {
            column["name"]
            for column in inspector.get_columns("recurring_invoices")
        }
