"""Add snapshotted ISO currency codes to invoice records.

Revision ID: 20260728_09
Revises: 20260728_08
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_09"
down_revision = "20260728_08"
branch_labels = None
depends_on = None

_ALLOWED = "currency_code IN ('USD', 'CAD', 'EUR', 'GBP', 'AUD', 'JPY')"


def upgrade():
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "currency_code",
                sa.String(length=3),
                server_default="USD",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_invoices_currency_code",
            _ALLOWED,
        )

    with op.batch_alter_table("recurring_invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "currency_code",
                sa.String(length=3),
                server_default="USD",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_recurring_invoices_currency_code",
            _ALLOWED,
        )


def downgrade():
    with op.batch_alter_table("recurring_invoices") as batch_op:
        batch_op.drop_constraint(
            "ck_recurring_invoices_currency_code",
            type_="check",
        )
        batch_op.drop_column("currency_code")

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_constraint("ck_invoices_currency_code", type_="check")
        batch_op.drop_column("currency_code")
