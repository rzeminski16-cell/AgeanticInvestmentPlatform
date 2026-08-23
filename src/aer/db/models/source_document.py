"""Provenance: where a set of bytes came from, and what may be done with it.

An :class:`~aer.db.models.artefact.Artefact` is content. A source document is the *story*
of that content — the URL it came from, who published it, when, under what licence, and
whether robots allowed it. Two fetches of the same PDF share one artefact and get two
source documents, because they happened at different times and possibly under different
terms.

Separating them is what makes the audit trail honest. Bytes are identical or they are
not; provenance is a set of claims about those bytes, and claims are the thing that can
be wrong.

**The quarantine flag is the point-in-time safety net.** A document whose publication
date cannot be established cannot be shown to predate a request's as-of date, so under
point-in-time rules it is not admissible evidence. It is kept — throwing it away would
lose the record of what was seen — but flagged, so nothing downstream can cite it by
accident. The rule is applied in :mod:`aer.services.sources`, in code, with a test.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import Provider, SourceTier
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.artefact import Artefact
    from aer.db.models.extraction import Extraction
    from aer.db.models.request import ResearchRequest
    from aer.db.models.work_order import WorkOrder

__all__ = ["SourceDocument"]

NO_PUBLICATION_DATE = "no_publication_date"
"""Quarantine reason for a source whose publication date could not be established."""


def _enum(python_enum: type, name: str) -> SaEnum:
    return SaEnum(python_enum, name=name, values_callable=lambda e: [m.value for m in e])


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[UuidPk]

    # Which run this was gathered for. Not nullable, for the reason the request column
    # carried before ADR 0072 moved the run root: a source with no run is a source nobody
    # can explain the presence of, and point-in-time is a property of the run, so the link
    # is what makes it checkable at all. `visible_sources` compares against this column —
    # ADR 0061's rule that a source document is scoped by run *and* subject, where a fact
    # is scoped by subject alone.
    work_order_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False
    )

    # Kept for the transition and dropped by the follow-up revision, once nothing reads it.
    request_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE")
    )

    # Nullable because acquisition can happen outside a job -- a source supplied by hand,
    # or one gathered while planning. SET NULL rather than CASCADE: the provenance record
    # must survive the job that produced it, or deleting a failed run would erase the
    # evidence of what it looked at.
    job_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))

    # Which company this document is about, where it is about one. NULL is not "unknown":
    # a macro series, a regulator's guidance note or an index page legitimately has no
    # issuer, and forcing an answer would invite a wrong one where an honest absence
    # belongs. Those documents stay visible to every run that fetched them.
    #
    # Added by ADR 0061. Before peer acquisition existed, `request_id` was a sufficient
    # proxy for "about the subject" because a request only ever touched one company. It
    # stopped being one the moment a run fetched a peer's filings, and an Amazon report
    # cited Walmart, Alibaba and four others as evidence.
    #
    # SET NULL rather than CASCADE, for the reason `job_id` gives above: provenance must
    # outlive the rows it points at.
    company_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )

    artefact_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("artefacts.id", ondelete="RESTRICT"), nullable=False
    )

    # -- Identity ------------------------------------------------------------------------

    url: Mapped[str] = mapped_column(Text, nullable=False)

    # The URL after redirects and tracking parameters are stripped. Stored alongside the
    # original rather than replacing it: the URL actually requested is what a robots or
    # licence question is answered against.
    canonical_url: Mapped[str | None] = mapped_column(Text)

    title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)

    provider: Mapped[Provider] = mapped_column(_enum(Provider, "provider"), nullable=False)
    source_tier: Mapped[SourceTier] = mapped_column(
        _enum(SourceTier, "source_tier"), nullable=False
    )

    # -- Time ----------------------------------------------------------------------------

    # NULL means "could not be established", not "undated". The difference matters: it is
    # the trigger for quarantine under point-in-time rules.
    publication_date: Mapped[date | None] = mapped_column(Date)

    # How confident the extractor is in that date, 0..1. A date parsed from a filing
    # header is not the same evidence as one guessed from a URL slug, and a downstream
    # point-in-time decision should be able to tell.
    publication_date_confidence: Mapped[float | None] = mapped_column(Float)

    # Which kind of evidence won, as a `DateEvidence` value. Stored as text rather than as an
    # enum column because the vocabulary belongs to the extractor and will grow as adapters do;
    # a new kind of evidence should not need a migration on an enum type.
    publication_date_source: Mapped[str | None] = mapped_column(Text)

    # Every candidate that was found, not only the winner. A confidence of 0.48 is a number a
    # reviewer cannot act on; "the index said July, the PDF's metadata said August, and they
    # disagree" is a thing they can go and check. This is what makes the confidence explicable,
    # which is the difference between a score and an argument.
    publication_date_candidates: Mapped[list[Any] | None] = mapped_column(JSONB)

    # **The latest date any evidence supports**, which is what the point-in-time rule is decided
    # on. Kept alongside `publication_date` rather than replacing it, because the two answer
    # different questions: that one is the best estimate and is what gets shown, this one is the
    # conservative bound and is what admissibility turns on. Where the candidates agree they are
    # the same date, which is the ordinary case.
    publication_date_latest: Mapped[date | None] = mapped_column(Date)

    retrieved_at: Mapped[Timestamp] = mapped_column(nullable=False)

    # -- Terms of use --------------------------------------------------------------------

    http_status: Mapped[int | None] = mapped_column()

    # What is known about how this content may be used. Recorded at acquisition because it
    # is answerable then and much harder to reconstruct later.
    licence_note: Mapped[str | None] = mapped_column(Text)

    # NULL means robots.txt was not consulted (a provider API, say), which is different
    # from "consulted and permitted".
    robots_allowed: Mapped[bool | None] = mapped_column()

    # -- Admissibility -------------------------------------------------------------------

    quarantined: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    quarantine_reason: Mapped[str | None] = mapped_column(Text)

    # -- Using a quarantined source anyway, on the record ---------------------------------

    # **The quarantine flag is never cleared.** An override sits beside it rather than undoing
    # it, so the record still says the document could not be dated and now also says who decided
    # to use it and why. Clearing the flag would erase the first half, and a reader of the
    # finished report would have no way to know a judgement had been made at all.
    #
    # Same shape as a citation override, deliberately: a person, a reason, a time.
    admissibility_override_by_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    admissibility_override_reason: Mapped[str | None] = mapped_column(Text)
    admissibility_overridden_at: Mapped[Timestamp | None] = mapped_column()

    @property
    def is_admissible(self) -> bool:
        """Whether this source may support a claim.

        Quarantined and overridden is admissible; quarantined and not overridden is not. The
        report still shows both facts — see the columns above.
        """
        return not self.quarantined or self.admissibility_override_reason is not None

    # -- What the document tried ----------------------------------------------------------

    # **A flag, not a quarantine.** A document that hides text is shown to a human at gate 2;
    # it is not refused, because hidden text has innocent uses and because nothing downstream
    # depends on detection — see `aer.extract.injection`. Kept separate from `quarantined`,
    # which is the point-in-time rule and is a refusal.
    injection_flagged: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    # The findings themselves, with their locators, so a reviewer is shown the passage rather
    # than a category. JSONB because the shape is per-signal and will grow with the heuristics.
    injection_findings: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[Timestamp] = created_at_column()

    # -- Relationships -------------------------------------------------------------------

    artefact: Mapped[Artefact] = relationship(back_populates="sources")
    work_order: Mapped[WorkOrder] = relationship(back_populates="sources")
    request: Mapped[ResearchRequest | None] = relationship(back_populates="sources")

    # CASCADE, because an extraction is derived and regenerable from bytes that are never
    # deleted. What is cited is protected one level further out; see the model's docstring.
    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="source_document", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # The same URL fetched at the same instant for the same request is the same
        # acquisition. Fetching it again later is a new row, which is correct: the content
        # may have changed, and that change is itself worth recording.
        UniqueConstraint("work_order_id", "url", "retrieved_at", name="uq_source_acquisition"),
        # One record per artefact per request, enforced where the race actually is. The
        # A43 pre-read closed the sequential duplicate; parallel research nodes each hold
        # their own session, so neither can see the other's uncommitted insert, and a
        # live run recorded its own 10-Q twice. The database is the only participant that
        # sees both writers, so the database holds the rule; the service turns a
        # violation into a read of the row that won (gap C4).
        UniqueConstraint("work_order_id", "artefact_id", name="uq_source_document_per_artefact"),
        CheckConstraint(
            "publication_date_confidence IS NULL"
            " OR (publication_date_confidence >= 0 AND publication_date_confidence <= 1)",
            name="publication_date_confidence_is_a_probability",
        ),
        # A quarantine with no reason is a flag nobody can act on, and a reason with no
        # quarantine is a note that does not do anything. Neither state is meaningful, so
        # the database refuses both.
        CheckConstraint(
            "(quarantined AND quarantine_reason IS NOT NULL)"
            " OR (NOT quarantined AND quarantine_reason IS NULL)",
            name="quarantine_has_a_reason",
        ),
        Index(
            "ix_source_documents_work_order_id_publication_date",
            "work_order_id",
            "publication_date",
        ),
        Index("ix_source_documents_artefact_id", "artefact_id"),
        # The shape every source listing now asks for: this run's documents, narrowed to
        # the subject and the issuer-less ones (ADR 0061).
        Index("ix_source_documents_work_order_id_company_id", "work_order_id", "company_id"),
        # A flag with no findings, or findings with no flag, would be a record nobody could
        # act on: the page shows the passages, and the badge is what sends a reviewer to them.
        CheckConstraint(
            # One direction only since migration 0047 (polish P9): a flag with no
            # findings is a badge nobody can act on, but findings no longer force the
            # flag — informational rows (inline XBRL's own hidden facts) are recorded
            # for the reviewer without lighting it.
            "NOT injection_flagged OR (injection_findings IS NOT NULL"
            " AND jsonb_array_length(injection_findings) > 0)",
            name="ck_source_documents_flagged_has_findings",
        ),
        # An override is a person, a reason and a time, or none of the three. Two out of three
        # is a record nobody can read: a reason with no author names no one accountable, and an
        # author with no reason records a click.
        CheckConstraint(
            "(admissibility_override_by_id IS NULL) = (admissibility_override_reason IS NULL)"
            " AND (admissibility_override_by_id IS NULL) = (admissibility_overridden_at IS NULL)",
            name="ck_source_documents_override_is_whole",
        ),
        CheckConstraint(
            "admissibility_override_reason IS NULL"
            " OR char_length(admissibility_override_reason) > 0",
            name="ck_source_documents_override_reason_is_present",
        ),
        # Nothing to override on a source that was never refused. Permitting it would put a
        # justification in the record implying a doubt the evidence does not support.
        CheckConstraint(
            "admissibility_override_reason IS NULL OR quarantined",
            name="ck_source_documents_override_needs_a_quarantine",
        ),
        Index("ix_source_documents_job_id", "job_id"),
        # Partial: quarantined sources are the minority and are always wanted as a set
        # ("what did this run refuse to use, and why?"), so indexing only those rows keeps
        # the index small enough to stay in cache.
        Index(
            "ix_source_documents_quarantined",
            "work_order_id",
            postgresql_where=text("quarantined"),
        ),
    )

    def __repr__(self) -> str:
        return f"<SourceDocument {self.provider.value} {self.url[:60]!r} tier={self.source_tier}>"
