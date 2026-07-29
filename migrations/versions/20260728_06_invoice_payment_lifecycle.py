"""Add invoice payment lifecycle fields and payment records.

Revision ID: 20260728_06
Revises: 20260728_05
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_06"
down_revision = "20260728_05"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "invoice_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=True),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_invoice_payments_amount_positive",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_payments_invoice_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invoice_payments_invoice_id",
        "invoice_payments",
        ["invoice_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_invoice_payments_invoice_id",
        table_name="invoice_payments",
    )
    op.drop_table("invoice_payments")

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_column("voided_at")
        batch_op.drop_column("paid_at")
