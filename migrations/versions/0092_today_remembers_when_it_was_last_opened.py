"""Today remembers when it was last opened, so its briefing knows where to start.

The page specification's hub (§1, corrected 28 September 2026) opens with *Since {day}*: what
the platform did while the operator was away, where *{day}* is when they last opened Today,
which the page records. Two moments on the operator's own row, both nullable because an
operator who has never opened the page has neither:

- ``today_seen_at``, the last time Today was rendered for them;
- ``looked_before``, the last look before the visit that render belonged to — NULL while the
  operator is still on their first.

Two rather than one because a visit is not a page load (``aer.core.visits``): reloading the
page, or coming back to it from a report, must not empty the briefing the operator has not
finished reading. Both are looks, never a window's start. Nothing else reads either column,
and neither is a figure.

Revision ID: 0092
Revises: 0091
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("today_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("looked_before", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "looked_before")
    op.drop_column("users", "today_seen_at")
