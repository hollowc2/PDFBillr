from __future__ import annotations

import logging
import sqlite3

from sqlalchemy import inspect, text

from app import create_app
from extensions import db
from utils.database_migrations import BASELINE_REVISION


def _migration_app(database_path, *, testing=True, auto_create=False):
    class MigrationConfig:
        TESTING = testing
        APP_ENV = "test"
        SECRET_KEY = "tests-only-deterministic-secret-key-000000"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_CREATE_DB = auto_create
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        WTF_CSRF_ENABLED = False
        PUBLIC_BASE_URL = "https://billing.example.test/pdfbillr"
        TRUST_PROXY_HEADERS = False
        TRUSTED_HOSTS = ["localhost"]
        STRIPE_WEBHOOK_SECRET = "whsec_test"

    return create_app(MigrationConfig)


def test_fresh_database_bootstrap_reaches_head_and_is_rerunnable(tmp_path):
    application = _migration_app(tmp_path / "fresh.db")
    runner = application.test_cli_runner()

    first = runner.invoke(args=["db-bootstrap"])
    second = runner.invoke(args=["db-bootstrap"])

    assert first.exit_code == 0, first.output
    assert "(fresh)" in first.output
    assert second.exit_code == 0, second.output
    assert "(versioned)" in second.output
    schema_check = runner.invoke(args=["db", "check"])
    assert schema_check.exit_code == 0, schema_check.output

    with application.app_context():
        inspector = inspect(db.engine)
        assert {
            "alembic_version",
            "users",
            "invoices",
            "recurring_occurrences",
            "invoice_deliveries",
            "billing_notification_deliveries",
            "invoice_payments",
            "business_defaults",
            "clients",
            "service_items",
            "reminder_preferences",
        } <= set(inspector.get_table_names())
        user_columns = {
            column["name"] for column in inspector.get_columns("users")
        }
        assert "auth_session_version" in user_columns
        invoice_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("invoices")
        }
        assert ("user_id", "invoice_number") in invoice_unique_constraints
        billing_columns = {
            column["name"]
            for column in inspector.get_columns(
                "billing_notification_deliveries"
            )
        }
        assert {
            "stripe_event_id",
            "user_id",
            "template",
            "status",
            "attempt_count",
            "last_attempt_at",
            "sent_at",
            "last_error",
            "created_at",
            "updated_at",
        } <= billing_columns
        billing_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "billing_notification_deliveries"
            )
        }
        assert ("stripe_event_id",) in billing_unique_constraints
        billing_indexes = {
            index["name"]
            for index in inspector.get_indexes(
                "billing_notification_deliveries"
            )
        }
        assert "ix_billing_notification_deliveries_status" in billing_indexes
        invoice_columns = {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert {
            "invoice_date_value",
            "due_date_value",
            "tax_rate_decimal",
            "discount_decimal",
            "subtotal_decimal",
            "total_decimal",
            "paid_at",
            "voided_at",
            "client_id",
            "payment_reminders_enabled",
            "currency_code",
        } <= invoice_columns
        payment_columns = {
            column["name"] for column in inspector.get_columns("invoice_payments")
        }
        assert {
            "id",
            "invoice_id",
            "amount",
            "paid_at",
            "method",
            "reference",
            "note",
            "created_at",
        } <= payment_columns
        payment_indexes = {
            index["name"] for index in inspector.get_indexes("invoice_payments")
        }
        assert "ix_invoice_payments_invoice_id" in payment_indexes
        recurring_columns = {
            column["name"]
            for column in inspector.get_columns("recurring_invoices")
        }
        assert {
            "tax_rate_decimal",
            "discount_decimal",
        } <= recurring_columns
        revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "20260728_11"


def test_migration_logging_does_not_disable_application_loggers(tmp_path):
    application = _migration_app(tmp_path / "logging.db")
    runner = application.test_cli_runner()
    application.logger.disabled = False

    result = runner.invoke(args=["db", "upgrade", "head"])

    assert result.exit_code == 0, result.output
    assert application.logger.disabled is False
    assert logging.getLogger("app").disabled is False


def test_development_auto_create_uses_alembic_head(tmp_path):
    application = _migration_app(
        tmp_path / "auto-create.db",
        testing=False,
        auto_create=True,
    )

    with application.app_context():
        revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert revision == "20260728_11"


def test_financial_shadow_migration_is_additive_and_reversible(tmp_path):
    application = _migration_app(tmp_path / "financial-shadows.db")
    runner = application.test_cli_runner()
    before = runner.invoke(args=["db", "upgrade", "20260728_04"])
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
                    "(id, user_id, invoice_number, invoice_date, due_date, "
                    "tax_rate, discount, subtotal, total) "
                    "VALUES (1, 1, 'INV-001', '2026-07-28', '2026-08-28', "
                    "7.25, 1.00, 10.00, 9.73)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO recurring_invoices "
                    "(id, user_id, next_run_date, interval, tax_rate, discount) "
                    "VALUES (1, 1, '2026-08-28', 'monthly', 5.5, 2.0)"
                )
            )

    upgraded = runner.invoke(args=["db", "upgrade", "20260728_05"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        invoice = db.session.execute(
            text(
                "SELECT invoice_date, due_date, tax_rate, discount, "
                "subtotal, total, invoice_date_value, due_date_value, "
                "tax_rate_decimal, discount_decimal, subtotal_decimal, "
                "total_decimal FROM invoices WHERE id = 1"
            )
        ).one()
        assert tuple(invoice[:6]) == (
            "2026-07-28",
            "2026-08-28",
            7.25,
            1.0,
            10.0,
            9.73,
        )
        assert tuple(invoice[6:]) == (None, None, None, None, None, None)
        recurring = db.session.execute(
            text(
                "SELECT tax_rate, discount, tax_rate_decimal, "
                "discount_decimal FROM recurring_invoices WHERE id = 1"
            )
        ).one()
        assert tuple(recurring) == (5.5, 2.0, None, None)

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_04"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        invoice_columns = {
            column["name"] for column in inspector.get_columns("invoices")
        }
        recurring_columns = {
            column["name"]
            for column in inspector.get_columns("recurring_invoices")
        }
        assert "invoice_date_value" not in invoice_columns
        assert "tax_rate_decimal" not in recurring_columns
        assert db.session.execute(
            text("SELECT invoice_number FROM invoices WHERE id = 1")
        ).scalar_one() == "INV-001"


def test_payment_lifecycle_migration_is_additive_and_reversible(tmp_path):
    application = _migration_app(tmp_path / "payment-lifecycle.db")
    runner = application.test_cli_runner()
    before = runner.invoke(args=["db", "upgrade", "20260728_05"])
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

    upgraded = runner.invoke(args=["db", "upgrade", "20260728_06"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        invoice = db.session.execute(
            text(
                "SELECT invoice_number, status, total, paid_at, voided_at "
                "FROM invoices WHERE id = 1"
            )
        ).one()
        assert tuple(invoice) == ("INV-001", "sent", 125.0, None, None)
        db.session.execute(
            text(
                "INSERT INTO invoice_payments "
                "(id, invoice_id, amount, paid_at, method, created_at) "
                "VALUES (1, 1, 25.00, CURRENT_TIMESTAMP, 'cash', "
                "CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()
        payment = db.session.execute(
            text(
                "SELECT invoice_id, amount, method "
                "FROM invoice_payments WHERE id = 1"
            )
        ).one()
        assert tuple(payment) == (1, 25, "cash")

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_05"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "invoice_payments" not in inspector.get_table_names()
        invoice_columns = {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "paid_at" not in invoice_columns
        assert "voided_at" not in invoice_columns
        assert db.session.execute(
            text("SELECT invoice_number FROM invoices WHERE id = 1")
        ).scalar_one() == "INV-001"


def test_sqlite_connections_enforce_declared_foreign_keys(app):
    with app.app_context():
        enabled = db.session.execute(text("PRAGMA foreign_keys")).scalar_one()

    assert enabled == 1


def test_verified_unversioned_legacy_database_is_stamped_then_upgraded(tmp_path):
    application = _migration_app(tmp_path / "legacy.db")
    runner = application.test_cli_runner()
    baseline = runner.invoke(args=["db", "upgrade", BASELINE_REVISION])
    assert baseline.exit_code == 0, baseline.output

    with application.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))

    result = runner.invoke(args=["db-bootstrap"])

    assert result.exit_code == 0, result.output
    assert "(legacy)" in result.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "auth_session_version" in {
            column["name"] for column in inspector.get_columns("users")
        }
        assert "invoice_deliveries" in inspector.get_table_names()


def test_legacy_upgrade_preserves_rows_with_foreign_keys(tmp_path):
    application = _migration_app(tmp_path / "legacy-data.db")
    runner = application.test_cli_runner()
    baseline = runner.invoke(args=["db", "upgrade", BASELINE_REVISION])
    assert baseline.exit_code == 0, baseline.output

    with application.app_context():
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, is_active) "
                    "VALUES (1, 'owner@example.test', 'hash', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoices "
                    "(id, user_id, invoice_number) "
                    "VALUES (1, 1, 'INV-001')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id, user_id, plan, status) "
                    "VALUES (1, 1, 'free', 'active')"
                )
            )

    result = runner.invoke(args=["db-bootstrap"])

    assert result.exit_code == 0, result.output
    with application.app_context():
        assert db.session.execute(
            text("SELECT invoice_number FROM invoices WHERE id = 1")
        ).scalar_one() == "INV-001"
        assert db.session.execute(
            text("SELECT plan FROM subscriptions WHERE id = 1")
        ).scalar_one() == "free"


def test_unknown_partial_legacy_schema_fails_without_stamping(tmp_path):
    database_path = tmp_path / "partial.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    application = _migration_app(database_path)

    result = application.test_cli_runner().invoke(args=["db-bootstrap"])

    assert result.exit_code != 0
    assert "does not match a supported PDFBillr legacy schema" in str(
        result.exception
    )
    with application.app_context():
        assert "alembic_version" not in inspect(db.engine).get_table_names()


def test_duplicate_invoice_numbers_block_constraint_migration(tmp_path):
    application = _migration_app(tmp_path / "duplicates.db")
    runner = application.test_cli_runner()
    baseline = runner.invoke(args=["db", "upgrade", BASELINE_REVISION])
    assert baseline.exit_code == 0, baseline.output

    with application.app_context():
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, is_active) "
                    "VALUES (1, 'owner@example.test', 'hash', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoices "
                    "(id, user_id, invoice_number) VALUES "
                    "(1, 1, 'INV-DUP'), (2, 1, 'INV-DUP')"
                )
            )

    result = runner.invoke(args=["db-bootstrap"])

    assert result.exit_code != 0
    assert "duplicate group(s) exist" in str(result.exception)
    with application.app_context():
        revision = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == BASELINE_REVISION


def test_deprecated_database_command_uses_alembic_bootstrap(tmp_path):
    application = _migration_app(tmp_path / "alias.db")

    result = application.test_cli_runner().invoke(args=["db-upgrade"])

    assert result.exit_code == 0, result.output
    assert "deprecated" in result.output
    assert "Alembic head" in result.output


def test_clients_catalog_migration_is_additive_and_reversible(tmp_path):
    application = _migration_app(tmp_path / "clients-catalog.db")
    runner = application.test_cli_runner()
    before = runner.invoke(args=["db", "upgrade", "20260728_06"])
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
                    "(id, user_id, invoice_number, to_name, total) "
                    "VALUES (1, 1, 'INV-001', 'Snapshot Client', 125.00)"
                )
            )

    upgraded = runner.invoke(args=["db", "upgrade", "20260728_07"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert {
            "business_defaults",
            "clients",
            "service_items",
        } <= set(inspector.get_table_names())
        assert "client_id" in {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "ix_invoices_client_id" in {
            index["name"] for index in inspector.get_indexes("invoices")
        }
        client_unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("clients")
        }
        assert ("user_id", "normalized_name") in client_unique_constraints

        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO clients "
                    "(id, user_id, name, normalized_name, created_at, "
                    "updated_at) "
                    "VALUES (1, 1, 'Snapshot Client', 'snapshot client', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "UPDATE invoices SET client_id = 1 WHERE id = 1"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO service_items "
                    "(id, user_id, name, normalized_name, description, "
                    "default_rate, default_quantity, created_at, updated_at) "
                    "VALUES (1, 1, 'Consulting', 'consulting', "
                    "'Consulting services', 100.00, 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_06"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "client_id" not in {
            column["name"] for column in inspector.get_columns("invoices")
        }
        assert "clients" not in inspector.get_table_names()
        assert "service_items" not in inspector.get_table_names()
        assert db.session.execute(
            text("SELECT to_name FROM invoices WHERE id = 1")
        ).scalar_one() == "Snapshot Client"
