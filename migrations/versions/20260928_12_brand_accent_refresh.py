"""Move branding profiles on the old default accent to the new brand blue.

The branding form pre-filled #1e3a8a, so profiles saved without choosing a
colour carry the old default. They move to #2743A6 with the rest of the brand;
any other colour a user picked is left alone.

Revision ID: 20260928_12
Revises: 20260728_11
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260928_12"
down_revision = "20260728_11"
branch_labels = None
depends_on = None

OLD_DEFAULT = "#1e3a8a"
NEW_DEFAULT = "#2743A6"


def upgrade():
    op.execute(
        sa.text(
            "UPDATE branding_profiles SET accent_color = :new "
            "WHERE lower(accent_color) = :old"
        ).bindparams(new=NEW_DEFAULT, old=OLD_DEFAULT)
    )


def downgrade():
    op.execute(
        sa.text(
            "UPDATE branding_profiles SET accent_color = :old "
            "WHERE upper(accent_color) = upper(:new)"
        ).bindparams(new=NEW_DEFAULT, old=OLD_DEFAULT)
    )
