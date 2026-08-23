"""A numeric claim may name an attestation, and invariant 3 is restated to say so.

ADR 0069. The claims constraint was a two-way exclusive choice:

    (kind = 'numeric') = (
      (financial_fact_id IS NOT NULL)::int + (calculation_id IS NOT NULL)::int = 1
    )

which is invariant 3 written as a check — "no figure reaches a report unless it is a stored
fact or a recorded calculation" — and which meant a report could never make a numeric claim
about a holding. Revision 0053 admitted the fourth record kind; this is the seam it has to
come through.

**The widening admits a third kind of figure, not an unevidenced one.** A ``documented``
attestation traces to a hashed artefact by the same chain a filing does. An ``attested`` one
reaches no shareable surface at all, because the type it propagates into has no field for the
figure — so the containment is upstream of this constraint rather than relaxed by it.

**Still exactly one arm.** A claim naming both a fact and an attestation would be a sentence
asserting two numbers, and which one a reader saw would depend on which column a renderer
happened to read.

**Macro remains a seam this does not close.** A ``macro_observations`` row is neither a
financial fact nor an attestation, so a gilt yield still reaches a report only wrapped in a
calculation. ADR 0069 named that question and picked neither answer; nothing here picks one
either.

The downgrade drops the column, and drops any numeric claim resting on an attestation with
it — there is nowhere else for such a claim's figure to live, and a claim silently relabelled
as resting on something else would be worse than a migration that removes it.

Revision ID: 0054
Revises: 0053
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_TWO_WAY = (
    "(kind = 'numeric') = ("
    "  (financial_fact_id IS NOT NULL)::int + (calculation_id IS NOT NULL)::int = 1"
    ")"
)
_THREE_WAY = (
    "(kind = 'numeric') = ("
    "  (financial_fact_id IS NOT NULL)::int + (calculation_id IS NOT NULL)::int"
    "  + (attestation_id IS NOT NULL)::int = 1"
    ")"
)
_CONSTRAINT = "ck_claims_numeric_claims_name_one_figure"


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column(
            "attestation_id",
            sa.Uuid(),
            sa.ForeignKey("attestations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    # Dropped and recreated rather than altered: Postgres has no ALTER for a CHECK's
    # expression, and the two statements run in one transaction so no window exists in which
    # a numeric claim could be written naming no figure at all.
    op.drop_constraint(_CONSTRAINT, "claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "claims", _THREE_WAY)


def downgrade() -> None:
    op.execute("DELETE FROM claims WHERE attestation_id IS NOT NULL")
    op.drop_constraint(_CONSTRAINT, "claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "claims", _TWO_WAY)
    op.drop_column("claims", "attestation_id")
