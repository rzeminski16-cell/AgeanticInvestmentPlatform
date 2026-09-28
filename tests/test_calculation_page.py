"""The calculation page over every kind of leaf a lineage can end in.

The page reads a leaf's detail by key, and the keys differ by relation: a filing line names
the document it came from, a listing does not, and only an assumption the operator confirmed
at a gate carries a justification. Under strict undefined a key read on a leaf that lacks it
is a server error, and it was one for every valuation struck on market weights (ROADMAP §3.19
item 86): a price enters the discount rate, a price's leaf is a listing, and the page
answered 500 to the walk the report's footnotes send a reader on. So this renders the page
over one calculation resting on each leaf the valuation tests never build — a listing, a
stated shock, a planned weight and a question's what-if — rather than over made-up ids,
which resolve to nothing and exercise none of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.calc.basic import ratio
from aer.calc.units import Quantity, SourceRef
from aer.core.enums import JobStatus, ShockKind, UserRole
from aer.db.models import (
    Company,
    Job,
    Portfolio,
    Question,
    RiskScenario,
    RiskScenarioShock,
    Security,
    User,
    WorkOrder,
)
from aer.services import calculations as calculation_service
from aer.services.calculations import new_context
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.request_fixtures import research_request
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration


@pytest.fixture
async def scene(db_engine: Any) -> Any:
    """One committed calculation whose lineage reaches all four leaves."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="leaves@example.invalid", display_name="Leaves", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

        company = Company(name="Testco plc", ticker="TEST", exchange="NASDAQ", cik="0000123456")
        book = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
        listing = Security(
            ticker="TEST", exchange="NASDAQ", provider_symbol="TEST.US", quote_currency="USD"
        )
        mandate = research_request(
            user_id=user.id,
            company_name="Testco plc",
            ticker="TEST",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
            portfolio_context={"planned_weight": "0.05", "purpose": "new_position"},
        )
        session.add_all([company, book, listing, mandate])
        await session.flush()

        scenario = RiskScenario(portfolio_id=book.id, name="A bad year", stated_by=user.email)
        session.add(scenario)
        await session.flush()
        shock = RiskScenarioShock(
            scenario_id=scenario.id,
            position=0,
            kind=ShockKind.BOOK,
            shock=Decimal("-0.20"),
        )
        question = Question(
            user_id=user.id,
            company_id=company.id,
            question="What if the price were ten per cent lower?",
            tier=1,
            tier_rationale="A changed input to a stored model.",
        )
        job = await seed_job(session, request=mandate)
        job.status = JobStatus.SUCCEEDED
        session.add_all([shock, question])
        await session.flush()

        ledger = new_context()
        priced = ratio(
            ledger,
            numerator=Quantity.of(Decimal("410"), source=SourceRef.security(listing.id)),
            denominator=Quantity.of(
                Decimal("0.05"), source=SourceRef.planned_weight(mandate.id, label="planned")
            ),
        )
        stated = ratio(
            ledger,
            numerator=Quantity.of(Decimal("-0.20"), source=SourceRef.scenario_shock(shock.id)),
            denominator=Quantity.of(Decimal("0.90"), source=SourceRef.question(question.id)),
        )
        ratio(ledger, numerator=priced, denominator=stated)
        rows = await calculation_service.persist_context(session, ledger, job_id=job.id)
        await session.commit()
        yield {
            "top": rows[-1].id,
            "rows": [row.id for row in rows],
            "job": job.id,
            "user": user.id,
            "company": company.id,
        }
    await delete_all(db_engine)


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, scene: dict[str, Any]) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


class TestEveryLeafRenders:
    async def test_the_walk_over_all_four_leaves_answers(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get(f"/calculations/{scene['top']}")

        assert page.status_code == 200, page.text[:2000]
        assert 'id="lineage"' in page.text

    @pytest.mark.parametrize("position", [0, 1])
    async def test_each_calculation_over_the_leaves_answers(
        self, api: Any, scene: dict[str, Any], position: int
    ) -> None:
        page = await api.get(f"/calculations/{scene['rows'][position]}")

        assert page.status_code == 200, page.text[:2000]

    async def test_no_leaf_is_shown_as_unresolved(
        self, api: Any, scene: dict[str, Any], db_engine: Any
    ) -> None:
        # A leaf the loader could not find renders as missing rather than failing, which
        # would make the first test pass for the wrong reason.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            tree = await calculation_service.lineage(session, scene["top"])

        assert {leaf.detail.get("table") for leaf in tree.leaves} == {
            "securities",
            "research_requests",
            "risk_scenario_shocks",
            "questions",
        }
        assert all(leaf.is_resolved for leaf in tree.leaves)


class TestTheWayBack:
    """The page links back to where its figure came from (ROADMAP §3.19 item 88).

    Five kinds of work strike calculations, and the breadcrumb said *The valuation* on all of
    them, sending a risk figure's or an answer's walk to a research page about a run that had
    no valuation.
    """

    async def test_a_research_figure_goes_back_to_its_valuation(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = (await api.get(f"/calculations/{scene['top']}")).text

        assert f'href="/runs/{scene["job"]}/valuation"' in page
        assert "The valuation" in page

    async def test_a_risk_figure_goes_back_to_risk(
        self, api: Any, scene: dict[str, Any], db_engine: Any
    ) -> None:
        calculation = await _struck_under(db_engine, scene, tool="risk")

        page = (await api.get(f"/calculations/{calculation}")).text

        assert 'href="/risk"' in page
        assert "The valuation" not in page

    async def test_an_answer_figure_goes_back_to_its_question(
        self, api: Any, scene: dict[str, Any], db_engine: Any
    ) -> None:
        calculation = await _struck_under(db_engine, scene, tool="ask", with_question=True)

        page = (await api.get(f"/calculations/{calculation}")).text

        assert 'href="/ask/' in page
        assert "The answer" in page
        assert "The valuation" not in page


async def _struck_under(
    db_engine: Any, scene: dict[str, Any], *, tool: str, with_question: bool = False
) -> Any:
    """One calculation, struck by a run of another tool for the scene's operator."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        order = WorkOrder(user_id=scene["user"], tool=tool, as_of_date=AS_OF_DATE)
        session.add(order)
        await session.flush()
        job = Job(
            work_order_id=order.id,
            workflow_version="test",
            code_version="test",
            status=JobStatus.SUCCEEDED,
            started_at=datetime.now(UTC),
        )
        session.add(job)
        await session.flush()
        if with_question:
            session.add(
                Question(
                    user_id=scene["user"],
                    company_id=scene["company"],
                    job_id=job.id,
                    question="What if the discount rate were a point higher?",
                    tier=1,
                    tier_rationale="A changed input to a stored model.",
                )
            )
        ledger = new_context()
        ratio(
            ledger,
            numerator=Quantity.of(Decimal("2"), source=SourceRef.assumption("stated")),
            denominator=Quantity.of(Decimal("4"), source=SourceRef.assumption("stated")),
        )
        rows = await calculation_service.persist_context(session, ledger, job_id=job.id)
        await session.commit()
        return rows[-1].id
