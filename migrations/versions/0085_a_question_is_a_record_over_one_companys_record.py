"""A question is a record over one company's record (F6, ADR 0130).

Data model Gap 5's ``questions`` table, with three corrections the record makes:
``company_id`` is not null, because a question is answered from a company's record;
``job_id`` is the question's own run root, under which its calculations and cost rows are
written; ``content`` is the structured answer the page and the drawer are composed from,
beside the prose the specification keeps in ``answer``.

The downgrade drops the table. A question's cost rows outlive it under their job, as every
cost row does.

Revision ID: 0085
Revises: 0084
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_id", sa.Uuid(), sa.ForeignKey("reports.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
        ),
        # The statement clock: the page lists questions in the order asked, and two asked
        # in one transaction must not share an instant.
        sa.Column(
            "asked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("tier_rationale", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_cost_gbp", sa.Numeric(12, 6), nullable=True),
        sa.Column("actual_cost_gbp", sa.Numeric(12, 6), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("documents_added", JSONB, nullable=True),
        sa.CheckConstraint("tier IN (1, 2, 3)", name="question_tier_is_one_of_three"),
        sa.CheckConstraint("char_length(btrim(question)) > 0", name="question_is_not_blank"),
        sa.CheckConstraint(
            "char_length(btrim(tier_rationale)) > 0", name="question_tier_has_a_rationale"
        ),
        sa.CheckConstraint(
            "estimated_cost_gbp IS NULL OR estimated_cost_gbp >= 0",
            name="question_estimate_is_not_negative",
        ),
        sa.CheckConstraint(
            "actual_cost_gbp IS NULL OR actual_cost_gbp >= 0",
            name="question_cost_is_not_negative",
        ),
        sa.CheckConstraint(
            "(answer IS NULL) = (answered_at IS NULL)", name="question_answer_is_dated"
        ),
    )
    op.create_index("ix_questions_user_id_asked_at", "questions", ["user_id", "asked_at"])
    op.create_index("ix_questions_company_id_asked_at", "questions", ["company_id", "asked_at"])


def downgrade() -> None:
    op.drop_table("questions")
