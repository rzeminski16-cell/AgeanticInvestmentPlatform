"""Settings an operator may change without editing a file.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings_overrides",
        # The setting's name on `Settings`, so an override is traceable to the field it
        # replaces without a translation table nobody maintains.
        sa.Column("key", sa.Text(), primary_key=True),
        # JSONB rather than text: a model-routing table is a nested object, and storing it
        # as a string would move parsing into every reader.
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("settings_overrides")
