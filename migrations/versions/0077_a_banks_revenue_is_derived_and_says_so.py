"""A derived fact carries its workings: `financial_facts.derivation` (ADR 0114).

A bank's income statement has no revenue caption, so the concept map resolved `revenue` to
the nearest tag with the right word in its name — the ASC 606 fee-income disclosure — and
the M&T run published a 172.1% net margin from a correctly extracted, correctly hashed,
correctly cited figure that was an answer to a question nobody asked.

Under ADR 0114 a confirmed bank's revenue is the sum of net interest income and
non-interest income, derived at the fact layer so that all six readers of a fact see the
same top line. That row is not a re-labelled tag: it has `basis = 'derived'` and a
`derivation` naming its formula, both inputs by id with their own units and source
documents, and the code version that produced it. The check constraint makes the pair
inseparable in both directions.

**Nothing already stored changes.** Every existing row is `as_reported` with no
derivation, which is exactly what the constraint requires of it. The downgrade drops the
column and the constraint; it deletes no rows, so a database that has derived facts must
have them removed before an older build reads it — the enum value cannot be withdrawn from
PostgreSQL, and a `derived` row with nowhere to keep its workings would be the number
somebody typed that invariant 3 exists to make impossible.

Revision ID: 0077
Revises: 0076
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

_CHECK = "a_derived_fact_carries_its_workings"


def upgrade() -> None:
    # Its own transaction: PostgreSQL permits a new enum value inside one but refuses to
    # let the same transaction use it, and the check constraint below names it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fact_basis ADD VALUE IF NOT EXISTS 'derived'")

    op.add_column(
        "financial_facts",
        sa.Column("derivation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        _CHECK,
        "financial_facts",
        "(basis = 'derived') = (derivation IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, "financial_facts", type_="check")
    op.drop_column("financial_facts", "derivation")
