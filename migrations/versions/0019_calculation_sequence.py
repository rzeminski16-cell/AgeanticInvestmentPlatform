"""Give a calculation its place in the ledger it came from.

``aer.calc.engine`` is explicit that order is significant: "a calculation can only cite ones
that came before it, so persisting in this order never writes a row referencing one that does
not exist". The database threw that ordering away, and ``created_at`` cannot stand in for it —
**Postgres ``now()`` is transaction-start time**, so every row one context persists carries an
identical timestamp. Two valuations written in a single transaction were indistinguishable,
and the tie-break was a random UUID.

Found by the valuation surface, which had to say which of a job's recorded valuations a page
was showing and could not. It is a provenance gap independent of that page: a reader of the
calculations table has the same question.

**Backfilled to zero rather than guessed at.** Existing rows have no recoverable order — that
is the whole point — so they all get the default, and a run predating this migration is
honestly ambiguous rather than plausibly ordered.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calculations",
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    # Every read is "this job's calculations, in order", so the index is on the pair.
    op.create_index("ix_calculations_job_sequence", "calculations", ["job_id", "sequence"])
    op.create_check_constraint(
        "sequence_is_not_negative",
        "calculations",
        "sequence >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_calculations_sequence_is_not_negative", "calculations")
    op.drop_index("ix_calculations_job_sequence", table_name="calculations")
    op.drop_column("calculations", "sequence")
