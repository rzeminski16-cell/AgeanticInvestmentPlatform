"""A job can be stepped through, and the flag lives on the row.

ADR 0090. Step mode pauses a run after every step that actually executes, before the next
one spends anything. It is a column rather than a CLI argument because the pause must hold
wherever the run executes: the operator stepping in the terminal and the worker continuing
the same job after a gate approval have to read the same flag, or approving a gate on the
web would quietly run the rest of a run somebody was debugging one step at a time.

Nothing else in ADR 0090 needs schema. `PAUSED` has been in the `job_status` enum since
revision 0001, documented as resumable and set by nothing until now; a resume is an
appended `audit_events` row, and that table already exists.

Revision ID: 0058
Revises: 0057
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("step_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("jobs", "step_mode")
