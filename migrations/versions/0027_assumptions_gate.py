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
# Every column typed `gate_kind`, which the downgrade has to convert before the type can be
# dropped. `approvals.gate` created it in 0001; `disagreements.escalated_to_gate` arrived in
# 0014 and is nullable, which the text cast handles without a special case.
_COLUMNS_ON_THE_TYPE = (
    ("approvals", "gate"),
    ("disagreements", "escalated_to_gate"),
)

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
    # **Every** column on the type, not just the obvious one. `approvals.gate` created
    # `gate_kind` in 0001 and `disagreements.escalated_to_gate` joined it in 0014; a
    # downgrade that converted only the first left the type undroppable and failed here
    # rather than in production, which is what the round-trip test is for.
    #
    # The cast goes through `text` with no fallback expression, so a row carrying the
    # removed value fails and takes the downgrade with it. That is deliberate: an approval
    # silently relabelled as a different gate is worse than a migration that stops.
    for table, column in _COLUMNS_ON_THE_TYPE:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE gate_kind_old "
            f"USING {column}::text::gate_kind_old"
        )
    op.execute("DROP TYPE gate_kind")
    op.execute("ALTER TYPE gate_kind_old RENAME TO gate_kind")
