"""Step four of ADR 0072: the transitional columns go, and the duplication with them.

Migration 0054 made `work_orders` the run root and left three `request_id` columns and six
duplicated ones behind on purpose, so that dropping `work_orders` would still discard
nothing — a downgrade that was real rather than declared. That transition is over: the
mandate is read through the work order's own key everywhere, and nothing reads the copies.

**What goes.** `jobs.request_id`, `approvals.request_id` and `source_documents.request_id`,
with their foreign keys and `ix_jobs_request_id`. And from `research_requests`: `user_id`,
`as_of_date`, `point_in_time`, `max_cost_gbp`, `status` and `archived_at`, each of which is
a property of a *run* rather than of an equity report and each of which the work order has
carried since 0054.

**`research_requests.id` becomes a foreign key to `work_orders.id`.** It has held that
value since 0054's backfill wrote it; the constraint is what makes the detail row's shared
key a fact the database enforces rather than a convention two modules remember.

**The downgrade is honest about what it cannot restore.** It puts the columns back and
backfills every one of them from the work order, which is exact — the values came from
there and were kept in step by `_mirror_to_work_order` throughout. What it cannot restore
is a research request for a run that never had one, so it deletes work orders with no
detail row before re-imposing `NOT NULL`. That is a real deletion of real rows, and it is
stated here rather than discovered: downgrading past this point is downgrading past the
existence of runs that are not about a company.

Revision ID: 0064
Revises: 0063
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

# The three that pointed a run at a mandate, and the constraint each carried.
_POINTERS = (
    ("jobs", "fk_jobs_request_id_research_requests"),
    ("approvals", "fk_approvals_request_id_research_requests"),
    ("source_documents", "fk_source_documents_request_id_research_requests"),
)

# What a run is, as opposed to what an equity report is about.
_RUN_ROOT_COLUMNS = (
    "user_id",
    "as_of_date",
    "point_in_time",
    "max_cost_gbp",
    "status",
    "archived_at",
)


def upgrade() -> None:
    op.drop_index("ix_jobs_request_id", table_name="jobs")
    for table, constraint in _POINTERS:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.drop_column(table, "request_id")

    # The list page asked "whose, and not archived" of the mandate; it asks the run root
    # now, so the indexes that answered it move with the columns.
    op.drop_index("ix_research_requests_live", table_name="research_requests")
    op.drop_index("ix_research_requests_user_id_created_at", table_name="research_requests")
    op.drop_index("ix_research_requests_ticker_as_of_date", table_name="research_requests")
    op.create_index("ix_research_requests_ticker", "research_requests", ["ticker"])

    op.drop_constraint("max_cost_positive", "research_requests", type_="check")
    op.drop_constraint(
        "fk_research_requests_user_id_users", "research_requests", type_="foreignkey"
    )
    for column in _RUN_ROOT_COLUMNS:
        op.drop_column("research_requests", column)

    # The shared key, made a fact the database keeps rather than one two modules remember.
    op.create_foreign_key(
        "fk_research_requests_id_work_orders",
        "research_requests",
        "work_orders",
        ["id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_research_requests_id_work_orders", "research_requests", type_="foreignkey"
    )

    op.add_column(
        "research_requests",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("research_requests", sa.Column("as_of_date", sa.Date(), nullable=True))
    op.add_column("research_requests", sa.Column("point_in_time", sa.Boolean(), nullable=True))
    op.add_column("research_requests", sa.Column("max_cost_gbp", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "research_requests",
        sa.Column(
            "status",
            postgresql.ENUM(name="request_status", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "research_requests",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Exact, because the values came from here and were kept in step throughout.
    op.execute(
        sa.text(
            "UPDATE research_requests AS r SET "
            "  user_id = w.user_id, as_of_date = w.as_of_date, "
            "  point_in_time = w.point_in_time, max_cost_gbp = w.max_cost_gbp, "
            "  status = w.status, archived_at = w.archived_at "
            "FROM work_orders AS w WHERE w.id = r.id"
        )
    )
    for column in ("user_id", "as_of_date", "point_in_time", "max_cost_gbp", "status"):
        op.alter_column("research_requests", column, nullable=False)

    op.create_foreign_key(
        "fk_research_requests_user_id_users",
        "research_requests",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint("max_cost_positive", "research_requests", "max_cost_gbp > 0")
    op.drop_index("ix_research_requests_ticker", table_name="research_requests")
    op.create_index(
        "ix_research_requests_user_id_created_at",
        "research_requests",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_research_requests_ticker_as_of_date", "research_requests", ["ticker", "as_of_date"]
    )
    op.create_index(
        "ix_research_requests_live",
        "research_requests",
        ["user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    # A run that never had a mandate cannot be given one, and the columns below are about
    # to become NOT NULL. Deleted rather than invented: a placeholder request would put a
    # ticker nobody typed into the record this platform exists to keep honest.
    op.execute(
        sa.text("DELETE FROM work_orders WHERE id NOT IN (SELECT id FROM research_requests)")
    )

    for table, constraint in _POINTERS:
        op.add_column(table, sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET request_id = work_order_id"))  # noqa: S608
        op.create_foreign_key(
            constraint, table, "research_requests", ["request_id"], ["id"], ondelete="CASCADE"
        )
    op.create_index("ix_jobs_request_id", "jobs", ["request_id"])
