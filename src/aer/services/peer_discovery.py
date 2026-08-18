"""Turning proposed tickers into peers this platform actually holds facts for.

ADR 0059. `aer.agents.peers` names comparable companies; this module decides which of those
names are real, acquires what a comparison needs, and says plainly what it refused.

**A ticker is a claim, and the registry answers it.** Every proposal goes through
:meth:`~aer.sources.sec.client.SecEdgarClient.resolve_entity`, which reads EDGAR's own
ticker index. A ticker EDGAR does not carry, or cannot disambiguate, is refused by name and
never fetched — so a hallucinated company costs one lookup against a file the run had
already downloaded, and appears in the step's refusals rather than at the gate. The subject
is refused as its own peer by CIK, because a model asked for comparables will sometimes
name the company it was asked about, and a string comparison on the ticker would miss a
second listing of it.

**A peer's facts come down the subject's own chain.** Fetch, hash, record provenance,
upsert the company, parse, select point-in-time, persist. Every peer figure therefore traces
to a hashed artefact and no fact published after the as-of date can support a comparison —
the same guarantees the subject's facts carry, because they are the same code.

Two steps of the subject's path are deliberately skipped, and the omission is the ADR's:

* **No excerpt location.** Peer facts feed comps arithmetic — multiples computed and
  recorded as calculations — and are never quoted in prose. There is no claim for an
  excerpt to verify, so locating one would be work whose product nothing reads.
* **No segment sweep.** Nothing presents a peer's segment breakdown, and a dimensioned
  fact that reached the store would be an observation no reader of this table wants.

**A refusal is an output, not a silence.** A peer that resolves but has no usable facts at
or before the as-of date is named with the reason. The alternative — dropping it — makes a
proposal of six that arrives as four look like a model that proposed four.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.peers import ProposedPeer
from aer.core.enums import FactBasis, Provider, SourceTier
from aer.db.models import Company, FinancialFact, ResearchRequest
from aer.errors import AerError
from aer.services.acquisition import record_acquisition
from aer.services.comps import MAX_PROPOSED_PEERS, PeerProposal
from aer.services.facts import persist_facts, upsert_company
from aer.sources.base import ResolvedEntity
from aer.sources.sec.companyfacts import parse_company_facts
from aer.sources.sec.pit import select_point_in_time
from aer.storage.protocol import ArtefactStore

__all__ = ["DiscoveredPeers", "RefusedPeer", "discover_peers", "merged_with"]

_log = structlog.get_logger("aer.services.peer_discovery")

# Same confidence the workflow puts on a publication date it derived from a document's
# contents rather than read off the document. An aggregate has no date of its own; the
# newest filing it carries is the day it could first have existed.
_DERIVED_FROM_CONTENTS = 0.9


@dataclass(frozen=True, slots=True)
class RefusedPeer:
    """A proposal that did not become a peer, and why.

    ``ticker`` and ``name`` are as the model wrote them, because that is what a reviewer
    needs to see: the point of showing a refusal is to show what was proposed.
    """

    ticker: str
    name: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"ticker": self.ticker, "name": self.name, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class DiscoveredPeers:
    """What survived resolution and acquisition, and what did not."""

    peers: tuple[PeerProposal, ...] = ()
    refused: tuple[RefusedPeer, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "peers": [peer.as_dict() for peer in self.peers],
            "refused": [item.as_dict() for item in self.refused],
        }


async def discover_peers(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    subject: Company,
    proposals: Sequence[ProposedPeer],
    job_id: uuid.UUID | None = None,
    limit: int = MAX_PROPOSED_PEERS,
) -> DiscoveredPeers:
    """Resolve proposed tickers, acquire what each peer needs, and name every refusal.

    Args:
        client: The SEC client. Typed loosely so a test can substitute a stub, exactly as
            :func:`aer.services.filings.acquire_filings` does.

    Nothing here raises for a peer that cannot be had. A ticker EDGAR does not know, a
    fetch the layer refuses, a filer with nothing filed by the as-of date — each is
    recorded as a refusal and the rest continue, because a run that died for one bad
    suggestion would be a run that dies whenever the model is imperfect, which is always.
    """
    kept: list[PeerProposal] = []
    refused: list[RefusedPeer] = []
    seen: set[str] = set()

    for proposal in proposals:
        if len(kept) >= limit:
            refused.append(
                RefusedPeer(
                    ticker=proposal.ticker,
                    name=proposal.name,
                    reason=(
                        f"Not resolved: this run already has {limit} peers, which is as "
                        "many as a reviewer can meaningfully confirm."
                    ),
                )
            )
            continue

        outcome = await _one_peer(
            session,
            store,
            client=client,
            request=request,
            subject=subject,
            proposal=proposal,
            job_id=job_id,
            seen=seen,
        )
        if isinstance(outcome, RefusedPeer):
            refused.append(outcome)
        else:
            kept.append(outcome)

    _log.info(
        "peers.discovered",
        request_id=str(request.id),
        proposed=len(proposals),
        resolved=len(kept),
        refused=len(refused),
    )
    return DiscoveredPeers(peers=tuple(kept), refused=tuple(refused))


def merged_with(
    discovered: Sequence[PeerProposal],
    floor: Sequence[PeerProposal],
    *,
    limit: int = MAX_PROPOSED_PEERS,
) -> tuple[PeerProposal, ...]:
    """The model's peers first, then any from the deterministic floor it did not name.

    Order is the answer to a real question: the two proposers write rationales of different
    quality, and a reviewer reading top-down should meet the reasoned ones first. The floor
    is kept underneath rather than discarded because a company this platform has already
    researched is one it can price without another fetch, and because a model that returns
    nothing must not leave a database that holds comparables proposing none.
    """
    merged = list(discovered)
    known = {peer.identifier for peer in merged}
    for peer in floor:
        if len(merged) >= limit:
            break
        if peer.identifier in known:
            continue
        merged.append(peer)
        known.add(peer.identifier)
    return tuple(merged)


async def _identified(
    *, client: Any, subject: Company, proposal: ProposedPeer, seen: set[str]
) -> ResolvedEntity | RefusedPeer:
    """The company this ticker names, or the reason it names no peer.

    Everything that can be decided *without fetching anything about the peer* happens here,
    which is the containment ADR 0059 rests on: a name the registry does not carry, the
    subject under another listing, and a company already proposed all stop before any
    request is made for their filings.
    """
    ticker = proposal.ticker.strip().upper()
    if not ticker:
        return RefusedPeer(
            ticker=proposal.ticker,
            name=proposal.name,
            reason="Not resolved: no ticker was given, so there is nothing to look up.",
        )

    try:
        entity: ResolvedEntity = await client.resolve_entity(ticker)
    except AerError as exc:
        return RefusedPeer(
            ticker=ticker,
            name=proposal.name,
            reason=(
                f"Not resolved: EDGAR does not list {ticker} unambiguously ({exc}). "
                "Nothing was fetched for it."
            ),
        )

    if subject.cik and entity.identifier == subject.cik:
        return RefusedPeer(
            ticker=ticker,
            name=proposal.name,
            reason=(
                f"Refused: {ticker} resolves to the subject's own CIK. A company is not "
                "comparable with itself."
            ),
        )

    if entity.identifier in seen:
        return RefusedPeer(
            ticker=ticker,
            name=proposal.name,
            reason=f"Refused: {ticker} resolves to a company already proposed for this run.",
        )

    seen.add(entity.identifier)
    return entity


async def _one_peer(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    subject: Company,
    proposal: ProposedPeer,
    job_id: uuid.UUID | None,
    seen: set[str],
) -> PeerProposal | RefusedPeer:
    """One proposed ticker, resolved and acquired, or refused with the reason."""
    identified = await _identified(client=client, subject=subject, proposal=proposal, seen=seen)
    if isinstance(identified, RefusedPeer):
        return identified

    entity = identified
    ticker = entity.ticker or proposal.ticker.strip().upper()

    company = await _acquire_peer_facts(
        session,
        store,
        client=client,
        request=request,
        entity=entity,
        job_id=job_id,
    )
    if company is None:
        return RefusedPeer(
            ticker=ticker,
            name=proposal.name,
            reason=(
                f"Refused: {ticker} resolved, but its filings could not be acquired or "
                "parsed, so there is nothing to compare against."
            ),
        )

    period_end = await session.scalar(
        select(func.max(FinancialFact.period_end)).where(
            FinancialFact.company_id == company.id,
            FinancialFact.period_end <= request.as_of_date,
        )
    )
    if period_end is None:
        return RefusedPeer(
            ticker=ticker,
            name=proposal.name,
            reason=(
                f"Refused: {ticker} has no financial facts at or before "
                f"{request.as_of_date.isoformat()}, so no period of its could be aligned "
                "against the subject's."
            ),
        )

    return PeerProposal(
        identifier=str(company.id),
        name=company.name,
        rationale=proposal.rationale,
        period_end=period_end,
    )


async def _acquire_peer_facts(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    entity: ResolvedEntity,
    job_id: uuid.UUID | None,
) -> Company | None:
    """The subject's acquisition chain, run for a peer. ``None`` if it could not be had.

    Idempotent by construction rather than by a check: the artefact store is
    content-addressed, ``upsert_company`` finds an existing row, and ``persist_facts``
    inserts on conflict do nothing. A peer already in the database from an earlier run is
    therefore re-fetched at most once per run and duplicates nothing.
    """
    # Everything about *this peer's document* is guarded together, because a fetch that
    # fails and a payload that will not parse are the same event to the caller: this peer
    # cannot be compared against, and the run carries on without it. The guard is
    # `AerError` rather than `Exception` deliberately — a database failure is not a fact
    # about the peer, and absorbing one here would turn a broken machine into a run that
    # quietly proposed fewer companies.
    try:
        response = await client.fetch_company_facts(entity.identifier)
        acquisition = await record_acquisition(
            session,
            store,
            request=request,
            job_id=job_id,
            result=response.fetch,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            title=f"{entity.name} XBRL company facts",
            publisher="US Securities and Exchange Commission",
            publication_date=response.data.latest_filed,
            publication_date_confidence=_DERIVED_FROM_CONTENTS,
        )
        parsed = parse_company_facts(await store.read(acquisition.sha256))
    except AerError as exc:
        _log.warning(
            "peers.facts_unavailable",
            cik=entity.identifier,
            error_code=getattr(exc, "code", ""),
        )
        return None

    company = await upsert_company(
        session,
        entity=entity,
        # The peer's own listing, not the subject's. `entity.ticker` is EDGAR's spelling,
        # which is the one that resolved; the exchange comes with it or is left unstated
        # rather than borrowed from the company that happens to be under research.
        ticker=entity.ticker or "",
        exchange=entity.exchange or "",
    )

    selection = select_point_in_time(parsed.facts, as_of_date=request.as_of_date)
    await persist_facts(
        session,
        company=company,
        source_document=acquisition.source_document,
        facts=selection.chosen,
        basis=FactBasis.AS_REPORTED,
    )
    return company
