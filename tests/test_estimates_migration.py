from __future__ import annotations

from sqlalchemy import inspect, text

from app import create_app
from extensions import db


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


def test_estimates_migration_upgrades_and_downgrades_without_invoice_loss(
    tmp_path,
):
    application = _migration_app(tmp_path / "estimates.db")
    runner = application.test_cli_runner()
    before = runner.invoke(args=["db", "upgrade", "20260728_09"])
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
                    "(id, user_id, invoice_number, total, currency_code) "
                    "VALUES (1, 1, 'INV-001', 125.00, 'USD')"
                )
            )

    upgraded = runner.invoke(args=["db", "upgrade", "20260728_10"])
    assert upgraded.exit_code == 0, upgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "estimates" in inspector.get_table_names()
        estimate_columns = {
            column["name"] for column in inspector.get_columns("estimates")
        }
        assert {
            "user_id",
            "client_id",
            "converted_invoice_id",
            "estimate_number",
            "public_token",
            "issue_date",
            "expiry_date",
            "currency_code",
            "line_items_json",
            "tax_rate",
            "discount",
            "subtotal",
            "total",
            "client_comment",
            "status",
        } <= estimate_columns
        unique_constraints = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("estimates")
        }
        assert ("user_id", "estimate_number") in unique_constraints
        public_token_indexes = {
            (tuple(index["column_names"]), index["unique"])
            for index in inspector.get_indexes("estimates")
        }
        assert (("public_token",), 1) in public_token_indexes
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO estimates "
                    "(id, user_id, estimate_number, public_token, status, "
                    "issue_date, expiry_date, currency_code, to_name, "
                    "line_items_json, tax_rate, discount, subtotal, total, "
                    "created_at, updated_at) "
                    "VALUES (1, 1, 'EST-001', 'strong-public-token', "
                    "'accepted', '2026-07-28', '2026-08-28', 'USD', "
                    "'Client', '[]', 0, 0, 0, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        assert db.session.execute(
            text("SELECT status FROM estimates WHERE id = 1")
        ).scalar_one() == "accepted"

    downgraded = runner.invoke(args=["db", "downgrade", "20260728_09"])
    assert downgraded.exit_code == 0, downgraded.output
    with application.app_context():
        inspector = inspect(db.engine)
        assert "estimates" not in inspector.get_table_names()
        assert db.session.execute(
            text("SELECT invoice_number FROM invoices WHERE id = 1")
        ).scalar_one() == "INV-001"
