"""From a fetch to a provenance record.

:class:`~aer.fetch.client.SafeFetcher` archives bytes and returns a description of what
happened. :mod:`aer.services.artefacts` and :mod:`aer.services.sources` write the database
rows. This module is the join between them, and it exists so that the rule "one fetch
produces exactly one artefact row and exactly one source document" lives in one place
rather than being re-implemented by each adapter that acquires something.

**The bytes are not stored twice.** The fetcher has already written them to the artefact
store, and the store is content-addressed, so writing them again would be a no-op that
costs a full hash and a disk round-trip. The artefact row is created from the digest the
fetcher already computed.

**Failures are recorded, not raised past.** A 404 or a 500 produces a source document like
any other, carrying the status. A run that fetched nothing and a run whose every fetch
returned 403 are very different situations, and only one of them is a network problem —
telling them apart later requires that both left a record.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Provider, SourceTier
from aer.db.models import Artefact, ResearchRequest, SourceDocument
from aer.extract.dates import PublicationDate
from aer.fetch.client import FetchResult
from aer.services.artefacts import ArtefactRecord, record_fetched_artefact
from aer.services.sources import record_source_document
from aer.storage.protocol import ArtefactStore

__all__ = ["Acquisition", "record_acquisition"]

_log = structlog.get_logger("aer.services.acquisition")


class Acquisition:
    """What one fetch produced: the artefact row and the provenance row."""

    __slots__ = ("artefact_record", "source_document")

    def __init__(self, artefact_record: ArtefactRecord, source_document: SourceDocument) -> None:
        self.artefact_record = artefact_record
        self.source_document = source_document

    @property
    def artefact(self) -> Artefact:
        return self.artefact_record.artefact

    @property
    def sha256(self) -> str:
        return self.artefact_record.sha256

    @property
    def quarantined(self) -> bool:
        return self.source_document.quarantined


async def record_acquisition(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    request: ResearchRequest,
    result: FetchResult,
    provider: Provider,
    source_tier: SourceTier,
    publication_date: date | None = None,
    publication_date_confidence: float | None = None,
    published: PublicationDate | None = None,
    title: str | None = None,
    publisher: str | None = None,
    retrieved_at: datetime | None = None,
    job_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
) -> Acquisition:
    """Record the artefact and the provenance for one completed fetch.

    Args:
        request: The request this was gathered for. Supplies the point-in-time setting
            that decides admissibility.
        company_id: Which issuer the document is about, passed straight through to
            :func:`~aer.services.sources.record_source_document`. See ADR 0061: a request
            can hold documents about more than one company, so the document has to say
            which, and "none" is a legitimate answer for anything that is not about an
            issuer at all.
        result: What the fetcher returned, successful or not.
        publication_date: When the document was published, where that is knowable.
            ``None`` for a generated aggregate such as an API index — and under
            point-in-time rules a document with no date is quarantined, which is the
            correct outcome for something that is not a published document at all.
        published: The extractor's whole conclusion, for a caller that derived the date
            rather than being handed one — passed through to
            :func:`~aer.services.sources.record_source_document`, whose admissibility
            decision then reads the conservative ``latest`` rather than the estimate.

    Returns:
        Both rows, so a caller can link facts to the source document without a second
        query.
    """
    artefact_record = await record_fetched_artefact(
        session,
        store,
        sha256=result.sha256,
        size_bytes=result.size_bytes,
        media_type=result.media_type,
    )

    document = await record_source_document(
        session,
        request=request,
        artefact=artefact_record.artefact,
        url=result.url,
        provider=provider,
        source_tier=source_tier,
        retrieved_at=retrieved_at,
        # The URL after redirects, kept alongside the one that was asked for. A licence or
        # robots question is answered against the request; a duplicate check is answered
        # against the destination.
        canonical_url=result.final_url if result.final_url != result.url else None,
        title=title,
        publisher=publisher,
        publication_date=publication_date,
        publication_date_confidence=publication_date_confidence,
        published=published,
        http_status=result.status_code,
        licence_note=result.licence_note or None,
        robots_allowed=result.robots_allowed,
        job_id=job_id,
        company_id=company_id,
    )

    _log.info(
        "acquisition.recorded",
        url=result.url,
        provider=provider.value,
        status=result.status_code,
        sha256=result.sha256,
        artefact_was_new=artefact_record.was_new,
        quarantined=document.quarantined,
    )
    return Acquisition(artefact_record=artefact_record, source_document=document)
