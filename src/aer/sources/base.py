"""What every data-source adapter must be able to do.

A ``Protocol`` rather than a base class, for the same reason as
:class:`~aer.storage.protocol.ArtefactStore`: an adapter satisfies it by having the right
methods, so a test double needs no inheritance and a future adapter does not have to
import this module to be usable by code that expects one.

The three operations are deliberately the smallest set that supports the pipeline:

* **Resolve** — turn "MSFT on NASDAQ" into the publisher's own identifier. Nothing else
  can happen until this succeeds, and it is where a typo in a ticker becomes a clear
  failure rather than an empty result set three steps later.
* **Discover** — list what documents exist for that entity, each with the date it was
  published. The date is provenance: a document's own record of when it became public,
  which the source page prints and the evidence tier reads (an undated document is never
  primary, ADR 0111). An adapter that cannot supply it cannot say what its documents are.
* **Extract** — parse documents into typed facts.

Notably absent: anything that decides whether a number is *good*. An adapter reports what
the publisher said. Weighing two publishers who disagree is a tier comparison, and it
happens above this layer where both answers are visible at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from aer.core.dates import fiscal_year_of
from aer.core.enums import Provider, SourceTier
from aer.core.schemas.facts import RawFact

if TYPE_CHECKING:
    from aer.extract.ixbrl import IxbrlFact

__all__ = [
    "ANNUAL",
    "DocumentRef",
    "ResolvedEntity",
    "SourceAdapter",
    "raw_fact_from_ixbrl",
]

ANNUAL: Final = "FY"

# A duration this far from a year is not a fiscal year. The window is generous because
# 52/53-week fiscal calendars and transition periods both move the count, and the point
# is only to tell an annual figure from a quarterly one inside an annual report.
#
# **Moved here from `services/segments.py` unchanged.** The UK adapter needs the same rule,
# and two answers to "is this iXBRL duration a year?" would be two answers to which periods
# a company reported.
_FY_DAYS_LOW: Final = 330
_FY_DAYS_HIGH: Final = 400


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    """A company as one publisher identifies it.

    ``identifier`` is the publisher's own key — a CIK for EDGAR, a company number for
    Companies House. Deliberately a string: a CIK is zero-padded to ten characters and an
    integer cannot hold the padding, which is exactly the kind of detail that turns into a
    404 the first time it is dropped.
    """

    identifier: str
    name: str
    ticker: str | None = None
    exchange: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """A document that exists, and enough about it to decide whether to fetch it.

    ``publication_date`` is not optional. An index that names a document names the day it
    became public, and that date is what the evidence tier reads (an undated document is
    admitted and never primary, ADR 0111) — an adapter that returned undated references
    would push that judgement downstream to a place with less information about it.
    """

    url: str
    title: str
    publication_date: date
    form: str | None = None
    accession: str | None = None
    is_primary: bool = True


@runtime_checkable
class SourceAdapter(Protocol):
    """The interface every publisher adapter presents."""

    @property
    def provider(self) -> Provider:
        """Which provider this adapter fetches as. Fixes the rate limit and licence."""
        ...

    @property
    def source_tier(self) -> SourceTier:
        """How much weight documents from this adapter carry."""
        ...

    async def resolve_entity(self, ticker: str, *, exchange: str | None = None) -> ResolvedEntity:
        """Find the publisher's identifier for a listed company.

        Raises:
            ValidationError: If the ticker is not one this publisher knows about.
        """
        ...

    async def discover_documents(
        self,
        entity: ResolvedEntity,
        *,
        forms: frozenset[str] | None = None,
    ) -> tuple[DocumentRef, ...]:
        """List documents for an entity, newest first, as the publisher's index stands.

        A run reads the filings as they stand when it runs (ADR 0113): nothing here is
        bounded by a date, and the index's own order is the order returned.
        """
        ...

    async def fetch_facts(self, entity: ResolvedEntity) -> tuple[RawFact, ...]:
        """Return every fact this publisher holds for the entity.

        Unfiltered by concept and unfiltered by filing — selection happens in
        :mod:`aer.sources.sec.selection`, on the full set, so the facts that were rejected
        and the reason for each are recoverable. An adapter that filtered here would leave
        no trace of what it discarded.
        """
        ...


# -- Joining a parsed document to its filing -------------------------------------------------


def raw_fact_from_ixbrl(
    fact: IxbrlFact,
    *,
    form: str,
    accession: str,
    filed_date: date,
    concept: str | None = None,
) -> RawFact | None:
    """One inline-XBRL fact as a :class:`RawFact`, or ``None`` where it cannot be one.

    :mod:`aer.extract.ixbrl` reports only what the bytes say; the form, the filing
    identifier and the filed date come from the index that pointed at the document. This is
    that join, and it lives here because **two adapters make it** — the segment reader over a
    US annual report and the Companies House adapter over a UK one — and a second
    implementation would be a second answer to which periods a company reported.

    ``concept`` defaults to the tag's canonical concept, falling back to the tag itself, which
    is what :class:`RawFact` documents and what the EDGAR parser does. A caller that wants
    only mapped facts passes the concept it already resolved.

    Returns ``None`` for a cell with more than one explicit dimension: a cross-tab — segment
    *by* geography — is something this platform has no consumer for, and a row stating only
    one of its axes would misstate what the number measures.
    """
    if len(fact.dimensions) > 1:
        return None
    axis, member = fact.dimensions[0] if fact.dimensions else (None, None)
    period = _ixbrl_fiscal_period(fact)
    return RawFact(
        concept=concept or fact.concept or fact.tag,
        raw_concept=fact.tag,
        taxonomy=fact.taxonomy,
        unit=fact.unit,
        value=fact.value,
        period_start=fact.period_start,
        period_end=fact.period_end,
        # Stated only where the period is a fiscal year at all. ADR 0062 owns the rule that
        # a year ending September 2025 is FY2025, including the early-January carve-out for
        # 52/53-week calendars.
        fiscal_year=fiscal_year_of(fact.period_end) if period == ANNUAL else None,
        fiscal_period=period,
        dimension_axis=axis,
        dimension_member=member,
        form=form,
        accession=accession,
        filed_date=filed_date,
    )


def _ixbrl_fiscal_period(fact: IxbrlFact) -> str | None:
    """``FY`` for a duration the length of a year, ``None`` for anything else.

    Derived from the span because an inline document does not state a fiscal period the
    way the frames API does. An annual report's comparatives are year-long durations too,
    so the prior years' figures label themselves the same way. A quarter inside an annual
    report — some filers tag one — stays unlabelled rather than guessed.
    """
    if fact.period_start is None:
        return None
    days = (fact.period_end - fact.period_start).days
    return ANNUAL if _FY_DAYS_LOW <= days <= _FY_DAYS_HIGH else None
