"""Add a PDF paper size per invoice and a default per account.

Revision ID: 20260928_13
Revises: 20260928_12
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260928_13"
down_revision = "20260928_12"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("business_defaults") as batch_op:
        batch_op.add_column(
            sa.Column(
                "default_page_size",
                sa.String(length=10),
                nullable=False,
                server_default="A4",
            )
        )

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "page_size",
                sa.String(length=10),
                nullable=False,
                server_default="A4",
            )
        )


def downgrade():
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_column("page_size")

    with op.batch_alter_table("business_defaults") as batch_op:
        batch_op.drop_column("default_page_size")
