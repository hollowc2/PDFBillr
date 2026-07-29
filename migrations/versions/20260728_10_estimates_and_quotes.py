"""Add owner-scoped estimates with public response and conversion state.

Revision ID: 20260728_10
Revises: 20260728_09
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_10"
down_revision = "20260728_09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "estimates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("converted_invoice_id", sa.Integer(), nullable=True),
        sa.Column("estimate_number", sa.String(length=200), nullable=False),
        sa.Column("public_token", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            server_default="USD",
            nullable=False,
        ),
        sa.Column("from_company", sa.String(length=200), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("from_email", sa.String(length=200), nullable=True),
        sa.Column("from_phone", sa.String(length=200), nullable=True),
        sa.Column("to_name", sa.String(length=200), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=True),
        sa.Column("to_email", sa.String(length=200), nullable=True),
        sa.Column("line_items_json", sa.Text(), nullable=False),
        sa.Column(
            "tax_rate",
            sa.Numeric(precision=7, scale=4),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "discount",
            sa.Numeric(precision=18, scale=2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "subtotal",
            sa.Numeric(precision=18, scale=2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total",
            sa.Numeric(precision=18, scale=2),
            server_default="0",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payment_info", sa.Text(), nullable=True),
        sa.Column("client_comment", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discount >= 0 AND subtotal >= 0 AND total >= 0",
            name="ck_estimates_amounts_nonnegative",
        ),
        sa.CheckConstraint(
            "currency_code IN ('USD', 'CAD', 'EUR', 'GBP', 'AUD', 'JPY')",
            name="ck_estimates_currency_code",
        ),
        sa.CheckConstraint(
            "expiry_date >= issue_date",
            name="ck_estimates_date_order",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'accepted', 'declined', "
            "'expired', 'converted')",
            name="ck_estimates_status",
        ),
        sa.CheckConstraint(
            "tax_rate >= 0 AND tax_rate <= 100",
            name="ck_estimates_tax_rate_range",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_estimates_client_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["converted_invoice_id"],
            ["invoices.id"],
            name="fk_estimates_converted_invoice_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_estimates_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "converted_invoice_id",
            name="uq_estimates_converted_invoice_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "estimate_number",
            name="uq_estimates_user_id_estimate_number",
        ),
    )
    op.create_index(
        "ix_estimates_client_id",
        "estimates",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        "ix_estimates_public_token",
        "estimates",
        ["public_token"],
        unique=True,
    )
    op.create_index(
        "ix_estimates_user_id",
        "estimates",
        ["user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_estimates_user_id", table_name="estimates")
    op.drop_index("ix_estimates_public_token", table_name="estimates")
    op.drop_index("ix_estimates_client_id", table_name="estimates")
    op.drop_table("estimates")
