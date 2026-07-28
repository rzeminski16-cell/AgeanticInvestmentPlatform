"""Add the ticker-resolution flag to research requests.

A request records what the operator typed. Nothing confirms that "MSFT on NASDAQ" is a
real security until an external lookup happens, and no such lookup happens while a
request is being written — deliberately, so that writing one is fast, offline and free.

``resolved`` is what lets everything downstream tell a confirmed identity from an
unverified string. It is added now, defaulting to false, rather than when resolution is
implemented: introduced later it would have to be nullable for the rows that predate it,
and "we never recorded whether this was checked" is not a state worth having in a system
whose premise is that the record can be trusted.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_requests",
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("research_requests", "resolved")
