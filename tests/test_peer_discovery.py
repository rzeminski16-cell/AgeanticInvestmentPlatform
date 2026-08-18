"""Peers a model proposed, and what code does with the names it gave.

ADR 0059. The interesting behaviour is nearly all *refusal*: a comparables table is only as
good as the set behind it, and the set now starts as text a model wrote. So what is under
test is the boundary between a proposed ticker and an acquired peer — a name the registry
does not carry never becomes a fetch, the subject is never comparable with itself, and a
company whose filings say nothing by the as-of date is named as refused rather than dropped.

The deterministic floor is tested in `test_comps_service.py` and is not retested here; what
this file adds is what happens when there are two proposers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.peers import (
    PEER_SLATE_LIMIT,
    PeerProposalAgent,
    PeerProposalInput,
    PeerSlate,
    ProposedPeer,
)
from aer.core.enums import JobStatus, UserRole
from aer.db.models import Company, FinancialFact, Job, ResearchRequest, SourceDocument, User
from aer.errors import ValidationError
from aer.services.comps import MAX_PROPOSED_PEERS, PeerProposal
from aer.services.peer_discovery import discover_peers, merged_with
from aer.sources.base import ResolvedEntity
from aer.storage.local import LocalArtefactStore
from tests.sec_fixtures import MSFT_CIK
from tests.workflow_fixtures import AS_OF_DATE, StubSecClient

pytestmark = pytest.mark.integration

SUBJECT_CIK = "0000000009"

# The fixture's filer, whose facts the stub serves. A peer resolving to this CIK gets a real
# fact set out of the real parser, which is what makes "the peer has a period end" a fact
# about acquisition rather than about the test's seeding.
PEER_CIK = MSFT_CIK


class RegistryStub(StubSecClient):
    """EDGAR's ticker index, as a dictionary, with everything else inherited.

    Subclassed rather than rewritten so the facts path stays the real one: the stub stores
    the fixture bytes in the artefact store and returns a fetch result describing them, so
    ``discover_peers`` runs the genuine record-parse-persist chain over them.
    """

    def __init__(self, store: LocalArtefactStore, *, known: dict[str, str] | None = None) -> None:
        super().__init__(store)
        self._known = known if known is not None else {"PEER": PEER_CIK}

    async def resolve_entity(self, ticker: str, *, exchange: str | None = None) -> ResolvedEntity:
        self.entity_calls.append(ticker)
        cik = self._known.get(ticker.upper())
        if cik is None:
            message = f"No EDGAR filer has the ticker {ticker!r}."
            raise ValidationError(message, context={"ticker": ticker})
        return ResolvedEntity(
            identifier=cik, name=f"{ticker.upper()} Corporation", ticker=ticker, exchange="NASDAQ"
        )


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    user = User(email="peers@example.invalid", display_name="P", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Contoso Corporation",
        ticker="CTSO",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    subject = Company(name="Contoso Corporation", ticker="CTSO", exchange="NASDAQ", cik=SUBJECT_CIK)
    db_session.add_all([request, subject])
    await db_session.flush()

    job = Job(
        request_id=request.id,
        workflow_version="test",
        code_version="abc",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    store = LocalArtefactStore(tmp_path / "artefacts", max_bytes=50_000_000)
    return {
        "session": db_session,
        "request": request,
        "subject": subject,
        "job": job,
        "store": store,
        "client": RegistryStub(store),
    }


def proposed(ticker: str, *, name: str = "", rationale: str = "Same end market.") -> ProposedPeer:
    return ProposedPeer(ticker=ticker, name=name or f"{ticker} Corporation", rationale=rationale)


async def discover(scene: dict[str, Any], *proposals: ProposedPeer, **kwargs: Any) -> Any:
    return await discover_peers(
        scene["session"],
        scene["store"],
        client=kwargs.pop("client", scene["client"]),
        request=scene["request"],
        subject=scene["subject"],
        proposals=proposals,
        job_id=scene["job"].id,
        **kwargs,
    )


class TestOnlyATickerTheRegistryKnowsBecomesAPeer:
    """ADR 0059's containment: the model names, the registry decides."""

    async def test_a_resolvable_ticker_becomes_a_peer(self, scene: dict[str, Any]) -> None:
        outcome = await discover(scene, proposed("PEER"))

        assert len(outcome.peers) == 1
        assert outcome.refused == ()
        assert outcome.peers[0].period_end <= scene["request"].as_of_date

    async def test_the_model_s_rationale_is_what_the_reviewer_sees(
        self, scene: dict[str, Any]
    ) -> None:
        """The whole reason for asking a model: a reason somebody can disagree with."""
        outcome = await discover(
            scene, proposed("PEER", rationale="Sells the same software to the same buyers.")
        )

        assert outcome.peers[0].rationale == "Sells the same software to the same buyers."

    async def test_the_registry_s_name_replaces_the_model_s(self, scene: dict[str, Any]) -> None:
        """A right ticker with a wrong name self-corrects; the reverse is refused."""
        outcome = await discover(scene, proposed("PEER", name="Completely Wrong Holdings"))

        assert outcome.peers[0].name != "Completely Wrong Holdings"

    async def test_an_unknown_ticker_is_refused_by_name(self, scene: dict[str, Any]) -> None:
        outcome = await discover(scene, proposed("NOPE"))

        assert outcome.peers == ()
        assert [item.ticker for item in outcome.refused] == ["NOPE"]
        assert "does not list NOPE" in outcome.refused[0].reason

    async def test_nothing_is_fetched_for_a_ticker_that_did_not_resolve(
        self, scene: dict[str, Any]
    ) -> None:
        """The containment is that a hallucination costs one index lookup and no more."""
        await discover(scene, proposed("NOPE"))

        assert scene["client"].facts_calls == []

    async def test_the_subject_is_not_comparable_with_itself(self, scene: dict[str, Any]) -> None:
        """By CIK, because a second listing of the subject has a different ticker."""
        client = RegistryStub(scene["store"], known={"CTSO2": SUBJECT_CIK})

        outcome = await discover(scene, proposed("CTSO2"), client=client)

        assert outcome.peers == ()
        assert "own CIK" in outcome.refused[0].reason
        assert client.facts_calls == []

    async def test_the_same_company_twice_is_proposed_once(self, scene: dict[str, Any]) -> None:
        client = RegistryStub(scene["store"], known={"PEER": PEER_CIK, "PEER2": PEER_CIK})

        outcome = await discover(scene, proposed("PEER"), proposed("PEER2"), client=client)

        assert len(outcome.peers) == 1
        assert "already proposed" in outcome.refused[0].reason

    async def test_a_blank_ticker_is_refused_rather_than_looked_up(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await discover(scene, proposed("   ", name="Nameless"))

        assert outcome.peers == ()
        assert scene["client"].entity_calls == []


class TestWhatAPeerHasToHaveToBeOne:
    async def test_a_peer_with_no_facts_by_the_as_of_date_is_refused(
        self, scene: dict[str, Any]
    ) -> None:
        """Point-in-time applies to the comparison, not only to the subject."""
        scene["request"].as_of_date = date(1995, 1, 1)
        await scene["session"].flush()

        outcome = await discover(scene, proposed("PEER"))

        assert outcome.peers == ()
        assert "no financial facts at or before 1995-01-01" in outcome.refused[0].reason

    async def test_a_refusal_names_what_was_proposed(self, scene: dict[str, Any]) -> None:
        """A proposal of six arriving as four must not look like a proposal of four."""
        scene["request"].as_of_date = date(1995, 1, 1)
        await scene["session"].flush()

        outcome = await discover(scene, proposed("PEER", name="Peer Corporation"))

        assert outcome.refused[0].name == "Peer Corporation"
        assert outcome.refused[0].ticker == "PEER"

    async def test_a_fetch_that_fails_refuses_rather_than_raising(
        self, scene: dict[str, Any]
    ) -> None:
        """One bad suggestion must not cost the run its peer step."""

        class Unreachable(RegistryStub):
            async def fetch_company_facts(self, cik: str) -> Any:
                message = "EDGAR returned 500."
                raise ValidationError(message, context={"cik": cik})

        outcome = await discover(scene, proposed("PEER"), client=Unreachable(scene["store"]))

        assert outcome.peers == ()
        assert "could not be acquired" in outcome.refused[0].reason


class TestThePeerSFactsAreAcquiredLikeTheSubjectS:
    async def test_the_company_row_is_written(self, scene: dict[str, Any]) -> None:
        await discover(scene, proposed("PEER"))

        found = await scene["session"].scalar(select(Company).where(Company.cik == PEER_CIK))
        assert found is not None

    async def test_its_facts_are_persisted_against_it(self, scene: dict[str, Any]) -> None:
        outcome = await discover(scene, proposed("PEER"))

        count = await scene["session"].scalar(
            select(FinancialFact).where(FinancialFact.company_id == _uuid(outcome.peers[0]))
        )
        assert count is not None

    async def test_every_fact_traces_to_a_source_document(self, scene: dict[str, Any]) -> None:
        """Invariant 1, for a company the operator never named."""
        outcome = await discover(scene, proposed("PEER"))

        facts = list(
            await scene["session"].scalars(
                select(FinancialFact).where(FinancialFact.company_id == _uuid(outcome.peers[0]))
            )
        )
        assert facts
        for fact in facts:
            assert fact.source_document_id is not None

        document = await scene["session"].get(SourceDocument, facts[0].source_document_id)
        assert document is not None
        assert document.publication_date is not None

    async def test_no_fact_postdates_the_as_of_date(self, scene: dict[str, Any]) -> None:
        """Point-in-time is enforced at acquisition, in code — for peers too."""
        outcome = await discover(scene, proposed("PEER"))

        facts = list(
            await scene["session"].scalars(
                select(FinancialFact).where(FinancialFact.company_id == _uuid(outcome.peers[0]))
            )
        )
        assert facts
        for fact in facts:
            assert fact.filed_date is None or fact.filed_date <= scene["request"].as_of_date

    async def test_proposing_the_same_peer_twice_over_two_calls_duplicates_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        """The chain is idempotent by construction, which is what lets a second run rerun it."""
        first = await discover(scene, proposed("PEER"))
        before = len(
            list(
                await scene["session"].scalars(
                    select(FinancialFact).where(FinancialFact.company_id == _uuid(first.peers[0]))
                )
            )
        )

        second = await discover(scene, proposed("PEER"))
        after = len(
            list(
                await scene["session"].scalars(
                    select(FinancialFact).where(FinancialFact.company_id == _uuid(second.peers[0]))
                )
            )
        )

        assert first.peers[0].identifier == second.peers[0].identifier
        assert after == before


class TestTheSetStaysSmallEnoughToReview:
    async def test_it_stops_at_the_cap(self, scene: dict[str, Any]) -> None:
        client = RegistryStub(
            scene["store"], known={f"P{index}": PEER_CIK for index in range(MAX_PROPOSED_PEERS + 2)}
        )

        outcome = await discover(scene, proposed("P0"), proposed("P1"), client=client, limit=1)

        assert len(outcome.peers) == 1
        assert len(outcome.refused) == 1

    async def test_what_it_dropped_says_why(self, scene: dict[str, Any]) -> None:
        client = RegistryStub(scene["store"], known={"P0": PEER_CIK, "P1": PEER_CIK})

        outcome = await discover(scene, proposed("P0"), proposed("P1"), client=client, limit=1)

        assert "as many as a reviewer can meaningfully confirm" in outcome.refused[0].reason


class TestTheFloorStaysUnderneath:
    """A model that answers nothing must not lose a set the database could support."""

    def test_the_model_s_peers_come_first(self) -> None:
        merged = merged_with([_peer("a")], [_peer("b")])

        assert [peer.identifier for peer in merged] == ["a", "b"]

    def test_a_company_both_proposed_appears_once(self) -> None:
        merged = merged_with([_peer("a", "from the model")], [_peer("a", "from the lookup")])

        assert len(merged) == 1
        assert merged[0].rationale == "from the model"

    def test_no_model_peers_leaves_the_lookup_s(self) -> None:
        merged = merged_with([], [_peer("b")])

        assert [peer.identifier for peer in merged] == ["b"]

    def test_the_merge_respects_the_cap(self) -> None:
        merged = merged_with([_peer("a")], [_peer("b"), _peer("c")], limit=2)

        assert [peer.identifier for peer in merged] == ["a", "b"]


class TestTheSlateCannotCarryMoreThanTheGateWants:
    def test_the_schema_bound_and_the_service_cap_are_the_same_number(self) -> None:
        """Two constants that must agree, written out separately to avoid an import cycle."""
        assert PEER_SLATE_LIMIT == MAX_PROPOSED_PEERS

    def test_an_oversized_slate_is_refused_by_the_contract(self) -> None:
        with pytest.raises(ValueError, match="peers"):
            PeerSlate(peers=[proposed(f"P{index}") for index in range(MAX_PROPOSED_PEERS + 1)])

    def test_the_prompt_states_every_bound_the_schema_carries(self) -> None:
        """A35/A42: a schema's bounds reach the model as description text, not as a rule."""
        prompt = PeerProposalAgent().system_prompt(
            PeerProposalInput(
                company_name="Contoso", ticker="CTSO", exchange="NASDAQ", as_of_date="2024-06-28"
            )
        )

        assert str(PEER_SLATE_LIMIT) in prompt
        assert "400" in prompt
        assert "120" in prompt

    def test_it_says_a_ticker_is_verified_against_the_registry(self) -> None:
        """A model told a guess will be refused proposes fewer of them."""
        prompt = PeerProposalAgent().system_prompt(
            PeerProposalInput(
                company_name="Contoso", ticker="CTSO", exchange="NASDAQ", as_of_date="2024-06-28"
            )
        )

        assert "EDGAR" in prompt
        assert "never produce a figure" in prompt


def _peer(identifier: str, rationale: str = "because") -> PeerProposal:
    return PeerProposal(
        identifier=identifier,
        name=identifier.upper(),
        rationale=rationale,
        period_end=date(2021, 6, 30),
    )


def _uuid(peer: PeerProposal) -> uuid.UUID:
    return uuid.UUID(peer.identifier)
