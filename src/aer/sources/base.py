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
  published. The date is what makes point-in-time filtering possible, so an adapter that
  cannot supply it cannot support point-in-time research.
* **Extract** — parse documents into typed facts.

Notably absent: anything that decides whether a number is *good*. An adapter reports what
the publisher said. Weighing two publishers who disagree is a tier comparison, and it
happens above this layer where both answers are visible at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from aer.core.enums import Provider, SourceTier
from aer.core.schemas.facts import RawFact

__all__ = ["DocumentRef", "ResolvedEntity", "SourceAdapter"]


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

    ``publication_date`` is not optional. A document with no date cannot be shown to
    predate an as-of date, so under point-in-time rules it is inadmissible — and an
    adapter that returned undated references would push that decision downstream to a
    place with less information about it.
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
        as_of_date: date | None = None,
        forms: frozenset[str] | None = None,
    ) -> tuple[DocumentRef, ...]:
        """List documents for an entity, newest first.

        ``as_of_date`` filters at acquisition rather than afterwards: a document published
        after the as-of date is not fetched at all under point-in-time rules, so it cannot
        leak into a run through a later code path that forgot to check.
        """
        ...

    async def fetch_facts(
        self, entity: ResolvedEntity, *, as_of_date: date | None = None
    ) -> tuple[RawFact, ...]:
        """Return every fact this publisher holds for the entity.

        Unfiltered by concept, and **unfiltered by point-in-time** — selection happens in
        :mod:`aer.sources.sec.pit`, on the full set, so the facts that were rejected and
        the reason for each are recoverable. An adapter that filtered here would leave no
        trace of what it discarded.
        """
        ...
