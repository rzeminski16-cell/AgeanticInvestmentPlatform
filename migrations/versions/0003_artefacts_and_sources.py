"""Add the evidence substrate: artefacts and source documents.

Two tables and one guarantee.

``artefacts`` holds one row per distinct set of bytes, keyed by the SHA-256 of those
bytes. ``source_documents`` holds the provenance of each acquisition — where it came
from, when, under what terms, and whether it is admissible.

**Artefact rows are made immutable by a trigger, not by convention.** The spec for this
task allowed either a trigger or a documented TODO; the trigger was chosen, and the
reason is the same one recorded in ADR 0005. The application is not the only thing that
will ever write to this database — scripts, migrations and an ad-hoc ``psql`` session all
will — so a rule enforced only in Python is a rule those writers do not have. The whole
claim of this platform is that a report's evidence can be re-checked later; a row whose
``sha256`` could be edited to point at different bytes would quietly make that claim
false, and nothing would show it.

**DELETE is deliberately left possible.** Retention, erasure and a mistaken fetch are all
legitimate reasons to remove an artefact, and a table nothing can ever be deleted from is
a table that eventually forces someone to disable the protection wholesale. There is no
delete path in the service layer, so it cannot happen by accident, and
``source_documents.artefact_id`` uses ``ON DELETE RESTRICT`` so an artefact still cited by
a provenance record cannot be removed while that record stands.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_SOURCE_TIERS = (
    "T1_REGULATORY",
    "T2_ISSUER",
    "T3_OFFICIAL_STATS",
    "T4_LICENSED_MARKET",
    "T5_SECONDARY",
    "T6_UNVERIFIED",
)

_PROVIDERS = (
    "sec_edgar",
    "companies_house",
    "fca_nsm",
    "eodhd",
    "fred",
    "issuer_ir",
    "web_search",
    "user_supplied",
)

# Raises rather than silently ignoring the write. A trigger that returned OLD would make
# an UPDATE appear to succeed while changing nothing, which is worse than refusing it:
# the caller would carry on believing the edit had landed.
_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION artefacts_reject_update() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION
    'artefacts rows are immutable: an artefact is identified by the hash of its own '
    'content, so altering one would break every citation that verifies against it'
    USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def _enum(values: tuple[str, ...], name: str) -> postgresql.ENUM:
    """Reference an enum this migration created, without trying to create it again."""
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    source_tier = postgresql.ENUM(*_SOURCE_TIERS, name="source_tier")
    provider = postgresql.ENUM(*_PROVIDERS, name="provider")
    source_tier.create(bind, checkfirst=True)
    provider.create(bind, checkfirst=True)

    op.create_table(
        "artefacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_backend", sa.Text(), server_default=sa.text("'local'"), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_artefacts_artefact_is_not_empty"),
        sa.CheckConstraint(
            "char_length(sha256) = 64", name="ck_artefacts_artefact_sha256_is_full_length"
        ),
        sa.CheckConstraint(
            "sha256 = lower(sha256)", name="ck_artefacts_artefact_sha256_is_lowercase"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artefacts"),
        sa.UniqueConstraint("sha256", name="uq_artefacts_sha256"),
    )
    op.create_index("ix_artefacts_created_at", "artefacts", ["created_at"])

    op.execute(_IMMUTABILITY_FUNCTION)
    op.execute(
        "CREATE TRIGGER artefacts_are_immutable "
        "BEFORE UPDATE ON artefacts "
        "FOR EACH ROW EXECUTE FUNCTION artefacts_reject_update()"
    )

    op.create_table(
        "source_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artefact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("provider", _enum(_PROVIDERS, "provider"), nullable=False),
        sa.Column("source_tier", _enum(_SOURCE_TIERS, "source_tier"), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("publication_date_confidence", sa.Float(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("licence_note", sa.Text(), nullable=True),
        sa.Column("robots_allowed", sa.Boolean(), nullable=True),
        sa.Column("quarantined", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "publication_date_confidence IS NULL"
            " OR (publication_date_confidence >= 0 AND publication_date_confidence <= 1)",
            name="ck_source_documents_publication_date_confidence_is_a_probability",
        ),
        sa.CheckConstraint(
            "(quarantined AND quarantine_reason IS NOT NULL)"
            " OR (NOT quarantined AND quarantine_reason IS NULL)",
            name="ck_source_documents_quarantine_has_a_reason",
        ),
        sa.ForeignKeyConstraint(
            ["artefact_id"],
            ["artefacts.id"],
            name="fk_source_documents_artefact_id_artefacts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_source_documents_job_id_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["research_requests.id"],
            name="fk_source_documents_request_id_research_requests",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_documents"),
        sa.UniqueConstraint("request_id", "url", "retrieved_at", name="uq_source_acquisition"),
    )
    op.create_index(
        "ix_source_documents_request_id_publication_date",
        "source_documents",
        ["request_id", "publication_date"],
    )
    op.create_index("ix_source_documents_artefact_id", "source_documents", ["artefact_id"])
    op.create_index("ix_source_documents_job_id", "source_documents", ["job_id"])
    op.create_index(
        "ix_source_documents_quarantined",
        "source_documents",
        ["request_id"],
        postgresql_where=sa.text("quarantined"),
    )


def downgrade() -> None:
    op.drop_index("ix_source_documents_quarantined", table_name="source_documents")
    op.drop_index("ix_source_documents_job_id", table_name="source_documents")
    op.drop_index("ix_source_documents_artefact_id", table_name="source_documents")
    op.drop_index("ix_source_documents_request_id_publication_date", table_name="source_documents")
    op.drop_table("source_documents")

    op.execute("DROP TRIGGER IF EXISTS artefacts_are_immutable ON artefacts")
    op.execute("DROP FUNCTION IF EXISTS artefacts_reject_update()")

    op.drop_index("ix_artefacts_created_at", table_name="artefacts")
    op.drop_table("artefacts")

    bind = op.get_bind()
    postgresql.ENUM(name="provider").drop(bind, checkfirst=True)
    postgresql.ENUM(name="source_tier").drop(bind, checkfirst=True)
