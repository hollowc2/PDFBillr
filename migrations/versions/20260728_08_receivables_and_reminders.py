"""Add configurable payment reminder preferences.

Revision ID: 20260728_08
Revises: 20260728_07
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_08"
down_revision = "20260728_07"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reminder_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "before_due_days",
            sa.Integer(),
            server_default="3",
            nullable=True,
        ),
        sa.Column(
            "on_due_date",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "overdue_days",
            sa.Integer(),
            server_default="7",
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "before_due_days IS NULL "
            "OR (before_due_days >= 1 AND before_due_days <= 30)",
            name="ck_reminder_preferences_before_due_days",
        ),
        sa.CheckConstraint(
            "overdue_days IS NULL "
            "OR (overdue_days >= 1 AND overdue_days <= 90)",
            name="ck_reminder_preferences_overdue_days",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_reminder_preferences_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            name="uq_reminder_preferences_user_id",
        ),
    )

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "payment_reminders_enabled",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )


def downgrade():
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_column("payment_reminders_enabled")

    op.drop_table("reminder_preferences")
