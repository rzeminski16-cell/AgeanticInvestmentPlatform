"""Risk is measured over the weights the book holds now, a scenario is a shock the operator
states, and the analyst reads figures it cannot write.

Four layers. The figures: a book with a year of daily closes is measured, one without
enough is named unmeasured rather than filled in, every figure is a record in the ledger,
and a single measured holding's contribution to the book's risk is the whole of it. The
scenarios: a shock reaches what the exposure bands say it reaches, cash in a currency
included and the book's own currency excluded, and a scenario reaching nothing says so.
The reading: the commentary lands on the pass, a numeral the block does not hold is
refused once with the problem carried back and then recorded, and a prescription is refused
by its words. And the pages prove a person can state, withdraw and read.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.agents.risk_analyst import RiskCommentary, RiskInput, commentary_problems
from aer.calc.engine import CalculationContext
from aer.config import Settings
from aer.core.enums import JobStatus, Provider, ShockKind, SourceTier, TransactionKind, UserRole
from aer.db.models import (
    Artefact,
    AuditEvent,
    Calculation,
    Job,
    Portfolio,
    PriceBar,
    Security,
    SourceDocument,
    User,
    WorkOrder,
)
from aer.errors import ConflictError, ExternalServiceError, ValidationError
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import calculations as calculation_service
from aer.services import risk as risk_service
from aer.storage.local import LocalArtefactStore
from aer.web.overview import risk as risk_feed
from aer.web.overview.attention import Severity
from tests import portfolio_fixtures
from tests.api_fixtures import build_app, client_for
from tests.portfolio_fixtures import AS_OF, daily_bars, funded, trade
from tests.schema_guard import refuse_unanswerable_schema

pytestmark = pytest.mark.integration

# The shared book, rebound so pytest finds the fixture under this module's name — the same
# binding `test_performance_service` makes, for the same reason.
book = portfolio_fixtures.book


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


async def _holding_barc(session: AsyncSession, scene: dict[str, Any], *, bars: int = 40) -> None:
    """A funded book holding a hundred Barclays at 250p, with a run of daily closes."""
    await funded(session, scene)
    await trade(
        session,
        scene,
        kind=TransactionKind.BUY,
        security=scene["barc"],
        quantity="100",
        price="250",
        currency="GBX",
    )
    if bars:
        await daily_bars(session, scene["barc"], until=AS_OF, days=bars)


async def _holding_msft(session: AsyncSession, scene: dict[str, Any]) -> None:
    await trade(
        session,
        scene,
        kind=TransactionKind.BUY,
        security=scene["msft"],
        quantity="10",
        price="400",
        currency="USD",
    )
    await daily_bars(session, scene["msft"], until=AS_OF, days=40, close="410")


async def _risk(
    session: AsyncSession, context: CalculationContext, scene: dict[str, Any]
) -> risk_service.RiskView:
    return await risk_service.risk_as_at(
        session, context, portfolio=scene["portfolio"], as_of=AS_OF
    )


def _shock(kind: ShockKind, target: str = "", shock: str = "-0.2") -> risk_service.Shock:
    return risk_service.Shock(kind=kind, target=target, shock=Decimal(shock))


async def _state(
    session: AsyncSession, scene: dict[str, Any], name: str, *shocks: risk_service.Shock
) -> Any:
    return await risk_service.state_scenario(
        session, actor=scene["user"], portfolio=scene["portfolio"], name=name, shocks=list(shocks)
    )


def _commentary(**overrides: str) -> RiskCommentary:
    fields = {
        "exposure": "Everything priced is one London bank, so the sector and country bands "
        "are the same slice under two names.",
        "movement": "The drawdown and the volatility are that one holding's, and the cash "
        "beside it is what keeps the book's figures below the holding's.",
        "scenarios": "",
    }
    fields.update(overrides)
    return RiskCommentary(**fields)


def _provider(commentary: RiskCommentary | None = None) -> FakeProvider:
    return FakeProvider(
        {"RiskCommentary": commentary or _commentary()},
        inspect_schema=refuse_unanswerable_schema,
    )


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        http_user_agent="Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
        **overrides,
    )


async def _read(
    session: AsyncSession,
    scene: dict[str, Any],
    tmp_path: Path,
    *,
    provider: FakeProvider,
    **overrides: Any,
) -> Any:
    settings = _settings(tmp_path, **overrides)
    return await risk_service.run_reading(
        session,
        settings=settings,
        provider=provider,
        router=Router(settings),
        store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
        user=scene["user"],
        portfolio=scene["portfolio"],
        as_of=AS_OF,
    )


# -- The figures ----------------------------------------------------------------------------


class TestTheFigures:
    async def test_a_book_with_history_is_measured(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)

        view = await _risk(db_session, context, book)

        assert view.is_measured
        assert view.volatility is not None
        assert view.volatility.value > 0
        assert view.drawdown is not None
        assert Decimal(-1) < view.drawdown.value < 0
        assert view.expected_shortfall is not None
        assert view.expected_shortfall.value < 0
        assert view.observations >= 24
        assert view.window_from == AS_OF - timedelta(days=365)
        # Coverage is the holding's weight: everything else in the book is cash.
        [holding] = view.holdings
        assert holding.is_measured
        assert holding.weight is not None
        assert view.coverage is not None
        assert view.coverage.value == holding.weight.value
        assert view.coverage.value < 1

    async def test_one_measured_holding_is_the_whole_of_the_risk(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """ADR 0106's check: contributions to the book's own series sum to one. With one
        measured holding its beta to the book is the inverse of its weight, and weight
        times that is exactly one."""
        await _holding_barc(db_session, book)

        view = await _risk(db_session, context, book)

        [holding] = view.holdings
        assert holding.contribution is not None
        assert abs(holding.contribution.value - 1) < Decimal("1e-12")
        assert holding.beta_to_book is not None
        assert holding.beta_to_book.value > 1

    async def test_two_holdings_share_the_risk(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)
        await _holding_msft(db_session, book)

        view = await _risk(db_session, context, book)

        assert len(view.measured) == 2
        total = sum(row.contribution.value for row in view.measured if row.contribution)
        assert abs(total - 1) < Decimal("1e-12")

    async def test_every_figure_is_a_recorded_calculation(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)

        view = await _risk(db_session, context, book)

        names = {record.name for record in context.records}
        assert {
            "annualised_volatility",
            "max_drawdown",
            "expected_shortfall",
            "variance",
            "covariance",
            "beta",
            "risk_contribution",
            "exposure",
        } <= names
        assert view.volatility is not None
        [record] = [row for row in context.records if row.id == view.volatility.record.id]
        assert record.parameters["periods_per_year"] == 252

    async def test_a_holding_without_enough_history_is_named_unmeasured(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """Left out and named, never filled in: a figure over a holding with no history
        would be a figure about a different book."""
        await _holding_barc(db_session, book, bars=0)

        view = await _risk(db_session, context, book)

        assert not view.is_measured
        assert "enough price history" in view.problem
        [holding] = view.holdings
        assert not holding.is_measured
        assert "at least 24" in holding.problem
        assert view.coverage is None

    async def test_an_empty_book_has_no_risk_figure(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        view = await _risk(db_session, context, book)

        assert not view.is_measured
        assert view.problem
        assert view.holdings == ()


# -- Scenarios ------------------------------------------------------------------------------


class TestAScenario:
    async def test_the_whole_book_down_a_fifth(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)
        await _state(db_session, book, "Everything down a fifth", _shock(ShockKind.BOOK))

        view = await _risk(db_session, context, book)

        [outcome] = view.scenarios
        assert outcome.reached == ("BARC",)
        assert outcome.pnl is not None
        # A hundred shares at £2.50, down a fifth.
        assert outcome.pnl.value == Decimal("-50")
        assert outcome.pnl.unit.symbol == "GBP"
        assert outcome.impact is not None
        assert view.book.net_assets is not None
        assert outcome.impact.value == Decimal("-50") / view.book.net_assets.value

    async def test_a_currency_shock_reaches_cash_in_it_and_never_the_books_own(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)
        await _holding_msft(db_session, book)
        await trade(
            db_session,
            book,
            kind=TransactionKind.DEPOSIT,
            security=None,
            price=None,
            quantity="1000",
            currency="USD",
            on=date(2026, 6, 2),
        )
        await _state(
            db_session, book, "Dollar down a tenth", _shock(ShockKind.CURRENCY, "USD", "-0.1")
        )
        await _state(
            db_session, book, "Sterling down a tenth", _shock(ShockKind.CURRENCY, "GBP", "-0.1")
        )

        view = await _risk(db_session, context, book)

        dollar, sterling = view.scenarios
        assert set(dollar.reached) == {"MSFT", "USD cash"}
        assert dollar.pnl is not None
        assert dollar.pnl.value < 0
        assert sterling.reached == ()
        assert "reaches nothing" in sterling.problem

    async def test_two_shocks_on_one_holding_compound(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)
        await _state(
            db_session,
            book,
            "Twice",
            _shock(ShockKind.BOOK, shock="-0.2"),
            _shock(ShockKind.HOLDING, "barc", "-0.1"),
        )

        view = await _risk(db_session, context, book)

        [outcome] = view.scenarios
        assert outcome.pnl is not None
        # (1 - 0.2)(1 - 0.1) - 1 = -0.28, on £250.
        assert outcome.pnl.value == Decimal("-70")

    async def test_a_sector_nobody_has_named_reaches_nothing(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)
        await _state(db_session, book, "Banks", _shock(ShockKind.SECTOR, "Banks"))

        view = await _risk(db_session, context, book)

        [outcome] = view.scenarios
        assert outcome.pnl is None
        assert "reaches nothing" in outcome.problem

    async def test_a_scenario_is_on_the_audit_chain_and_can_be_withdrawn(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        scenario = await _state(db_session, book, "Everything", _shock(ShockKind.BOOK))

        event = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "risk.scenario_stated")
        )
        assert event is not None
        assert event.subject_kind == "risk_scenario"
        assert event.subject_id == scenario.id
        assert scenario.stated_by == book["user"].email
        assert [row.kind for row in scenario.shocks] == [ShockKind.BOOK]

        await risk_service.withdraw_scenario(db_session, actor=book["user"], scenario=scenario)

        assert await risk_service.scenarios_for(db_session, portfolio=book["portfolio"]) == []
        [kept] = await risk_service.scenarios_for(
            db_session, portfolio=book["portfolio"], include_withdrawn=True
        )
        assert kept.is_withdrawn
        with pytest.raises(ConflictError, match="already withdrawn"):
            await risk_service.withdraw_scenario(db_session, actor=book["user"], scenario=kept)

    @pytest.mark.parametrize(
        ("name", "shocks", "match"),
        [
            ("  ", [_shock(ShockKind.BOOK)], "needs a name"),
            ("Nothing", [], "at least one shock"),
            ("Nil", [_shock(ShockKind.BOOK, shock="0")], "moves nothing"),
            ("Wipeout", [_shock(ShockKind.BOOK, shock="-1")], "nil or below"),
            ("Somewhere", [_shock(ShockKind.SECTOR, "")], "needs a target"),
        ],
    )
    async def test_a_scenario_that_does_not_say_what_it_moves_is_refused(
        self,
        db_session: AsyncSession,
        book: dict[str, Any],
        name: str,
        shocks: list[risk_service.Shock],
        match: str,
    ) -> None:
        with pytest.raises(ValidationError, match=match):
            await _state(db_session, book, name, *shocks)

    async def test_another_persons_book_is_refused(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        other = User(email="other@example.invalid", display_name="Other", role=UserRole.OWNER)
        db_session.add(other)
        await db_session.flush()

        with pytest.raises(ConflictError, match="whose book"):
            await risk_service.state_scenario(
                db_session,
                actor=other,
                portfolio=book["portfolio"],
                name="Theirs",
                shocks=[_shock(ShockKind.BOOK)],
            )

    async def test_the_shock_resolves_in_the_provenance_viewer(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """ADR 0076: a lineage node resolves by table. A scenario profit and loss rests on
        the operator's stated shock, and the reader can open it."""
        await _holding_barc(db_session, book)
        scenario = await _state(db_session, book, "Everything", _shock(ShockKind.BOOK))
        view = await _risk(db_session, context, book)
        [outcome] = view.scenarios
        assert outcome.pnl is not None
        order = WorkOrder(user_id=book["user"].id, as_of_date=AS_OF, point_in_time=False)
        db_session.add(order)
        await db_session.flush()
        job = Job(
            work_order_id=order.id,
            workflow_version="test",
            code_version="test",
            status=JobStatus.SUCCEEDED,
        )
        db_session.add(job)
        await db_session.flush()
        await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, outcome.pnl.record.id)

        found = [node for node in tree.walk() if node.detail.get("table") == "risk_scenario_shocks"]
        assert found, "the shock is not in the lineage"
        assert found[0].kind == "assumption"
        assert found[0].detail["scenario"] == scenario.name


# -- The deterministic edge ------------------------------------------------------------------


class TestAShockIsANumberTheColumnCanHold:
    async def test_a_shock_that_is_not_a_number_is_refused(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        """ "nan" parses as a Decimal and then poisons every comparison as an unhandled
        error; refused where the operator is, with a sentence."""
        with pytest.raises(ValidationError, match="not a number"):
            await _state(db_session, book, "Nonsense", _shock(ShockKind.BOOK, shock="NaN"))

    async def test_a_shock_finer_than_six_places_is_settled_before_it_is_judged(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        """The column holds six places: 0.0000001 would round to nothing on the way in and
        fail the row's own check as a database error, so it is refused as moving nothing."""
        with pytest.raises(ValidationError, match="moves nothing"):
            await _state(db_session, book, "Too fine", _shock(ShockKind.BOOK, shock="0.0000001"))

        stated = await _state(
            db_session, book, "Fine enough", _shock(ShockKind.BOOK, shock="-0.1234567")
        )

        assert [row.shock for row in stated.shocks] == [Decimal("-0.123457")]


class TestTwoListingsOfOneIssuer:
    async def test_they_are_measured_apart(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        """Securities are unique on (ticker, exchange); a series keyed by ticker alone let
        one listing overwrite the other's returns and weight."""
        await _holding_barc(db_session, book)
        twin = Security(
            company_id=book["barc"].company_id,
            ticker="BARC",
            exchange="NYSE",
            provider_symbol="BARC.US",
            name="Barclays plc ADR",
            quote_currency="USD",
        )
        db_session.add(twin)
        await db_session.flush()
        await trade(
            db_session,
            book,
            kind=TransactionKind.BUY,
            security=twin,
            quantity="10",
            price="8",
            currency="USD",
        )
        await daily_bars(db_session, twin, until=AS_OF, days=40, close="8.5")

        view = await _risk(db_session, context, book)

        measured = [row for row in view.holdings if row.problem == ""]
        assert len(measured) == 2
        assert {row.security.exchange for row in measured} == {"LSE", "NYSE"}


class TestAReadingThatFailsOnSomethingElse:
    async def test_it_is_a_failed_pass_with_its_reason_and_its_spend(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        """Not the budget: an outage, a reply billed and refused. The page commits what the
        service returns, so failing the pass here keeps the cost row; a rollback would not."""
        await _holding_barc(db_session, book)
        broken = FakeProvider(
            fail_with=ExternalServiceError("the provider is unavailable", provider="anthropic")
        )

        job = await _read(db_session, book, tmp_path, provider=broken)

        assert job.status is JobStatus.FAILED
        assert job.error is not None
        assert "unavailable" in job.error["message"]


class TestTheCommentarysEdge:
    def _block(self) -> RiskInput:
        return RiskInput(
            book_name="ISA",
            currency="GBP",
            as_of="2026-06-30",
            window="2025-06-30 to 2026-06-30, daily",
            coverage="83.3% of net assets measured",
            book=[
                {
                    "label": "Annualised volatility",
                    "value": "21.4%",
                    "note": "over 39 daily returns",
                }
            ],
            holdings=[{"ticker": "BARC", "weight": "83.3%", "volatility": "25.7%"}],
        )

    def test_a_figure_quoted_as_given_passes(self) -> None:
        commentary = _commentary(
            movement="A volatility of 21.4% is the one holding's, at 83.3% of the book."
        )

        assert commentary_problems(commentary, self._block()) == []

    def test_a_number_of_its_own_is_refused_by_name(self) -> None:
        commentary = _commentary(
            movement="Roughly a fifth, so the book could lose 37% in a bad year."
        )

        [problem] = commentary_problems(commentary, self._block())
        assert "37" in problem
        assert "movement" in problem

    def test_a_prescription_is_refused_by_its_words(self) -> None:
        commentary = _commentary(exposure="You should trim the largest holding and set a limit.")

        problems = commentary_problems(commentary, self._block())
        assert any("should trim" in problem for problem in problems)
        assert any("set a limit" in problem for problem in problems)

    def test_the_output_has_no_field_for_a_figure(self) -> None:
        with pytest.raises(ValueError, match="extra"):
            RiskCommentary(exposure="x", movement="y", risk_score="7")  # type: ignore[call-arg]


# -- The reading ----------------------------------------------------------------------------


class TestTheReading:
    async def test_the_commentary_lands_on_the_pass_with_its_ledger(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        await _holding_barc(db_session, book)
        provider = _provider()

        job = await _read(db_session, book, tmp_path, provider=provider)

        assert job.status is JobStatus.SUCCEEDED
        assert provider.call_count == 1
        order = await db_session.get(WorkOrder, job.work_order_id)
        assert order is not None
        assert order.tool == risk_service.TOOL
        assert order.subject_id == book["portfolio"].id
        reading = await risk_service.latest_reading(db_session, portfolio=book["portfolio"])
        assert reading is not None
        assert reading.job.id == job.id
        assert reading.commentary is not None
        assert reading.commentary.exposure.startswith("Everything priced")
        assert reading.refusals == []
        assert reading.as_of == AS_OF
        assert reading.output["block"]["holdings"][0]["ticker"] == "BARC"
        recorded = {
            row.name
            for row in await db_session.scalars(
                select(Calculation).where(Calculation.job_id == job.id)
            )
        }
        assert "annualised_volatility" in recorded

    async def test_a_number_of_its_own_is_refused_once_with_the_problem_and_then_recorded(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        await _holding_barc(db_session, book)
        provider = _provider(_commentary(movement="The book could lose 37% in a bad year."))

        job = await _read(db_session, book, tmp_path, provider=provider)

        assert job.status is JobStatus.SUCCEEDED
        assert provider.call_count == 2
        reading = await risk_service.reading_of(db_session, job.id, user_id=book["user"].id)
        assert reading is not None
        assert reading.commentary is None
        assert reading.output["attempts"] == 2
        [problem] = reading.refusals
        assert "37" in problem
        assert reading.output["last_draft"]["movement"].startswith("The book could lose")

    async def test_a_book_with_nothing_priced_is_not_sent_to_the_model(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        provider = _provider()

        job = await _read(db_session, book, tmp_path, provider=provider)

        assert job.status is JobStatus.SUCCEEDED
        assert provider.call_count == 0
        reading = await risk_service.latest_reading(db_session, portfolio=book["portfolio"])
        assert reading is not None
        assert reading.nothing_to_read
        assert reading.commentary is None

    async def test_a_pass_that_hits_its_ceiling_stops(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        await _holding_barc(db_session, book)

        job = await _read(
            db_session, book, tmp_path, provider=_provider(), per_run_budget_gbp=Decimal("0.01")
        )

        assert job.status is JobStatus.FAILED
        reading = await risk_service.latest_reading(db_session, portfolio=book["portfolio"])
        assert reading is not None
        assert reading.failed
        assert reading.reason

    async def test_another_persons_book_is_refused(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        other = User(email="other@example.invalid", display_name="Other", role=UserRole.OWNER)
        db_session.add(other)
        await db_session.flush()
        settings = _settings(tmp_path)

        with pytest.raises(ConflictError, match="whose book"):
            await risk_service.run_reading(
                db_session,
                settings=settings,
                provider=_provider(),
                router=Router(settings),
                store=LocalArtefactStore(
                    settings.artefact_root, max_bytes=settings.max_artefact_bytes
                ),
                user=other,
                portfolio=book["portfolio"],
                as_of=AS_OF,
            )


# -- The work list --------------------------------------------------------------------------


class TestTheWorkList:
    async def test_an_unread_book_is_not_started(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _holding_barc(db_session, book)

        [item] = await risk_feed.items(db_session, user_id=book["user"].id)

        assert item.severity is Severity.IDLE
        assert "has not been read" in item.title
        assert item.href == "/risk"

    async def test_an_empty_book_asks_nothing(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        assert await risk_feed.items(db_session, user_id=book["user"].id) == []

    async def test_a_read_book_asks_nothing_until_it_trades(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        await _holding_barc(db_session, book)
        await _read(db_session, book, tmp_path, provider=_provider())

        assert await risk_feed.items(db_session, user_id=book["user"].id) == []

        await trade(
            db_session,
            book,
            kind=TransactionKind.BUY,
            security=book["barc"],
            quantity="10",
            price="250",
            currency="GBX",
            on=date(2026, 6, 20),
            recorded_at=datetime.now(UTC) + timedelta(seconds=5),
        )

        [item] = await risk_feed.items(db_session, user_id=book["user"].id)
        assert item.severity is Severity.IDLE
        assert "has changed since" in item.title

    async def test_a_stopped_reading_needs_diagnosis(
        self, db_session: AsyncSession, book: dict[str, Any], tmp_path: Path
    ) -> None:
        await _holding_barc(db_session, book)
        await _read(
            db_session, book, tmp_path, provider=_provider(), per_run_budget_gbp=Decimal("0.01")
        )

        [item] = await risk_feed.items(db_session, user_id=book["user"].id)
        assert item.severity is Severity.BROKEN
        assert "stopped" in item.title


# -- The pages -------------------------------------------------------------------------------


_TABLES = "audit_events, users, artefacts, companies, securities, portfolios, work_orders"


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    """A committed book holding Barclays with a year of closes, seen by the application.

    Emptied before seeding as well as after: the application's operator is the *earliest*
    user in the table, and a user another module committed and did not clear would be the
    one the page renders for — a book of nobody's, with no figures on it.
    """
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        artefact = Artefact(
            sha256="d" * 64, size_bytes=32, media_type="text/csv", storage_key="dd/d"
        )
        session.add_all([user, artefact])
        await session.flush()
        portfolio = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
        barc = Security(
            ticker="BARC", exchange="LSE", provider_symbol="BARC.LSE", quote_currency="GBX"
        )
        session.add_all([portfolio, barc])
        await session.flush()
        order = WorkOrder(user_id=user.id, as_of_date=AS_OF, point_in_time=False)
        session.add(order)
        await session.flush()
        document = SourceDocument(
            work_order_id=order.id,
            artefact_id=artefact.id,
            url="https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
            provider=Provider.ECB,
            source_tier=SourceTier.T3_OFFICIAL_STATS,
            title="ECB euro reference rates",
            retrieved_at=datetime.now(UTC),
        )
        session.add(document)
        await session.flush()
        session.add(
            PriceBar(
                security_id=barc.id,
                bar_date=AS_OF,
                open=Decimal("248"),
                high=Decimal("252"),
                low=Decimal("247"),
                close=Decimal("250"),
            )
        )
        scene = {"user": user, "portfolio": portfolio, "barc": barc, "document": document}
        await _holding_barc(session, scene)
        await session.commit()
        yield scene
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, committed: Any) -> Any:
    store = LocalArtefactStore(
        api_settings.artefact_root, max_bytes=api_settings.max_artefact_bytes
    )
    app = build_app(
        api_settings, engine=db_engine, redis=fake_redis, provider=_provider(), store=store
    )
    async for client in client_for(app):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return str(found.group(1))


class TestThePages:
    async def test_the_page_shows_the_figures_with_their_lineage(
        self, api: Any, committed: Any
    ) -> None:
        body = (await api.get(f"/risk?as_of={AS_OF.isoformat()}")).text

        assert 'data-figure="annualised-volatility"' in body
        assert 'data-figure="maximum-drawdown"' in body
        # Computed on the way to the page and persisted nowhere, so no link is offered:
        # a link to a calculation row that does not exist is a dead link.
        assert "/calculations/" not in body
        assert 'data-holding="BARC" data-measured="yes"' in body
        assert "holding is measured" in body
        assert "No scenario stated" in body
        assert "has not read this book" in body

    async def test_state_a_scenario_read_the_book_and_withdraw(
        self, api: Any, committed: Any
    ) -> None:
        page = await api.get(f"/risk?as_of={AS_OF.isoformat()}")
        stated = await api.post(
            "/risk/scenarios",
            data={
                "csrf_token": _csrf(page.text),
                "name": "Everything down a fifth",
                "kind_1": "book",
                "target_1": "",
                "shock_1": "-20",
                "kind_2": "holding",
                "target_2": "BARC",
                "shock_2": "",
            },
        )
        assert stated.status_code == 303, stated.text

        body = (await api.get(f"/risk?as_of={AS_OF.isoformat()}")).text
        assert "Everything down a fifth" in body
        assert 'data-loss="yes"' in body
        assert "-50.00 GBP" in body

        read = await api.post(
            "/risk/read", data={"csrf_token": _csrf(body), "as_of": AS_OF.isoformat()}
        )
        assert read.status_code == 303, read.text
        body = (await api.get(read.headers["location"])).text
        assert 'data-commentary="exposure"' in body
        assert "Read by the analyst on" in body

        scenario_id = re.search(r'data-scenario="([0-9a-f-]{36})"', body)
        assert scenario_id is not None
        withdrawn = await api.post(
            f"/risk/scenarios/{scenario_id.group(1)}/withdraw", data={"csrf_token": _csrf(body)}
        )
        assert withdrawn.status_code == 303
        assert "Everything down a fifth" not in (await api.get("/risk")).text

    async def test_a_scenario_is_shown_position_by_position(self, api: Any, committed: Any) -> None:
        """The scenario as a diff of the book: each reached position with what it is worth,
        what it takes and what that costs — each its own recorded calculation, summing to
        the total on the row above."""
        page = await api.get(f"/risk?as_of={AS_OF.isoformat()}")
        stated = await api.post(
            "/risk/scenarios",
            data={
                "csrf_token": _csrf(page.text),
                "name": "Everything down a fifth",
                "kind_1": "book",
                "target_1": "",
                "shock_1": "-20",
            },
        )
        assert stated.status_code == 303, stated.text

        body = (await api.get(f"/risk?as_of={AS_OF.isoformat()}")).text

        assert "What Everything down a fifth does, position by position" in body
        assert 'data-position="BARC" data-loss="yes"' in body
        assert "-20.0%" in body
        assert body.count("-50.00 GBP") == 2, "the one position's loss is the scenario's total"

    async def test_the_weight_and_the_contribution_are_bars_beside_their_figures(
        self, api: Any, committed: Any
    ) -> None:
        body = (await api.get(f"/risk?as_of={AS_OF.isoformat()}")).text

        assert 'data-bar="weight"' in body
        assert 'data-bar="contribution"' in body
        # One measured holding is the whole of the book's risk: the bar runs the full width.
        full_width = (
            'data-bar="contribution"><div class="h-1 rounded-full bg-decision" style="width: 100%"'
        )
        assert full_width in body

    async def test_the_reading_sits_beside_the_sheet_it_reads(
        self, api: Any, committed: Any
    ) -> None:
        page = await api.get(f"/risk?as_of={AS_OF.isoformat()}")
        read = await api.post(
            "/risk/read", data={"csrf_token": _csrf(page.text), "as_of": AS_OF.isoformat()}
        )
        assert read.status_code == 303, read.text

        body = (await api.get(read.headers["location"])).text

        # The exposure commentary is inside the exposure sheet, before the holdings sheet,
        # and the movement commentary inside the movement sheet before the exposure sheet.
        assert body.index('id="movement"') < body.index('data-commentary="movement"')
        assert body.index('data-commentary="movement"') < body.index('id="exposure"')
        assert body.index('id="exposure"') < body.index('data-commentary="exposure"')
        assert body.index('data-commentary="exposure"') < body.index('id="holdings"')
        assert "Each commentary sits beside the sheet it reads" in body

    async def test_a_shock_that_is_not_a_number_is_refused(self, api: Any, committed: Any) -> None:
        page = await api.get("/risk")
        response = await api.post(
            "/risk/scenarios",
            data={
                "csrf_token": _csrf(page.text),
                "name": "Nonsense",
                "kind_1": "book",
                "target_1": "",
                "shock_1": "a fifth",
            },
        )

        assert response.status_code == 400
        assert "must be a number" in response.text

    async def test_a_form_without_a_token_is_refused(self, api: Any, committed: Any) -> None:
        response = await api.post("/risk/read", data={"as_of": AS_OF.isoformat()})

        assert response.status_code == 403
        assert "Nothing was read" in response.text

    async def test_a_person_with_no_book_is_pointed_at_the_portfolio(
        self, api_settings: Any, db_engine: Any, fake_redis: Any
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            session.add(User(email="new@example.invalid", display_name="New", role=UserRole.OWNER))
            await session.commit()
        try:
            async for client in client_for(
                build_app(api_settings, engine=db_engine, redis=fake_redis)
            ):
                body = (await client.get("/risk")).text
                assert "No book to read" in body
                assert 'href="/portfolio"' in body
        finally:
            async with db_engine.begin() as connection:
                await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
