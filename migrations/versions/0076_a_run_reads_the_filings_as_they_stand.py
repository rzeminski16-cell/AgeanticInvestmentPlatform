"""The point-in-time mode is retired: `work_orders.point_in_time` is dropped (ADR 0113).

The column said whether evidence published after the run's as-of date was admissible, and
the as-of date has been the day the run was commissioned since ADR 0110 — a stamp, not a
choice. A rule refusing what was published after today never had anything to refuse, and
on every stored run the metric that measured it read "not exercised". The mode, the
quarantine branch, the claim-time check, the two metrics and the trigger all go with the
column; what stays is `undated_sources_admissible`, which is a policy about evidence
quality rather than about a date, and `as_of_date`, which is the run's own stamp.

**Nothing that already happened changes.** Every quarantine decision is recorded on its
`source_documents` row with its reason, every evaluation row keeps its metric name, and
every sealed gate payload keeps its record; a run stored under the old build replays from
its own rows exactly as before. The downgrade restores the column with its old default so
an older build can read the table again; it cannot restore the settings individual runs
carried, because a mode that constrained nothing left no trace worth keeping.

Revision ID: 0076
Revises: 0075
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("work_orders", "point_in_time")


def downgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column(
            "point_in_time",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
