"""Macro series, and observations keyed by the date they were published as well as the period.

**The vintage is part of an observation's identity.** GDP for the first quarter of 2020 has
one period and many values — the advance estimate, two revisions, the annual revision, and
every rebasing since. A schema keeping only the latest could not answer "what did this
analysis have available on its as-of date?", which is the question the whole point-in-time
design exists to answer. So the unique key is ``(series, period, vintage)``, two rows for one
period at different vintages are correct, and two at the same vintage are a bug.

``is_archived`` records how strong the vintage claim is, because the two sources differ.
ALFRED genuinely serves a series as it stood on a chosen date. The ONS serves the current
series and reports its release date, which is weaker — and a UK figure inheriting a US
figure's point-in-time guarantee would be exactly the failure this table prevents.

The composite index is descending on vintage because every read is "this series, this period,
the newest vintage not after a cutoff", and an ascending index would scan every vintage ever
stored to find the one that matters.

Adds ``ons`` to the ``provider`` enum. UK CPI comes from the Office for National Statistics
rather than from FRED: FRED's UK series are OECD-sourced and carry OECD copyright, while the
ONS publishes the same figures under the Open Government Licence.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Outside a transaction block in older Postgres; 12+ allows it inline, and the project
    # targets 16.
    op.execute("ALTER TYPE provider ADD VALUE IF NOT EXISTS 'ons'")

    op.create_table(
        "macro_series",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        # References the type migration 0003 created, with the value added above. Built with
        # `create_type=False` because a bare `sa.Enum` inside `create_table` emits a
        # CREATE TYPE regardless and fails on the second migration to use it.
        sa.Column("provider", postgresql.ENUM(name="provider", create_type=False), nullable=False),
        sa.Column("identifier", sa.String(64), nullable=False),
        sa.Column("dataset", sa.String(32), server_default="", nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(32), server_default="pure", nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("originator", sa.Text(), nullable=False),
        sa.Column("licence_note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
        sa.CheckConstraint("char_length(btrim(key)) > 0", name="macro_series_key_is_not_blank"),
        sa.CheckConstraint(
            "char_length(btrim(identifier)) > 0", name="macro_series_identifier_is_not_blank"
        ),
        sa.CheckConstraint(
            "char_length(btrim(licence_note)) > 0", name="macro_series_licence_is_not_blank"
        ),
    )
    op.create_index("ix_macro_series_provider", "macro_series", ["provider"])

    op.create_table(
        "macro_observations",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("series_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("vintage", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(38, 12), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("source_document_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["series_id"], ["macro_series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "series_id", "observed_on", "vintage", name="uq_macro_observations_point"
        ),
        sa.CheckConstraint("vintage >= observed_on", name="macro_vintage_not_before_period"),
    )
    op.create_index("ix_macro_observations_series_id", "macro_observations", ["series_id"])
    op.create_index(
        "ix_macro_observations_pit",
        "macro_observations",
        ["series_id", "observed_on", sa.text("vintage DESC")],
    )


def downgrade() -> None:
    op.drop_table("macro_observations")
    op.drop_table("macro_series")
    # The enum value stays. Postgres cannot drop one, and recreating the type would mean
    # rewriting every column that uses it -- a far larger operation than this migration was.
