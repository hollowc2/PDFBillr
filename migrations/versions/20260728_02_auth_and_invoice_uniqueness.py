"""Add session versioning and per-user invoice-number uniqueness.

Revision ID: 20260728_02
Revises: 20260728_01
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_02"
down_revision = "20260728_01"
branch_labels = None
depends_on = None


def _reject_duplicate_invoice_numbers() -> None:
    connection = op.get_bind()
    duplicate_group_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT user_id, invoice_number FROM invoices "
            "GROUP BY user_id, invoice_number HAVING COUNT(*) > 1"
            ") AS duplicate_invoice_numbers"
        )
    ).scalar_one()
    if duplicate_group_count:
        raise RuntimeError(
            "Cannot add per-user invoice-number uniqueness: "
            f"{duplicate_group_count} duplicate group(s) exist. Back up the "
            "database, assign distinct invoice numbers within each user "
            "account, and rerun db-bootstrap."
        )


def upgrade():
    _reject_duplicate_invoice_numbers()
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "auth_session_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.create_unique_constraint(
            "uq_invoices_user_id_invoice_number",
            ["user_id", "invoice_number"],
        )


def downgrade():
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_constraint(
            "uq_invoices_user_id_invoice_number",
            type_="unique",
        )
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("auth_session_version")
