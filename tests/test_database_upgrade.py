from __future__ import annotations

import sqlite3

from sqlalchemy import inspect

from app import create_app
from extensions import db


def test_legacy_invoice_columns_upgrade_and_rerun(tmp_path):
    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    class LegacyConfig:
        TESTING = True
        APP_ENV = "test"
        SECRET_KEY = "tests-only-deterministic-secret-key-000000"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_CREATE_DB = True
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        PUBLIC_BASE_URL = "https://billing.example.test/pdfbillr"
        TRUST_PROXY_HEADERS = False
        TRUSTED_HOSTS = ["localhost"]
        STRIPE_WEBHOOK_SECRET = "whsec_test"

    first = create_app(LegacyConfig)
    second = create_app(LegacyConfig)

    for application in (first, second):
        with application.app_context():
            columns = {
                column["name"]
                for column in inspect(db.engine).get_columns("invoices")
            }
            assert {
                "view_token",
                "viewed_at",
                "view_count",
                "reminder_3d_sent",
                "reminder_0d_sent",
                "reminder_7d_sent",
            } <= columns
            indexes = {
                index["name"] for index in inspect(db.engine).get_indexes("invoices")
            }
            assert "ix_invoices_view_token" in indexes
            subscription_columns = {
                column["name"]
                for column in inspect(db.engine).get_columns("subscriptions")
            }
            assert "last_stripe_event_created" in subscription_columns
