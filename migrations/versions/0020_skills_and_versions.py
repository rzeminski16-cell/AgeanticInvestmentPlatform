"""Skills as data: an identity table, and an immutable version per save.

Task 35, `docs/PLAN.md` §2.12. The frontmatter fields the platform acts on are typed
columns; the nested structures are JSONB that only ever holds what the schema validated,
because the service refuses to write anything the validator refused. History mirrors the
assumptions pattern: editing creates a ``skill_versions`` row, never rewrites one, so the
version a run pins (task 36) is the version it ran with, forever.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint("uq_skills_key", "skills", ["key"])
    op.create_check_constraint(
        "kind_is_known",
        "skills",
        "kind IN ('custom_section', 'methodology', 'preference', 'house_view')",
    )

    op.create_table(
        "skill_versions",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("position", sa.String(80), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("applicability", JSONB(), nullable=False),
        sa.Column("min_sources", sa.Integer(), nullable=True),
        sa.Column("requires_primary", sa.Boolean(), nullable=True),
        sa.Column("max_tier", sa.Integer(), nullable=True),
        sa.Column("allow_forward_looking", sa.Boolean(), nullable=True),
        sa.Column("output_contract", JSONB(), nullable=True),
        sa.Column("token_budget", sa.Integer(), nullable=True),
        sa.Column("allowed_tools", JSONB(), nullable=False),
        sa.Column("charts", JSONB(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_skill_versions_one_row_per_version", "skill_versions", ["skill_id", "version"]
    )
    op.create_unique_constraint(
        "uq_skill_versions_content_is_versioned_once",
        "skill_versions",
        ["skill_id", "content_hash"],
    )
    op.create_check_constraint("versions_start_at_one", "skill_versions", "version >= 1")
    op.create_check_constraint(
        "budget_is_positive", "skill_versions", "token_budget IS NULL OR token_budget >= 1"
    )
    op.create_index("ix_skill_versions_skill_id", "skill_versions", ["skill_id"])


def downgrade() -> None:
    op.drop_table("skill_versions")
    op.drop_table("skills")
