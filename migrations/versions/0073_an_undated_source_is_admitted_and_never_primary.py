"""Two rules stop wearing one flag: look-ahead keeps `point_in_time`, datability gets its own.

`decide_quarantine` refused a source whose publication date could not be established
whenever `point_in_time` was on, before it ever reached the look-ahead test. That is two
policies on one boolean, and the only way to admit an undated news page was to switch off
the check that refuses a source published after the as-of date — which is not a trade
anybody would choose on its merits.

So the work order carries a second policy. `undated_sources_admissible` defaults to true:
a page nobody can date is worth reading, and refusing every one of them is why a run's
plan named news sources and its evidence table held none. The compensating rule is not a
column — `SourceTier.as_evidence` caps an undated document at tier 5, so it may
corroborate and may never be the primary source a section's policy requires.

**Existing rows are backfilled to true and nothing that already happened changes.** The
quarantine decision is recorded on each `source_documents` row at acquisition, so a
document already refused for `no_publication_date` stays refused, with its reason, in
every run that fetched it. The policy governs what the *next* acquisition decides.

Revision ID: 0073
Revises: 0072
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column(
            "undated_sources_admissible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("work_orders", "undated_sources_admissible")
