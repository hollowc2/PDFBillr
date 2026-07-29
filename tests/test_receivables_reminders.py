from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import inspect, text

from app import create_app
from extensions import db
from models import (
    Invoice,
    InvoiceDelivery,
    InvoicePayment,
    ReminderPreference,
)
from utils.scheduler import retry_invoice_deliveries, send_payment_reminders


def _migration_app(database_path):
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

    return create_app(MigrationConfig)


def test_dashboard_metrics_are_owned_date_based_and_grouped_by_currency(
    app,
    client,
    login,
    make_invoice,
    make_user,
    monkeypatch,
):
    today = date(2026, 7, 28)
    monkeypatch.setattr("blueprints.dashboard._dashboard_today", lambda: today)
    owner = make_user("metrics@example.test")
    other = make_user("other-metrics@example.test")
    overdue = make_invoice(
        owner.id,
        invoice_number="OVERDUE-OWNED",
        status="sent",
        due_date_value=today - timedelta(days=1),
        total=100,
        currency_code="USD",
    )
    make_invoice(
        owner.id,
        invoice_number="SOON-OWNED",
        status="sent",
        due_date_value=today + timedelta(days=7),
        total=50,
        currency_code="USD",
    )
    paid = make_invoice(
        owner.id,
        invoice_number="PAID-OWNED",
        status="paid",
        due_date_value=today,
        total=80,
        currency_code="CAD",
    )
    make_invoice(
        owner.id,
        invoice_number="DRAFT-IGNORED",
        status="draft",
        due_date_value=today - timedelta(days=20),
        total=999,
    )
    make_invoice(
        other.id,
        invoice_number="OTHER-IGNORED",
        status="sent",
        due_date_value=today - timedelta(days=1),
        total=700,
    )
    with app.app_context():
        stored_paid = db.session.get(Invoice, paid.id)
        stored_paid.payments.append(
            InvoicePayment(
                amount=Decimal("80.00"),
                paid_at=datetime(2026, 7, 1, 0, 30, tzinfo=timezone.utc),
            )
        )
        db.session.commit()

    login(owner.email)
    response = client.get("/dashboard/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "$150.00" in html
    assert "$100.00" in html
    assert "$50.00" in html
    assert "CA$80.00" in html
    assert "OTHER-IGNORED" not in html
    assert overdue.id


def test_dashboard_status_date_and_sort_filters_use_business_dates(
    app,
    client,
    login,
    make_invoice,
    make_user,
    monkeypatch,
):
    today = date(2026, 7, 28)
    monkeypatch.setattr("blueprints.dashboard._dashboard_today", lambda: today)
    owner = make_user("filters@example.test")
    make_invoice(
        owner.id,
        invoice_number="OVERDUE-LOW",
        status="sent",
        due_date="1999-01-01",
        due_date_value=today - timedelta(days=1),
        total=10,
    )
    make_invoice(
        owner.id,
        invoice_number="OVERDUE-HIGH",
        status="sent",
        due_date_value=today - timedelta(days=2),
        total=90,
    )
    make_invoice(
        owner.id,
        invoice_number="DUE-TODAY-NOT-OVERDUE",
        status="sent",
        due_date_value=today,
        total=100,
    )

    login(owner.email)
    response = client.get(
        "/dashboard/?status=overdue&due_from=2026-07-20"
        "&due_to=2026-07-27&sort=amount_high"
    )

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.index("OVERDUE-HIGH") < html.index("OVERDUE-LOW")
    assert "DUE-TODAY-NOT-OVERDUE" not in html
    assert 'value="overdue" selected' in html


def test_paid_this_month_uses_configured_business_timezone(
    app,
    client,
    login,
    make_invoice,
    make_user,
    monkeypatch,
):
    business_today = date(2026, 7, 31)
    app.config["SCHEDULER_TIMEZONE"] = "America/Los_Angeles"
    monkeypatch.setattr(
        "blueprints.dashboard._dashboard_today",
        lambda: business_today,
    )
    owner = make_user("timezone-metrics@example.test")
    paid = make_invoice(
        owner.id,
        invoice_number="PAID-AT-UTC-BOUNDARY",
        status="paid",
        total=25,
        currency_code="USD",
    )
    with app.app_context():
        stored = db.session.get(Invoice, paid.id)
        stored.payments.append(
            InvoicePayment(
                amount=Decimal("25.00"),
                # August in UTC, but still July for the configured business.
                paid_at=datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc),
            )
        )
        db.session.commit()

    login(owner.email)
    response = client.get("/dashboard/")

    assert response.status_code == 200
    assert "$25.00" in response.get_data(as_text=True)


def test_reminder_settings_are_owner_scoped_and_validate_bounds(
    app,
    client,
    login,
    make_invoice,
    make_user,
):
    owner = make_user("reminder-owner@example.test", pro=True)
    other = make_user("reminder-other@example.test", pro=True)
    owner_invoice = make_invoice(owner.id, invoice_number="OWNED")
    other_invoice = make_invoice(other.id, invoice_number="OTHER")
    login(owner.email)

    invalid = client.post(
        "/dashboard/reminders",
        data={
            "enabled": "1",
            "before_due_days": "31",
            "on_due_date": "1",
            "overdue_days": "7",
        },
    )
    assert invalid.status_code == 400
    assert "between 1 and 30 days" in invalid.get_data(as_text=True)

    saved = client.post(
        "/dashboard/reminders",
        data={
            "enabled": "1",
            "before_due_days": "5",
            "overdue_days": "14",
        },
    )
    assert saved.status_code == 302
    with app.app_context():
        preference = ReminderPreference.query.filter_by(user_id=owner.id).one()
        assert preference.enabled is True
        assert preference.before_due_days == 5
        assert preference.on_due_date is False
        assert preference.overdue_days == 14
        assert ReminderPreference.query.filter_by(user_id=other.id).count() == 0

    toggled = client.post(
        f"/dashboard/invoice/{owner_invoice.id}/reminders",
        data={"enabled": "0"},
    )
    assert toggled.status_code == 302
    with app.app_context():
        assert db.session.get(
            Invoice,
            owner_invoice.id,
        ).payment_reminders_enabled is False

    forbidden = client.post(
        f"/dashboard/invoice/{other_invoice.id}/reminders",
        data={"enabled": "0"},
    )
    assert forbidden.status_code == 404
    with app.app_context():
        assert db.session.get(
            Invoice,
            other_invoice.id,
        ).payment_reminders_enabled is True


def test_invoice_and_global_disable_prevent_new_and_retry_reminders(
    app,
    make_invoice,
    make_user,
    monkeypatch,
):
    today = date(2026, 7, 28)
    monkeypatch.setattr("utils.scheduler._business_today", lambda _app: today)
    owner = make_user("disable-reminders@example.test", pro=True)
    invoice = make_invoice(
        owner.id,
        invoice_number="DISABLED",
        status="sent",
        due_date_value=today + timedelta(days=3),
        to_email="client@example.test",
        payment_reminders_enabled=False,
    )
    calls = []
    monkeypatch.setattr("extensions.mail.send", lambda message: calls.append(message))

    send_payment_reminders(app)
    with app.app_context():
        assert InvoiceDelivery.query.filter_by(invoice_id=invoice.id).count() == 0

        stored = db.session.get(Invoice, invoice.id)
        stored.payment_reminders_enabled = True
        db.session.add(
            ReminderPreference(
                user_id=owner.id,
                enabled=False,
            )
        )
        db.session.commit()

    send_payment_reminders(app)
    with app.app_context():
        assert InvoiceDelivery.query.filter_by(invoice_id=invoice.id).count() == 0
    assert calls == []


def test_custom_offset_delivers_and_disabled_preference_discards_retry(
    app,
    make_invoice,
    make_user,
    monkeypatch,
):
    today = date(2026, 7, 28)
    monkeypatch.setattr("utils.scheduler._business_today", lambda _app: today)
    owner = make_user("custom-reminders@example.test", pro=True)
    invoice = make_invoice(
        owner.id,
        invoice_number="CUSTOM",
        status="sent",
        due_date_value=today + timedelta(days=5),
        due_date=(today + timedelta(days=5)).isoformat(),
        to_email="client@example.test",
    )
    with app.app_context():
        db.session.add(
            ReminderPreference(
                user_id=owner.id,
                enabled=True,
                before_due_days=5,
                on_due_date=False,
                overdue_days=14,
            )
        )
        db.session.commit()

    calls = []

    def fail_once(message):
        calls.append(message)
        raise OSError("temporary SMTP failure")

    monkeypatch.setattr("extensions.mail.send", fail_once)
    send_payment_reminders(app)
    assert len(calls) == 1
    assert "due in 5 days" in calls[0].body

    with app.app_context():
        preference = ReminderPreference.query.filter_by(user_id=owner.id).one()
        preference.enabled = False
        db.session.commit()

    monkeypatch.setattr(
        "extensions.mail.send",
        lambda message: calls.append(message),
    )
    retry_invoice_deliveries(app)
    with app.app_context():
        delivery = InvoiceDelivery.query.filter_by(
            invoice_id=invoice.id,
            delivery_kind="reminder_3d",
        ).one()
        assert delivery.status == "discarded"
        assert db.session.get(Invoice, invoice.id).reminder_3d_sent is False
    assert len(calls) == 1


def test_default_reminder_schedule_still_sends_at_three_days(
    app,
    make_invoice,
    make_user,
    monkeypatch,
):
    today = date(2026, 7, 28)
    monkeypatch.setattr("utils.scheduler._business_today", lambda _app: today)
    owner = make_user("default-reminders@example.test", pro=True)
    invoice = make_invoice(
        owner.id,
        invoice_number="DEFAULT",
        status="sent",
        due_date_value=today + timedelta(days=3),
        due_date=(today + timedelta(days=3)).isoformat(),
        to_email="client@example.test",
    )
    calls = []
    monkeypatch.setattr("extensions.mail.send", lambda message: calls.append(message))

    send_payment_reminders(app)

    assert len(calls) == 1
    assert "due in 3 days" in calls[0].body
    with app.app_context():
        assert db.session.get(Invoice, invoice.id).reminder_3d_sent is True


def test_reminder_migration_is_additive_and_reversible(tmp_path):
    application = _migration_app(tmp_path / "reminder-migration.db")
    runner = application.test_cli_runner()
    before = runner.invoke(args=["db", "upgrade", "20260728_07"])
    assert before.exit_code == 0, before.output

    with application.app_context():
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, is_active, "
                    "auth_session_version) "
                    "VALUES (1, 'owner@example.test', 'hash', 1, 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoices "
                    "(id, user_id, invoice_number, status, total) "
                    "VALUES (1, 1, 'INV-001', 'sent', 125.00)"
                )
            )

    upgraded = runner.invoke(args=["db", "upgrade", "20260728_08"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "reminder_preferences" in inspector.get_table_names()
        invoice_columns = {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "payment_reminders_enabled" in invoice_columns
        enabled = db.session.execute(
            text(
                "SELECT payment_reminders_enabled "
                "FROM invoices WHERE id = 1"
            )
        ).scalar_one()
        assert enabled == 1
        db.session.execute(
            text(
                "INSERT INTO reminder_preferences "
                "(user_id, enabled, before_due_days, on_due_date, "
                "overdue_days, created_at, updated_at) "
                "VALUES (1, 1, 5, 0, 14, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_07"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "reminder_preferences" not in inspector.get_table_names()
        invoice_columns = {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "payment_reminders_enabled" not in invoice_columns
        assert db.session.execute(
            text("SELECT invoice_number FROM invoices WHERE id = 1")
        ).scalar_one() == "INV-001"
