"""Add ``evaluations``: one run's score against one §2.10 metric.

Task 39. The per-run validators write these rows — the same `aer/eval` arithmetic the CI
gate trusts, applied to a live run's own tables — and the gate 2 dashboard and the task 41
escalation triggers read them. ``passed`` is nullable: NULL means the metric was not
exercised on this run (no post-dated source for look-ahead recall to catch, no assumption
for completeness to check), which is a different statement from a pass and is recorded as
one.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(20, 8), nullable=True),
        sa.Column("threshold", sa.Numeric(20, 8), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("job_id", "metric", name="uq_evaluations_one_row_per_metric"),
        sa.CheckConstraint(
            "(value IS NULL) = (passed IS NULL)",
            name="ck_evaluations_score_and_verdict_travel_together",
        ),
    )
    op.create_index("ix_evaluations_job_id", "evaluations", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluations_job_id", table_name="evaluations")
    op.drop_table("evaluations")
