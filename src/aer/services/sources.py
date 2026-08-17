"""Recording provenance, and refusing what may not be used.

A source document says where a set of bytes came from and what may be done with it. This
module writes those records and applies the admissibility rules that can be decided at
acquisition time. Two of the three are the defence against look-ahead bias (threat T13):

1. **A source whose publication date is unknown is quarantined**, because a document that
   cannot be dated cannot be shown to have existed before the as-of date. It might be from
   last week; it might be from after the quarter being analysed, in which case a report
   citing it would quietly use information nobody had at the time.
2. **A source published after the as-of date is quarantined.** The same failure, except
   demonstrated rather than merely possible.
3. **A source at a tier that may never be cited is quarantined**, whatever its date.

The date checked is the **latest** any evidence supports, not the best estimate. The question
is not when a document was probably published but whether it can be shown to predate the as-of
date, and one with any evidence of being newer cannot. See :mod:`aer.extract.dates`.

**This is one of two checks, not the only one.** The same rule runs again in
:mod:`aer.verify.citations` when a claim is made, because acquisition cannot know what a claim
will later rest on and cannot see an as-of date that moves afterwards.

The document is **kept**, not discarded. Losing it would erase the record of what the run
looked at, and "we saw this and refused to use it" is a more useful audit trail than
silence. It is flagged instead, so nothing downstream can cite it by accident — and a person
who disagrees can record an override against it, which never clears the flag.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Provider, SourceTier
from aer.db.models import Artefact, AuditEvent, ResearchRequest, SourceDocument, User
from aer.db.models.source_document import NO_PUBLICATION_DATE
from aer.errors import ConflictError, ValidationError
from aer.extract.dates import PublicationDate

__all__ = [
    "NOT_CITABLE",
    "NO_PUBLICATION_DATE",
    "PUBLISHED_AFTER_AS_OF",
    "QuarantineDecision",
    "decide_quarantine",
    "override_admissibility",
    "record_source_document",
]

_log = structlog.get_logger("aer.services.sources")

NOT_CITABLE = "tier_not_citable"
"""Quarantine reason for a tier that may never be cited as evidence."""

PUBLISHED_AFTER_AS_OF = "published_after_as_of_date"
"""Quarantine reason for a source that did not exist when the research is dated.

The look-ahead rule proper. An undatable source is refused because it *might* be too new; this
one is refused because it demonstrably is.
"""


@dataclass(frozen=True, slots=True)
class QuarantineDecision:
    """Whether a source is admissible, and why not if it is not."""

    quarantined: bool
    reason: str | None = None


def decide_quarantine(
    *,
    publication_date: date | None,
    point_in_time: bool,
    source_tier: SourceTier,
    as_of_date: date | None = None,
) -> QuarantineDecision:
    """Decide admissibility from the facts alone.

    A pure function, separate from the write, so the rule can be tested exhaustively
    without a database and read without tracing through a service call.

    Args:
        publication_date: For a document with several date candidates, pass the **latest** of
            them rather than the best estimate. The question here is not when the document was
            probably published but whether it can be shown to predate ``as_of_date``, and a
            document with any evidence of being newer cannot. See :mod:`aer.extract.dates`.
        as_of_date: The request's as-of date. ``None`` skips the look-ahead check, which is
            correct only where the caller has no as-of date to check against.

    Order matters. An undatable source is quarantined for *that* reason first, because it
    is the reason the operator can act on — supplying a date makes it admissible, whereas
    a tier-6 source is inadmissible whatever its date.
    """
    if point_in_time and publication_date is None:
        return QuarantineDecision(
            quarantined=True,
            reason=NO_PUBLICATION_DATE,
        )
    # The look-ahead rule proper, and the one this whole module is named for. Checked here at
    # acquisition and again at claim time in `aer.verify.citations`, because the two moments
    # know different things: this one cannot know what a claim will later rest on, and that one
    # cannot un-fetch a document.
    if (
        point_in_time
        and publication_date is not None
        and as_of_date is not None
        and publication_date > as_of_date
    ):
        return QuarantineDecision(quarantined=True, reason=PUBLISHED_AFTER_AS_OF)
    if not source_tier.is_citable:
        return QuarantineDecision(quarantined=True, reason=NOT_CITABLE)
    return QuarantineDecision(quarantined=False)


async def record_source_document(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    artefact: Artefact,
    url: str,
    provider: Provider,
    source_tier: SourceTier,
    retrieved_at: datetime | None = None,
    canonical_url: str | None = None,
    title: str | None = None,
    publisher: str | None = None,
    publication_date: date | None = None,
    publication_date_confidence: float | None = None,
    published: PublicationDate | None = None,
    http_status: int | None = None,
    licence_note: str | None = None,
    robots_allowed: bool | None = None,
    job_id: uuid.UUID | None = None,
) -> SourceDocument:
    """Record where an artefact came from, applying the admissibility rules.

    ``retrieved_at`` defaults to now. It is a parameter rather than always the clock so
    that a replayed or backfilled acquisition can record when it actually happened rather
    than when it was written down.

    Args:
        published: The whole result from :func:`aer.extract.dates.extract_publication_date`,
            which is what a caller that extracted a date should pass. It fills the date, the
            confidence, the winning evidence and every losing candidate, and — the part that
            matters — the admissibility decision is then made on
            :attr:`~aer.extract.dates.PublicationDate.latest` rather than on the best estimate.
        publication_date: A bare date, for callers that have one from somewhere other than the
            extractor — an adapter with an authoritative filing date, or a test. Ignored when
            ``published`` is given, because the richer value already carries it.

    Raises:
        ValidationError: If ``retrieved_at`` is naive. A provenance timestamp without a
            timezone is ambiguous by up to a day, which is exactly the precision a
            point-in-time decision depends on.
    """
    moment = retrieved_at or datetime.now(UTC)
    if moment.tzinfo is None:
        message = (
            "retrieved_at must be timezone-aware. A provenance timestamp without an "
            "offset cannot be compared against an as-of date without guessing."
        )
        raise ValidationError(message, context={"url": url})

    chosen = published.value if published is not None else publication_date
    confidence = published.confidence if published is not None else publication_date_confidence
    # The conservative bound, and what admissibility turns on. Falls back to the estimate when
    # there is only one date, which is the same value.
    latest = published.latest if published is not None else chosen

    decision = decide_quarantine(
        publication_date=latest,
        point_in_time=request.point_in_time,
        source_tier=source_tier,
        as_of_date=request.as_of_date,
    )

    document = SourceDocument(
        request_id=request.id,
        job_id=job_id,
        artefact_id=artefact.id,
        url=url,
        canonical_url=canonical_url,
        title=title,
        publisher=publisher,
        provider=provider,
        source_tier=source_tier,
        publication_date=chosen,
        publication_date_confidence=confidence,
        publication_date_latest=latest,
        publication_date_source=(published.chosen.evidence.value if published else None),
        publication_date_candidates=(
            [
                {
                    "date": candidate.value.isoformat(),
                    "evidence": candidate.evidence.value,
                    "raw": candidate.raw,
                }
                for candidate in published.candidates
            ]
            if published
            else None
        ),
        retrieved_at=moment.astimezone(UTC),
        http_status=http_status,
        licence_note=licence_note,
        robots_allowed=robots_allowed,
        quarantined=decision.quarantined,
        quarantine_reason=decision.reason,
    )
    try:
        # A savepoint, so the constraint violation below leaves the caller's transaction
        # usable rather than poisoned.
        async with session.begin_nested():
            session.add(document)
            await session.flush()
    except IntegrityError:
        # The race the A43 pre-read cannot see: another session recorded this artefact
        # for this request between our read and our write. The constraint is the arbiter;
        # losing it means the row exists, so the answer is that row — same request, same
        # bytes, one record (uq_source_document_per_artefact). Any other integrity
        # failure has no such row and propagates unchanged.
        held = await session.scalar(
            select(SourceDocument).where(
                SourceDocument.request_id == request.id,
                SourceDocument.artefact_id == artefact.id,
            )
        )
        if held is None:
            raise
        _log.info(
            "source.already_recorded",
            source_document_id=str(held.id),
            url=url,
            provider=provider.value,
        )
        return held

    if decision.quarantined:
        # Audited, not merely logged. "What did this run refuse to use, and why?" is a
        # question a reviewer will ask about a report, and the answer has to survive the
        # process that decided it.
        previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
        session.add(
            AuditEvent.create_linked(
                actor="system",
                event_type="source.quarantined",
                payload={
                    "source_document_id": str(document.id),
                    "url": url,
                    "provider": provider.value,
                    "source_tier": source_tier.value,
                    "reason": decision.reason,
                },
                previous=previous,
                request_id=request.id,
                job_id=job_id,
            )
        )
        await session.flush()

    _log.info(
        "source.recorded",
        source_document_id=str(document.id),
        provider=provider.value,
        source_tier=source_tier.value,
        quarantined=decision.quarantined,
        quarantine_reason=decision.reason,
    )
    return document


async def override_admissibility(
    session: AsyncSession,
    *,
    source: SourceDocument,
    actor: User,
    reason: str,
) -> SourceDocument:
    """Use a quarantined source anyway, on the record.

    **This does not clear the quarantine.** The row goes on saying the document could not be
    dated, or was published too late, and now also says who decided to proceed and why. Both
    facts belong in the output; collapsing them would let an override read downstream as though
    the document had passed.

    Raises:
        ValidationError: The reason is empty. An override with no justification records a click.
        ConflictError: The source was never quarantined, so there is nothing to override, and
            recording a reason against it would imply a doubt the evidence does not support.
    """
    if not reason.strip():
        message = "An override needs a written reason. Without one it records a click."
        raise ValidationError(message, context={"source_document_id": str(source.id)})

    if not source.quarantined:
        message = (
            "This source is not quarantined, so there is nothing to override. Recording a "
            "reason against it would imply a doubt the evidence does not support."
        )
        raise ConflictError(message, context={"source_document_id": str(source.id)})

    source.admissibility_override_by_id = actor.id
    source.admissibility_override_reason = reason.strip()
    source.admissibility_overridden_at = datetime.now(UTC)

    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor.email,
            event_type="source.admissibility_overridden",
            payload={
                "source_document_id": str(source.id),
                "url": source.url,
                "quarantine_reason": source.quarantine_reason,
                "reason": source.admissibility_override_reason,
            },
            previous=previous,
            request_id=source.request_id,
            job_id=source.job_id,
        )
    )
    await session.flush()

    _log.warning(
        "source.admissibility_overridden",
        source_document_id=str(source.id),
        quarantine_reason=source.quarantine_reason,
        actor=actor.email,
    )
    return source


async def list_quarantined(session: AsyncSession, *, request_id: uuid.UUID) -> list[SourceDocument]:
    """Every source this request gathered but may not cite."""
    result = await session.scalars(
        select(SourceDocument)
        .where(SourceDocument.request_id == request_id, SourceDocument.quarantined)
        .order_by(SourceDocument.created_at)
    )
    return list(result.all())
