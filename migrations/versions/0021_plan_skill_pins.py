"""Pin skill versions to plans, and give section_definitions.skill_id its constraint.

Task 36. The pin row is what lets a report name the exact version of every skill that
shaped it: a version reference (immutable rows, task 35), the composed policy exactly as
gate 1 displayed it, and — for a skill that did not apply — the reason, recorded rather
than shrugged.

``section_definitions.skill_id`` was created in migration 0006 as a bare UUID with a
comment promising its foreign key "arrives with the table, in the migration that creates
it". The table arrived in 0020; the promise is kept here.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_skill_pins",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "skill_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("min_sources", sa.Integer(), nullable=True),
        sa.Column("requires_primary", sa.Boolean(), nullable=True),
        sa.Column("max_tier", sa.Integer(), nullable=True),
        sa.Column("allow_forward_looking", sa.Boolean(), nullable=True),
        sa.Column("token_budget", sa.Integer(), nullable=True),
        sa.Column("granted_tools", JSONB(), nullable=True),
        sa.Column("clamps", JSONB(), nullable=True),
        sa.Column("estimated_cost_gbp", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_plan_skill_pins_plan_id", "plan_skill_pins", ["plan_id"])
    op.create_unique_constraint(
        "uq_plan_skill_pins_one_pin_per_skill", "plan_skill_pins", ["plan_id", "skill_id"]
    )
    op.create_check_constraint(
        "status_is_known", "plan_skill_pins", "status IN ('planned', 'skipped_not_applicable')"
    )
    op.create_check_constraint(
        "skips_carry_reasons",
        "plan_skill_pins",
        "status != 'skipped_not_applicable' OR reason != ''",
    )

    op.create_foreign_key(
        "fk_section_definitions_skill_id_skills",
        "section_definitions",
        "skills",
        ["skill_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_section_definitions_skill_id_skills", "section_definitions", type_="foreignkey"
    )
    op.drop_table("plan_skill_pins")
