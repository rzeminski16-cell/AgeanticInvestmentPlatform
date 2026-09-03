"""A closed position is an episode, its outcome is code's, the reviewer proposes, and the
operator confirms.

Four layers. The walk proves an episode is where a holding returned to nil and nothing
else — an open holding has none, a round trip made twice has two, a dividend belongs to the
episode it fell in. The outcome proves every figure the reviewer reads is a recorded
calculation, converted at its own trade's date, and that a flow that cannot be converted
leaves no return rather than a return over part of the trades. The pass proves the draft
lands on the step and never on a judgement; the confirmation proves the review is the
operator's, held on their basis, with the draft kept beside it. The structural tests prove
ADR 0074 still holds with a third subtype — `aer.calc` has no word for a review or a
verdict. And the pages prove a person can do all of it in this process.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import aer.calc
from aer.agents.post_trade_reviewer import PremiseVerdictDraft, ReviewDraft
from aer.config import Settings
from aer.core.enums import (
    DecisionAction,
    JobStatus,
    JudgementKind,
    PremiseVerdict,
    ProcessQuality,
    TransactionKind,
    UserRole,
)
from aer.db.models import (
    AuditEvent,
    Calculation,
    Company,
    Judgement,
    Portfolio,
    Review,
    ReviewVerdict,
    Security,
    Transaction,
    User,
    WorkOrder,
)
from aer.errors import ConflictError, ValidationError
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import decisions as decision_service
from aer.services import post_trade
from aer.services import theses as thesis_service
from aer.services.calculations import new_context
from aer.storage.local import LocalArtefactStore
from aer.web.overview import review as review_feed
from aer.web.overview.attention import Severity
from tests.api_fixtures import build_app, client_for
from tests.portfolio_fixtures import trade
from tests.schema_guard import refuse_unanswerable_schema

pytestmark = pytest.mark.integration

OPENED_ON = date(2026, 3, 2)
CLOSED_ON = date(2026, 6, 15)
DECIDED_AT = datetime(2026, 3, 1, 9, tzinfo=UTC)


# -- The scene ---------------------------------------------------------------------------------


async def _scene(
    session: AsyncSession, *, email: str = "reviewer@example.invalid"
) -> dict[str, Any]:
    """A person, a sterling book, a London listing in pence, and a thesis with one premise.

    Pence rather than dollars on purpose: the pounds conversion is definitional, so the
    outcome needs no rate row and the tests are about the review rather than about FX.
    """
    user = User(email=email, display_name="Reviewer", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    book = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
    company = Company(name="Barclays plc", ticker="BARC", exchange="LSE", company_number="00048839")
    session.add_all([book, company])
    await session.flush()
    barc = Security(
        company_id=company.id,
        ticker="BARC",
        exchange="LSE",
        provider_symbol="BARC.LSE",
        name="Barclays plc",
        quote_currency="GBX",
    )
    session.add(barc)
    await session.flush()
    thesis = await thesis_service.write_thesis(
        session, user=user, company=company, title="Barclays re-rates on its return on equity"
    )
    premise = await thesis_service.add_premise(
        session,
        thesis=thesis,
        actor=user,
        statement="Return on tangible equity reaches 12%.",
        basis="The FY25 guidance.",
        predicate=None,
        review_by=date(2027, 3, 31),
    )
    loaded = await thesis_service.thesis_of(session, thesis.id, user_id=user.id)
    assert loaded is not None
    return {
        "session": session,
        "user": user,
        "portfolio": book,
        "company": company,
        "barc": barc,
        "thesis": loaded,
        "premise": premise,
        "document": None,
    }


async def _round_trip(
    scene: dict[str, Any],
    *,
    opened_on: date = OPENED_ON,
    closed_on: date = CLOSED_ON,
    bought_at: str = "250",
    sold_at: str = "300",
    decided: bool = True,
) -> Any:
    """Buy a hundred at 250p, sell them at 300p: a 20% return before the reviewer says a word."""
    session = scene["session"]
    bought = await trade(
        session,
        scene,
        kind=TransactionKind.BUY,
        security=scene["barc"],
        quantity="100",
        price=bought_at,
        currency="GBX",
        on=opened_on,
    )
    await trade(
        session,
        scene,
        kind=TransactionKind.SELL,
        security=scene["barc"],
        quantity="-100",
        price=sold_at,
        currency="GBX",
        on=closed_on,
        at_hour=16,
    )
    if not decided:
        return None
    decision = await decision_service.record_decision(
        session,
        actor=scene["user"],
        thesis=scene["thesis"],
        action=DecisionAction.BUY,
        statement="Open an initial position.",
        basis="The FY25 guidance on returns.",
        security=scene["barc"],
        size_statement="about 2% of the book",
        horizon_months=12,
        exit_plan="Sell if returns guidance is cut.",
        decided_at=DECIDED_AT,
    )
    transaction = await session.scalar(
        select(Transaction).where(Transaction.attestation_id == bought.id)
    )
    assert transaction is not None
    await decision_service.carry_out(
        session, transaction=transaction, decision=decision, actor=scene["user"]
    )
    return decision


async def _episode(scene: dict[str, Any]) -> post_trade.Episode:
    [episode] = await post_trade.closed_episodes(scene["session"], portfolio=scene["portfolio"])
    return episode


def _draft(
    premise_id: uuid.UUID,
    *,
    quality: ProcessQuality = ProcessQuality.SOUND,
    verdict: PremiseVerdict = PremiseVerdict.HELD,
    extra_premise: str | None = None,
) -> ReviewDraft:
    verdicts = [
        PremiseVerdictDraft(
            premise_id=str(premise_id), verdict=verdict, note="Guidance was met in the half."
        )
    ]
    if extra_premise is not None:
        verdicts.append(
            PremiseVerdictDraft(premise_id=extra_premise, verdict=PremiseVerdict.FAILED)
        )
    return ReviewDraft(
        verdicts=verdicts,
        process_quality=quality,
        basis="A decision was written before the trade with a basis, a size and an exit plan.",
        lessons="The position closed at four months against twelve intended.",
    )


def _provider(draft: ReviewDraft) -> FakeProvider:
    return FakeProvider({"ReviewDraft": draft}, inspect_schema=refuse_unanswerable_schema)


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        http_user_agent="Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
        **overrides,
    )


async def _run(
    scene: dict[str, Any], tmp_path: Path, *, provider: FakeProvider, **overrides: Any
) -> Any:
    settings = _settings(tmp_path, **overrides)
    return await post_trade.run_review(
        scene["session"],
        settings=settings,
        provider=provider,
        router=Router(settings),
        store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
        user=scene["user"],
        episode=await _episode(scene),
    )


async def _proposal(scene: dict[str, Any], job: Any) -> post_trade.Proposal:
    proposal = await post_trade.proposal_of(scene["session"], job.id, user_id=scene["user"].id)
    assert proposal is not None
    return proposal


async def _confirm(
    scene: dict[str, Any],
    proposal: post_trade.Proposal,
    *,
    quality: ProcessQuality = ProcessQuality.SOUND,
    basis: str = "I agree with the reviewer: the decision was written first and followed.",
    verdicts: dict[uuid.UUID, tuple[PremiseVerdict, str]] | None = None,
) -> Review:
    return await post_trade.confirm_review(
        scene["session"],
        user=scene["user"],
        proposal=proposal,
        process_quality=quality,
        basis=basis,
        lessons="Look at the horizon before selling early.",
        verdicts=verdicts
        if verdicts is not None
        else {scene["premise"].judgement_id: (PremiseVerdict.HELD, "")},
    )


# -- Episodes ----------------------------------------------------------------------------------


class TestAnEpisode:
    async def test_a_round_trip_is_one_closed_position(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene, decided=False)

        episodes = await post_trade.closed_episodes(db_session, portfolio=scene["portfolio"])

        [episode] = episodes
        assert episode.opened_on == OPENED_ON
        assert episode.closed_on == CLOSED_ON
        assert [row.kind for row in episode.trades] == [TransactionKind.BUY, TransactionKind.SELL]
        assert episode.key == f"{scene['barc'].id}:{CLOSED_ON.isoformat()}"

    async def test_an_open_holding_has_no_episode(self, db_session: AsyncSession) -> None:
        """The only condition ADR 0081 admits the role on, enforced where the walk runs."""
        scene = await _scene(db_session)
        await trade(
            db_session,
            scene,
            kind=TransactionKind.BUY,
            security=scene["barc"],
            quantity="100",
            price="250",
            currency="GBX",
            on=OPENED_ON,
        )

        assert await post_trade.closed_episodes(db_session, portfolio=scene["portfolio"]) == []

    async def test_bought_sold_and_bought_again_is_two_episodes(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene, decided=False)
        await _round_trip(
            scene, opened_on=date(2026, 7, 1), closed_on=date(2026, 8, 1), decided=False
        )
        # And a third, still open, which is nobody's to review.
        await trade(
            db_session,
            scene,
            kind=TransactionKind.BUY,
            security=scene["barc"],
            quantity="50",
            price="310",
            currency="GBX",
            on=date(2026, 8, 15),
        )

        episodes = await post_trade.closed_episodes(db_session, portfolio=scene["portfolio"])

        assert [row.closed_on for row in episodes] == [date(2026, 8, 1), CLOSED_ON]
        assert (
            post_trade.episode_of(episodes, security_id=scene["barc"].id, closed_on=CLOSED_ON)
            is episodes[1]
        )
        assert (
            post_trade.episode_of(episodes, security_id=uuid.uuid4(), closed_on=CLOSED_ON) is None
        )

    async def test_a_dividend_belongs_to_the_episode_it_fell_in(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)
        await trade(
            db_session,
            scene,
            kind=TransactionKind.BUY,
            security=scene["barc"],
            quantity="100",
            price="250",
            currency="GBX",
            on=OPENED_ON,
        )
        await trade(
            db_session,
            scene,
            kind=TransactionKind.DIVIDEND,
            security=scene["barc"],
            quantity="500",
            price=None,
            currency="GBX",
            on=date(2026, 4, 10),
        )
        await trade(
            db_session,
            scene,
            kind=TransactionKind.SELL,
            security=scene["barc"],
            quantity="-100",
            price="300",
            currency="GBX",
            on=CLOSED_ON,
            at_hour=16,
        )

        [episode] = await post_trade.closed_episodes(db_session, portfolio=scene["portfolio"])

        assert [row.kind for row in episode.trades] == [
            TransactionKind.BUY,
            TransactionKind.DIVIDEND,
            TransactionKind.SELL,
        ]
        assert episode.opened_on == OPENED_ON


# -- The outcome -------------------------------------------------------------------------------


class TestTheOutcome:
    async def test_every_figure_is_a_recorded_calculation_in_the_books_currency(
        self, db_session: AsyncSession
    ) -> None:
        """Invariant 3, and ADR 0105 §2: pence in, pounds out, and a formula behind each."""
        scene = await _scene(db_session)
        await _round_trip(scene)
        ledger = new_context()

        outcome = await post_trade.outcome_for(db_session, ledger, episode=await _episode(scene))

        assert outcome.problem == ""
        assert outcome.cost is not None
        assert outcome.cost.value == Decimal(250)
        assert outcome.cost.unit.symbol == "GBP"
        assert outcome.proceeds is not None
        assert outcome.proceeds.value == Decimal(300)
        assert outcome.realised_return is not None
        assert outcome.realised_return.value == Decimal("0.2")
        assert outcome.holding_days == (CLOSED_ON - OPENED_ON).days
        assert outcome.intended_horizon_months == 12
        assert [row.judgement_id for row in outcome.decisions]
        assert outcome.thesis is not None
        assert outcome.thesis.id == scene["thesis"].id
        names = {record.name for record in ledger.records}
        assert {"episode_cost", "episode_proceeds", "realised_return"} <= names
        as_json = outcome.as_json(ledger)
        assert as_json["realised_return"] == "0.2"
        assert as_json["cost"] == "250"
        assert set(as_json["calculation_ids"]) == {
            "episode_cost",
            "episode_proceeds",
            "realised_return",
        }

    async def test_a_dividend_is_proceeds(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        await trade(
            db_session,
            scene,
            kind=TransactionKind.BUY,
            security=scene["barc"],
            quantity="100",
            price="250",
            currency="GBX",
            on=OPENED_ON,
        )
        await trade(
            db_session,
            scene,
            kind=TransactionKind.DIVIDEND,
            security=scene["barc"],
            quantity="500",
            price=None,
            currency="GBX",
            on=date(2026, 4, 10),
        )
        await trade(
            db_session,
            scene,
            kind=TransactionKind.SELL,
            security=scene["barc"],
            quantity="-100",
            price="250",
            currency="GBX",
            on=CLOSED_ON,
            at_hour=16,
        )

        outcome = await post_trade.outcome_for(
            db_session, new_context(), episode=await _episode(scene)
        )

        # 500p of dividend on a £250 position bought and sold at the same price: 2%.
        assert outcome.proceeds is not None
        assert outcome.proceeds.value == Decimal(255)
        assert outcome.realised_return is not None
        assert outcome.realised_return.value == Decimal("0.02")

    async def test_a_flow_that_cannot_be_converted_leaves_no_return(
        self, db_session: AsyncSession
    ) -> None:
        """A figure missing a purchase looks like an answer; a stated problem does not."""
        scene = await _scene(db_session)
        msft = Security(
            ticker="MSFT", exchange="NASDAQ", provider_symbol="MSFT.US", quote_currency="USD"
        )
        db_session.add(msft)
        await db_session.flush()
        await trade(
            db_session,
            scene,
            kind=TransactionKind.BUY,
            security=msft,
            quantity="10",
            price="400",
            currency="USD",
            on=OPENED_ON,
        )
        await trade(
            db_session,
            scene,
            kind=TransactionKind.SELL,
            security=msft,
            quantity="-10",
            price="410",
            currency="USD",
            on=CLOSED_ON,
            at_hour=16,
        )

        outcome = await post_trade.outcome_for(
            db_session, new_context(), episode=await _episode(scene)
        )

        assert outcome.realised_return is None
        assert outcome.cost is None
        assert outcome.problem


# -- The pass ----------------------------------------------------------------------------------


class TestThePass:
    async def test_the_draft_lands_on_the_step_and_nowhere_else(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        provider = _provider(_draft(scene["premise"].judgement_id))

        job = await _run(scene, tmp_path, provider=provider)

        assert job.status is JobStatus.SUCCEEDED
        assert provider.call_count == 1
        order = await db_session.get(WorkOrder, job.work_order_id)
        assert order is not None
        assert order.tool == post_trade.TOOL
        assert order.subject_kind == post_trade.SUBJECT_POSITION
        assert order.subject_id == scene["barc"].id
        assert order.as_of_date == CLOSED_ON
        proposal = await _proposal(scene, job)
        assert proposal.draft is not None
        assert proposal.draft.process_quality is ProcessQuality.SOUND
        assert proposal.output["outcome"]["realised_return"] == "0.2"
        assert proposal.output["thesis_id"] == str(scene["thesis"].id)
        assert [row["premise_id"] for row in proposal.output["premises"]] == [
            str(scene["premise"].judgement_id)
        ]
        assert proposal.output["decisions"][0]["carried_out_by"] == 1
        assert proposal.output["decisions"][0]["horizon_months"] == 12
        # Nothing is a judgement yet.
        assert (
            await db_session.scalar(select(Judgement).where(Judgement.kind == JudgementKind.REVIEW))
            is None
        )
        # And the ledger is persisted against this pass, so the figure resolves.
        recorded = list(
            await db_session.scalars(select(Calculation).where(Calculation.job_id == job.id))
        )
        assert {row.name for row in recorded} >= {
            "episode_cost",
            "episode_proceeds",
            "realised_return",
        }

    async def test_a_verdict_on_a_premise_the_thesis_lacks_is_dropped(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        draft = _draft(scene["premise"].judgement_id, extra_premise=str(uuid.uuid4()))

        job = await _run(scene, tmp_path, provider=_provider(draft))

        proposal = await _proposal(scene, job)
        assert proposal.draft is not None
        assert [row.premise_id for row in proposal.draft.verdicts] == [
            str(scene["premise"].judgement_id)
        ]

    async def test_a_pass_that_hits_its_ceiling_stops_and_keeps_the_outcome(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """ADR 0078: it fails with the reason rather than pausing for nobody, and the
        position stays unreviewed so it can be run again."""
        scene = await _scene(db_session)
        await _round_trip(scene)

        job = await _run(
            scene,
            tmp_path,
            provider=_provider(_draft(scene["premise"].judgement_id)),
            per_run_budget_gbp=Decimal("0.01"),
        )

        assert job.status is JobStatus.FAILED
        assert job.error is not None
        assert job.error["message"]
        proposal = await _proposal(scene, job)
        assert proposal.failed
        assert proposal.draft is None
        assert proposal.output["outcome"]["realised_return"] == "0.2"
        [state] = await post_trade.states_for(db_session, portfolio=scene["portfolio"])
        assert state.state == "stopped"

    async def test_another_persons_book_is_refused(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        other = User(email="other@example.invalid", display_name="Other", role=UserRole.OWNER)
        db_session.add(other)
        await db_session.flush()
        settings = _settings(tmp_path)

        with pytest.raises(ConflictError, match="whose book"):
            await post_trade.run_review(
                db_session,
                settings=settings,
                provider=_provider(_draft(scene["premise"].judgement_id)),
                router=Router(settings),
                store=LocalArtefactStore(
                    settings.artefact_root, max_bytes=settings.max_artefact_bytes
                ),
                user=other,
                episode=await _episode(scene),
            )

    async def test_a_reviewed_position_is_not_run_again(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        provider = _provider(_draft(scene["premise"].judgement_id))
        job = await _run(scene, tmp_path, provider=provider)
        await _confirm(scene, await _proposal(scene, job))

        with pytest.raises(ConflictError, match="already reviewed"):
            await _run(scene, tmp_path, provider=provider)


# -- Confirming ---------------------------------------------------------------------------------


class TestConfirming:
    async def test_the_review_is_the_operators_judgement_with_the_draft_beside_it(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))
        proposal = await _proposal(scene, job)

        review = await _confirm(scene, proposal)

        assert review.judgement.kind is JudgementKind.REVIEW
        assert review.judgement.held_by == scene["user"].email
        assert review.judgement.basis.startswith("I agree with the reviewer")
        assert review.process_quality is ProcessQuality.SOUND
        assert review.job_id == job.id
        assert review.thesis_id == scene["thesis"].id
        assert review.security_id == scene["barc"].id
        assert (review.opened_on, review.closed_on) == (OPENED_ON, CLOSED_ON)
        assert review.outcome["realised_return"] == "0.2"
        assert review.proposal is not None
        assert review.proposal["process_quality"] == "sound"
        [verdict] = review.verdicts
        assert verdict.premise_id == scene["premise"].judgement_id
        assert verdict.position == 1
        assert verdict.statement == "Return on tangible equity reaches 12%."
        assert verdict.verdict is PremiseVerdict.HELD
        # On the audit chain, with the review as its subject.
        event = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "review.confirmed")
        )
        assert event is not None
        assert event.subject_kind == "review"
        assert event.subject_id == review.judgement_id
        assert event.payload["proposed_quality"] == "sound"
        # And the list knows.
        [state] = await post_trade.states_for(db_session, portfolio=scene["portfolio"])
        assert state.state == "reviewed"
        assert state.review is not None
        assert state.review.judgement_id == review.judgement_id

    async def test_an_amended_verdict_is_the_operators_and_the_draft_is_kept(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """Whether the operator agreed with the reviewer is decision data (ADR 0105 §3)."""
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))

        review = await _confirm(
            scene,
            await _proposal(scene, job),
            quality=ProcessQuality.QUESTIONABLE,
            basis="The exit plan named a guidance cut, and the sale followed none.",
            verdicts={scene["premise"].judgement_id: (PremiseVerdict.UNTESTED, "Not yet filed.")},
        )

        assert review.process_quality is ProcessQuality.QUESTIONABLE
        assert review.proposal is not None
        assert review.proposal["process_quality"] == "sound"
        [verdict] = review.verdicts
        assert verdict.verdict is PremiseVerdict.UNTESTED
        assert verdict.note == "Not yet filed."
        analytics = await post_trade.analytics_for(db_session, user_id=scene["user"].id)
        assert {part.label: part.count for part in analytics.agreement.parts}["amended"] == 1

    async def test_a_blank_basis_is_refused(self, db_session: AsyncSession, tmp_path: Path) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))

        with pytest.raises(ValidationError, match="basis"):
            await _confirm(scene, await _proposal(scene, job), basis="   ")

    async def test_a_position_is_reviewed_once(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))
        proposal = await _proposal(scene, job)
        await _confirm(scene, proposal)

        with pytest.raises(ConflictError, match="already reviewed"):
            await _confirm(scene, proposal)

    async def test_a_stopped_pass_has_no_outcome_to_confirm(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """A pass that stopped kept its outcome; a pass with no step output has nothing."""
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))
        proposal = post_trade.Proposal(job=job, output={})

        with pytest.raises(ValidationError, match="no outcome"):
            await _confirm(scene, proposal)


# -- Analytics ---------------------------------------------------------------------------------


class TestAStatistic:
    def test_the_parts_must_account_for_the_total(self) -> None:
        with pytest.raises(ValueError, match="do not account"):
            post_trade.Statistic("x", 3, (post_trade.Part("a", 1), post_trade.Part("b", 1)))

    def test_below_the_minimum_sample_it_is_a_tally(self) -> None:
        two = post_trade.Statistic("x", 2, (post_trade.Part("a", 2),))
        three = post_trade.Statistic("x", 3, (post_trade.Part("a", 3),))

        assert not two.is_a_finding
        assert three.is_a_finding
        assert post_trade.MINIMUM_SAMPLE == 3


class TestAnalytics:
    async def test_nothing_reviewed_counts_nothing(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)

        analytics = await post_trade.analytics_for(db_session, user_id=scene["user"].id)

        assert analytics.reviewed == 0
        assert analytics.cells.count == 0
        assert not analytics.cells.is_a_finding

    async def test_one_review_lands_in_its_cell_with_its_n(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))
        await _confirm(scene, await _proposal(scene, job))

        analytics = await post_trade.analytics_for(db_session, user_id=scene["user"].id)

        assert analytics.reviewed == 1
        cells = {part.label: part.count for part in analytics.cells.parts}
        assert cells["sound process, gain"] == 1
        assert analytics.cells.count == 1
        assert not analytics.cells.is_a_finding
        assert {part.label: part.count for part in analytics.verdicts.parts}["held"] == 1
        # 105 days against 12 months intended: closed early.
        assert {part.label: part.count for part in analytics.horizons.parts}["closed early"] == 1
        assert {part.label: part.count for part in analytics.written_down.parts}["yes"] == 1
        assert {part.label: part.count for part in analytics.agreement.parts}["agreed"] == 1


# -- The work list -----------------------------------------------------------------------------


class TestTheWorkList:
    async def test_an_unreviewed_position_is_not_started(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene, decided=False)

        items = await review_feed.items(db_session, user_id=scene["user"].id)

        [item] = items
        assert item.severity is Severity.IDLE
        assert item.key == f"review.unreviewed.{scene['barc'].id}:{CLOSED_ON.isoformat()}"
        assert "has not been reviewed" in item.title
        assert item.href == "/review"

    async def test_a_proposal_is_waiting_for_you(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))

        items = await review_feed.items(db_session, user_id=scene["user"].id)

        [item] = items
        assert item.severity is Severity.BLOCKED
        assert item.href == f"/review/passes/{job.id}"
        assert "waiting for you" in item.title

    async def test_a_stopped_pass_needs_diagnosis(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(
            scene,
            tmp_path,
            provider=_provider(_draft(scene["premise"].judgement_id)),
            per_run_budget_gbp=Decimal("0.01"),
        )

        items = await review_feed.items(db_session, user_id=scene["user"].id)

        [item] = items
        assert item.severity is Severity.BROKEN
        assert item.href == f"/review/passes/{job.id}"

    async def test_a_reviewed_position_asks_nothing(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        await _round_trip(scene)
        job = await _run(scene, tmp_path, provider=_provider(_draft(scene["premise"].judgement_id)))
        await _confirm(scene, await _proposal(scene, job))

        assert await review_feed.items(db_session, user_id=scene["user"].id) == []


# -- Structure ---------------------------------------------------------------------------------


class TestAJudgementEntersNoLineage:
    def test_the_calculation_core_has_no_word_for_a_review(self) -> None:
        """ADR 0074, a third time. A review is a judgement; `aer.calc` has no identifier
        that names one, so no calculation can take one as an input. (`verdict` is not on
        the list: `calc/statements.py` has long used the word for what a statement check
        found, which is a fact about a filing and not a judgement.)"""
        forbidden = re.compile(r"review|lesson|process_quality", re.IGNORECASE)
        offending: list[str] = []
        for path in Path(aer.calc.__file__).parent.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = (
                    getattr(node, "id", None)
                    or getattr(node, "attr", None)
                    or getattr(node, "arg", None)
                    or (node.name if isinstance(node, ast.FunctionDef | ast.ClassDef) else None)
                )
                if isinstance(name, str) and forbidden.search(name):
                    offending.append(f"{path.name}:{name}")
        assert not offending, offending

    def test_the_review_row_is_keyed_on_its_judgement(self) -> None:
        assert [column.name for column in Review.__table__.primary_key] == ["judgement_id"]
        assert ReviewVerdict.__table__.c.review_id.foreign_keys


# -- The pages -------------------------------------------------------------------------------


_TABLES = "audit_events, users, companies, securities, portfolios, theses, judgements, work_orders"


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        scene = await _scene(session, email="owner@example.invalid")
        await _round_trip(scene)
        await session.commit()
        yield scene
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, committed: Any) -> Any:
    provider = _provider(_draft(committed["premise"].judgement_id))
    store = LocalArtefactStore(
        api_settings.artefact_root, max_bytes=api_settings.max_artefact_bytes
    )
    app = build_app(
        api_settings, engine=db_engine, redis=fake_redis, provider=provider, store=store
    )
    async for client in client_for(app):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return str(found.group(1))


async def _run_from_the_page(api: Any, committed: dict[str, Any]) -> str:
    page = await api.get("/review")
    response = await api.post(
        "/review/run",
        data={
            "csrf_token": _csrf(page.text),
            "portfolio_id": str(committed["portfolio"].id),
            "security_id": str(committed["barc"].id),
            "closed_on": CLOSED_ON.isoformat(),
        },
    )
    assert response.status_code == 303, response.text
    location = str(response.headers["location"])
    assert location.startswith("/review/passes/")
    return location


class TestThePages:
    async def test_the_list_offers_to_run_the_reviewer(self, api: Any, committed: Any) -> None:
        body = (await api.get("/review")).text

        assert 'data-state="unreviewed"' in body
        assert "Run the reviewer" in body
        assert "closed position has not been reviewed" in body

    async def test_run_then_confirm_then_read(self, api: Any, committed: Any) -> None:
        """The whole loop from the page: the pass runs in this process, the proposal is
        prefilled, the operator amends the quality, and the review shows both."""
        location = await _run_from_the_page(api, committed)

        proposal = await api.get(location)
        assert proposal.status_code == 200
        assert 'data-figure="realised-return"' in proposal.text
        assert "+20.0%" in proposal.text
        assert "/calculations/" in proposal.text
        assert 'id="confirm-review"' in proposal.text
        assert '<option value="sound" selected>' in proposal.text
        premise_id = str(committed["premise"].judgement_id)
        assert f'name="verdict-{premise_id}"' in proposal.text

        confirmed = await api.post(
            f"{location}/confirm",
            data={
                "csrf_token": _csrf(proposal.text),
                "process_quality": "questionable",
                "basis": "The sale followed no part of the exit plan.",
                "lessons": "",
                f"verdict-{premise_id}": "untested",
                f"note-{premise_id}": "The year was not yet filed.",
            },
        )
        assert confirmed.status_code == 303, confirmed.text
        review_url = confirmed.headers["location"]
        assert re.fullmatch(r"/review/[0-9a-f-]{36}", review_url)

        review = await api.get(review_url)
        assert review.status_code == 200
        assert "Questionable" in review.text
        assert "Amended" in review.text
        assert "The reviewer proposed: held" in review.text
        assert "The year was not yet filed." in review.text

        # The pass page now points at the review rather than offering the form again.
        again = await api.get(location)
        assert 'id="confirmed-notice"' in again.text
        assert 'id="confirm-review"' not in again.text

        # The list says so, and the work list asks nothing.
        listed = (await api.get("/review")).text
        assert 'data-state="reviewed"' in listed
        assert 'data-state="unreviewed"' not in listed

    async def test_the_analytics_are_a_tally_until_the_sample_can_bear_a_proportion(
        self, api: Any, committed: Any
    ) -> None:
        empty = (await api.get("/analytics")).text
        assert "Nothing reviewed yet" in empty

        location = await _run_from_the_page(api, committed)
        proposal = await api.get(location)
        premise_id = str(committed["premise"].judgement_id)
        await api.post(
            f"{location}/confirm",
            data={
                "csrf_token": _csrf(proposal.text),
                "process_quality": "sound",
                "basis": "Written first, sized, and followed.",
                "lessons": "",
                f"verdict-{premise_id}": "held",
                f"note-{premise_id}": "",
            },
        )

        body = (await api.get("/analytics")).text

        assert "n = 1" in body
        assert 'data-statistic="process-against-outcome" data-count="1" data-finding="no"' in body
        assert "a tally, not a proportion" in body
        assert "Share" not in body

    async def test_a_form_without_a_token_is_refused(self, api: Any, committed: Any) -> None:
        response = await api.post(
            "/review/run",
            data={
                "portfolio_id": str(committed["portfolio"].id),
                "security_id": str(committed["barc"].id),
                "closed_on": CLOSED_ON.isoformat(),
            },
        )

        assert response.status_code == 403
        assert "Nothing was run" in response.text

    async def test_a_pass_that_is_not_there_is_not_found(self, api: Any, committed: Any) -> None:
        assert (await api.get(f"/review/passes/{uuid.uuid4()}")).status_code == 404
        assert (await api.get(f"/review/{uuid.uuid4()}")).status_code == 404

    async def test_a_position_that_is_not_closed_cannot_be_run(
        self, api: Any, committed: Any
    ) -> None:
        page = await api.get("/review")
        response = await api.post(
            "/review/run",
            data={
                "csrf_token": _csrf(page.text),
                "portfolio_id": str(committed["portfolio"].id),
                "security_id": str(committed["barc"].id),
                "closed_on": "2026-01-01",
            },
        )

        assert response.status_code == 404
        assert "No such closed position" in response.text
