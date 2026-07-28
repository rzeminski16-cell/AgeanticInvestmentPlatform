"""Add calculations and assumptions.

The two tables that make "every number traces to a formula" a property of the database
rather than a promise in a document.

``calculations`` holds one row per computation: the readable formula, every input with its
unit and source, the code version that ran, and the result. ``assumptions`` holds the
numbers that were chosen rather than observed, each with a mandatory justification.

**The GIN index on ``inputs``** is what makes "what depends on this fact?" answerable. That
question gets asked the moment a fact turns out to be wrong, and without the index it is a
full scan of every calculation ever performed.

**The justification check is not decoration.** An assumption with a blank reason is a guess
with a label, and the constraint is what stops the table filling with them the first time
somebody is in a hurry.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calculations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("function_ref", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("output_value", sa.Numeric(precision=38, scale=12), nullable=False),
        sa.Column("output_unit", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(formula)) > 0", name="ck_calculations_formula_is_not_blank"
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name="ck_calculations_name_is_not_blank"
        ),
        sa.CheckConstraint(
            "char_length(btrim(code_version)) > 0",
            name="ck_calculations_code_version_is_recorded",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(inputs) = 'array'", name="ck_calculations_inputs_are_an_array"
        ),
        sa.CheckConstraint(
            "char_length(output_unit) > 0", name="ck_calculations_output_unit_is_present"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_calculations_job_id_jobs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calculations"),
    )
    op.create_index("ix_calculations_job_id_name", "calculations", ["job_id", "name"])
    op.create_index("ix_calculations_created_at", "calculations", ["created_at"])
    op.create_index("ix_calculations_inputs", "calculations", ["inputs"], postgresql_using="gin")

    op.create_table(
        "assumptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=38, scale=12), nullable=False),
        sa.Column("unit", sa.String(length=32), server_default="pure", nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("proposed_by", sa.Text(), server_default="system", nullable=False),
        sa.Column("approved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(justification)) > 0",
            name="ck_assumptions_justification_is_not_blank",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_assumptions_confidence_is_a_probability",
        ),
        sa.CheckConstraint("char_length(name) > 0", name="ck_assumptions_name_is_not_blank"),
        sa.CheckConstraint(
            "(approved AND approved_at IS NOT NULL) OR (NOT approved AND approved_at IS NULL)",
            name="ck_assumptions_approval_has_a_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_assumptions_job_id_jobs", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["research_requests.id"],
            name="fk_assumptions_request_id_research_requests",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assumptions"),
        sa.UniqueConstraint("request_id", "name", name="uq_assumptions_name_per_request"),
    )
    op.create_index("ix_assumptions_request_id", "assumptions", ["request_id"])
    op.create_index("ix_assumptions_job_id", "assumptions", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_assumptions_job_id", table_name="assumptions")
    op.drop_index("ix_assumptions_request_id", table_name="assumptions")
    op.drop_table("assumptions")

    op.drop_index("ix_calculations_inputs", table_name="calculations")
    op.drop_index("ix_calculations_created_at", table_name="calculations")
    op.drop_index("ix_calculations_job_id_name", table_name="calculations")
    op.drop_table("calculations")
