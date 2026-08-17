"""Every calculation names the reporting period it was struck on.

The live AAPL report printed an EBITDA above its own revenue — the ratio was an annual
figure, the fact beside it quarterly, and nothing in the row said which. The period is
part of what a figure *is*: two values are only comparable when both name the same span.

Three nullable columns rather than a backfill, because the honest state of every existing
row is "not stamped": the code that struck them carried no period, and inventing one now
would claim a provenance those rows never had. ``period_label`` is the human form a
reader compares by ("FY2025"); the dates bound the span exactly for code that needs more
than a name. NULL remains meaningful for new rows too — a discount rate or a price
multiple is not a statement-period figure and never gains a stamp.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calculations", sa.Column("period_label", sa.String(32), nullable=True))
    op.add_column("calculations", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("calculations", sa.Column("period_end", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("calculations", "period_end")
    op.drop_column("calculations", "period_start")
    op.drop_column("calculations", "period_label")
