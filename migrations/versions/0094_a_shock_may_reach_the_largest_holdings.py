"""A shock may reach the largest holdings: the drawn panel's *the largest three*.

`shock_kind` gains `largest`, whose target is how many (ADR 0106, amended 29 September 2026).
The set is read as the book stands on the date the scenario is applied, so one statement
reaches whichever holdings are largest then; nothing about the holdings is stored with it.

Postgres cannot remove an enum value, so the downgrade leaves it in place, as 0078 and 0083
did theirs.

Revision ID: 0094
Revises: 0093
"""

from __future__ import annotations

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Outside the transaction: Postgres refuses to add an enum value inside a transaction
    # block that then uses it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE shock_kind ADD VALUE IF NOT EXISTS 'largest'")


def downgrade() -> None:
    # A one-way door. Rows that use the value go, so a later upgrade finds none it cannot read.
    op.execute("DELETE FROM risk_scenario_shocks WHERE kind = 'largest'")
