"""The finished report.

One row per job, assembled from ``report_sections`` in position order and frozen when a
human approves it.

**``immutable`` is not decoration.** A report is the artefact a decision was made against.
Once approved, its content must not change — not because editing is dishonest, but because
an edited report and the original are two different documents and only one of them is what
was approved. A revision is a new run producing a new report, which is also what keeps the
history of a view over time honest.

**``rating`` is a non-binding personal view, and the schema says so nowhere.** It cannot:
the disclaimer belongs on every rendered surface, which is a rendering concern. What this
table does is keep the rating nullable, because a run that could not reach a view must be
able to say so rather than being forced to pick one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Sha256, Timestamp, TimestampOptional, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job import Job
    from aer.db.models.request import ResearchRequest

__all__ = ["Report"]


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[UuidPk]

    # One report per job. A second would be a second opinion with no way to tell which the
    # operator saw.
    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    request_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False
    )

    # Nullable: a run can fail before the company is resolved, and the partial report is
    # still worth keeping to explain what happened.
    company_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT")
    )

    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    # -- The view ---------------------------------------------------------------------------

    # Nullable, because a run that could not reach a view must be able to say so rather
    # than being forced to invent one.
    rating: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)

    valuation_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    valuation_base: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    valuation_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    valuation_currency: Mapped[str | None] = mapped_column(String(3))

    # -- The content ------------------------------------------------------------------------

    # Assembled from report_sections in position order. Stored so the approved document is
    # a single object that can be hashed, rather than something reconstructed by rerunning
    # a query whose result could change.
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    content_hash: Mapped[Sha256] = mapped_column(nullable=False)

    markdown_artefact_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("artefacts.id", ondelete="RESTRICT")
    )
    # The stored preview HTML — the PDF's exact input, archived so "what was approved"
    # is a file, not a re-render (task 48, migration 0024).
    html_artefact_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("artefacts.id", ondelete="RESTRICT")
    )
    pdf_artefact_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("artefacts.id", ondelete="RESTRICT")
    )

    # -- Approval ----------------------------------------------------------------------------

    approved_by: Mapped[UuidFkOptional] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[TimestampOptional]

    immutable: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    job: Mapped[Job] = relationship()
    request: Mapped[ResearchRequest] = relationship()

    __table_args__ = (
        CheckConstraint("char_length(content_hash) = 64", name="content_hash_is_sha256"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_is_a_probability",
        ),
        # An immutable report is one somebody approved. Freezing an unapproved draft would
        # make it unfixable without recording that anyone agreed to it.
        CheckConstraint(
            "(NOT immutable) OR (approved_at IS NOT NULL)",
            name="immutable_reports_were_approved",
        ),
        CheckConstraint(
            "valuation_currency IS NULL OR char_length(valuation_currency) = 3",
            name="valuation_currency_iso4217",
        ),
        # A range that runs backwards is a modelling error, and one that reaches a report
        # would read as though the bear case were better than the bull.
        CheckConstraint(
            "valuation_low IS NULL OR valuation_high IS NULL OR valuation_low <= valuation_high",
            name="valuation_range_runs_forwards",
        ),
        Index("ix_reports_request_id_as_of_date", "request_id", "as_of_date"),
    )

    def __repr__(self) -> str:
        return f"<Report job={self.job_id} rating={self.rating} immutable={self.immutable}>"
