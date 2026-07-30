"""Record what two sources disagreed about, and seed the sectors the standard model breaks on.

Two tables, four enums, and eight seeded rows.

**Both positions are columns, not one.** The losing evidence is retained — that is the
requirement, and it is why there is no ``value`` column and no ``winner_id``. A row that kept
only the accepted figure would document a decision while destroying the thing that made it a
decision. The report's disagreement appendix reads both.

**Agreement is not a row.** A check constraint refuses ``resolution = 'agreed'``. Two sources
saying the same thing is the ordinary case and rows for it would bury the ones that matter, so
the recording rule lives in the schema rather than in the one service that currently obeys it.

**An escalation reaches a gate or it is not an escalation.** ``escalated_to_gate`` is non-null
exactly when the outcome is ``escalated``, both directions. Without the second half, a resolved
conflict could carry a gate and sit on an operator's banner forever.

**``sector_profiles`` is seeded here so Phase 3 opens on data.** The rows mirror
``aer.core.sectors.SECTOR_PROFILES``; the seed is written out literally rather than imported,
because a migration that imports application code stops describing the schema as it was and
starts describing the code as it is now. A test asserts the two still agree.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_DISAGREEMENT_KINDS = ("source_conflict", "calculation_conflict", "thesis_conflict")
_RESOLUTION_OUTCOMES = ("agreed", "chose_a", "chose_b", "escalated")
_RESOLUTION_RULES = (
    "unit_mismatch",
    "values_agree",
    "suspected_scale_error",
    "lower_tier_wins",
    "basis_mismatch",
    "later_filing_wins",
    "same_tier_same_date",
    "thesis_conflict",
)
_RESOLVED_BY = ("rule", "human", "agent")

# Mirrors `aer.core.sectors.SECTOR_PROFILES`. See the module docstring for why it is copied.
_SECTOR_PROFILES: tuple[dict[str, object], ...] = (
    {
        "key": "banks",
        "label": "Banks",
        "sic_prefixes": ["602", "6021", "6022", "6029"],
        "icb_codes": ["301010"],
        "allowed_models": ["comps_multiples", "dividend_discount"],
        "blocked_models": ["dcf_fcff"],
        "required_metrics": [
            "net_interest_margin",
            "cet1_ratio",
            "cost_income_ratio",
            "loan_loss_provisions",
            "tangible_book_value_per_share",
        ],
        "warnings": [
            "Enterprise value and free cash flow to the firm are not meaningful for a bank: "
            "deposits and debt are raw material, not financing.",
            "Capital adequacy, net interest margin and provisioning govern the valuation. "
            "This build produces P/TBV and P/E comparables only, and does not implement a "
            "specialist bank model.",
        ],
    },
    {
        "key": "insurers",
        "label": "Insurers",
        "sic_prefixes": ["631", "632", "633", "6311", "6331"],
        "icb_codes": ["303010", "303020"],
        "allowed_models": ["comps_multiples", "dividend_discount"],
        "blocked_models": ["dcf_fcff"],
        "required_metrics": [
            "combined_ratio",
            "reserve_development",
            "embedded_value",
            "solvency_ratio",
        ],
        "warnings": [
            "Free cash flow to the firm is not meaningful for an insurer; float is a "
            "liability that funds the assets.",
            "Combined ratio, reserve development and embedded value are required and are "
            "flagged as absent rather than estimated.",
        ],
    },
    {
        "key": "reits",
        "label": "REITs and property companies",
        "sic_prefixes": ["6798", "6512", "6531"],
        "icb_codes": ["351020", "351030"],
        "allowed_models": ["comps_multiples", "net_asset_value"],
        "blocked_models": ["dcf_fcff"],
        "required_metrics": ["ffo", "affo", "net_asset_value", "occupancy", "loan_to_value"],
        "warnings": [
            "Depreciation dominates reported earnings for a property company, so FCFF is "
            "not the cash the business generates.",
            "FFO/AFFO and NAV are required. This build produces P/FFO comparables and "
            "discloses that NAV was not computed.",
        ],
    },
    {
        "key": "utilities",
        "label": "Utilities and regulated networks",
        "sic_prefixes": ["491", "492", "493", "4911", "4931"],
        "icb_codes": ["651010", "651020"],
        "allowed_models": ["dcf_fcff", "dcf_fcfe", "comps_multiples"],
        "blocked_models": [],
        "required_metrics": ["regulated_asset_base", "allowed_return", "regulatory_period_end"],
        "warnings": [
            "Returns are set by a regulator over a defined period, so the terminal value "
            "depends on a regulatory settlement rather than on competitive dynamics.",
            "State the regulatory period and the allowed return, or the model is a guess "
            "dressed as a forecast.",
        ],
    },
    {
        "key": "biotech_pre_revenue",
        "label": "Pre-revenue biotechnology",
        "sic_prefixes": ["2836", "8731"],
        "icb_codes": ["201020"],
        "allowed_models": ["comps_multiples"],
        "blocked_models": ["dcf_fcff", "dcf_fcfe"],
        "required_metrics": ["cash_runway_months", "pipeline_stage", "trial_readout_dates"],
        "warnings": [
            "There are no cash flows to discount. A DCF here is arithmetic performed on "
            "assumptions, and the answer is whatever the assumptions were.",
            "Risk-adjusted NPV over the pipeline is the right model and is not implemented "
            "in this build.",
        ],
    },
    {
        "key": "mining_energy",
        "label": "Mining, oil and gas",
        "sic_prefixes": ["101", "131", "1311", "1221"],
        "icb_codes": ["551010", "601010"],
        "allowed_models": ["dcf_fcff", "dcf_fcfe", "comps_multiples"],
        "blocked_models": [],
        "required_metrics": ["reserve_life", "commodity_price_deck", "all_in_sustaining_cost"],
        "warnings": [
            "Reserve life and the commodity price deck dominate the valuation; the discount "
            "rate is a detail beside them.",
            "State the commodity assumption explicitly. A resource model with an implicit "
            "price forecast is untestable.",
        ],
    },
    {
        "key": "early_stage_tech",
        "label": "Early-stage and loss-making technology",
        "sic_prefixes": ["737", "7372", "7379"],
        "icb_codes": ["101010", "102010"],
        "allowed_models": ["dcf_fcff", "dcf_fcfe", "comps_multiples"],
        "blocked_models": [],
        "required_metrics": ["revenue_growth", "gross_margin", "rule_of_40", "cash_runway_months"],
        "warnings": [
            "Terminal value will be most of the answer. Report the terminal-value share and "
            "run a sensitivity on it, or the model conceals where the number came from.",
        ],
    },
    {
        "key": "holding_companies",
        "label": "Holding companies and conglomerates",
        "sic_prefixes": ["6719", "6742"],
        "icb_codes": ["302020"],
        "allowed_models": ["comps_multiples"],
        "blocked_models": [],
        "required_metrics": ["segment_revenue", "segment_operating_profit", "stake_percentages"],
        "warnings": [
            "A consolidated model of a holding company values an accounting artefact. Sum "
            "of the parts is the right approach and is not implemented in this build.",
            "Disclose the holding-company discount as an assumption rather than embedding "
            "it in a multiple.",
        ],
    },
)


def upgrade() -> None:
    bind = op.get_bind()

    for values, name in (
        (_DISAGREEMENT_KINDS, "disagreement_kind"),
        (_RESOLUTION_OUTCOMES, "resolution_outcome"),
        (_RESOLUTION_RULES, "resolution_rule"),
        (_RESOLVED_BY, "resolved_by"),
    ):
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "disagreements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(*_DISAGREEMENT_KINDS, name="disagreement_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("position_a", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position_b", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "resolution",
            postgresql.ENUM(*_RESOLUTION_OUTCOMES, name="resolution_outcome", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "rule",
            postgresql.ENUM(*_RESOLUTION_RULES, name="resolution_rule", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "resolved_by",
            postgresql.ENUM(*_RESOLVED_BY, name="resolved_by", create_type=False),
            nullable=False,
        ),
        sa.Column("resolution_rationale", sa.Text(), nullable=False),
        sa.Column("resolved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        # Reuses the enum `approvals` already created; `create_type=False` because a second
        # CREATE TYPE would fail the migration on a database that has ever run 0001.
        sa.Column(
            "escalated_to_gate",
            postgresql.ENUM(name="gate_kind", create_type=False),
            nullable=True,
        ),
        sa.Column("material", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("relative_difference", sa.Numeric(18, 9), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_disagreements"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_disagreements_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name="fk_disagreements_resolved_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("char_length(topic) > 0", name="ck_disagreements_topic_is_present"),
        sa.CheckConstraint(
            "char_length(resolution_rationale) > 0",
            name="ck_disagreements_rationale_is_present",
        ),
        sa.CheckConstraint(
            "(resolution = 'escalated') = (escalated_to_gate IS NOT NULL)",
            name="ck_disagreements_escalations_reach_a_gate",
        ),
        sa.CheckConstraint(
            "(resolved_by = 'human') = "
            "(resolved_by_user_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_disagreements_human_resolutions_name_the_human",
        ),
        sa.CheckConstraint(
            "resolution <> 'agreed'",
            name="ck_disagreements_agreement_is_not_recorded",
        ),
    )
    op.create_index(
        "uq_disagreements_job_fingerprint",
        "disagreements",
        ["job_id", "fingerprint"],
        unique=True,
    )
    op.create_index("ix_disagreements_job_id", "disagreements", ["job_id"])
    op.create_index("ix_disagreements_job_resolution", "disagreements", ["job_id", "resolution"])

    op.create_table(
        "sector_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("sic_prefixes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("icb_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("allowed_models", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("blocked_models", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sector_profiles"),
        sa.UniqueConstraint("key", name="uq_sector_profiles_key"),
        sa.CheckConstraint("char_length(key) > 0", name="ck_sector_profiles_key_is_present"),
    )

    insert = sa.text(
        "INSERT INTO sector_profiles "
        "(key, label, sic_prefixes, icb_codes, allowed_models, blocked_models, "
        " required_metrics, warnings) "
        "VALUES (:key, :label, CAST(:sic_prefixes AS jsonb), CAST(:icb_codes AS jsonb), "
        " CAST(:allowed_models AS jsonb), CAST(:blocked_models AS jsonb), "
        " CAST(:required_metrics AS jsonb), CAST(:warnings AS jsonb))"
    )
    for profile in _SECTOR_PROFILES:
        bind.execute(
            insert,
            {
                key: value if isinstance(value, str) else json.dumps(value)
                for key, value in profile.items()
            },
        )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("sector_profiles")

    op.drop_index("ix_disagreements_job_resolution", table_name="disagreements")
    op.drop_index("ix_disagreements_job_id", table_name="disagreements")
    op.drop_index("uq_disagreements_job_fingerprint", table_name="disagreements")
    op.drop_table("disagreements")

    # `gate_kind` is not dropped: `approvals` created it in 0001 and still uses it.
    for name in ("resolved_by", "resolution_rule", "resolution_outcome", "disagreement_kind"):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
