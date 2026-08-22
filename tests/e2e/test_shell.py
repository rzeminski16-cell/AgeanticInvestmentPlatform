"""The shell's own behaviour, driven by a real browser.

The badge count is the first thing in this codebase that a page renders empty and fills
afterwards, and the two ways it can be wrong are both invisible to an in-process test. A
swap that lands nowhere leaves a slot blank for ever and htmx says nothing about it; a swap
that replaces the live region rather than its contents fills the slot correctly and stops
announcing anything, to exactly the readers who need it announced. Both need htmx to have
actually run, so both are here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.core.enums import JobStatus
from aer.db.models import Job, ResearchRequest, User
from aer.services.runs import awaiting_approval_count
from tests.db_fixtures import run_async
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# The fragment is fetched on `load`, so the number arrives a round trip after the page.
BADGE_TIMEOUT_MS = 10_000


class StoppedRuns:
    """Runs parked at a gate in the live server's database.

    Built directly rather than by driving the workflow: what is under test is the sidebar,
    and a run's status is the only thing about it the count reads.
    """

    def __init__(self, database_url: str, *, count: int) -> None:
        self._database_url = database_url
        self.count = count
        run_async(self._create())

    async def _create(self) -> None:
        # A throwaway engine per operation, pooling nothing; see `RunFixture` in
        # `test_run_console` for why an asyncpg connection must not outlive its loop.
        engine = create_async_engine(self._database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                user = await session.scalar(select(User))
                assert user is not None, "the live_server fixture seeds one"

                for index in range(self.count):
                    request = ResearchRequest(
                        user_id=user.id,
                        company_name=f"Contoso {index}",
                        ticker=f"CTS{index}",
                        exchange="NASDAQ",
                        as_of_date=AS_OF_DATE,
                        point_in_time=True,
                        base_currency="USD",
                        investment_horizon_months=12,
                        max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
                    )
                    session.add(request)
                    await session.flush()
                    session.add(
                        Job(
                            work_order_id=request.id,
                            request_id=request.id,
                            workflow_version="test",
                            code_version="abc",
                            status=JobStatus.AWAITING_APPROVAL,
                            started_at=datetime.now(UTC),
                        )
                    )
                await session.commit()
        finally:
            await engine.dispose()


@pytest.fixture
def stopped_runs(live_server: str, database_url: str) -> StoppedRuns:
    return StoppedRuns(database_url, count=2)


def _number(page: Page) -> Any:
    # The visible half. The slot also holds an `sr-only` sentence, so asking the slot
    # itself for its text would get the digit twice.
    return page.locator('#aer-badge-approvals > span[aria-hidden="true"]')


class TestTheCountArrivesAfterThePage:
    def test_the_slot_fills_with_the_number_of_stopped_runs(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/reports")

        expect(_number(page)).to_have_text(str(stopped_runs.count), timeout=BADGE_TIMEOUT_MS)

    def test_it_says_what_it_counted(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        # A bare numeral beside a word is read as "Requests 2" and means nothing.
        page.goto(f"{live_server}/reports")

        expect(page.locator("#aer-badge-approvals .sr-only")).to_have_text(
            f"{stopped_runs.count} runs waiting for your approval", timeout=BADGE_TIMEOUT_MS
        )

    def test_the_live_region_survives_the_swap(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        """ADR 0073's second gap, proved rather than asserted about a template.

        `hx-swap-oob="true"` — the default — would replace the whole element with the one
        the server sent, and the server's copy carries no `aria-live`. The slot would still
        show the right number and would never announce another one. This is the difference
        between the two swap modes, read off the live DOM after htmx has run.
        """
        page.goto(f"{live_server}/reports")
        expect(_number(page)).to_have_text(str(stopped_runs.count), timeout=BADGE_TIMEOUT_MS)

        expect(page.locator("#aer-badge-approvals")).to_have_attribute("aria-live", "polite")

    def test_a_run_nobody_stopped_leaves_the_slot_empty(self, page: Page, live_server: str) -> None:
        """No runs, no pill — not a zero.

        `empty:hidden` keys on the slot having no content at all, so this is also the check
        that the first paint renders nothing rather than whitespace.
        """
        page.goto(f"{live_server}/reports")
        page.wait_for_load_state("networkidle")

        expect(page.locator("#aer-badge-approvals")).to_be_hidden()
        assert page.locator("#aer-badge-approvals").inner_html() == ""


class TestWithScriptingOff:
    def test_the_nav_renders_and_the_slot_stays_empty(
        self, browser: Browser, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        """ADR 0006's rule, at the one place this slice could have broken it.

        The count is the tool's, so losing it costs a hint. What must not happen is an
        empty pill painted beside every nav item on a browser that never ran the fetch.
        """
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/reports")

            expect(page.get_by_role("link", name="Requests")).to_be_visible()
            expect(page.locator("#aer-badge-approvals")).to_be_hidden()
        finally:
            context.close()


class TestTheFragmentIsNotADestination:
    def test_it_is_reachable_but_holds_no_page(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        # Named in `UNLISTED` rather than in the nav, and this is what that excuse means:
        # opening it yields spans, not a page with a way back.
        page.goto(f"{live_server}/_shell/badges")

        assert "aer-badge-approvals" in page.content()
        assert page.locator("header nav").count() == 0


def test_the_badge_is_scoped_to_the_operator(live_server: str, database_url: str) -> None:
    """A second operator's stopped runs are not this one's number.

    Asserted against the counter rather than the browser: the live server seeds one user
    and signs every request in as them, so a browser has no way to be somebody else. What
    a browser could not show, the query can.
    """
    StoppedRuns(database_url, count=1)

    async def counted() -> tuple[int, int]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                user = await session.scalar(select(User))
                assert user is not None
                return (
                    await awaiting_approval_count(session, user_id=user.id),
                    await awaiting_approval_count(session, user_id=uuid.uuid4()),
                )
        finally:
            await engine.dispose()

    mine, theirs = run_async(counted())

    assert mine == 1
    assert theirs == 0
