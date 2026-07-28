"""Add nullable typed shadow columns for staged financial-data migration.

Revision ID: 20260728_05
Revises: 20260728_04
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_05"
down_revision = "20260728_04"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(sa.Column("invoice_date_value", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("due_date_value", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "tax_rate_decimal",
                sa.Numeric(precision=7, scale=4),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "discount_decimal",
                sa.Numeric(precision=18, scale=2),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "subtotal_decimal",
                sa.Numeric(precision=18, scale=2),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "total_decimal",
                sa.Numeric(precision=18, scale=2),
                nullable=True,
            )
        )

    with op.batch_alter_table("recurring_invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "tax_rate_decimal",
                sa.Numeric(precision=7, scale=4),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "discount_decimal",
                sa.Numeric(precision=18, scale=2),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("recurring_invoices") as batch_op:
        batch_op.drop_column("discount_decimal")
        batch_op.drop_column("tax_rate_decimal")

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_column("total_decimal")
        batch_op.drop_column("subtotal_decimal")
        batch_op.drop_column("discount_decimal")
        batch_op.drop_column("tax_rate_decimal")
        batch_op.drop_column("due_date_value")
        batch_op.drop_column("invoice_date_value")
