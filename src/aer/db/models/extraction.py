"""One located span of text inside an archived document.

This is the row the citation verifier stands on. It answers "where exactly, in which document,
does this sentence appear?" — and it answers it in a form that can be checked again later
without trusting anything stored here except the coordinates.

**A row holds an excerpt, not a document.** The full extracted text is not stored. It is a
deterministic function of the artefact, the extractor and the extractor version, all three of
which *are* stored, so keeping a second copy of every filing's text would double the disk for
something regenerable. ``content_hash`` is the hash of that **whole extracted text**, not of
the excerpt — the one thing worth keeping from it, because it distinguishes "the excerpt is
wrong" from "the extractor changed and every locator from it has shifted".

**``CASCADE`` on the source document, unlike ``financial_facts``, which is ``RESTRICT``.** The
difference is real rather than an inconsistency. A fact is a first-class record that a report
cites directly, so losing one silently would break a published report; an extraction is derived
and regenerable from bytes that are never deleted. What must survive is protected one level up:
a citation references an extraction with ``RESTRICT``, so an extraction something cites cannot
be removed, and the cascade cannot reach past it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import ExtractionKind
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.source_document import SourceDocument

__all__ = ["Extraction"]


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[UuidPk]

    source_document_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[ExtractionKind] = mapped_column(
        SaEnum(
            ExtractionKind,
            name="extraction_kind",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    # The function that produced the text this locator indexes into. Both are part of the
    # verification contract, and both are columns rather than fields inside `locator` so that
    # "which extractions came from html v1?" is a query rather than a JSON scan — which is the
    # question asked the day an extractor's output changes.
    extractor: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(16), nullable=False)

    # Character range, plus page and bounding box where the extractor has them. JSONB because
    # the shape is per-kind: a PDF table cell needs coordinates a character offset cannot carry.
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # The canonical hash of `locator`, so uniqueness is an ordinary btree index. A unique
    # constraint over JSONB fields would need an expression index per field and would have to be
    # rewritten every time a locator gains one.
    locator_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # The text at the locator, verbatim. Stored rather than re-derived on read because this is
    # what a reviewer is shown and what a verifier compares against; deriving it at display
    # time would mean a page that cannot render without re-parsing a filing.
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)

    # SHA-256 of the **entire** extracted text, not of `excerpt`. See the module docstring.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    source_document: Mapped[SourceDocument] = relationship(back_populates="extractions")

    __table_args__ = (
        # Re-extracting the same span of the same document with the same extractor is not a
        # second piece of evidence. Re-running a step must be free of side effects, and without
        # this a resumed run would accumulate a duplicate excerpt per attempt.
        UniqueConstraint(
            "source_document_id",
            "extractor",
            "extractor_version",
            "locator_hash",
            name="uq_extractions_locator",
        ),
        CheckConstraint("char_length(excerpt) > 0", name="ck_extractions_excerpt_is_present"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_extractions_content_hash_len"),
        CheckConstraint("char_length(locator_hash) = 64", name="ck_extractions_locator_hash_len"),
        Index("ix_extractions_source_document_id", "source_document_id"),
        # Answers "has this document's text changed under us?" across every extraction of it.
        Index("ix_extractions_content_hash", "content_hash"),
    )

    def __repr__(self) -> str:
        return (
            f"Extraction(id={self.id!r}, extractor={self.extractor!r}"
            f"@{self.extractor_version!r}, kind={self.kind!r})"
        )
