"""Add claims and citations: what a report asserts, and what each assertion rests on.

Two tables and one enum. Together they turn "is this report supported?" from a reading
exercise into a query.

**``citations.excerpt_verified`` defaults to false, and false is a refusal.** It is set only by
``aer.verify.citations.verify``, which re-reads the artefact by hash and confirms the excerpt
appears at the recorded locator. A check constraint requires a verified row to say *how* and
*when* it was verified, so nothing can claim the flag without leaving a record of what was done.

**Both foreign keys out of ``citations`` are ``RESTRICT``.** The one to ``extractions`` is the
protection ADR 0017 promised when it made extractions cascade from source documents: an
extraction something cites cannot be deleted, so the cascade stops at the evidence a published
claim rests on rather than reaching through it.

**A numeric claim names exactly one figure.** Enforced by a check constraint over
``financial_fact_id`` and ``calculation_id``, because invariant 3 says no figure reaches a
report unless it is a stored fact or a recorded calculation — and a rule that only lives in
application code is a rule the next writer of an INSERT does not know about. The constraint
also refuses the reverse: a non-numeric claim carrying a figure id would look verified to every
reader downstream while nothing checked it.

The "at least one citation" half of §2.9 is deliberately **not** here. It is a fact about
another table, which no check constraint can see, and it is enforced at gate 2 in code.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_CLAIM_KINDS = ("numeric", "factual", "forward_looking", "opinion")


def upgrade() -> None:
    bind = op.get_bind()

    claim_kind = postgresql.ENUM(*_CLAIM_KINDS, name="claim_kind")
    claim_kind.create(bind, checkfirst=True)

    op.create_table(
        "claims",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("report_section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(*_CLAIM_KINDS, name="claim_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("financial_fact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("calculation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claims"),
        sa.ForeignKeyConstraint(
            ["report_section_id"],
            ["report_sections.id"],
            name="fk_claims_report_section_id_report_sections",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["financial_fact_id"],
            ["financial_facts.id"],
            name="fk_claims_financial_fact_id_financial_facts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculations.id"],
            name="fk_claims_calculation_id_calculations",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("char_length(text) > 0", name="ck_claims_text_is_present"),
        sa.CheckConstraint(
            "(kind = 'numeric') = ("
            "  (financial_fact_id IS NOT NULL)::int + (calculation_id IS NOT NULL)::int = 1"
            ")",
            name="ck_claims_numeric_claims_name_one_figure",
        ),
    )
    op.create_index("ix_claims_report_section_id", "claims", ["report_section_id"])
    op.create_index("ix_claims_kind", "claims", ["kind"])

    op.create_table(
        "citations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "excerpt_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("verification_method", sa.String(length=32), nullable=True),
        sa.Column("match_ratio", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("verification_error", sa.Text(), nullable=True),
        sa.Column("verified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("overridden_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("overridden_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_citations"),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], name="fk_citations_claim_id_claims", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name="fk_citations_source_document_id_source_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["extractions.id"],
            name="fk_citations_extraction_id_extractions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["overridden_by_user_id"],
            ["users.id"],
            name="fk_citations_overridden_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "match_ratio IS NULL OR (match_ratio >= 0 AND match_ratio <= 1)",
            name="ck_citations_match_ratio_is_a_ratio",
        ),
        sa.CheckConstraint(
            "NOT excerpt_verified OR (verification_method IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_citations_verified_records_how_and_when",
        ),
        sa.CheckConstraint(
            "(override_reason IS NULL) = (overridden_by_user_id IS NULL)",
            name="ck_citations_override_has_an_author_and_a_reason",
        ),
        sa.CheckConstraint(
            "override_reason IS NULL OR char_length(override_reason) > 0",
            name="ck_citations_override_reason_is_present",
        ),
    )
    op.create_index("ix_citations_claim_id", "citations", ["claim_id"])
    op.create_index("ix_citations_extraction_id", "citations", ["extraction_id"])
    op.create_index("ix_citations_excerpt_verified", "citations", ["excerpt_verified"])


def downgrade() -> None:
    op.drop_index("ix_citations_excerpt_verified", table_name="citations")
    op.drop_index("ix_citations_extraction_id", table_name="citations")
    op.drop_index("ix_citations_claim_id", table_name="citations")
    op.drop_table("citations")

    op.drop_index("ix_claims_kind", table_name="claims")
    op.drop_index("ix_claims_report_section_id", table_name="claims")
    op.drop_table("claims")

    postgresql.ENUM(name="claim_kind").drop(op.get_bind(), checkfirst=True)
