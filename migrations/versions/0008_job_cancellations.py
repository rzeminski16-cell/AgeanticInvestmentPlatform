"""Add ``job_cancellations``: a request to stop a run.

A column on ``jobs`` would have been the obvious place and does not work. The worker sets
``jobs.status = RUNNING``, flushes, and commits only when the whole run ends — so it holds
that row's lock for the run's entire lifetime. A second session's ``UPDATE`` on the same row
waits for it, which means a cancel issued from the web process would block for precisely as
long as cancelling remained useful. That was measured, not assumed.

A separate row is written by nobody else, so the web process never waits. It also records
the two facts separately, which is the truthful shape: an in-flight model call or HTTP fetch
cannot be interrupted, so when the operator asked and when the run actually stopped are
different times and both belong in the audit trail.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_cancellations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_cancellations"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_cancellations_job_id_jobs",
            ondelete="CASCADE",
        ),
        # SET NULL, not CASCADE: deleting a user must not erase the record that a run was
        # stopped, only who stopped it.
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_job_cancellations_requested_by_users",
            ondelete="SET NULL",
        ),
        # One standing request per job. A second click is idempotent rather than a second
        # row nobody can interpret.
        sa.UniqueConstraint("job_id", name="uq_job_cancellations_job_id"),
    )


def downgrade() -> None:
    op.drop_table("job_cancellations")
