"""Make an assumption arguable: keep every proposal, diff every scenario, source every cell.

Five tables, and each exists to close a way a valuation stops being checkable.

**``assumption_proposals``** — an amendment is a new row, never an edit. Before this, an
operator overriding a model's proposed discount rate destroyed the proposal, and the report
rested on a number with no record that anything was ever different. "Who chose this, and what
did they choose it over?" is the most useful question about a valuation and it had no answer.

**``scenarios`` and ``scenario_overrides``** — a scenario stores only what it overrides. A
bear case that copied the base case would go on using the base case's old tax rate after
somebody corrected it, silently, and every comparison between the two would then be measuring
the correction as well as the scenario.

**``sensitivities`` and ``sensitivity_cells``** — ``calculation_id`` is ``NOT NULL``. A
nine-by-nine grid looks like eighty-one pieces of analysis whether or not it is one, and the
only thing that distinguishes a computed grid from an interpolated or invented one is whether
each cell can name the calculation behind it. ``ON DELETE RESTRICT`` on that key, not
CASCADE: deleting a calculation that a published grid depends on should fail loudly rather
than quietly emptying the grid.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assumption_proposals",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("assumption_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Numeric(38, 12), nullable=False),
        sa.Column("unit", sa.String(32), server_default="pure", nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.Column("by_human", sa.Boolean(), nullable=False),
        sa.Column("supersedes_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["assumption_id"], ["assumptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["assumption_proposals.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "char_length(btrim(justification)) > 0", name="proposal_justification_is_not_blank"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="proposal_confidence_is_a_probability",
        ),
        sa.CheckConstraint("char_length(btrim(proposed_by)) > 0", name="proposer_is_not_blank"),
        sa.CheckConstraint("id <> supersedes_id", name="proposal_does_not_supersede_itself"),
        sa.CheckConstraint("sequence >= 1", name="proposal_sequence_starts_at_one"),
        sa.UniqueConstraint("assumption_id", "sequence", name="uq_assumption_proposals_sequence"),
    )
    op.create_index(
        "ix_assumption_proposals_assumption_id", "assumption_proposals", ["assumption_id"]
    )
    op.create_index("ix_assumption_proposals_created_at", "assumption_proposals", ["created_at"])

    op.create_table(
        "scenarios",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["request_id"], ["research_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("request_id", "key", name="uq_scenarios_key_per_request"),
        sa.CheckConstraint("char_length(btrim(key)) > 0", name="scenario_key_is_not_blank"),
        sa.CheckConstraint("char_length(btrim(label)) > 0", name="scenario_label_is_not_blank"),
        sa.CheckConstraint(
            "char_length(btrim(description)) > 0", name="scenario_description_is_not_blank"
        ),
    )
    op.create_index("ix_scenarios_request_id", "scenarios", ["request_id"])

    op.create_table(
        "scenario_overrides",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("scenario_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assumption_name", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(38, 12), nullable=False),
        sa.Column("unit", sa.String(32), server_default="pure", nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "scenario_id", "assumption_name", name="uq_scenario_overrides_name_per_scenario"
        ),
        sa.CheckConstraint(
            "char_length(btrim(assumption_name)) > 0", name="override_name_is_not_blank"
        ),
        sa.CheckConstraint(
            "char_length(btrim(justification)) > 0", name="override_justification_is_not_blank"
        ),
    )
    op.create_index("ix_scenario_overrides_scenario_id", "scenario_overrides", ["scenario_id"])

    op.create_table(
        "sensitivities",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scenario_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("x_assumption", sa.Text(), nullable=False),
        sa.Column("y_assumption", sa.Text(), nullable=False),
        sa.Column("output_name", sa.Text(), nullable=False),
        sa.Column("output_unit", sa.String(32), server_default="pure", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["request_id"], ["research_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "char_length(btrim(x_assumption)) > 0 AND char_length(btrim(y_assumption)) > 0",
            name="sensitivity_axes_are_named",
        ),
        sa.CheckConstraint("x_assumption <> y_assumption", name="sensitivity_axes_differ"),
        sa.CheckConstraint(
            "char_length(btrim(output_name)) > 0", name="sensitivity_output_is_named"
        ),
    )
    op.create_index("ix_sensitivities_request_id", "sensitivities", ["request_id"])
    op.create_index("ix_sensitivities_job_id", "sensitivities", ["job_id"])

    op.create_table(
        "sensitivity_cells",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sensitivity_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("x_value", sa.Numeric(38, 12), nullable=False),
        sa.Column("y_value", sa.Numeric(38, 12), nullable=False),
        sa.Column("output_value", sa.Numeric(38, 12), nullable=False),
        sa.Column("calculation_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["sensitivity_id"], ["sensitivities.id"], ondelete="CASCADE"),
        # RESTRICT, not CASCADE. Deleting a calculation a published grid rests on should fail
        # loudly rather than quietly emptying the grid.
        sa.ForeignKeyConstraint(["calculation_id"], ["calculations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "sensitivity_id", "x_value", "y_value", name="uq_sensitivity_cells_point"
        ),
    )
    op.create_index("ix_sensitivity_cells_sensitivity_id", "sensitivity_cells", ["sensitivity_id"])
    op.create_index("ix_sensitivity_cells_calculation_id", "sensitivity_cells", ["calculation_id"])


def downgrade() -> None:
    op.drop_table("sensitivity_cells")
    op.drop_table("sensitivities")
    op.drop_table("scenario_overrides")
    op.drop_table("scenarios")
    op.drop_table("assumption_proposals")
