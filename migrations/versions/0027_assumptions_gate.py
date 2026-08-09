"""A gate for the numbers a valuation is about to be built on.

Gap B2, ADR 0046. Every other gate approves work already done; this one approves the
assumptions a discounted cash flow is about to run on, six of them derived from the
filings and two proposed by a model. It is conditional — a run whose sector mandate blocks
a discounted cash flow never reaches it.

``ALTER TYPE ... ADD VALUE`` runs outside the migration's transaction. PostgreSQL permits
it inside one from 12 onwards but refuses to *use* the new value in the same transaction,
and the safest reading of that is not to try: the autocommit block makes the addition
durable on its own before anything else looks at the type.

Going back is a rebuild rather than a removal, because PostgreSQL has no
``DROP VALUE``. The rebuild deliberately has no ``USING`` clause that could coerce an
``ASSUMPTIONS`` row into some other gate: if one exists, the cast fails and the downgrade
stops. An approval silently relabelled as a different decision is worse than a failed
migration.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_GATE = "ASSUMPTIONS"

# The order the type is rebuilt in on the way down: GateKind without ASSUMPTIONS.
_WITHOUT_ASSUMPTIONS = (
    "PLAN",
    "UK_FINANCIALS",
    "PEER_SET",
    "SECTOR_SPECIALIST",
    "BUDGET",
    "FINAL",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE gate_kind ADD VALUE IF NOT EXISTS '{_GATE}'")


def downgrade() -> None:
    values = ", ".join(f"'{value}'" for value in _WITHOUT_ASSUMPTIONS)
    op.execute(f"CREATE TYPE gate_kind_old AS ENUM ({values})")
    # No USING expression: a row carrying the removed value fails the cast and takes the
    # downgrade with it, which is the intended outcome.
    op.execute(
        "ALTER TABLE approvals ALTER COLUMN gate TYPE gate_kind_old USING gate::text::gate_kind_old"
    )
    op.execute("DROP TYPE gate_kind")
    op.execute("ALTER TYPE gate_kind_old RENAME TO gate_kind")
