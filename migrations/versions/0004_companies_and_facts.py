"""Add companies and financial facts.

Two tables and one enum.

``companies`` is the resolved identity a research request points at once its ticker has
been checked against a registry. ``financial_facts`` is every reported number, each linked
to the source document it came from and stamped with the date it was filed.

**The uniqueness index uses ``NULLS NOT DISTINCT``.** ``fiscal_period`` is nullable — many
facts have no fiscal period at all — and under the SQL default two NULLs never compare
equal, so an ordinary unique constraint would permit unlimited duplicates of exactly those
rows. Postgres 15 added the modifier that fixes it. The alternative, a sentinel string
standing in for "none", stores a falsehood in a column to work around a comparison rule,
and every query thereafter has to know about it.

**``financial_facts`` links to ``source_documents``, not to ``extractions``.** The
extraction layer does not exist yet; the provenance chain here is fact → source document →
artefact → hash, which is unbroken and sufficient. See
``docs/adr/0010-facts-cite-source-documents-until-extractions-exist.md``.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_FACT_BASES = ("as_reported", "restated", "vendor_standardised")


def upgrade() -> None:
    bind = op.get_bind()

    fact_basis = postgresql.ENUM(*_FACT_BASES, name="fact_basis")
    fact_basis.create(bind, checkfirst=True)

    op.create_table(
        "companies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("cik", sa.String(length=10), nullable=True),
        sa.Column("company_number", sa.String(length=16), nullable=True),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("sic", sa.String(length=8), nullable=True),
        sa.Column("sic_description", sa.Text(), nullable=True),
        sa.Column("fiscal_year_end", sa.String(length=4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cik IS NOT NULL OR company_number IS NOT NULL",
            name="ck_companies_has_a_registry_identifier",
        ),
        sa.CheckConstraint(
            "cik IS NULL OR char_length(cik) = 10", name="ck_companies_cik_is_zero_padded"
        ),
        sa.CheckConstraint(
            "isin IS NULL OR char_length(isin) = 12", name="ck_companies_isin_is_iso6166_length"
        ),
        sa.CheckConstraint(
            "fiscal_year_end IS NULL OR char_length(fiscal_year_end) = 4",
            name="ck_companies_fiscal_year_end_is_mmdd",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_companies"),
        sa.UniqueConstraint("cik", name="uq_companies_cik"),
        sa.UniqueConstraint("company_number", name="uq_companies_company_number"),
        sa.UniqueConstraint("ticker", "exchange", name="uq_companies_listing"),
    )
    op.create_index("ix_companies_name", "companies", ["name"])

    op.create_table(
        "financial_facts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("raw_concept", sa.Text(), nullable=True),
        sa.Column("taxonomy", sa.String(length=32), nullable=True),
        sa.Column("value", sa.Numeric(precision=38, scale=6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("scale", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=8), nullable=True),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("form", sa.String(length=16), nullable=True),
        sa.Column("accession", sa.String(length=20), nullable=True),
        sa.Column(
            "basis",
            postgresql.ENUM(*_FACT_BASES, name="fact_basis", create_type=False),
            server_default="as_reported",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "period_start IS NULL OR period_start <= period_end",
            name="ck_financial_facts_period_runs_forwards",
        ),
        sa.CheckConstraint("char_length(unit) > 0", name="ck_financial_facts_unit_is_present"),
        sa.CheckConstraint(
            "char_length(concept) > 0", name="ck_financial_facts_concept_is_present"
        ),
        sa.CheckConstraint(
            "scale BETWEEN -12 AND 12", name="ck_financial_facts_scale_is_a_sane_power_of_ten"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_financial_facts_company_id_companies",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name="fk_financial_facts_source_document_id_source_documents",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_financial_facts"),
    )

    op.create_index(
        "uq_financial_facts_observation",
        "financial_facts",
        ["company_id", "concept", "unit", "period_end", "fiscal_period", "basis", "filed_date"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_financial_facts_company_concept_period",
        "financial_facts",
        ["company_id", "concept", sa.text("period_end DESC"), sa.text("filed_date DESC")],
    )
    op.create_index(
        "ix_financial_facts_source_document_id", "financial_facts", ["source_document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_financial_facts_source_document_id", table_name="financial_facts")
    op.drop_index("ix_financial_facts_company_concept_period", table_name="financial_facts")
    op.drop_index("uq_financial_facts_observation", table_name="financial_facts")
    op.drop_table("financial_facts")

    op.drop_index("ix_companies_name", table_name="companies")
    op.drop_table("companies")

    postgresql.ENUM(name="fact_basis").drop(op.get_bind(), checkfirst=True)
