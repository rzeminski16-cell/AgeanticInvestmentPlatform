"""A work order is the run root, and a research request is a detail of one.

ADR 0072. Every run in this platform hung off a ``research_requests`` row, and so did every
model call: ``Agent._refuse_what_cannot_be_afforded`` walks a step to its job to its request
to find a per-run cap, and refuses when it cannot. The refusal is right — invariant 6 says a
cap that only warns is a cap that does not work — but it made an *equity mandate* the
precondition for spending a penny on anything, so a thesis monitor with no company to
research had no way to be paid for.

``work_orders`` carries the cap, the clock and the subject. ``research_requests`` keeps the
equity mandate and becomes a detail row.

**Three of the four steps ADR 0072 describes are in this revision**, and the fourth is
deliberately not:

1. Create ``work_orders``; add nullable ``work_order_id`` to ``jobs``, ``approvals``,
   ``source_documents`` and ``plan_skill_pins``; add the subject correlation to
   ``audit_events``.
2. Backfill one work order per research request, then set every ``work_order_id`` from its
   own table's existing pointer. The correspondence is 1:1, so each is exact.
3. ``SET NOT NULL`` on the four new columns, and ``DROP NOT NULL`` on the three
   ``request_id`` columns. This is not the nullable cap the ADR rejected: after this step
   the cap hangs off a column that is ``NOT NULL``, and what is nullable is a pointer to a
   mandate that genuinely does not exist for a monitor run.
4. **A later revision** drops the three ``request_id`` columns and the duplicated columns on
   ``research_requests``, once no code reads them. Staging it is what makes this reversible:
   dropping ``work_orders`` discards nothing ``research_requests`` does not still hold.

**The two unique constraints on ``source_documents`` move now rather than at step 4**, and
the reason is a Postgres detail. Both are keyed on ``request_id``, and Postgres treats NULLs
in a unique key as distinct — so the moment step 3 relaxes that column, a run with no
research request would write rows no unique constraint could collide with. Both were added
against a live duplicate. A window in which the first monitor run has that protection quietly
switched off is not a window worth opening.

**``plan_skill_pins.plan_id`` is replaced rather than kept.** Nothing needs a transitional
pointer to a plan: the pin's own foreign key is what moves, and the model drops the column in
the same commit, so leaving it would fail ``compare_metadata`` rather than help anybody.

Revision ID: 0054
Revises: 0053
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Step 1: the new shapes, all nullable ---------------------------------------------

    op.create_table(
        "work_orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool", sa.String(32), server_default="research", nullable=False),
        sa.Column("subject_kind", sa.String(32), server_default="company", nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("point_in_time", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "max_cost_gbp", sa.Numeric(10, 2), server_default=sa.text("2.50"), nullable=False
        ),
        # Reuses the existing `request_status` type rather than minting a second one: a work
        # order's lifecycle is the request lifecycle, and two enums with identical members
        # is two things to keep in step.
        sa.Column(
            "status",
            postgresql.ENUM(name="request_status", create_type=False),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_work_orders"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_work_orders_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("max_cost_gbp > 0", name="ck_work_orders_cost_is_positive"),
    )
    op.create_index("ix_work_orders_user_id", "work_orders", ["user_id"])
    op.create_index("ix_work_orders_subject", "work_orders", ["subject_kind", "subject_id"])
    op.create_index(
        "ix_work_orders_tool_created_at",
        "work_orders",
        ["tool", "created_at"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    for table in ("jobs", "approvals", "source_documents", "plan_skill_pins"):
        op.add_column(
            table, sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=True)
        )

    op.add_column("audit_events", sa.Column("subject_kind", sa.String(32), nullable=True))
    op.add_column(
        "audit_events", sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_index(
        "ix_audit_events_subject_id", "audit_events", ["subject_kind", "subject_id", "id"]
    )

    # -- Step 2: the backfill, one work order per request ----------------------------------

    bind = op.get_bind()

    # `id` is reused rather than minted, which makes every pointer below a copy rather than
    # a lookup, and makes the correspondence checkable by eye afterwards.
    bind.execute(
        sa.text(
            "INSERT INTO work_orders "
            "(id, user_id, tool, subject_kind, subject_id, as_of_date, point_in_time, "
            " max_cost_gbp, status, created_at, archived_at) "
            "SELECT r.id, r.user_id, 'research', 'company', r.company_id, r.as_of_date, "
            "       r.point_in_time, r.max_cost_gbp, r.status, r.created_at, r.archived_at "
            "FROM research_requests AS r"
        )
    )

    for table in ("jobs", "approvals", "source_documents"):
        bind.execute(sa.text(f"UPDATE {table} SET work_order_id = request_id"))  # noqa: S608

    # A pin hangs off a plan, and a plan hangs off a request, which is now a work order of
    # the same id. Two hops rather than one, and exact for the same reason.
    bind.execute(
        sa.text(
            "UPDATE plan_skill_pins AS p SET work_order_id = pl.request_id "
            "FROM research_plans AS pl WHERE p.plan_id = pl.id"
        )
    )

    # -- Step 3: tighten the new, relax the old --------------------------------------------

    for table in ("jobs", "approvals", "source_documents", "plan_skill_pins"):
        op.alter_column(table, "work_order_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_work_order_id_work_orders",
            table,
            "work_orders",
            ["work_order_id"],
            ["id"],
            ondelete="CASCADE",
        )

    for table in ("jobs", "approvals", "source_documents"):
        op.alter_column(table, "request_id", nullable=True)

    op.create_index("ix_jobs_work_order_id", "jobs", ["work_order_id"])
    op.create_index("ix_plan_skill_pins_work_order_id", "plan_skill_pins", ["work_order_id"])

    # The gate index follows its column.
    op.drop_index("ix_approvals_request_id_gate", table_name="approvals")
    op.create_index("ix_approvals_work_order_id_gate", "approvals", ["work_order_id", "gate"])

    # The two constraints that must not spend a moment keyed on a nullable column.
    op.drop_constraint("uq_source_acquisition", "source_documents", type_="unique")
    op.create_unique_constraint(
        "uq_source_acquisition", "source_documents", ["work_order_id", "url", "retrieved_at"]
    )
    op.drop_constraint("uq_source_document_per_artefact", "source_documents", type_="unique")
    op.create_unique_constraint(
        "uq_source_document_per_artefact", "source_documents", ["work_order_id", "artefact_id"]
    )

    op.drop_index("ix_source_documents_request_id_publication_date", table_name="source_documents")
    op.create_index(
        "ix_source_documents_work_order_id_publication_date",
        "source_documents",
        ["work_order_id", "publication_date"],
    )
    op.drop_index("ix_source_documents_request_id_company_id", table_name="source_documents")
    op.create_index(
        "ix_source_documents_work_order_id_company_id",
        "source_documents",
        ["work_order_id", "company_id"],
    )
    op.drop_index("ix_source_documents_quarantined", table_name="source_documents")
    op.create_index(
        "ix_source_documents_quarantined",
        "source_documents",
        ["work_order_id"],
        postgresql_where=sa.text("quarantined"),
    )

    # The pin's own pointer is replaced rather than kept: nothing needs a transitional plan.
    op.drop_constraint("uq_plan_skill_pins_one_pin_per_skill", "plan_skill_pins", type_="unique")
    op.create_unique_constraint(
        "uq_plan_skill_pins_one_pin_per_skill",
        "plan_skill_pins",
        ["work_order_id", "skill_id"],
    )
    op.drop_index("ix_plan_skill_pins_plan_id", table_name="plan_skill_pins")
    op.drop_constraint(
        "fk_plan_skill_pins_plan_id_research_plans", "plan_skill_pins", type_="foreignkey"
    )
    op.drop_column("plan_skill_pins", "plan_id")


def downgrade() -> None:
    # Genuinely lossless, and only because step 4 has not run: every column copied onto a
    # work order is still on the research request it came from.

    op.add_column(
        "plan_skill_pins", sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    bind = op.get_bind()
    # A work order maps back to a request of the same id; a request may hold several plans,
    # so the pin returns to the most recent one. That ambiguity is the cost ADR 0072 records
    # for this move, and it is why the downgrade cannot claim to be a perfect inverse of a
    # re-planned run.
    bind.execute(
        sa.text(
            "UPDATE plan_skill_pins AS p SET plan_id = ( "
            "  SELECT pl.id FROM research_plans AS pl "
            "  WHERE pl.request_id = p.work_order_id "
            "  ORDER BY pl.created_at DESC LIMIT 1)"
        )
    )
    bind.execute(sa.text("DELETE FROM plan_skill_pins WHERE plan_id IS NULL"))
    op.alter_column("plan_skill_pins", "plan_id", nullable=False)
    op.create_foreign_key(
        "fk_plan_skill_pins_plan_id_research_plans",
        "plan_skill_pins",
        "research_plans",
        ["plan_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_plan_skill_pins_plan_id", "plan_skill_pins", ["plan_id"])
    op.drop_constraint("uq_plan_skill_pins_one_pin_per_skill", "plan_skill_pins", type_="unique")
    op.create_unique_constraint(
        "uq_plan_skill_pins_one_pin_per_skill", "plan_skill_pins", ["plan_id", "skill_id"]
    )

    op.drop_index("ix_source_documents_quarantined", table_name="source_documents")
    op.create_index(
        "ix_source_documents_quarantined",
        "source_documents",
        ["request_id"],
        postgresql_where=sa.text("quarantined"),
    )
    op.drop_index("ix_source_documents_work_order_id_company_id", table_name="source_documents")
    op.create_index(
        "ix_source_documents_request_id_company_id",
        "source_documents",
        ["request_id", "company_id"],
    )
    op.drop_index(
        "ix_source_documents_work_order_id_publication_date", table_name="source_documents"
    )
    op.create_index(
        "ix_source_documents_request_id_publication_date",
        "source_documents",
        ["request_id", "publication_date"],
    )
    op.drop_constraint("uq_source_document_per_artefact", "source_documents", type_="unique")
    op.create_unique_constraint(
        "uq_source_document_per_artefact", "source_documents", ["request_id", "artefact_id"]
    )
    op.drop_constraint("uq_source_acquisition", "source_documents", type_="unique")
    op.create_unique_constraint(
        "uq_source_acquisition", "source_documents", ["request_id", "url", "retrieved_at"]
    )

    op.drop_index("ix_approvals_work_order_id_gate", table_name="approvals")
    op.create_index("ix_approvals_request_id_gate", "approvals", ["request_id", "gate"])

    op.drop_index("ix_plan_skill_pins_work_order_id", table_name="plan_skill_pins")
    op.drop_index("ix_jobs_work_order_id", table_name="jobs")

    # A row whose request_id was never set is a row created after this migration ran, and
    # there is nowhere to put it in the old shape. Removing it is the honest inverse of
    # having admitted it.
    for table in ("jobs", "approvals", "source_documents"):
        bind.execute(sa.text(f"DELETE FROM {table} WHERE request_id IS NULL"))  # noqa: S608
        op.alter_column(table, "request_id", nullable=False)

    for table in ("jobs", "approvals", "source_documents", "plan_skill_pins"):
        op.drop_constraint(f"fk_{table}_work_order_id_work_orders", table, type_="foreignkey")
        op.drop_column(table, "work_order_id")

    op.drop_index("ix_audit_events_subject_id", table_name="audit_events")
    op.drop_column("audit_events", "subject_id")
    op.drop_column("audit_events", "subject_kind")

    op.drop_index("ix_work_orders_tool_created_at", table_name="work_orders")
    op.drop_index("ix_work_orders_subject", table_name="work_orders")
    op.drop_index("ix_work_orders_user_id", table_name="work_orders")
    op.drop_table("work_orders")
