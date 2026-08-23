"""Obsidian exports, and the provider value for a prior run's own output.

Task 50. ``obsidian_exports`` records each act of exporting an approved report to the
vault — when, which files, and which generator version wrote them — shown on the report
page so "is the vault current?" has an answer. The vault itself stays a derived,
one-directional projection: nothing in it is ever read back as evidence, which is also
why the ``provider`` enum gains ``internal_prior_run`` here — the value that marks a
prior run's output when it is fed forward, so the citation verifier can hard-reject any
claim leaning on it (docs/archive/PLAN.md section 2.8, rule 4).

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Permitted inside a transaction since PostgreSQL 12, provided the new value is not
    # used in the same transaction — and nothing here uses it.
    op.execute("ALTER TYPE provider ADD VALUE IF NOT EXISTS 'internal_prior_run'")

    op.create_table(
        "obsidian_exports",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        # Vault-relative paths, in the order they were written. JSONB rather than rows:
        # the list is read whole to answer "what did this export touch", never queried by
        # element.
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column("generator_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_obsidian_exports_report_id", "obsidian_exports", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_obsidian_exports_report_id", table_name="obsidian_exports")
    op.drop_table("obsidian_exports")
    # The enum value stays: PostgreSQL cannot remove one, and rows may carry it.
