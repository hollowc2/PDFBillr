"""Add reusable clients, services, and business defaults.

Revision ID: 20260728_07
Revises: 20260728_06
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_07"
down_revision = "20260728_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_defaults",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("from_company", sa.String(length=200), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("from_email", sa.String(length=200), nullable=True),
        sa.Column("from_phone", sa.String(length=200), nullable=True),
        sa.Column("default_notes", sa.Text(), nullable=True),
        sa.Column("default_payment_info", sa.Text(), nullable=True),
        sa.Column(
            "default_tax_rate",
            sa.Numeric(precision=7, scale=4),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "default_payment_terms_days",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "default_payment_terms_days >= 0 "
            "AND default_payment_terms_days <= 3650",
            name="ck_business_defaults_payment_terms_range",
        ),
        sa.CheckConstraint(
            "default_tax_rate >= 0 AND default_tax_rate <= 100",
            name="ck_business_defaults_tax_rate_range",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_business_defaults_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            name="uq_business_defaults_user_id",
        ),
    )

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "default_tax_rate",
            sa.Numeric(precision=7, scale=4),
            nullable=True,
        ),
        sa.Column("default_payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "default_payment_terms_days IS NULL "
            "OR (default_payment_terms_days >= 0 "
            "AND default_payment_terms_days <= 3650)",
            name="ck_clients_payment_terms_range",
        ),
        sa.CheckConstraint(
            "default_tax_rate IS NULL "
            "OR (default_tax_rate >= 0 AND default_tax_rate <= 100)",
            name="ck_clients_tax_rate_range",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_clients_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_clients_user_id_normalized_name",
        ),
    )
    op.create_index(
        "ix_clients_user_id",
        "clients",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "service_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "default_rate",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
        ),
        sa.Column(
            "default_quantity",
            sa.Numeric(precision=18, scale=4),
            server_default="1",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "default_quantity > 0",
            name="ck_service_items_default_quantity_positive",
        ),
        sa.CheckConstraint(
            "default_rate >= 0",
            name="ck_service_items_default_rate_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_service_items_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_service_items_user_id_normalized_name",
        ),
    )
    op.create_index(
        "ix_service_items_user_id",
        "service_items",
        ["user_id"],
        unique=False,
    )

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(sa.Column("client_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_invoices_client_id",
            "clients",
            ["client_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_invoices_client_id",
            ["client_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_index("ix_invoices_client_id")
        batch_op.drop_constraint(
            "fk_invoices_client_id",
            type_="foreignkey",
        )
        batch_op.drop_column("client_id")

    op.drop_index("ix_service_items_user_id", table_name="service_items")
    op.drop_table("service_items")
    op.drop_index("ix_clients_user_id", table_name="clients")
    op.drop_table("clients")
    op.drop_table("business_defaults")
