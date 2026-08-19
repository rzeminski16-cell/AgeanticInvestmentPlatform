"""Reading the segment breakdown out of the annual report the run already holds.

The live report's segment-mix exhibit rendered its placeholder, and the reason ran deeper
than a missing query: the companyfacts aggregate carries only consolidated figures, the
fact schema could not hold a dimensioned observation, and nothing read the one document
that states the breakdown — the annual report itself, which on EDGAR is inline XBRL and
tags every segment's revenue with the axis and member that name it.

This module is the missing reader. The annual report's artefact is read back by hash,
:func:`aer.extract.ixbrl.extract_ixbrl` — offline, as always — yields the tagged facts
with their dimensional contexts, and the single-axis facts whose tags map to canonical
concepts are persisted as ordinary fact rows carrying their dimension. Everything that
wants *the* number for a period filters those rows out; the segment exhibit and any
future segment analysis are what they exist for.

**The unmapped tags from this sweep do not reach the confirmation gate.** The gate exists
for the case where the *statement lines themselves* hang on a filer extension; here the
statements already came from the aggregate, and this sweep is supplementary — a segment
tagged with an invented element is dropped and counted, exactly as the aggregate's
unmapped tags are counted, rather than stopping the run to ask a person about a breakdown
it can do without.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.dates import fiscal_year_of
from aer.core.schemas.facts import RawFact
from aer.db.models import Company, SourceDocument
from aer.errors import AerError
from aer.extract.ixbrl import IxbrlFact, extract_ixbrl
from aer.services.facts import persist_facts
from aer.sources.sec.submissions import ANNUAL_FORMS
from aer.storage.protocol import ArtefactStore

__all__ = ["SegmentSweep", "sweep_segment_facts"]

_log = structlog.get_logger("aer.services.segments")

# A duration this far from a year is not a fiscal year. The window is generous because
# 52/53-week fiscal calendars and transition periods both move the count, and the point
# is only to tell an annual figure from a quarterly one inside an annual report.
_FY_DAYS_LOW: Final = 330
_FY_DAYS_HIGH: Final = 400


@dataclass(frozen=True, slots=True)
class SegmentSweep:
    """What the sweep read, wrote and could not use."""

    facts_written: int = 0
    facts_seen: int = 0
    unmapped_tags: int = 0
    notes: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        # Named so nothing here merges into the extract step's `unmapped_tags`, which
        # feeds the confirmation gate — see the module docstring for why it must not.
        return {
            "segment_facts_written": self.facts_written,
            "segment_facts_seen": self.facts_seen,
            "segment_unmapped_tags": self.unmapped_tags,
            "segment_notes": list(self.notes),
        }


async def sweep_segment_facts(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    company: Company,
    filings: list[dict[str, Any]],
) -> SegmentSweep:
    """Extract and persist the dimensioned facts from the run's annual report.

    Args:
        filings: The acquire step's ``filings`` output entries — each names its form,
            accession, artefact digest and source document, which is everything needed to
            read the right bytes and record provenance.

    Annual reports only. The segment exhibit is an annual picture, quarterly segment rows
    would sit unread, and each document is an arelle parse this step pays for in latency.
    Nothing here fails the run: a report that will not parse, or that tags no dimensions,
    costs its segment facts and is said so in ``notes``.
    """
    annuals = [
        entry
        for entry in filings
        if str(entry.get("form", "")) in ANNUAL_FORMS and entry.get("artefact_sha256")
    ]
    if not annuals:
        return SegmentSweep(notes=("No annual report was acquired, so no segment sweep ran.",))

    written = 0
    seen = 0
    unmapped = 0
    notes: list[str] = []

    for entry in annuals:
        outcome = await _sweep_one(session, store, company=company, entry=entry)
        if isinstance(outcome, str):
            notes.append(outcome)
            continue
        wrote, saw, unknown = outcome
        written += wrote
        seen += saw
        unmapped += unknown

    _log.info(
        "segments.swept",
        company_id=str(company.id),
        documents=len(annuals),
        facts_written=written,
        facts_seen=seen,
        unmapped_tags=unmapped,
    )
    return SegmentSweep(
        facts_written=written, facts_seen=seen, unmapped_tags=unmapped, notes=tuple(notes)
    )


async def _sweep_one(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    company: Company,
    entry: dict[str, Any],
) -> tuple[int, int, int] | str:
    """One annual report swept. Returns the reason instead when nothing could be read."""
    form = str(entry["form"])
    label = f"{form} {entry.get('accession', '')}".strip()

    document_id = _uuid_of(entry.get("source_document_id"))
    document = await session.get(SourceDocument, document_id) if document_id else None
    if document is None:
        return f"{label}: the acquire step's source document row is missing."

    filed = document.publication_date
    if filed is None:  # pragma: no cover -- the sweep dates every filing it records
        return f"{label}: the filing carries no publication date."

    try:
        payload = await store.read(str(entry["artefact_sha256"]))
        extraction = extract_ixbrl(payload)
    except AerError as unreadable:
        # An annual report that is not inline XBRL, or that arelle cannot load, costs its
        # segment facts and nothing else — the excerpts and the aggregate's figures are
        # already recorded through their own paths.
        return f"{label}: {unreadable.message}"

    dimensioned = [fact for fact in extraction.facts if fact.is_dimensioned]
    usable: list[RawFact] = []
    unknown = 0
    for fact in dimensioned:
        if len(fact.dimensions) != 1:
            # A two-axis cell — segment by geography, say — is a cross-tab the platform
            # has no consumer for, and a row stating only one of its axes would misstate
            # what the number measures.
            continue
        if not _entity_matches(fact, cik=company.cik):
            continue
        concept = fact.concept
        if concept is None:
            unknown += 1
            continue
        usable.append(_raw_fact(fact, concept=concept, form=form, entry=entry, filed=filed))

    if not usable:
        return (0, len(dimensioned), unknown) if dimensioned else f"{label}: no dimensioned facts."

    inserted = await persist_facts(session, company=company, source_document=document, facts=usable)
    return inserted, len(dimensioned), unknown


def _raw_fact(
    fact: IxbrlFact, *, concept: str, form: str, entry: dict[str, Any], filed: date
) -> RawFact:
    (axis, member) = fact.dimensions[0]
    return RawFact(
        concept=concept,
        raw_concept=fact.tag,
        taxonomy=fact.taxonomy,
        unit=fact.unit,
        value=fact.value,
        period_start=fact.period_start,
        period_end=fact.period_end,
        fiscal_year=_fiscal_year(fact),
        fiscal_period=_fiscal_period(fact),
        dimension_axis=axis,
        dimension_member=member,
        form=form,
        accession=str(entry.get("accession") or "unstated"),
        filed_date=filed,
    )


def _fiscal_period(fact: IxbrlFact) -> str | None:
    """``FY`` for a duration the length of a year, ``None`` for anything else.

    Derived from the span because an inline document does not state a fiscal period the
    way the frames API does. An annual report's comparatives are year-long durations too,
    so the prior years' segment figures label themselves the same way. A quarter inside
    an annual report — some filers tag one — stays unlabelled rather than guessed.
    """
    if fact.period_start is None:
        return None
    days = (fact.period_end - fact.period_start).days
    return "FY" if _FY_DAYS_LOW <= days <= _FY_DAYS_HIGH else None


def _fiscal_year(fact: IxbrlFact) -> int | None:
    """The year the period belongs to, for a fiscal-year duration.

    This module derived the rule first — "a year ending September 2025 is FY2025" — and
    ADR 0062 promoted it to :func:`aer.core.dates.fiscal_year_of`, which adds the
    early-January carve-out for 52/53-week calendars this local version lacked. Only
    stated where the period is a fiscal year at all.
    """
    return fiscal_year_of(fact.period_end) if _fiscal_period(fact) == "FY" else None


def _entity_matches(fact: IxbrlFact, *, cik: str | None) -> bool:
    """Whether the fact names the company this run researches, where it names anyone.

    EDGAR contexts identify the entity by CIK. A fact naming a different registrant —
    a subsidiary's own filing pasted in, a typo — must not become this company's row; a
    fact naming nobody is kept, because the document as a whole was already fetched from
    this company's own index entry.
    """
    identifier = (fact.entity_identifier or "").strip()
    if not identifier or not cik:
        return True
    if not identifier.isdigit() or not cik.isdigit():
        return True
    return int(identifier) == int(cik)


def _uuid_of(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None
