"""Add extractions: where a span of text sits inside an archived document.

One table and one enum. This is the row the citation verifier will stand on — it records the
artefact-derived text an excerpt came from, the function that derived it, and the character
range within it.

**The uniqueness key includes ``extractor_version``.** The same character range means something
different once the extractor changes, so those are genuinely different rows rather than a
collision to suppress. Leaving the version out would make a re-extraction after an extractor
change silently collide with the old locator and keep the stale excerpt.

**Uniqueness is over ``locator_hash``, not over the ``locator`` JSONB.** A unique constraint on
JSON fields needs an expression index per field, and would have to be rewritten each time a
locator kind gains a coordinate — which task 14's page-and-bounding-box locator does
immediately. Hashing the canonical form keeps it an ordinary btree index.

**``ON DELETE CASCADE`` to ``source_documents``**, where ``financial_facts`` uses ``RESTRICT``.
The asymmetry is deliberate: an extraction is derived and regenerable from artefact bytes that
are never deleted, whereas a fact is a first-class record a report cites. What must not vanish
is protected one level out, when citations arrive referencing extractions with ``RESTRICT``.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_EXTRACTION_KINDS = ("text", "table")


def upgrade() -> None:
    bind = op.get_bind()

    extraction_kind = postgresql.ENUM(*_EXTRACTION_KINDS, name="extraction_kind")
    extraction_kind.create(bind, checkfirst=True)

    op.create_table(
        "extractions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(*_EXTRACTION_KINDS, name="extraction_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("extractor", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=16), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("locator_hash", sa.String(length=64), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extractions"),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name="fk_extractions_source_document_id_source_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "source_document_id",
            "extractor",
            "extractor_version",
            "locator_hash",
            name="uq_extractions_locator",
        ),
        sa.CheckConstraint("char_length(excerpt) > 0", name="ck_extractions_excerpt_is_present"),
        sa.CheckConstraint(
            "char_length(content_hash) = 64", name="ck_extractions_content_hash_len"
        ),
        sa.CheckConstraint(
            "char_length(locator_hash) = 64", name="ck_extractions_locator_hash_len"
        ),
    )

    op.create_index("ix_extractions_source_document_id", "extractions", ["source_document_id"])
    op.create_index("ix_extractions_content_hash", "extractions", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_extractions_content_hash", table_name="extractions")
    op.drop_index("ix_extractions_source_document_id", table_name="extractions")
    op.drop_table("extractions")
    postgresql.ENUM(name="extraction_kind").drop(op.get_bind(), checkfirst=True)
