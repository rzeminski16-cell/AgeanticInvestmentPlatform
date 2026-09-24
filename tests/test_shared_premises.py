"""What else rests on a belief (ADR 0122 §2, Phase 6b.2).

Two questions over the same rows. The monitor's is narrow on purpose: a broken premise
surfaces against other held positions asserting a premise on the same metric *and* sharing
a confirmed theme or sector. Ask's is the wider one — the metric alone — because the
operator asked. Both read the record and only the record.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.ask import empty_record, resolve
from aer.core.enums import (
    FindingKind,
    PremiseComparator,
    PremiseStatus,
    TransactionKind,
)
from aer.db.models import Finding, Portfolio, Security
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import ask as ask_service
from aer.services import shared_premises
from aer.services import theses as thesis_service
from aer.services.theses import Predicate
from aer.storage.local import LocalArtefactStore
from tests.api_fixtures import build_app, client_for
from tests.portfolio_fixtures import trade
from tests.test_graph_view import _confirmed_theme
from tests.test_knowledge_stats import _company, _run, _user

pytestmark = pytest.mark.integration

HELD = datetime(2026, 3, 1, 9, tzinfo=UTC)


def _margin() -> Predicate:
    return Predicate(
        metric="operating margin",
        comparator=PremiseComparator.AT_LEAST,
        threshold=Decimal("0.20"),
        unit="ratio",
    )


async def _scene(session: AsyncSession) -> dict[str, Any]:
    """Three held positions — Alpha, Beta, Gamma — each with a thesis asserting an operating
    margin premise. Alpha and Beta share a confirmed theme; Gamma shares only the metric.
    Alpha's premise has been read and contradicted."""
    user = await _user(session)
    alpha = await _company(session, "ALPH", "Alpha plc")
    beta = await _company(session, "BETA", "Beta plc")
    gamma = await _company(session, "GAMM", "Gamma plc")
    report_alpha = await _run(session, user=user, company=alpha)
    report_beta = await _run(session, user=user, company=beta)
    await _confirmed_theme(
        session,
        label="AI capital expenditure",
        key="ai-capex",
        memberships=[(alpha, report_alpha), (beta, report_beta)],
    )

    book = Portfolio(user_id=user.id, name="Main", base_currency="USD")
    session.add(book)
    await session.flush()
    securities: dict[str, Security] = {}
    for company in (alpha, beta, gamma):
        security = Security(
            company_id=company.id,
            ticker=company.ticker,
            exchange="NASDAQ",
            provider_symbol=f"{company.ticker}.US",
            name=company.name,
            quote_currency="USD",
        )
        session.add(security)
        securities[str(company.ticker)] = security
    await session.flush()
    held = {"portfolio": book, "document": None}
    for security in securities.values():
        await trade(
            session,
            held,
            kind=TransactionKind.BUY,
            security=security,
            quantity="10",
            price="100",
            currency="USD",
            on=date(2026, 3, 2),
        )

    theses: dict[str, Any] = {}
    premises: dict[str, Any] = {}
    for company, statement in (
        (alpha, "Operating margin stays above 20%."),
        (beta, "Beta's operating margin holds above 20%."),
        (gamma, "Gamma's margin stays above 20%."),
    ):
        thesis = await thesis_service.write_thesis(
            session, user=user, company=company, title=f"{company.name} compounds"
        )
        premises[str(company.ticker)] = await thesis_service.add_premise(
            session,
            thesis=thesis,
            actor=user,
            statement=statement,
            basis="Ten years of history.",
            predicate=_margin(),
            review_by=None,
            held_at=HELD,
        )
        theses[str(company.ticker)] = thesis
    # Beta also asserts something on another metric, which nothing else rests on.
    await thesis_service.add_premise(
        session,
        thesis=theses["BETA"],
        actor=user,
        statement="Revenue grows ten percent a year.",
        basis="The order book.",
        predicate=Predicate(
            metric="revenue growth",
            comparator=PremiseComparator.AT_LEAST,
            threshold=Decimal("0.10"),
            unit="ratio",
        ),
        review_by=None,
        held_at=HELD,
    )
    finding = Finding(
        user_id=user.id,
        thesis_id=theses["ALPH"].id,
        judgement_id=premises["ALPH"].judgement_id,
        kind=FindingKind.READING,
        status=PremiseStatus.CONTRADICTED,
        justification="FY26 operating margin came in at 18%.",
        opens_gate=True,
    )
    session.add(finding)
    await session.flush()
    return {
        "session": session,
        "user": user,
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "book": book,
        "theses": theses,
        "premises": premises,
        "finding": finding,
    }


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        http_user_agent="Tracework Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
    )


class TestTheKey:
    async def test_two_spellings_of_one_metric_share_a_key(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        spelt = await thesis_service.add_premise(
            db_session,
            thesis=scene["theses"]["GAMM"],
            actor=scene["user"],
            statement="The margin, spelt differently.",
            basis="The same history.",
            predicate=Predicate(
                metric="Operating  Margin",
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal("0.20"),
                unit="ratio",
            ),
            review_by=None,
        )

        assert shared_premises.metric_key(scene["premises"]["ALPH"]) == (
            shared_premises.metric_key(spelt)
        )

    async def test_a_premise_nothing_can_test_has_no_key(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        untested = await thesis_service.add_premise(
            db_session,
            thesis=scene["theses"]["GAMM"],
            actor=scene["user"],
            statement="Management allocates capital well.",
            basis="Buybacks below intrinsic value.",
            predicate=None,
            review_by=date(2027, 3, 31),
        )

        assert shared_premises.metric_key(untested) is None


class TestTheMonitorsNarrowQuestion:
    async def test_a_broken_premise_surfaces_where_the_metric_and_a_theme_are_shared(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)

        related = await shared_premises.load_bearing_elsewhere(db_session, finding=scene["finding"])

        # Beta shares the metric and the theme; Gamma shares the metric alone and is not here.
        [row] = related
        assert row.company.ticker == "BETA"
        assert row.premise.statement == "Beta's operating margin holds above 20%."
        assert row.shares == ("the metric operating margin", "the theme AI capital expenditure")
        assert row.state == "not yet read"

    async def test_a_retired_thesis_is_not_load_bearing(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        loaded = await thesis_service.thesis_of(
            db_session, scene["theses"]["BETA"].id, user_id=scene["user"].id
        )
        assert loaded is not None
        await thesis_service.retire_thesis(
            db_session, thesis=loaded, actor=scene["user"], reason="Sold out."
        )

        assert (
            await shared_premises.load_bearing_elsewhere(db_session, finding=scene["finding"]) == ()
        )

    async def test_a_position_sold_is_not_load_bearing(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        [beta_security] = [
            security
            for security in await _securities(db_session)
            if security.company_id == scene["beta"].id
        ]
        await trade(
            db_session,
            {"portfolio": scene["book"], "document": None},
            kind=TransactionKind.SELL,
            security=beta_security,
            quantity="-10",
            price="120",
            currency="USD",
            on=date(2026, 6, 1),
        )

        assert (
            await shared_premises.load_bearing_elsewhere(db_session, finding=scene["finding"]) == ()
        )

    async def test_a_stopped_pass_has_nothing_elsewhere(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        stopped = Finding(
            user_id=scene["user"].id,
            thesis_id=scene["theses"]["ALPH"].id,
            judgement_id=None,
            kind=FindingKind.STOPPED,
            status=None,
            justification="The pass stopped at its ceiling.",
        )
        db_session.add(stopped)
        await db_session.flush()

        assert await shared_premises.load_bearing_elsewhere(db_session, finding=stopped) == ()


class TestAsksWiderQuestion:
    async def test_the_shared_beliefs_are_the_metric_alone(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)

        beliefs = await shared_premises.shared_beliefs(
            db_session, user_id=scene["user"].id, company_id=scene["alpha"].id
        )

        [belief] = beliefs
        assert belief.premise.statement == "Operating margin stays above 20%."
        assert belief.metric == "operating margin"
        assert sorted(other.company.ticker for other in belief.others) == ["BETA", "GAMM"]
        assert all(other.shares == ("the metric operating margin",) for other in belief.others)

    async def test_the_question_resolves_to_tier_one_whatever_the_record_holds(self) -> None:
        resolved = resolve("Which of my positions rest on the same belief?", empty_record())

        assert resolved.tier == 1
        assert resolved.belief is True
        assert "nothing is spent" in resolved.rationale.lower()

    async def test_ask_answers_from_the_theses_and_the_book(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        settings = _settings(tmp_path)

        question = await ask_service.ask(
            db_session,
            settings=settings,
            provider=FakeProvider({}),
            router=Router(settings),
            store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
            user=scene["user"],
            company=scene["alpha"],
            text="Which of my other positions rest on the same belief?",
        )

        assert question.tier == 1
        assert question.is_answered
        assert question.actual_cost_gbp == Decimal(0)
        assert question.content["kind"] == "shared_belief"
        [belief] = question.content["beliefs"]
        assert sorted(other["ticker"] for other in belief["others"]) == ["BETA", "GAMM"]
        assert question.answer is not None
        assert "BETA, GAMM" in question.answer
        assert "nothing was re-read or fetched" in question.answer

    async def test_a_company_with_no_testable_premise_gets_a_plain_answer(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        scene = await _scene(db_session)
        settings = _settings(tmp_path)
        delta = await _company(db_session, "DELT", "Delta plc")
        await _run(db_session, user=scene["user"], company=delta)

        question = await ask_service.ask(
            db_session,
            settings=settings,
            provider=FakeProvider({}),
            router=Router(settings),
            store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
            user=scene["user"],
            company=delta,
            text="Which holdings depend on the same premise?",
        )

        assert question.is_answered
        assert question.content == {"kind": "shared_belief", "beliefs": []}
        assert question.answer is not None
        assert "no premise code can test" in question.answer


async def _securities(session: AsyncSession) -> list[Security]:
    return list(await session.scalars(select(Security)))


class TestThePages:
    @pytest.fixture
    async def committed(self, db_engine: Any, tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            scene = await _scene(session)
            settings = _settings(tmp_path)
            question = await ask_service.ask(
                session,
                settings=settings,
                provider=FakeProvider({}),
                router=Router(settings),
                store=LocalArtefactStore(
                    settings.artefact_root, max_bytes=settings.max_artefact_bytes
                ),
                user=scene["user"],
                company=scene["alpha"],
                text="Which of my positions rest on the same belief?",
            )
            await session.commit()
            yield {**scene, "question": question}

    @pytest.fixture
    async def api(
        self, api_settings: Any, db_engine: Any, fake_redis: Any, committed: Any
    ) -> AsyncIterator[Any]:
        app = build_app(api_settings, engine=db_engine, redis=fake_redis)
        async for client in client_for(app):
            yield client

    async def test_the_finding_page_names_what_else_rests_on_the_premise(
        self, api: Any, committed: Any
    ) -> None:
        body = (await api.get(f"/monitor/findings/{committed['finding'].id}")).text

        assert 'id="elsewhere"' in body
        assert 'data-elsewhere="BETA"' in body
        assert "the theme AI capital expenditure" in body
        assert 'data-elsewhere="GAMM"' not in body

    async def test_the_question_page_lists_the_positions(self, api: Any, committed: Any) -> None:
        body = (await api.get(f"/ask/{committed['question'].id}")).text

        assert 'id="beliefs"' in body
        assert 'data-other="BETA"' in body
        assert 'data-other="GAMM"' in body
        assert re.search(r"Tier 1", body)
