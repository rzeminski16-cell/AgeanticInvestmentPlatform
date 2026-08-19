"""Themes: cross-company edges a model proposes and a person confirms.

`docs/knowledge-graph.md` K1, ADR 0065. The comparable relation cannot express *AI capital
expenditure* linking a hyperscaler, a fab and a utility; a theme can, and it enters the
graph the way a peer set does — through a gate.

Three pieces. The ``THEME_SET`` value joins ``gate_kind`` in an autocommit block, the 0027
pattern: PostgreSQL permits ``ADD VALUE`` inside a transaction from 12 onwards but refuses
to *use* the value in the same transaction, and the safest reading is not to try. The
``themes`` table is the shared identity — one key however many runs name it, which is what
stops the graph filling with near-duplicate spellings. And ``theme_memberships`` is one
run's confirmed claim that its subject belongs, unique per (theme, company, report) so a
retried report step re-records the same set as a no-op.

The downgrade rebuilds ``gate_kind`` without ``THEME_SET`` and deliberately has no
``USING`` fallback: a recorded THEME_SET approval fails the cast and stops the downgrade,
because a decision silently relabelled as a different gate is worse than a migration that
refuses.

Revision ID: 0048
Revises: 0047
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_GATE = "THEME_SET"

# Every column typed `gate_kind`, which the downgrade must convert before the type can be
# dropped. The same pair 0027 and 0046 handled; nothing has joined the type since.
_COLUMNS_ON_THE_TYPE = (
    ("approvals", "gate"),
    ("disagreements", "escalated_to_gate"),
)

_WITHOUT_THEME_SET = (
    "PLAN",
    "UNMAPPED_CONCEPTS",
    "PEER_SET",
    "SECTOR_SPECIALIST",
    "ASSUMPTIONS",
    "BUDGET",
    "FINAL",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE gate_kind ADD VALUE IF NOT EXISTS '{_GATE}'")

    op.create_table(
        "themes",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # The identity a later run's proposal matches against. Slugged in code before it
        # reaches a row, so "AI Capex" and "ai-capex" cannot found two themes.
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "theme_memberships",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "theme_id",
            sa.Uuid(),
            sa.ForeignKey("themes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # CASCADE: destroying a report (the retention flow) takes its theme claims with
        # it. A membership outliving the run that made it would be an edge with no
        # provenance.
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # What the reviewer read at the gate, verbatim.
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "theme_id", "company_id", "report_id", name="uq_theme_memberships_triple"
        ),
    )


def downgrade() -> None:
    op.drop_table("theme_memberships")
    op.drop_table("themes")

    values = ", ".join(f"'{value}'" for value in _WITHOUT_THEME_SET)
    op.execute(f"CREATE TYPE gate_kind_old AS ENUM ({values})")
    for table, column in _COLUMNS_ON_THE_TYPE:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE gate_kind_old "
            f"USING {column}::text::gate_kind_old"
        )
    op.execute("DROP TYPE gate_kind")
    op.execute("ALTER TYPE gate_kind_old RENAME TO gate_kind")
