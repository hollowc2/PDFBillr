"""Add recurring occurrence and invoice delivery ledgers.

Revision ID: 20260728_03
Revises: 20260728_02
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_03"
down_revision = "20260728_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recurring_occurrences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recurring_invoice_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_recurring_occurrences_invoice_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recurring_invoice_id"],
            ["recurring_invoices.id"],
            name="fk_recurring_occurrences_recurring_invoice_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_id",
            name="uq_recurring_occurrences_invoice_id",
        ),
        sa.UniqueConstraint(
            "recurring_invoice_id",
            "scheduled_for",
            name="uq_recurring_occurrences_template_schedule",
        ),
    )
    op.create_index(
        "ix_recurring_occurrences_scheduled_for",
        "recurring_occurrences",
        ["scheduled_for"],
        unique=False,
    )

    op.create_table(
        "invoice_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("delivery_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_deliveries_invoice_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_id",
            "delivery_kind",
            name="uq_invoice_deliveries_invoice_kind",
        ),
    )
    op.create_index(
        "ix_invoice_deliveries_status",
        "invoice_deliveries",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_invoice_deliveries_status",
        table_name="invoice_deliveries",
    )
    op.drop_table("invoice_deliveries")
    op.drop_index(
        "ix_recurring_occurrences_scheduled_for",
        table_name="recurring_occurrences",
    )
    op.drop_table("recurring_occurrences")
