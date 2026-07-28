"""Establish the pre-Alembic PDFBillr schema baseline.

Revision ID: 20260728_01
Revises:
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "processed_stripe_events",
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("stripe_event_id"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=20), nullable=False),
        sa.Column("stripe_sub_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_stripe_event_created", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(length=200), nullable=False),
        sa.Column("invoice_date", sa.String(length=50), nullable=True),
        sa.Column("due_date", sa.String(length=50), nullable=True),
        sa.Column("from_company", sa.String(length=200), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("from_email", sa.String(length=200), nullable=True),
        sa.Column("from_phone", sa.String(length=200), nullable=True),
        sa.Column("to_name", sa.String(length=200), nullable=True),
        sa.Column("to_address", sa.Text(), nullable=True),
        sa.Column("to_email", sa.String(length=200), nullable=True),
        sa.Column("line_items_json", sa.Text(), nullable=True),
        sa.Column("tax_rate", sa.Float(), nullable=True),
        sa.Column("discount", sa.Float(), nullable=True),
        sa.Column("subtotal", sa.Float(), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payment_info", sa.Text(), nullable=True),
        sa.Column("logo_filename", sa.String(length=255), nullable=True),
        sa.Column("theme", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_token", sa.String(length=64), nullable=True),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=True),
        sa.Column("reminder_3d_sent", sa.Boolean(), nullable=True),
        sa.Column("reminder_0d_sent", sa.Boolean(), nullable=True),
        sa.Column("reminder_7d_sent", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invoices_view_token",
        "invoices",
        ["view_token"],
        unique=True,
    )

    op.create_table(
        "recurring_invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("invoice_number_prefix", sa.String(length=100), nullable=True),
        sa.Column("from_company", sa.String(length=200), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("from_email", sa.String(length=200), nullable=True),
        sa.Column("from_phone", sa.String(length=200), nullable=True),
        sa.Column("to_name", sa.String(length=200), nullable=True),
        sa.Column("to_address", sa.Text(), nullable=True),
        sa.Column("to_email", sa.String(length=200), nullable=True),
        sa.Column("line_items_json", sa.Text(), nullable=True),
        sa.Column("tax_rate", sa.Float(), nullable=True),
        sa.Column("discount", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payment_info", sa.Text(), nullable=True),
        sa.Column("theme", sa.String(length=50), nullable=True),
        sa.Column("interval", sa.String(length=20), nullable=False),
        sa.Column("net_days", sa.Integer(), nullable=True),
        sa.Column("next_run_date", sa.Date(), nullable=False),
        sa.Column("last_run_date", sa.Date(), nullable=True),
        sa.Column("auto_send", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "branding_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("logo_filename", sa.String(length=255), nullable=True),
        sa.Column("accent_color", sa.String(length=20), nullable=True),
        sa.Column("font_choice", sa.String(length=50), nullable=True),
        sa.Column("remove_footer", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade():
    op.drop_table("branding_profiles")
    op.drop_table("recurring_invoices")
    op.drop_index("ix_invoices_view_token", table_name="invoices")
    op.drop_table("invoices")
    op.drop_table("subscriptions")
    op.drop_table("processed_stripe_events")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
