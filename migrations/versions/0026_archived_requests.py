"""A request can be put out of the way without being destroyed.

Gap B1. The requests list had no per-row control at all, and ``delete_request`` refuses
anything a run left evidence or a report behind — correctly, since those are the two things
that exist nowhere else. That left an operator with one option for a finished run: keep it
on the list for ever.

``archived_at`` is the reversible half of the answer. A timestamp rather than a boolean
because archiving is an event somebody performed on a date, and a nullable column rather
than a new ``RequestStatus`` because being filed away is orthogonal to where a request sits
in the research lifecycle — an archived request keeps the status it earned, so restoring it
does not have to guess what it used to be.

The partial index carries only the live rows, because "what is not archived?" is the
question the list page asks every time it renders.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_requests",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_research_requests_live",
        "research_requests",
        ["user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_research_requests_live", table_name="research_requests")
    op.drop_column("research_requests", "archived_at")
