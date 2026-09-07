"""Revision notes: what the critique loop did, one row per decision.

ADR 0091. The critique-and-revise loop (roadmap §3.13) records every challenge it acted
on — and every one it deliberately declined — with the challenge's class as a column,
because recurrence is counted by class across runs and a grouping key buried in JSON is a
grouping key nobody queries.

The table is memory, not teaching: nothing reads these rows into a prompt. A recurring
class reaches a future run only as an operator-authored methodology skill, through the
additive-only boundary invariant 7 already governs.

Revision ID: 0059
Revises: 0058
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revision_notes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("section_key", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("disposition", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("scope IN ('plan', 'draft')", name="scope_is_known"),
        sa.CheckConstraint(
            "disposition IN ('revised', 'stood', 'skipped_custom')",
            name="disposition_is_known",
        ),
        sa.CheckConstraint("severity BETWEEN 1 AND 5", name="severity_is_scored"),
        sa.CheckConstraint("char_length(btrim(dimension)) > 0", name="dimension_is_recorded"),
    )
    op.create_index("ix_revision_notes_job_id", "revision_notes", ["job_id"])
    op.create_index("ix_revision_notes_scope_dimension", "revision_notes", ["scope", "dimension"])


def downgrade() -> None:
    op.drop_index("ix_revision_notes_scope_dimension", table_name="revision_notes")
    op.drop_index("ix_revision_notes_job_id", table_name="revision_notes")
    op.drop_table("revision_notes")
