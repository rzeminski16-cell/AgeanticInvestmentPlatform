"""The valuation drill-down, driven by a real browser.

What only a browser can prove: that the two clicks are actually clicks. The in-process suite
walks the ``href`` attributes, which catches a broken link but would still pass if the link
were invisible, covered, or rendered outside the page. Here a reader reaches the arithmetic
the way a reader reaches it.

The other thing a browser proves is the "works with JavaScript off" claim in its strongest
form: with scripting disabled entirely, the figures and the lineage are still on the page.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.calc.dcf import DcfInputs, DriverPath, GridAxis, GridMeasure, TerminalMethod
from aer.calc.units import Quantity, SourceRef, money
from aer.config import load_settings
from aer.core.enums import JobStatus
from aer.core.sectors import ValuationModel, unclassified_mandate
from aer.db.models import ResearchRequest, User
from aer.services import valuation as valuation_service
from tests.db_fixtures import run_async
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP, seed_job

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# The user the live server seeds. `get_current_user` returns the earliest-created user, so a
# valuation built under a *second* account would be invisible and every assertion here would
# fail as a 404 rather than as the thing it was checking.
EMAIL = "e2e@example.invalid"

ASSUMPTION = SourceRef.assumption("assumption-1")
FACT = SourceRef.fact("fact-1")
MANDATE = unclassified_mandate(ValuationModel.DCF_FCFF, subject="TESTCO")


def rate(value: str) -> Quantity:
    return Quantity.of(Decimal(value), source=ASSUMPTION)


def usd(value: str) -> Quantity:
    return money(value, "USD", source=FACT)


def flat(name: str, value: str) -> DriverPath:
    return DriverPath.flat(name, rate(value), years=3)


def inputs() -> DcfInputs:
    return DcfInputs(
        base_revenue=usd("1000"),
        revenue_growth=DriverPath("revenue_growth", (rate("0.10"), rate("0.08"), rate("0.06"))),
        ebit_margin=flat("ebit_margin", "0.20"),
        capex_intensity=flat("capex_intensity", "0.08"),
        depreciation_intensity=flat("depreciation_intensity", "0.05"),
        working_capital_intensity=flat("working_capital_intensity", "0.10"),
        opening_working_capital=usd("100"),
        tax_rate=rate("0.25"),
        wacc=rate("0.10"),
        terminal_growth=rate("0.02"),
        exit_multiple=rate("4.5"),
        net_debt=usd("500"),
        shares_outstanding=Quantity.of(Decimal(100), "shares", source=FACT),
        non_operating=(),
    )


class ValuationFixture:
    """A run with a real valuation and a real grid in the live server's database."""

    def __init__(self, database_url: str) -> None:
        self._settings = load_settings()
        self._database_url = database_url
        self.job_id: uuid.UUID = run_async(self._create())

    async def _create(self) -> uuid.UUID:
        # A throwaway engine per operation, pooling nothing; an asyncpg connection must not
        # outlive the loop it was opened on. See `RunFixture` in `test_run_console`.
        engine = create_async_engine(self._database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                job_id = await self._build(session)
                await session.commit()
                return job_id
        finally:
            await engine.dispose()

    async def _build(self, session: AsyncSession) -> uuid.UUID:
        # The live server seeds this user at startup. Looked up rather than created:
        # `get_current_user` returns the earliest-created one, so a second account would make
        # every page a 404 and every assertion here fail for the wrong reason.
        analyst = await session.scalar(select(User).order_by(User.created_at))
        assert analyst is not None, "the live_server fixture seeds one"

        research_request = ResearchRequest(
            user_id=analyst.id,
            company_name="Testco plc",
            ticker="TEST",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            point_in_time=True,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            # The platform default, read rather than restated (A33).
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        session.add(research_request)
        await session.flush()

        job = await seed_job(session, request=research_request)
        job.status = JobStatus.SUCCEEDED
        await session.flush()

        await valuation_service.run_valuation(
            session, job_id=job.id, inputs=inputs(), mandate=MANDATE
        )
        await valuation_service.run_sensitivity(
            session,
            request_id=research_request.id,
            job_id=job.id,
            inputs=inputs(),
            rows=GridAxis(field="wacc", values=(rate("0.09"), rate("0.10"), rate("0.11"))),
            columns=GridAxis(
                field="terminal_growth", values=(rate("0.01"), rate("0.02"), rate("0.03"))
            ),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=MANDATE,
            label="WACC against terminal growth",
        )
        return job.id


@pytest.fixture
def valuation(live_server: str, database_url: str) -> ValuationFixture:
    return ValuationFixture(database_url)


class TestTheDrillDownIsReachableByClicking:
    def test_two_clicks_from_the_console_reach_the_arithmetic(
        self, page: Page, live_server: str, valuation: ValuationFixture
    ) -> None:
        """Task 31's acceptance criterion, performed rather than inspected."""
        page.goto(f"{live_server}/runs/{valuation.job_id}")

        page.click("#valuation-link")
        page.wait_for_url(f"**/runs/{valuation.job_id}/valuation")

        page.click("#figure-gordon_growth-value_per_share")

        expect(page.locator("#output-value")).to_be_visible()
        expect(page.locator("#formula")).to_contain_text("value per share")
        expect(page.locator("#lineage")).to_be_visible()

    def test_a_third_click_reaches_what_that_figure_rests_on(
        self, page: Page, live_server: str, valuation: ValuationFixture
    ) -> None:
        """The chain does not stop at one level: an input is itself a calculation with
        inputs, and following one has to land on a page rather than a 404."""
        page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")
        page.click("#figure-gordon_growth-value_per_share")

        page.locator('#lineage a[href^="/calculations/"]').first.click()

        expect(page.locator("#output-value")).to_be_visible()
        expect(page.locator("#lineage")).to_be_visible()

    def test_a_grid_cell_is_a_valuation_of_its_own(
        self, page: Page, live_server: str, valuation: ValuationFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")

        page.locator('#grid-0 a[href^="/calculations/"]').first.click()

        expect(page.locator("#output-value")).to_be_visible()


class TestWhatTheReaderIsTold:
    def test_both_terminal_methods_are_on_the_page(
        self, page: Page, live_server: str, valuation: ValuationFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")

        expect(page.locator("#terminal-methods")).to_be_visible()
        expect(page.locator("#terminal-methods")).to_contain_text("Gordon growth")
        expect(page.locator("#terminal-methods")).to_contain_text("Exit multiple")

    def test_the_terminal_share_is_visible_beside_the_figure(
        self, page: Page, live_server: str, valuation: ValuationFixture
    ) -> None:
        """Not in a footnote. These inputs put most of the answer beyond the forecast, and a
        reader has to meet that where they meet the number."""
        page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")

        expect(page.locator("#figure-gordon_growth-terminal_share")).to_be_visible()
        expect(page.locator("#high-terminal-gordon_growth")).to_be_visible()

    def test_the_two_methods_disagreeing_is_stated(
        self, page: Page, live_server: str, valuation: ValuationFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")

        expect(page.locator("#method-disagreement")).to_be_visible()

    def test_a_run_with_no_comps_says_so(
        self, page: Page, live_server: str, valuation: ValuationFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")

        expect(page.locator("#no-comps")).to_contain_text("nobody can defend")


class TestWithScriptingOff:
    def test_the_valuation_table_renders(
        self, browser: Browser, live_server: str, valuation: ValuationFixture
    ) -> None:
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")

            expect(page.locator("#terminal-methods")).to_be_visible()
            expect(page.locator("#grid-0")).to_be_visible()
        finally:
            context.close()

    def test_the_drill_down_still_works(
        self, browser: Browser, live_server: str, valuation: ValuationFixture
    ) -> None:
        """Links are links. Nothing on this path depends on a script."""
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/runs/{valuation.job_id}/valuation")

            page.click("#figure-gordon_growth-value_per_share")

            expect(page.locator("#lineage")).to_be_visible()
        finally:
            context.close()
