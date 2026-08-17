"""Facts carry their XBRL dimension, so a segment's revenue can exist as a row.

The live report's segment-mix exhibit rendered its placeholder because the schema could
not hold what it needed: ``financial_facts`` keyed one value per concept per period, so
two segments' revenue for the same year were the same observation to the unique index and
the second could never be stored. The dimension — the axis and member a filing tags a
breakdown with — is part of the observation's identity, and now it is part of the key.

Both columns are NULL for every existing row, which is correct rather than convenient:
every fact stored so far came from the companyfacts aggregate, which carries only the
consolidated figures. NULLS NOT DISTINCT on the rebuilt index keeps those rows
deduplicating exactly as before.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_OBSERVATION_COLUMNS = (
    "company_id",
    "concept",
    "unit",
    "period_end",
    "fiscal_period",
    "basis",
    "filed_date",
)


def upgrade() -> None:
    op.add_column("financial_facts", sa.Column("dimension_axis", sa.String(128), nullable=True))
    op.add_column("financial_facts", sa.Column("dimension_member", sa.String(256), nullable=True))
    op.create_check_constraint(
        "dimension_names_both_halves",
        "financial_facts",
        "(dimension_axis IS NULL) = (dimension_member IS NULL)",
    )

    op.drop_index("uq_financial_facts_observation", table_name="financial_facts")
    op.create_index(
        "uq_financial_facts_observation",
        "financial_facts",
        [*_OBSERVATION_COLUMNS, "dimension_axis", "dimension_member"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    # Dimensioned rows cannot survive the narrower index — two segments' revenue would
    # collide the moment the columns are gone — so they go with the columns that made
    # them representable.
    op.execute(sa.text("DELETE FROM financial_facts WHERE dimension_axis IS NOT NULL"))

    op.drop_index("uq_financial_facts_observation", table_name="financial_facts")
    op.create_index(
        "uq_financial_facts_observation",
        "financial_facts",
        list(_OBSERVATION_COLUMNS),
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("dimension_names_both_halves", "financial_facts", type_="check")
    op.drop_column("financial_facts", "dimension_member")
    op.drop_column("financial_facts", "dimension_axis")
