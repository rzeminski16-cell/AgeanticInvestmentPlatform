"""A claim's link to the exact words that support it.

**``excerpt_verified`` is written by exactly one function**, :func:`aer.verify.citations.verify`,
and a test scans the source tree to prove it. That restriction is the control, not the column:
a boolean any code could set is a boolean a well-meaning caller sets optimistically, and the
platform's strongest guarantee — threat T10, "the model may propose a citation; only code may
confirm one" — would quietly become a naming convention.

**The default is false, and false is a refusal.** An unverified citation blocks gate 2. It does
not warn, and it is not rendered as though it were checked. Overriding one is possible and takes
a written reason, which is recorded on the row and in the audit chain, because an override is a
human taking responsibility for a specific sentence rather than a setting.

**RESTRICT on the extraction.** This is the protection ADR 0017 promised when it made
``extractions`` cascade from ``source_documents``: an extraction that something cites cannot be
deleted, so the cascade stops here rather than reaching a published report's evidence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.claim import Claim
    from aer.db.models.extraction import Extraction
    from aer.db.models.source_document import SourceDocument
    from aer.db.models.user import User

__all__ = ["Citation"]


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[UuidPk]

    claim_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )

    # Both RESTRICT. Evidence a published claim rests on is not deletable by any code path.
    source_document_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    extraction_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("extractions.id", ondelete="RESTRICT"), nullable=False
    )

    # -- Set only by the verifier -------------------------------------------------------

    # A **server** default, not a Python one. An INSERT written outside the ORM — a
    # migration, a psql statement, a future bulk import — gets false as well, and false is
    # the refusal. A default the application applies is a default that only applies when
    # the application is the one writing.
    excerpt_verified: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    # Which check produced the verdict, versioned. A citation verified under an older rule is
    # not the same assurance as one verified under the current rule, and after a threshold
    # changes "which ones need re-checking?" has to be answerable.
    verification_method: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # How closely the stored excerpt matched the document, 0 to 1. Kept on failures too: the
    # difference between 0.94 and 0.02 is the difference between a reflowed paragraph and a
    # fabrication, and an operator deciding whether to override needs to see which.
    match_ratio: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    # Why it failed, in words, or NULL when it passed.
    verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # -- Set only by a human ------------------------------------------------------------

    # An override does not make a citation verified. It records that a person looked at an
    # unverified one and accepted it anyway, so both facts survive into the report: the check
    # failed, and somebody took responsibility for it.
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overridden_by_user_id: Mapped[UuidFk | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    overridden_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    created_at: Mapped[Timestamp] = created_at_column()

    claim: Mapped[Claim] = relationship(back_populates="citations")
    source_document: Mapped[SourceDocument] = relationship()
    extraction: Mapped[Extraction] = relationship()
    overridden_by: Mapped[User | None] = relationship()

    __table_args__ = (
        CheckConstraint(
            "match_ratio IS NULL OR (match_ratio >= 0 AND match_ratio <= 1)",
            name="ck_citations_match_ratio_is_a_ratio",
        ),
        # A verified citation says which check verified it and when. Without this a row could
        # claim verification with nothing recording what was actually done.
        CheckConstraint(
            "NOT excerpt_verified OR (verification_method IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_citations_verified_records_how_and_when",
        ),
        # An override is a person and a reason, or neither. A reason with no author is not a
        # record of a decision; an author with no reason is not a justification.
        CheckConstraint(
            "(override_reason IS NULL) = (overridden_by_user_id IS NULL)",
            name="ck_citations_override_has_an_author_and_a_reason",
        ),
        CheckConstraint(
            "override_reason IS NULL OR char_length(override_reason) > 0",
            name="ck_citations_override_reason_is_present",
        ),
        Index("ix_citations_claim_id", "claim_id"),
        Index("ix_citations_extraction_id", "extraction_id"),
        # The gate's question: "does this run have anything unverified?" — answered without
        # scanning every citation ever recorded.
        Index("ix_citations_excerpt_verified", "excerpt_verified"),
    )

    @property
    def is_admissible(self) -> bool:
        """Whether this citation may support a claim at gate 2.

        Verified, or unverified and consciously overridden. Deliberately *not* the same
        property as ``excerpt_verified``: a report must be able to say which of its citations
        were checked by code and which were accepted by a person, and collapsing the two here
        would lose that distinction everywhere downstream.
        """
        return self.excerpt_verified or self.override_reason is not None

    def __repr__(self) -> str:
        return f"Citation(id={self.id!r}, verified={self.excerpt_verified!r})"
