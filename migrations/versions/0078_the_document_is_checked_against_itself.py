"""A contradiction is its own kind of disagreement (ADR 0125).

The cross-section check reaches published calculations and the draft's own sentences, and
what it finds is not a source conflict: both sides are this run's output, struck by the
same code from the same evidence on the same day. There is no tier to prefer, no later
filing to take, and no publisher to name — so the row says `self_contradiction` and the
rule says `document_contradicts_itself`, rather than borrowing a rung that decides between
sources and writing a rationale about a tier contest that never happened.

**Nothing already stored changes.** Every existing row carries one of the values that were
already there. The downgrade is a no-op by necessity: PostgreSQL cannot withdraw an enum
value, and dropping and recreating both types would have to rewrite every row of
`disagreements` — including rows an older build could not read anyway. A database that has
recorded a self-contradiction and must go back must have those rows removed first.

Revision ID: 0078
Revises: 0077
"""

from __future__ import annotations

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Its own transaction: PostgreSQL permits a new enum value inside one but refuses to
    # let the same transaction use it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE disagreement_kind ADD VALUE IF NOT EXISTS 'self_contradiction'")
        op.execute(
            "ALTER TYPE resolution_rule ADD VALUE IF NOT EXISTS 'document_contradicts_itself'"
        )


def downgrade() -> None:
    """Deliberately empty. See the module docstring."""
