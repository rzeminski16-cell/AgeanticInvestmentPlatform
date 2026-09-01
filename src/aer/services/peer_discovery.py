"""Turning proposed tickers into a peer set a person can confirm.

ADR 0059, as amended. `aer.agents.peers` names comparable companies; this module decides
which of those names are real and says plainly what it refused. It fetches nothing.

**A ticker is a claim, and the registry answers it.** Every proposal goes through
:meth:`~aer.sources.sec.client.SecEdgarClient.resolve_entity`, which reads EDGAR's own
ticker index. A ticker EDGAR does not carry, or cannot disambiguate, is refused by name —
so a hallucinated company costs one lookup against a file the run had already downloaded,
and appears in the step's refusals rather than at the gate. The subject is refused as its
own peer by CIK, because a model asked for comparables will sometimes name the company it
was asked about, and a string comparison on the ticker would miss a second listing of it.

**A resolved peer is recorded, not acquired.** The first complete run fetched eight peers'
companyfacts — 26 MB and seventy-eight thousand facts — to feed a comps table that could
not be built, because every multiple needs a price series nobody subscribes to; and the
acquisition was the vector that put other issuers' facts into the subject's evidence pool
(ADR 0061). So a peer this platform already holds keeps its company id and its latest
stored period, a peer it does not is carried by its registry identifier with no period,
and the fetch returns when a price feed makes a multiple computable — behind ADR 0061's
scoping, which now exists.

**A refusal is an output, not a silence.** The alternative — dropping a bad name — makes a
proposal of six that arrives as four look like a model that proposed four.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.peers import ProposedPeer
from aer.db.models import Company, FinancialFact, ResearchRequest
from aer.errors import AerError
from aer.services.comps import MAX_PROPOSED_PEERS, PeerProposal
from aer.sources.base import ResolvedEntity

__all__ = ["DiscoveredPeers", "RefusedPeer", "discover_peers", "merged_with"]

_log = structlog.get_logger("aer.services.peer_discovery")


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
    *,
    client: Any,
    request: ResearchRequest,
    subject: Company,
    proposals: Sequence[ProposedPeer],
    limit: int = MAX_PROPOSED_PEERS,
) -> DiscoveredPeers:
    """Resolve proposed tickers into a recordable set, and name every refusal.

    Args:
        client: The SEC client. Typed loosely so a test can substitute a stub, exactly as
            :func:`aer.services.filings.acquire_filings` does.

    Nothing here raises for a peer that cannot be had — a ticker EDGAR does not know is
    recorded as a refusal and the rest continue, because a run that died for one bad
    suggestion would be a run that dies whenever the model is imperfect, which is always.
    And nothing here fetches: resolution reads a file the run already holds (ADR 0059 as
    amended).
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
            client=client,
            request=request,
            subject=subject,
            proposal=proposal,
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
    *,
    client: Any,
    request: ResearchRequest,
    subject: Company,
    proposal: ProposedPeer,
    seen: set[str],
) -> PeerProposal | RefusedPeer:
    """One proposed ticker, resolved and recorded, or refused with the reason.

    A company this platform has already researched keeps its own id and the latest stored
    period at or before the as-of date — free, and honest, because those facts exist. One
    it has not is carried by its registry identifier with no period: recording the name is
    the whole point, and inventing an alignment date for a company nothing was fetched for
    would be a fabricated comparison waiting to happen.
    """
    identified = await _identified(client=client, subject=subject, proposal=proposal, seen=seen)
    if isinstance(identified, RefusedPeer):
        return identified

    entity = identified
    company = await session.scalar(select(Company).where(Company.cik == entity.identifier))
    if company is None:
        return PeerProposal(
            identifier=entity.identifier,
            name=entity.name or proposal.name,
            rationale=proposal.rationale,
        )

    period_end = await session.scalar(
        select(func.max(FinancialFact.period_end)).where(
            FinancialFact.company_id == company.id,
            FinancialFact.period_end <= request.work_order.as_of_date,
        )
    )
    return PeerProposal(
        identifier=str(company.id),
        name=company.name,
        rationale=proposal.rationale,
        period_end=period_end,
    )
