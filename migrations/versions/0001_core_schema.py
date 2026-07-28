"""Core schema: identity, request, plan, approval, execution and audit.

Seven tables, each with one job:

* ``users`` — who owns a request. One row for the whole MVP, but present from the start
  because backfilling an owner column onto populated tables is not something that can be
  done correctly later.
* ``research_requests`` — what was asked for: subject, as-of date, mandate, budget. The
  root of everything downstream and the primary input to reproducing a run.
* ``research_plans`` — what the system proposed to do, with its cost and runtime
  estimates. Written before the approval gate, never mutated after it.
* ``approvals`` — a human decision at a gate, with a hash of exactly what was displayed
  when it was taken.
* ``jobs`` — one execution of an approved plan, stamped with the git SHA that ran it.
* ``job_steps`` — the unit of resumability and audit: one row per (job, step, attempt).
* ``audit_events`` — append-only, hash-chained record of everything that happened.

Two extensions are required. ``citext`` gives case-insensitive email addresses, so a
single person cannot become two accounts. ``gen_random_uuid()`` is built in from
PostgreSQL 13 onward and needs no extension on 16.

TODO (deployment phase): revoke UPDATE and DELETE on ``audit_events`` from the application
role, so append-only becomes a database guarantee rather than a convention this codebase
observes. It needs a separate migration role to exist first, which is why it is not done
here. Until then the hash chain makes tampering detectable, not impossible.

Revision ID: 0001
Revises:
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


# Native PostgreSQL enums. Created once, explicitly, then referenced with
# create_type=False everywhere they are used -- `job_status` appears on two tables, and
# letting SQLAlchemy emit CREATE TYPE per column would fail on the second.
USER_ROLE = postgresql.ENUM("owner", "analyst", "viewer", name="user_role")
ANALYSIS_MODE = postgresql.ENUM("quick", "standard", "full", name="analysis_mode")
REQUEST_STATUS = postgresql.ENUM(
    "DRAFT",
    "PLANNED",
    "APPROVED",
    "RUNNING",
    "AWAITING_REVIEW",
    "COMPLETED",
    "REJECTED",
    "FAILED",
    "CANCELLED",
    name="request_status",
)
GATE_KIND = postgresql.ENUM(
    "PLAN",
    "UK_FINANCIALS",
    "PEER_SET",
    "SECTOR_SPECIALIST",
    "BUDGET",
    "FINAL",
    name="gate_kind",
)
DECISION = postgresql.ENUM("APPROVED", "REJECTED", "AMENDED", name="decision")
JOB_STATUS = postgresql.ENUM(
    "QUEUED",
    "RUNNING",
    "PAUSED",
    "AWAITING_APPROVAL",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "BUDGET_EXCEEDED",
    name="job_status",
)

ALL_ENUMS = (USER_ROLE, ANALYSIS_MODE, REQUEST_STATUS, GATE_KIND, DECISION, JOB_STATUS)


def _enum(source: postgresql.ENUM) -> postgresql.ENUM:
    """Reference an already-created enum type without re-emitting CREATE TYPE."""
    return postgresql.ENUM(name=source.name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    for enum in ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    # -- users -------------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", postgresql.CITEXT(320), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", _enum(USER_ROLE), server_default="owner", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index("ix_users_created_at", "users", ["created_at"])

    # -- research_requests -------------------------------------------------------------
    op.create_table(
        "research_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("ticker", sa.String(12), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("reporting_currency", sa.String(3), nullable=True),
        sa.Column("investment_horizon_months", sa.Integer(), nullable=False),
        sa.Column("horizon_label", sa.Text(), nullable=True),
        sa.Column("analysis_mode", _enum(ANALYSIS_MODE), server_default="full", nullable=False),
        sa.Column("point_in_time", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "portfolio_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("risk_tolerance", sa.Text(), nullable=True),
        sa.Column("liquidity_constraint_gbp", sa.Numeric(18, 2), nullable=True),
        sa.Column("esg_sensitivity", sa.Text(), nullable=True),
        sa.Column("focus_questions", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("excluded_sources", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "max_cost_gbp", sa.Numeric(10, 2), server_default=sa.text("2.50"), nullable=False
        ),
        sa.Column("status", _enum(REQUEST_STATUS), server_default="DRAFT", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_requests")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_research_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "investment_horizon_months BETWEEN 1 AND 240",
            name=op.f("ck_research_requests_horizon_months_in_range"),
        ),
        sa.CheckConstraint("max_cost_gbp > 0", name=op.f("ck_research_requests_max_cost_positive")),
        sa.CheckConstraint(
            "char_length(base_currency) = 3",
            name=op.f("ck_research_requests_base_currency_iso4217"),
        ),
        sa.CheckConstraint(
            "reporting_currency IS NULL OR char_length(reporting_currency) = 3",
            name=op.f("ck_research_requests_reporting_currency_iso4217"),
        ),
        sa.CheckConstraint(
            "portfolio_context = '{}'::jsonb OR ((portfolio_context->>'current_weight') IS NULL"
            " OR ((portfolio_context->>'current_weight')::numeric >= 0"
            " AND (portfolio_context->>'current_weight')::numeric <= 1))",
            name=op.f("ck_research_requests_current_weight_is_a_fraction"),
        ),
        sa.CheckConstraint(
            "portfolio_context = '{}'::jsonb OR ((portfolio_context->>'maximum_weight') IS NULL"
            " OR ((portfolio_context->>'maximum_weight')::numeric >= 0"
            " AND (portfolio_context->>'maximum_weight')::numeric <= 1))",
            name=op.f("ck_research_requests_maximum_weight_is_a_fraction"),
        ),
    )
    op.execute(
        "CREATE INDEX ix_research_requests_user_id_created_at"
        " ON research_requests (user_id, created_at DESC)"
    )
    op.create_index(
        "ix_research_requests_ticker_as_of_date", "research_requests", ["ticker", "as_of_date"]
    )

    # -- research_plans ----------------------------------------------------------------
    op.create_table(
        "research_plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version", sa.Text(), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("planned_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estimated_cost_gbp", sa.Numeric(10, 4), nullable=False),
        sa.Column("estimated_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "known_risks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_plans")),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["research_requests.id"],
            name=op.f("fk_research_plans_request_id_research_requests"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "estimated_cost_gbp >= 0", name=op.f("ck_research_plans_estimated_cost_non_negative")
        ),
        sa.CheckConstraint(
            "estimated_runtime_seconds >= 0",
            name=op.f("ck_research_plans_estimated_runtime_non_negative"),
        ),
    )
    op.execute(
        "CREATE INDEX ix_research_plans_request_id_created_at"
        " ON research_plans (request_id, created_at DESC)"
    )

    # -- approvals ---------------------------------------------------------------------
    op.create_table(
        "approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gate", _enum(GATE_KIND), nullable=False),
        sa.Column("decision", _enum(DECISION), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["research_requests.id"],
            name=op.f("fk_approvals_request_id_research_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_approvals_actor_user_id_users")
        ),
    )
    op.create_index("ix_approvals_request_id_gate", "approvals", ["request_id", "gate"])
    op.create_index("ix_approvals_job_id", "approvals", ["job_id"])

    # -- jobs --------------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_version", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("status", _enum(JOB_STATUS), server_default="QUEUED", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_cost_gbp", sa.Numeric(10, 4), server_default=sa.text("0"), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["research_requests.id"],
            name=op.f("fk_jobs_request_id_research_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["research_plans.id"],
            name=op.f("fk_jobs_plan_id_research_plans"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("total_cost_gbp >= 0", name=op.f("ck_jobs_total_cost_non_negative")),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name=op.f("ck_jobs_finished_implies_started"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_jobs_finished_after_started"),
        ),
    )
    op.create_index("ix_jobs_request_id", "jobs", ["request_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    # -- job_steps ---------------------------------------------------------------------
    op.create_table(
        "job_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", _enum(JOB_STATUS), server_default="QUEUED", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cost_gbp", sa.Numeric(10, 4), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_steps")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_steps_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint("job_id", "step_key", "attempt", name="uq_job_steps_job_step_attempt"),
        sa.CheckConstraint("attempt >= 0", name=op.f("ck_job_steps_attempt_non_negative")),
        sa.CheckConstraint("sequence >= 0", name=op.f("ck_job_steps_sequence_non_negative")),
        sa.CheckConstraint("cost_gbp >= 0", name=op.f("ck_job_steps_cost_non_negative")),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name=op.f("ck_job_steps_finished_implies_started"),
        ),
    )
    op.create_index("ix_job_steps_job_id_sequence", "job_steps", ["job_id", "sequence"])
    op.create_index("ix_job_steps_idempotency_key", "job_steps", ["idempotency_key"])

    # -- audit_events ------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=True),
        sa.Column("this_hash", sa.String(64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_job_id_id", "audit_events", ["job_id", "id"])
    op.create_index("ix_audit_events_request_id_id", "audit_events", ["request_id", "id"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("job_steps")
    op.drop_table("jobs")
    op.drop_table("approvals")
    op.drop_table("research_plans")
    op.drop_table("research_requests")
    op.drop_table("users")

    bind = op.get_bind()
    for enum in reversed(ALL_ENUMS):
        enum.drop(bind, checkfirst=True)

    # citext is deliberately left in place: other schemas may rely on it, and dropping an
    # extension is not a safe inverse of CREATE EXTENSION IF NOT EXISTS.
