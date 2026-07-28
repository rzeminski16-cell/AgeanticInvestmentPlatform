"""Add the agent, cost, prompt, section and report tables.

Six tables that between them close Phase 1: what a model was asked, what it cost, what
instruction produced it, what sections a report is made of, and the report itself.

**The two seeded section rows are the point of the section tables.** A report's content
model is data, not code. There is no section enum anywhere in the codebase and a test
scans for one; the built-ins are rows with ``origin='builtin'``, and a user-authored
section in Phase 4 will be a row with ``origin='skill'``. Everything that iterates
sections queries ``report_sections`` ordered by ``position``.

``position`` is NUMERIC and sparse — 100 and 200 — so a custom section slots in at 150
without renumbering anything and without two concurrent inserts colliding.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_SECTION_STATUSES = ("pending", "generated", "failed", "skipped_not_applicable")


# The evidence floor every built-in section inherits. A skill-authored section may tighten
# these and may never loosen them -- that rule is enforced in code when the definition is
# composed, because a JSONB column cannot express "additive only".
_EVIDENCE_POLICY = {
    "min_sources": 1,
    "max_tier": "T4_LICENSED_MARKET",
    "requires_primary": True,
    "allow_forward_looking": False,
}


# JSON Schema, and also what the generic renderer walks. Every field carries a `title`
# because that is what becomes the sub-heading in the rendered Markdown -- which is how a
# section gets acceptable output with no template of its own.
_EXECUTIVE_SUMMARY_CONTRACT = {
    "type": "object",
    "title": "Executive Summary",
    "required": ["thesis", "key_points"],
    "properties": {
        "thesis": {
            "type": "string",
            "title": "Thesis",
            "description": "The central view, in two or three sentences.",
        },
        "key_points": {
            "type": "array",
            "title": "Key Points",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "key_risks": {
            "type": "array",
            "title": "Key Risks",
            "items": {"type": "string"},
        },
    },
}

_HISTORICAL_ANALYSIS_CONTRACT = {
    "type": "object",
    "title": "Historical Financial Analysis",
    "required": ["commentary", "figures"],
    "properties": {
        "commentary": {
            "type": "string",
            "title": "Commentary",
            "description": "What the reported history shows.",
        },
        "figures": {
            "type": "array",
            "title": "Figures",
            "description": "Each figure must resolve to a calculation or a financial fact.",
            "items": {
                "type": "object",
                "required": ["label", "value", "unit"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                    "source_document_id": {"type": "string"},
                },
            },
        },
    },
}


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "prompts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(content_hash) = 64", name="ck_prompts_content_hash_is_sha256"
        ),
        sa.CheckConstraint(
            "char_length(btrim(template)) > 0", name="ck_prompts_template_is_not_blank"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prompts"),
        sa.UniqueConstraint("key", "version", name="uq_prompts_key_version"),
    )
    op.create_index("ix_prompts_key", "prompts", ["key"])

    op.create_table(
        "agent_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_role", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("effort", sa.Text(), nullable=True),
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "skill_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("request_payload_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_payload_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(btrim(agent_role)) > 0", name="ck_agent_runs_agent_role_is_recorded"
        ),
        sa.CheckConstraint("char_length(btrim(model)) > 0", name="ck_agent_runs_model_is_recorded"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_agent_runs_input_tokens_are_not_negative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_agent_runs_output_tokens_are_not_negative",
        ),
        sa.ForeignKeyConstraint(
            ["job_step_id"],
            ["job_steps.id"],
            name="fk_agent_runs_job_step_id_job_steps",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["prompts.id"],
            name="fk_agent_runs_prompt_id_prompts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["request_payload_ref"],
            ["artefacts.id"],
            name="fk_agent_runs_request_payload_ref_artefacts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["response_payload_ref"],
            ["artefacts.id"],
            name="fk_agent_runs_response_payload_ref_artefacts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
    )
    op.create_index("ix_agent_runs_job_step_id", "agent_runs", ["job_step_id"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    op.create_table(
        "costs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("units", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_type", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("amount_gbp", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("fx_rate", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("units >= 0", name="ck_costs_units_are_not_negative"),
        sa.CheckConstraint("amount_usd >= 0", name="ck_costs_usd_is_not_negative"),
        sa.CheckConstraint("amount_gbp >= 0", name="ck_costs_gbp_is_not_negative"),
        sa.CheckConstraint("fx_rate > 0", name="ck_costs_fx_rate_is_positive"),
        sa.CheckConstraint(
            "char_length(btrim(category)) > 0", name="ck_costs_category_is_recorded"
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_costs_agent_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_costs_job_id_jobs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_step_id"],
            ["job_steps.id"],
            name="fk_costs_job_step_id_job_steps",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_costs"),
    )
    op.create_index("ix_costs_job_id_occurred_at", "costs", ["job_id", "occurred_at"])
    op.create_index("ix_costs_agent_run_id", "costs", ["agent_run_id"])
    op.create_index("ix_costs_occurred_at", "costs", ["occurred_at"])

    op.create_table(
        "section_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("origin", sa.Text(), server_default="builtin", nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("output_contract", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "evidence_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("token_budget", sa.Integer(), server_default=sa.text("4000"), nullable=False),
        sa.Column(
            "allowed_tools",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "applicability",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "origin IN ('builtin', 'skill')", name="ck_section_definitions_origin_is_known"
        ),
        sa.CheckConstraint(
            "(origin = 'skill' AND skill_id IS NOT NULL)"
            " OR (origin = 'builtin' AND skill_id IS NULL)",
            name="ck_section_definitions_skill_sections_have_a_skill",
        ),
        sa.CheckConstraint(
            "char_length(btrim(key)) > 0", name="ck_section_definitions_key_is_not_blank"
        ),
        sa.CheckConstraint(
            "token_budget > 0", name="ck_section_definitions_token_budget_is_positive"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(output_contract) = 'object'",
            name="ck_section_definitions_output_contract_is_an_object",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_section_definitions"),
        sa.UniqueConstraint("key", "version", name="uq_section_definitions_key_version"),
    )
    op.create_index("ix_section_definitions_position", "section_definitions", ["position"])

    section_status = postgresql.ENUM(*_SECTION_STATUSES, name="section_status")
    section_status.create(bind, checkfirst=True)

    op.create_table(
        "report_sections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*_SECTION_STATUSES, name="section_status", create_type=False),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("low_confidence_reason", sa.Text(), nullable=True),
        sa.Column("token_cost", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(status <> 'generated') OR (content IS NOT NULL)",
            name="ck_report_sections_generated_sections_have_content",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_report_sections_confidence_is_a_probability",
        ),
        sa.CheckConstraint("token_cost >= 0", name="ck_report_sections_token_cost_is_not_negative"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_report_sections_job_id_jobs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["section_definition_id"],
            ["section_definitions.id"],
            name="fk_report_sections_section_definition_id_section_definitions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_sections"),
        sa.UniqueConstraint("job_id", "section_key", name="uq_report_sections_key_per_job"),
    )
    op.create_index("ix_report_sections_job_id_position", "report_sections", ["job_id", "position"])

    op.create_table(
        "reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("rating", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("valuation_low", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("valuation_base", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("valuation_high", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("valuation_currency", sa.String(length=3), nullable=True),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("markdown_artefact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pdf_artefact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("immutable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(content_hash) = 64", name="ck_reports_content_hash_is_sha256"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_reports_confidence_is_a_probability",
        ),
        sa.CheckConstraint(
            "(NOT immutable) OR (approved_at IS NOT NULL)",
            name="ck_reports_immutable_reports_were_approved",
        ),
        sa.CheckConstraint(
            "valuation_currency IS NULL OR char_length(valuation_currency) = 3",
            name="ck_reports_valuation_currency_iso4217",
        ),
        sa.CheckConstraint(
            "valuation_low IS NULL OR valuation_high IS NULL OR valuation_low <= valuation_high",
            name="ck_reports_valuation_range_runs_forwards",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name="fk_reports_approved_by_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_reports_company_id_companies",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_reports_job_id_jobs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["markdown_artefact_id"],
            ["artefacts.id"],
            name="fk_reports_markdown_artefact_id_artefacts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pdf_artefact_id"],
            ["artefacts.id"],
            name="fk_reports_pdf_artefact_id_artefacts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["research_requests.id"],
            name="fk_reports_request_id_research_requests",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reports"),
        sa.UniqueConstraint("job_id", name="uq_reports_job_id"),
    )
    op.create_index("ix_reports_request_id_as_of_date", "reports", ["request_id", "as_of_date"])

    _seed_builtin_sections()


def _seed_builtin_sections() -> None:
    """Insert the two built-in sections.

    Rows, not code. This is the entire mechanism: the workflow, the renderer and the
    Markdown exporter iterate whatever is in these tables, so adding a third section --
    built-in or user-authored -- is an INSERT and nothing else. There is a test that
    inserts a third and asserts it appears in the rendered report at the right position
    with the right footnote numbering, without a code change.
    """
    # The dicts are passed as objects, not as json.dumps output. A JSONB column
    # serialises what it is given; handing it a pre-serialised string stores a JSON
    # *string* rather than an object, which the output_contract check constraint
    # correctly refuses.
    op.bulk_insert(
        sa.table(
            "section_definitions",
            sa.column("key", sa.Text),
            sa.column("version", sa.Integer),
            sa.column("origin", sa.Text),
            sa.column("title", sa.Text),
            sa.column("position", sa.Numeric),
            sa.column("required", sa.Boolean),
            sa.column("output_contract", postgresql.JSONB),
            sa.column("evidence_policy", postgresql.JSONB),
            sa.column("token_budget", sa.Integer),
            sa.column("allowed_tools", postgresql.ARRAY(sa.Text)),
            sa.column("applicability", postgresql.JSONB),
        ),
        [
            {
                "key": "executive_summary",
                "version": 1,
                "origin": "builtin",
                "title": "Executive Summary",
                # Sparse: a custom section slots in at 150 without renumbering anything.
                "position": 100,
                "required": True,
                "output_contract": _EXECUTIVE_SUMMARY_CONTRACT,
                "evidence_policy": _EVIDENCE_POLICY,
                "token_budget": 2000,
                "allowed_tools": [],
                "applicability": {},
            },
            {
                "key": "historical_financial_analysis",
                "version": 1,
                "origin": "builtin",
                "title": "Historical Financial Analysis",
                "position": 200,
                "required": True,
                "output_contract": _HISTORICAL_ANALYSIS_CONTRACT,
                "evidence_policy": _EVIDENCE_POLICY,
                "token_budget": 4000,
                "allowed_tools": [],
                "applicability": {},
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_reports_request_id_as_of_date", table_name="reports")
    op.drop_table("reports")

    op.drop_index("ix_report_sections_job_id_position", table_name="report_sections")
    op.drop_table("report_sections")
    postgresql.ENUM(name="section_status").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_section_definitions_position", table_name="section_definitions")
    op.drop_table("section_definitions")

    op.drop_index("ix_costs_occurred_at", table_name="costs")
    op.drop_index("ix_costs_agent_run_id", table_name="costs")
    op.drop_index("ix_costs_job_id_occurred_at", table_name="costs")
    op.drop_table("costs")

    op.drop_index("ix_agent_runs_created_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_job_step_id", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index("ix_prompts_key", table_name="prompts")
    op.drop_table("prompts")
