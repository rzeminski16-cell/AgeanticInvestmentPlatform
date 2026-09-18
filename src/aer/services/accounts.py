"""Reading a UK company's figures out of the accounts the run already stored (ADR 0121).

**A US run reads its numbers from one document and its prose from others. A UK run reads
both out of the same four.** Companies House publishes no aggregate, so the accounts filings
:func:`~aer.services.filings.acquire_accounts` fetched are the whole of the fact base — and
each is inline XBRL, so every figure the company tagged is already inside an artefact this
run has hashed and stored.

This module is that reader, and it exists for the reason :mod:`aer.services.segments` does:
a parse of a whole annual report belongs beside the other services rather than inside a
workflow step, and one document must not be parsed twice to answer two questions about it.
Each artefact is read back by hash, parsed once, and what it held comes back split the way
the platform stores it — the consolidated figures, which go through retagging and
latest-filing selection exactly as EDGAR's aggregate does, and the single-axis dimensioned
ones, which are the segment breakdown and are stored as they stand.

**Nothing here writes, and nothing here selects.** The extract step owns the transaction and
owns ADR 0113's rule about which filing's word on a period wins; what needs one answer is the
parse and the join, and that is what lives here.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.schemas.facts import RawFact
from aer.db.models import SourceDocument
from aer.errors import AerError
from aer.extract.ixbrl import IxbrlFact, extract_ixbrl
from aer.sources.base import raw_fact_from_ixbrl
from aer.sources.sec.companyfacts import UnmappedConcept
from aer.storage.protocol import ArtefactStore

__all__ = ["AccountsDocument", "AccountsRead", "read_accounts"]

_log = structlog.get_logger("aer.services.accounts")


@dataclass(frozen=True, slots=True)
class AccountsDocument:
    """One accounts filing, parsed, beside the row it was recorded as."""

    document: SourceDocument

    # The register's identifier for the filing, carried because it is what joins a selected
    # fact back to the document that stated it — and a document that stated no facts at all
    # still has to be nameable.
    accession: str = ""

    # The digest of the bytes that were parsed. Carried rather than reached for through the
    # document row's artefact, so a caller that wants to read the same bytes again — to
    # locate a figure in them — needs no second query and no lazily-loaded relationship.
    sha256: str = ""

    facts: tuple[RawFact, ...] = ()
    segment_facts: tuple[RawFact, ...] = ()

    # Every dimensioned cell the document tagged, the cross-tabs refused above included.
    # Counted rather than dropped in silence, exactly as the segment sweep counts them:
    # "no segment facts" and "segments this platform has no consumer for" are different
    # answers to why an exhibit is empty.
    dimensioned_seen: int = 0
    segment_unmapped: int = 0


@dataclass(frozen=True, slots=True)
class AccountsRead:
    """What the run's accounts documents held, and what could not be read."""

    documents: tuple[AccountsDocument, ...] = ()

    # Tags that produced facts and reached no canonical concept, aggregated across the
    # documents. One list, because an operator confirming an extension at the gate is
    # deciding about the element rather than about each year that used it.
    unmapped: tuple[UnmappedConcept, ...] = ()

    # What arelle complained about. Kept because a filing that fails XBRL validation may
    # still yield usable facts, and the person deciding whether to trust them should see
    # the complaints rather than a boolean.
    load_errors: tuple[str, ...] = ()

    # Why a document contributed nothing, in a sentence. A year that will not parse costs
    # its own facts and leaves the others standing.
    notes: tuple[str, ...] = ()

    @property
    def facts(self) -> tuple[RawFact, ...]:
        """Every consolidated figure across every document, unfiltered and unselected."""
        return tuple(fact for document in self.documents for fact in document.facts)


async def read_accounts(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    filings: Sequence[Mapping[str, Any]],
) -> AccountsRead:
    """Parse the accounts documents the acquire step recorded.

    Args:
        filings: The acquire step's ``filings`` output entries — each names its form, its
            transaction identifier, its artefact digest and its source document row, which
            is everything needed to read the right bytes and record provenance.

    Documents are read newest-first, as the register listed them, and the order is carried
    through: it is what an operator sees when the run says which years it read.
    """
    parsed: list[AccountsDocument] = []
    unmapped: list[IxbrlFact] = []
    load_errors: list[str] = []
    notes: list[str] = []

    for entry in filings:
        outcome = await _read_one(session, store, entry=entry)
        if isinstance(outcome, str):
            notes.append(outcome)
            continue
        document, unplaced, complaints = outcome
        parsed.append(document)
        unmapped.extend(unplaced)
        load_errors.extend(complaints)

    _log.info(
        "accounts.read",
        documents=len(parsed),
        facts=sum(len(document.facts) for document in parsed),
        segment_facts=sum(len(document.segment_facts) for document in parsed),
        unreadable=len(notes),
    )
    return AccountsRead(
        documents=tuple(parsed),
        unmapped=_unmapped_concepts(unmapped),
        load_errors=tuple(dict.fromkeys(load_errors)),
        notes=tuple(notes),
    )


async def _read_one(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    entry: Mapping[str, Any],
) -> tuple[AccountsDocument, list[IxbrlFact], tuple[str, ...]] | str:
    """One accounts document parsed. Returns the reason instead when nothing could be read."""
    form = str(entry.get("form") or "accounts")
    accession = str(entry.get("accession") or "unstated")
    label = f"{form} {accession}".strip()

    document_id = _uuid_of(entry.get("source_document_id"))
    document = await session.get(SourceDocument, document_id) if document_id else None
    if document is None:
        return f"{label}: the acquire step's source document row is missing."

    filed = document.publication_date
    if filed is None:  # pragma: no cover -- the register dates every filing it serves
        return f"{label}: the filing carries no publication date."

    sha256 = str(entry.get("artefact_sha256") or "")
    try:
        payload = await store.read(sha256)
        extraction = extract_ixbrl(payload)
    except AerError as unreadable:
        return f"{label}: {unreadable.message}"

    consolidated: list[RawFact] = []
    segments: list[RawFact] = []
    dimensioned = 0
    segment_unmapped = 0

    for fact in extraction.facts:
        joined = _join(fact, form=form, accession=accession, filed=filed)
        if not fact.is_dimensioned:
            # Mapped or not. An unplaced tag comes through carrying its own tag as the
            # concept, exactly as the EDGAR parser returns one, so the confirmation gate
            # sees what a UK filer extended the taxonomy with.
            if joined is not None:
                consolidated.append(joined)
            continue
        dimensioned += 1
        if joined is None:
            # A cross-tab cell — segment *by* geography — which no row could state without
            # misstating what the number measures.
            continue
        if fact.concept is None:
            segment_unmapped += 1
            continue
        segments.append(joined)

    return (
        AccountsDocument(
            document=document,
            accession=accession,
            sha256=sha256,
            facts=tuple(consolidated),
            segment_facts=tuple(segments),
            dimensioned_seen=dimensioned,
            segment_unmapped=segment_unmapped,
        ),
        [fact for fact in extraction.facts if not fact.is_dimensioned and not fact.is_mapped],
        extraction.load_errors,
    )


def _join(fact: IxbrlFact, *, form: str, accession: str, filed: date) -> RawFact | None:
    """The document's fact joined to the filing that carried it.

    :func:`~aer.sources.base.raw_fact_from_ixbrl`, which is where that join lives because
    three readers make it and a second implementation would be a second answer to which
    period a company reported.
    """
    return raw_fact_from_ixbrl(fact, form=form, accession=accession, filed_date=filed)


def _unmapped_concepts(facts: Sequence[IxbrlFact]) -> tuple[UnmappedConcept, ...]:
    """The unplaced tags, one entry each, with how often they were used.

    **No label, and deliberately empty rather than filled with the tag.** EDGAR's aggregate
    states a human label beside each element; an inline document's fact stream does not, and
    the tag itself is already the row's first column. Repeating it as its own explanation
    would teach a reader that the label column says nothing.
    """
    by_qname: dict[str, list[IxbrlFact]] = defaultdict(list)
    for fact in facts:
        by_qname[fact.qname].append(fact)

    return tuple(
        UnmappedConcept(
            taxonomy=used[0].taxonomy,
            tag=used[0].tag,
            label="",
            units=tuple(sorted({fact.unit for fact in used})),
            observations=len(used),
            refusal=used[0].refusal,
        )
        for _, used in sorted(by_qname.items())
    )


def _uuid_of(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None
