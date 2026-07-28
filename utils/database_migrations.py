"""Explicit database migration and legacy-transition helpers.

Unversioned PDFBillr databases predate Alembic. They may be stamped at the
baseline revision only after their schema has been inspected and the small set
of historically supported compatibility columns has been added. Unknown or
partial schemas fail closed instead of being marked current.
"""

from __future__ import annotations

from collections.abc import Mapping, Set

from flask_migrate import stamp, upgrade
from sqlalchemy import inspect


BASELINE_REVISION = "20260728_01"

_BASELINE_COLUMNS: Mapping[str, Set[str]] = {
    "users": {
        "id",
        "email",
        "password_hash",
        "created_at",
        "is_active",
        "stripe_customer_id",
    },
    "subscriptions": {
        "id",
        "user_id",
        "plan",
        "stripe_sub_id",
        "stripe_price_id",
        "status",
        "current_period_end",
        "last_stripe_event_created",
        "created_at",
        "updated_at",
    },
    "invoices": {
        "id",
        "user_id",
        "invoice_number",
        "invoice_date",
        "due_date",
        "from_company",
        "from_address",
        "from_email",
        "from_phone",
        "to_name",
        "to_address",
        "to_email",
        "line_items_json",
        "tax_rate",
        "discount",
        "subtotal",
        "total",
        "notes",
        "payment_info",
        "logo_filename",
        "theme",
        "status",
        "sent_at",
        "created_at",
        "updated_at",
        "view_token",
        "viewed_at",
        "view_count",
        "reminder_3d_sent",
        "reminder_0d_sent",
        "reminder_7d_sent",
    },
    "processed_stripe_events": {"stripe_event_id", "created_at"},
    "recurring_invoices": {
        "id",
        "user_id",
        "invoice_number_prefix",
        "from_company",
        "from_address",
        "from_email",
        "from_phone",
        "to_name",
        "to_address",
        "to_email",
        "line_items_json",
        "tax_rate",
        "discount",
        "notes",
        "payment_info",
        "theme",
        "interval",
        "net_days",
        "next_run_date",
        "last_run_date",
        "auto_send",
        "is_active",
        "created_at",
    },
    "branding_profiles": {
        "id",
        "user_id",
        "logo_filename",
        "accent_color",
        "font_choice",
        "remove_footer",
    },
}


class LegacySchemaError(RuntimeError):
    """Raised when an unversioned schema cannot safely be stamped."""


def validate_invoice_number_uniqueness(db_obj) -> None:
    """Fail with operator guidance before Alembic attempts the constraint."""
    if "invoices" not in inspect(db_obj.engine).get_table_names():
        return

    with db_obj.engine.connect() as connection:
        duplicate_group_count = connection.execute(
            db_obj.text(
                "SELECT COUNT(*) FROM ("
                "SELECT user_id, invoice_number FROM invoices "
                "GROUP BY user_id, invoice_number HAVING COUNT(*) > 1"
                ") AS duplicate_invoice_numbers"
            )
        ).scalar_one()
    if duplicate_group_count:
        raise LegacySchemaError(
            "Cannot add per-user invoice-number uniqueness: "
            f"{duplicate_group_count} duplicate group(s) exist. Back up the "
            "database, assign distinct invoice numbers within each user "
            "account, and rerun db-bootstrap."
        )


def upgrade_known_legacy_columns(db_obj) -> None:
    """Add only columns introduced by PDFBillr's former compatibility bridge."""
    inspector = inspect(db_obj.engine)
    if "invoices" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("invoices")}
    dialect = db_obj.engine.dialect.name
    timestamp_type = (
        "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
    )
    bool_default = "FALSE" if dialect == "postgresql" else "0"
    invoice_columns = {
        "view_token": "VARCHAR(64)",
        "viewed_at": timestamp_type,
        "view_count": "INTEGER NOT NULL DEFAULT 0",
        "reminder_3d_sent": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "reminder_0d_sent": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "reminder_7d_sent": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
    }

    with db_obj.engine.begin() as connection:
        for column, column_type in invoice_columns.items():
            if column not in existing:
                connection.execute(
                    db_obj.text(
                        f"ALTER TABLE invoices ADD COLUMN {column} {column_type}"
                    )
                )
        connection.execute(
            db_obj.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_invoices_view_token "
                "ON invoices (view_token)"
            )
        )

    inspector = inspect(db_obj.engine)
    if "subscriptions" in inspector.get_table_names():
        subscription_columns = {
            column["name"] for column in inspector.get_columns("subscriptions")
        }
        if "last_stripe_event_created" not in subscription_columns:
            with db_obj.engine.begin() as connection:
                connection.execute(
                    db_obj.text(
                        "ALTER TABLE subscriptions "
                        "ADD COLUMN last_stripe_event_created BIGINT"
                    )
                )


def validate_legacy_baseline(db_obj) -> None:
    """Require a complete known schema before assigning an Alembic revision."""
    inspector = inspect(db_obj.engine)
    table_names = set(inspector.get_table_names())
    problems: list[str] = []

    for table_name, required_columns in _BASELINE_COLUMNS.items():
        if table_name not in table_names:
            problems.append(f"missing table {table_name}")
            continue
        actual_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        missing_columns = sorted(required_columns - actual_columns)
        if missing_columns:
            problems.append(
                f"{table_name} missing columns: {', '.join(missing_columns)}"
            )

    if problems:
        detail = "; ".join(problems)
        raise LegacySchemaError(
            "The unversioned database does not match a supported PDFBillr "
            f"legacy schema ({detail}). Restore from backup and migrate it "
            "with an application version that understands this schema; it "
            "was not stamped."
        )


def bootstrap_database(db_obj) -> str:
    """Upgrade a fresh, versioned, or verified unversioned legacy database.

    Returns a short transition label for CLI/test reporting.
    """
    inspector = inspect(db_obj.engine)
    table_names = set(inspector.get_table_names())

    if "alembic_version" in table_names:
        validate_invoice_number_uniqueness(db_obj)
        upgrade(revision="head")
        return "versioned"

    application_tables = table_names & set(_BASELINE_COLUMNS)
    if not application_tables:
        upgrade(revision="head")
        return "fresh"

    upgrade_known_legacy_columns(db_obj)
    validate_legacy_baseline(db_obj)
    validate_invoice_number_uniqueness(db_obj)
    stamp(revision=BASELINE_REVISION)
    upgrade(revision="head")
    return "legacy"
