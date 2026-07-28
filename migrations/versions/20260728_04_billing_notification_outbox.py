"""Add the durable Stripe billing-notification outbox.

Revision ID: 20260728_04
Revises: 20260728_03
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_04"
down_revision = "20260728_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_notification_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("template", sa.String(length=100), nullable=False),
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
            ["stripe_event_id"],
            ["processed_stripe_events.stripe_event_id"],
            name="fk_billing_notifications_stripe_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_billing_notifications_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stripe_event_id",
            name="uq_billing_notifications_stripe_event_id",
        ),
    )
    op.create_index(
        "ix_billing_notification_deliveries_status",
        "billing_notification_deliveries",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_billing_notification_deliveries_status",
        table_name="billing_notification_deliveries",
    )
    op.drop_table("billing_notification_deliveries")
