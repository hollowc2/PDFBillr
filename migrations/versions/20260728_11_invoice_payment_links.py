"""Add merchant-provided HTTPS invoice payment links.

Revision ID: 20260728_11
Revises: 20260728_10
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_11"
down_revision = "20260728_10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("business_defaults") as batch_op:
        batch_op.add_column(
            sa.Column("default_payment_url", sa.String(length=2048), nullable=True)
        )

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column("payment_url", sa.String(length=2048), nullable=True)
        )

    with op.batch_alter_table("recurring_invoices") as batch_op:
        batch_op.add_column(
            sa.Column("payment_url", sa.String(length=2048), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("recurring_invoices") as batch_op:
        batch_op.drop_column("payment_url")

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_column("payment_url")

    with op.batch_alter_table("business_defaults") as batch_op:
        batch_op.drop_column("default_payment_url")
