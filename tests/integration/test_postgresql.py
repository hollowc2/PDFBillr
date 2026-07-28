"""Opt-in PostgreSQL migration and integrity verification.

The test never connects unless POSTGRES_TEST_DATABASE_URL is explicitly set.
It also requires a database name containing ``test`` and runs within a fresh,
randomly named schema so cleanup cannot affect unrelated application schemas.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from app import create_app
from extensions import db
from models import Invoice, User


POSTGRES_TEST_DATABASE_URL = os.environ.get("POSTGRES_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not POSTGRES_TEST_DATABASE_URL,
    reason="POSTGRES_TEST_DATABASE_URL is not configured",
)


def _alembic_head() -> str:
    config = AlembicConfig("migrations/alembic.ini")
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"Expected one Alembic head, found {heads}"
    return heads[0]


def _postgresql_app(database_url: str, schema_name: str):
    class PostgreSQLTestConfig:
        TESTING = True
        APP_ENV = "test"
        SECRET_KEY = "postgres-tests-only-secret-key-000000"
        SQLALCHEMY_DATABASE_URI = database_url
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"options": f"-csearch_path={schema_name}"}}
        AUTO_CREATE_DB = False
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"
        WTF_CSRF_ENABLED = False
        MAIL_SUPPRESS_SEND = True
        PUBLIC_BASE_URL = "https://billing.example.test/pdfbillr"
        TRUST_PROXY_HEADERS = False
        TRUSTED_HOSTS = ["localhost"]
        STRIPE_WEBHOOK_SECRET = "whsec_postgres_test_not_real"

    return create_app(PostgreSQLTestConfig)


def test_postgresql_bootstrap_integrity_and_cleanup():
    database_url = make_url(POSTGRES_TEST_DATABASE_URL)
    if database_url.get_backend_name() != "postgresql":
        pytest.fail("POSTGRES_TEST_DATABASE_URL must use PostgreSQL")
    if "test" not in (database_url.database or "").casefold():
        pytest.fail(
            "Refusing PostgreSQL integration cleanup because the database "
            "name does not contain 'test'"
        )

    schema_name = f"pdfbillr_test_{uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    application = None
    schema_created = False

    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema_name))
        schema_created = True

        application = _postgresql_app(
            database_url.render_as_string(hide_password=False),
            schema_name,
        )
        runner = application.test_cli_runner()

        with application.app_context():
            assert db.session.execute(text("SELECT current_schema()")).scalar_one() == schema_name
            assert inspect(db.engine).get_table_names() == []

        first = runner.invoke(args=["db-bootstrap"])
        second = runner.invoke(args=["db-bootstrap"])

        assert first.exit_code == 0, first.output
        assert "(fresh)" in first.output
        assert second.exit_code == 0, second.output
        assert "(versioned)" in second.output

        schema_check = runner.invoke(args=["db", "check"])
        assert schema_check.exit_code == 0, schema_check.output

        with application.app_context():
            table_names = set(inspect(db.engine).get_table_names())
            assert {
                "alembic_version",
                "users",
                "invoices",
                "recurring_occurrences",
                "invoice_deliveries",
                "billing_notification_deliveries",
            } <= table_names
            assert (
                db.session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == _alembic_head()
            )

            source_timestamp = datetime(
                2026,
                7,
                28,
                12,
                34,
                56,
                tzinfo=timezone(timedelta(hours=-7)),
            )
            first_user = User(
                email="first-owner@example.test",
                password_hash="not-a-real-password-hash",
                created_at=source_timestamp,
            )
            second_user = User(
                email="second-owner@example.test",
                password_hash="not-a-real-password-hash",
            )
            db.session.add_all([first_user, second_user])
            db.session.flush()
            db.session.add_all(
                [
                    Invoice(user_id=first_user.id, invoice_number="INV-SHARED"),
                    Invoice(user_id=second_user.id, invoice_number="INV-SHARED"),
                ]
            )
            db.session.commit()

            db.session.expire_all()
            stored_timestamp = db.session.scalar(
                select(User.created_at).where(User.id == first_user.id)
            )
            assert stored_timestamp is not None
            assert stored_timestamp.utcoffset() is not None
            assert stored_timestamp.astimezone(timezone.utc) == source_timestamp.astimezone(
                timezone.utc
            )

            db.session.add(Invoice(user_id=first_user.id, invoice_number="INV-SHARED"))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            assert (
                db.session.scalar(
                    select(db.func.count(Invoice.id)).where(Invoice.invoice_number == "INV-SHARED")
                )
                == 2
            )

        downgrade = runner.invoke(args=["db", "downgrade", "base"])
        assert downgrade.exit_code == 0, downgrade.output

        with application.app_context():
            remaining_tables = set(inspect(db.engine).get_table_names())
            assert remaining_tables <= {"alembic_version"}
            db.session.execute(text("DROP TABLE IF EXISTS alembic_version"))
            db.session.commit()
            assert inspect(db.engine).get_table_names() == []
    finally:
        if application is not None:
            with application.app_context():
                db.session.remove()
                db.engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        admin_engine.dispose()
