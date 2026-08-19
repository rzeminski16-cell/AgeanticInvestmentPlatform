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

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.agents.peers import (
    PEER_SLATE_LIMIT,
    PeerProposalAgent,
    PeerProposalInput,
    PeerSlate,
    ProposedPeer,
)
from aer.calc.comps import MultipleBasis
from aer.config import Settings
from aer.core.enums import Decision, FactBasis, GateKind, JobStatus, Provider, SourceTier, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Artefact,
    Company,
    FinancialFact,
    Job,
    JobStep,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.errors import ValidationError
from aer.services import approvals as approval_service
from aer.services.comps import MAX_PROPOSED_PEERS, PEER_SET_STEP, PeerProposal
from aer.services.peer_discovery import discover_peers, merged_with
from aer.sources.base import ResolvedEntity
from aer.storage.local import LocalArtefactStore
from aer.workflow.workflows.vertical_slice_v1 import peer_gate_payload
from tests.api_fixtures import build_app, client_for
from tests.sec_fixtures import MSFT_CIK
from tests.workflow_fixtures import AS_OF_DATE, StubSecClient, seed_job

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


_GATE_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

REFUSED_ROW = {
    "ticker": "NOPE",
    "name": "Nonexistent Holdings",
    "reason": "Not resolved: EDGAR does not list NOPE unambiguously. Nothing was fetched for it.",
}


async def _seed_gate(engine: Any, *, peers: list[dict[str, str]] | None, refused: bool) -> Any:
    """A run paused at the peer-set gate, committed so the application's session sees it."""
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(text(f"TRUNCATE {_GATE_TABLES} RESTART IDENTITY CASCADE"))

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

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
        session.add(request)
        await session.flush()

        job = await seed_job(session, request=request)
        job.status = JobStatus.AWAITING_APPROVAL
        # Gates are passed in order, so this one needs the plan gate behind it.
        await approval_service.record_decision(
            session,
            job=job,
            gate=GateKind.PLAN,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash="1" * 64,
        )

        produced: dict[str, Any] = {
            "subject": str(uuid.uuid4()),
            "subject_name": "Contoso Corporation",
            "subject_period_end": AS_OF_DATE.isoformat(),
            "basis": MultipleBasis.TRAILING_TWELVE_MONTHS.value,
            "proposed_by": "aer.agents.peers",
            "peers": peers
            if peers is not None
            else [
                {
                    "identifier": str(uuid.uuid4()),
                    "name": "Peer Corporation",
                    "rationale": "Sells comparable software to comparable buyers.",
                    "period_end": AS_OF_DATE.isoformat(),
                }
            ],
            "refused": [REFUSED_ROW] if refused else [],
        }
        produced["payload_hash"] = sha256_hex(canonical_json(peer_gate_payload(produced)))
        session.add(
            JobStep(
                job_id=job.id,
                step_key=PEER_SET_STEP,
                sequence=6,
                status=JobStatus.SUCCEEDED,
                idempotency_key=f"{job.id}:{PEER_SET_STEP}",
                input_hash="0" * 64,
                output_ref=produced,
            )
        )
        # The paused gate step, shaped as `StepPaused` records it. `pending_gate` reads the
        # gate out of exactly this context, and that is what puts the link on the console —
        # the mechanism that was missing when a run stopped at the assumptions gate with
        # nowhere to go.
        if produced["peers"]:
            session.add(
                JobStep(
                    job_id=job.id,
                    step_key="gate_peer_set",
                    sequence=7,
                    status=JobStatus.AWAITING_APPROVAL,
                    idempotency_key=f"{job.id}:gate_peer_set",
                    input_hash="0" * 64,
                    error={
                        "code": "step_paused",
                        "message": (
                            "This run is waiting for the PEER_SET gate. Nothing further "
                            "happens, and nothing further is spent, until somebody approves "
                            "or rejects it."
                        ),
                        "context": {"gate": GateKind.PEER_SET.value},
                    },
                )
            )
        await session.commit()
        return {"job": job, "request": request, "user": user, "produced": produced}


@pytest.fixture
async def at_the_gate(db_engine: Any) -> Any:
    return await _seed_gate(db_engine, peers=None, refused=True)


@pytest.fixture
async def api(api_settings: Settings, db_engine: Any, fake_redis: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def proposed(ticker: str, *, name: str = "", rationale: str = "Same end market.") -> ProposedPeer:
    return ProposedPeer(ticker=ticker, name=name or f"{ticker} Corporation", rationale=rationale)


async def discover(scene: dict[str, Any], *proposals: ProposedPeer, **kwargs: Any) -> Any:
    return await discover_peers(
        scene["session"],
        client=kwargs.pop("client", scene["client"]),
        request=scene["request"],
        subject=scene["subject"],
        proposals=proposals,
        **kwargs,
    )


class TestOnlyATickerTheRegistryKnowsBecomesAPeer:
    """ADR 0059's containment: the model names, the registry decides."""

    async def test_a_resolvable_ticker_becomes_a_peer(self, scene: dict[str, Any]) -> None:
        outcome = await discover(scene, proposed("PEER"))

        assert len(outcome.peers) == 1
        assert outcome.refused == ()
        # Resolving is all that happened: nothing of this company has been fetched, so
        # there is no reporting period to state (ADR 0059).
        assert outcome.peers[0].period_end is None

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


class TestAPeerNeedsOnlyToResolve:
    """ADR 0059 as amended: recording the name is the step's whole product."""

    async def test_a_peer_this_platform_has_never_met_is_recorded_by_name(
        self, scene: dict[str, Any]
    ) -> None:
        """No facts, no company row, no period — and still on the list, because the set
        exists for the day a price feed makes the comparison computable."""
        outcome = await discover(scene, proposed("PEER"))

        (peer,) = outcome.peers
        assert peer.identifier == PEER_CIK
        assert peer.period_end is None
        assert outcome.refused == ()

    async def test_a_peer_already_in_the_database_keeps_its_id_and_period(
        self, scene: dict[str, Any]
    ) -> None:
        """Facts an earlier run banked are free and honest — those are used, not re-fetched."""
        session = scene["session"]
        company = Company(name="PEER Corporation", ticker="PEER", exchange="NASDAQ", cik=PEER_CIK)
        session.add(company)
        await session.flush()
        document = await _peer_document(scene, company)
        session.add(
            FinancialFact(
                company_id=company.id,
                source_document_id=document.id,
                concept="revenue",
                value=Decimal("100"),
                unit="USD",
                period_end=date(2021, 12, 31),
                fiscal_period="FY",
                fiscal_year=2021,
                filed_date=date(2022, 2, 1),
                basis=FactBasis.AS_REPORTED,
            )
        )
        await session.flush()

        outcome = await discover(scene, proposed("PEER"))

        (peer,) = outcome.peers
        assert peer.identifier == str(company.id)
        assert peer.period_end == date(2021, 12, 31)

    async def test_a_stored_period_after_the_as_of_date_is_not_offered(
        self, scene: dict[str, Any]
    ) -> None:
        """Point-in-time applies to the comparison too: a known company whose only stored
        year postdates the as-of date aligns nothing, and says so with a None period
        rather than a refusal — the name is still worth recording."""
        session = scene["session"]
        company = Company(name="PEER Corporation", ticker="PEER", exchange="NASDAQ", cik=PEER_CIK)
        session.add(company)
        await session.flush()
        document = await _peer_document(scene, company)
        session.add(
            FinancialFact(
                company_id=company.id,
                source_document_id=document.id,
                concept="revenue",
                value=Decimal("100"),
                unit="USD",
                period_end=scene["request"].as_of_date + timedelta(days=200),
                fiscal_period="FY",
                fiscal_year=scene["request"].as_of_date.year + 1,
                filed_date=scene["request"].as_of_date + timedelta(days=230),
                basis=FactBasis.AS_REPORTED,
            )
        )
        await session.flush()

        outcome = await discover(scene, proposed("PEER"))

        (peer,) = outcome.peers
        assert peer.period_end is None

    async def test_a_refusal_names_what_was_proposed(self, scene: dict[str, Any]) -> None:
        """A proposal of six arriving as four must not look like a proposal of four."""
        outcome = await discover(scene, proposed("GHOST", name="Ghost Corporation"))

        assert outcome.peers == ()
        assert outcome.refused[0].name == "Ghost Corporation"
        assert outcome.refused[0].ticker == "GHOST"


class TestNothingIsFetchedForAPeer:
    """The withdrawal itself. The first complete run fetched 26 MB for eight peers to
    feed a table no price feed existed to build — and the acquisition was the vector
    that put other issuers' facts into the subject's evidence pool (ADR 0061). This
    class is what stops it coming back by accident."""

    async def test_discovery_writes_no_rows_of_any_kind(self, scene: dict[str, Any]) -> None:
        session = scene["session"]

        async def counts() -> tuple[int, int, int]:
            return (
                len(list(await session.scalars(select(Company)))),
                len(list(await session.scalars(select(FinancialFact)))),
                len(list(await session.scalars(select(SourceDocument)))),
            )

        before = await counts()
        outcome = await discover(scene, proposed("PEER"))
        after = await counts()

        assert outcome.peers, "the peer resolved; the point is what did not happen next"
        assert after == before, (
            "discovering a peer wrote rows — the acquisition ADR 0059's amendment "
            "withdrew has come back"
        )

    async def test_the_facts_endpoint_is_never_asked(self, scene: dict[str, Any]) -> None:
        """The stub's fetch raises; a discovery that never fetches never notices."""

        class Unreachable(RegistryStub):
            async def fetch_company_facts(self, cik: str) -> Any:
                message = "fetch_company_facts was called for a peer."
                raise AssertionError(message)

        outcome = await discover(scene, proposed("PEER"), client=Unreachable(scene["store"]))

        (peer,) = outcome.peers
        assert peer.identifier == PEER_CIK


async def _peer_document(scene: dict[str, Any], company: Company) -> SourceDocument:
    """A stored provenance row for a peer an earlier run acquired."""
    session = scene["session"]
    artefact = Artefact(
        sha256=hashlib.sha256(company.cik.encode()).hexdigest(),
        media_type="application/json",
        size_bytes=3,
        storage_key=f"peer/{company.cik}",
    )
    session.add(artefact)
    await session.flush()
    document = SourceDocument(
        request_id=scene["request"].id,
        job_id=scene["job"].id,
        company_id=company.id,
        artefact_id=artefact.id,
        url=f"https://example.invalid/{company.cik}",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        publication_date=date(2022, 2, 1),
        quarantined=False,
    )
    session.add(document)
    await session.flush()
    return document


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


class TestWhatTheReviewerIsShown:
    """A refusal recorded in a step's output that no page renders is not visible."""

    async def test_the_page_lists_the_peers_and_what_was_refused(
        self, api: Any, at_the_gate: Any
    ) -> None:
        page = await api.get(f"/runs/{at_the_gate['job'].id}/peers")

        assert page.status_code == 200
        assert 'id="proposed-peers"' in page.text
        assert 'id="refused-peers"' in page.text
        assert "NOPE" in page.text
        assert "does not list NOPE" in page.text

    async def test_the_hash_is_of_the_set_and_moves_with_it(
        self, api: Any, at_the_gate: Any
    ) -> None:
        """What is confirmed is the peers; a refusal alongside them changes nothing."""
        page = await api.get(f"/runs/{at_the_gate['job'].id}/peers")

        assert at_the_gate["produced"]["payload_hash"] in page.text

    async def test_the_console_offers_the_link_when_the_run_stopped_here(
        self, api: Any, at_the_gate: Any
    ) -> None:
        """A gate an operator cannot reach is a run that pauses and never resumes.

        The peer gate used to pass straight through on every run, so nobody had met this
        page; a model proposing peers is what makes it a stop an operator actually hits.
        """
        console = await api.get(f"/runs/{at_the_gate['job'].id}")

        assert 'id="review-peers"' in console.text
        assert f"/runs/{at_the_gate['job'].id}/peers" in console.text

    async def test_a_run_that_resolved_nobody_says_what_it_tried(
        self, api: Any, db_engine: Any
    ) -> None:
        """Otherwise "no comparable companies" reads as a model that proposed none."""
        seeded = await _seed_gate(db_engine, peers=[], refused=True)

        page = await api.get(f"/runs/{seeded['job'].id}/peers")

        assert page.status_code == 404
        assert "none could be used" in page.text
        assert "does not list NOPE" in page.text


def _peer(identifier: str, rationale: str = "because") -> PeerProposal:
    return PeerProposal(
        identifier=identifier,
        name=identifier.upper(),
        rationale=rationale,
        period_end=date(2021, 6, 30),
    )


def _uuid(peer: PeerProposal) -> uuid.UUID:
    return uuid.UUID(peer.identifier)
