"""The run console and the approval gates, driven by a real browser.

What only a browser can show: that the approve button is inside the form, that the hidden
hash actually travels with it, that the console renders a run's state with no JavaScript
having run, and that the report is reachable by clicking rather than by knowing a URL.

**The run is advanced from the test, not by a worker.** There is no arq worker in the
suite, so each leg is executed directly against the same database the server is reading —
which is exactly what the worker does, minus the queue. What the browser exercises is the
pages; the workflow has its own tests.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.config import load_settings
from aer.core.enums import GateKind, JobStatus
from aer.db.models import Job, JobStep, Report, ResearchRequest, User
from aer.services import runs as run_service
from tests.db_fixtures import run_async
from tests.e2e.worker import Worker
from tests.workflow_fixtures import (
    AS_OF_DATE,
    DEFAULT_PER_RUN_BUDGET_GBP,
)

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

CONSOLE_URL = re.compile(r"/runs/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# The console polls once a second and then re-fetches, so a status change costs a poll, a
# navigation and a render. Generous, because the failure this guards is "it never happens".
REFETCH_TIMEOUT_MS = 20_000


class RunFixture:
    """A run in the live server's database, advanced on demand."""

    def __init__(self, database_url: str) -> None:
        self._settings = load_settings()
        self._database_url = database_url
        self._worker = Worker(database_url)
        self.request_id: uuid.UUID | None = None
        self.job_id: uuid.UUID = run_async(self._create())

    def request_path(self, live_server: str) -> str:
        return f"{live_server}/requests/{self.request_id}"

    async def _engine(self) -> Any:
        """A throwaway engine for one operation, pooling nothing.

        Each call runs on its own event loop (see :func:`run_async`), and an asyncpg
        connection belongs to the loop that opened it. ``NullPool`` closes every connection
        the moment its session ends, so nothing survives the loop to be garbage-collected
        later — which ``filterwarnings = ["error"]`` would otherwise turn into a failure in
        whichever test happened to run next.
        """
        return create_async_engine(self._database_url, poolclass=NullPool)

    async def _create(self) -> uuid.UUID:
        engine = await self._engine()
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                user = await session.scalar(select(User))
                assert user is not None, "the live_server fixture seeds one"

                request = ResearchRequest(
                    user_id=user.id,
                    company_name="Microsoft Corporation",
                    ticker="MSFT",
                    exchange="NASDAQ",
                    as_of_date=AS_OF_DATE,
                    point_in_time=True,
                    base_currency="USD",
                    reporting_currency="USD",
                    investment_horizon_months=12,
                    # The platform default, read rather than restated (A33): a run driven to
                    # gate 2 passes through the draft step, whose estimate a
                    # £2.50 ceiling now refuses.
                    max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
                )
                session.add(request)
                await session.flush()

                job = await run_service.start_run(session, request=request)
                await session.commit()
                self.request_id = request.id
                return job.id
        finally:
            await engine.dispose()

    def advance(self) -> JobStatus:
        """One leg, through the shared worker.

        The advancing itself lives in `tests/e2e/worker.py` because the journey test needs
        it too, and two advancers would drift into two ideas of what "advance" means — the
        one that stops clearing interim gates being the one nobody notices.
        """
        return self._worker.advance(self.job_id)

    def advance_to_the_final_gate(self) -> JobStatus:
        return self._worker.advance_to_the_final_gate(self.job_id)

    def hold_step(self, step_key: str, *, started_seconds_ago: int) -> None:
        """Put a step back into ``RUNNING``, as if the worker were still inside it.

        There is no worker in this suite and no way to pause a real step mid-flight, so the
        one state the console is built for -- a model call several minutes in, with nothing
        changing -- has to be staged.
        """
        run_async(self._hold_step(step_key, started_seconds_ago))

    async def _hold_step(self, step_key: str, started_seconds_ago: int) -> None:
        engine = await self._engine()
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                job = await session.get(Job, self.job_id)
                row = await session.scalar(
                    select(JobStep).where(
                        JobStep.job_id == self.job_id, JobStep.step_key == step_key
                    )
                )
                assert job is not None
                assert row is not None
                job.status = JobStatus.RUNNING
                row.status = JobStatus.RUNNING
                row.finished_at = None
                row.started_at = datetime.now(UTC) - timedelta(seconds=started_seconds_ago)
                await session.commit()
        finally:
            await engine.dispose()

    def report_id(self) -> uuid.UUID:
        return run_async(self._report_id())

    async def _report_id(self) -> uuid.UUID:
        engine = await self._engine()
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                report = await session.scalar(select(Report).where(Report.job_id == self.job_id))
                assert report is not None
                return report.id
        finally:
            await engine.dispose()


@pytest.fixture
def queued_run(live_server: str, database_url: str) -> RunFixture:
    """A run that exists and has not started, so the browser can watch it move."""
    return RunFixture(database_url)


@pytest.fixture
def waiting_run(live_server: str, database_url: str) -> RunFixture:
    """A run stopped at gate 1, in the server the browser will talk to."""
    run = RunFixture(database_url)
    assert run.advance() is JobStatus.AWAITING_APPROVAL
    return run


def leave_the_console(page: Page, live_server: str) -> None:
    """Navigate away before advancing a run from the test.

    The console re-fetches itself when the run's status moves, so a run advanced while it
    is open starts a navigation of its own — which races the test's next `goto` and
    surfaces as "interrupted by another navigation" against whichever test was unlucky.
    This suite already learned that lesson from the no-JavaScript meta refresh; see
    `tests/e2e/conftest.py`. Tests that are *about* the re-fetch stay on the page and say
    so.
    """
    page.goto(f"{live_server}/requests")


class TestTheConsole:
    def test_it_shows_the_run_and_its_steps(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        expect(page.locator("#run-status")).to_have_text(JobStatus.AWAITING_APPROVAL.value)
        expect(page.locator('[data-step="plan"]')).to_be_visible()
        expect(page.locator("#awaiting-approval")).to_be_visible()

    def test_it_shows_the_steps_that_have_not_started_yet(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """``render`` is the last step and has no row this early in the run."""
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        expect(page.locator('[data-step="render"]')).to_be_visible()
        expect(page.locator('[data-step="render"] [data-field="status"]')).to_have_text(
            JobStatus.QUEUED.value
        )

    def test_a_long_step_shows_a_clock_that_moves(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """The whole point of the console during a model call.

        Nothing in the database changes for minutes, so the event stream is silent and
        every static thing on the page stays put. A counter that visibly advances is the
        one signal separating "thinking" from "the tab is dead" -- and it is only worth
        anything if it really moves, which is why this is a browser test.
        """
        waiting_run.hold_step("plan", started_seconds_ago=125)
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        clock = page.locator('[data-step="plan"] [data-field="elapsed"]')
        # Counted from the server's start time, not from when the page loaded.
        expect(clock).to_have_text(re.compile(r"^0[23]:\d\d$"))

        first = clock.inner_text()
        expect(clock).not_to_have_text(first, timeout=5000)

    def test_it_says_what_it_is_waiting_for_and_where_to_look(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        waiting_run.hold_step("plan", started_seconds_ago=5)
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        expect(page.locator("#run-progress")).to_contain_text("Working on plan")
        expect(page.locator("#run-progress")).to_contain_text("just worker")

    def test_reaching_a_gate_reveals_the_banner_without_a_manual_refresh(
        self, page: Page, live_server: str, queued_run: RunFixture
    ) -> None:
        """The reported bug, in the browser.

        A gate is not a terminal state, so the stream's ``done`` event never fires. The
        status chip was patched to AWAITING_APPROVAL and nothing else changed: no banner,
        no "Review the plan" button, no way to act on the run until the operator thought to
        press F5. There is deliberately no ``page.reload()`` below.
        """
        page.goto(f"{live_server}/runs/{queued_run.job_id}")
        expect(page.locator("#awaiting-approval")).to_have_count(0)

        assert queued_run.advance() is JobStatus.AWAITING_APPROVAL

        expect(page.locator("#awaiting-approval")).to_be_visible(timeout=REFETCH_TIMEOUT_MS)
        expect(page.locator("#review-plan")).to_be_visible()

    def test_the_disclaimer_is_on_the_page(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")
        expect(page.get_by_text("not regulated investment advice")).to_be_visible()

    def test_the_console_is_reachable_from_the_request(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """By clicking, not by knowing the URL."""
        page.goto(f"{live_server}/requests")
        # By its text, not by an href prefix: "/requests/" also prefixes the nav link,
        # which would leave the browser on the list page and the failure would read as a
        # missing button rather than a mis-aimed click.
        page.get_by_role("link", name="Microsoft Corporation").click()
        page.click("#open-run")

        page.wait_for_url(CONSOLE_URL)
        expect(page.locator("#run-steps")).to_be_visible()


class TestTheFirstGate:
    def test_the_plan_is_reviewable_from_the_console(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")
        page.click("#review-plan")

        expect(page.locator("#plan-summary")).not_to_be_empty()
        expect(page.locator("#planned-sources")).to_be_visible()
        expect(page.locator("#known-risks")).to_be_visible()

    def test_the_form_carries_the_hash_of_what_is_displayed(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """The hidden field is what makes the approval an approval *of this plan*."""
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")

        digest = page.locator("#payload-hash").get_attribute("value")
        assert digest is not None
        assert len(digest) == 64

    def test_approving_returns_to_the_console_and_closes_the_gate(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        page.click("#approve")

        page.wait_for_url(CONSOLE_URL)

        # The decision is recorded, so the gate now reports itself rather than offering a
        # button the service would refuse.
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        expect(page.locator("#already-decided")).to_be_visible()
        expect(page.locator("#approve")).to_have_count(0)


class TestTheSecondGateAndTheReport:
    @pytest.fixture
    def drafted(self, page: Page, live_server: str, waiting_run: RunFixture) -> RunFixture:
        """Approve the plan in the browser, then let the run reach the final gate."""
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)
        leave_the_console(page, live_server)

        assert waiting_run.advance_to_the_final_gate() is JobStatus.AWAITING_APPROVAL
        return waiting_run

    def test_the_draft_is_shown_as_a_document(
        self, page: Page, live_server: str, drafted: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{drafted.job_id}/review")

        expect(page.locator("#draft-markdown")).to_contain_text("Research Note")
        expect(page.locator("#draft-sections")).to_be_visible()

    def test_approving_it_produces_a_report_the_console_links_to(
        self, page: Page, live_server: str, drafted: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{drafted.job_id}/review")
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)
        leave_the_console(page, live_server)

        assert drafted.advance() is JobStatus.SUCCEEDED

        page.goto(f"{live_server}/runs/{drafted.job_id}")
        expect(page.locator("#view-report")).to_be_visible()
        page.click("#view-report")

        expect(page.locator("#immutable-badge")).to_be_visible()
        expect(page.locator("#report-markdown")).to_contain_text("Executive Summary")
        expect(page.locator("#download-report")).to_be_visible()

    def test_the_download_serves_the_archived_markdown(
        self, page: Page, live_server: str, drafted: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{drafted.job_id}/review")
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)
        leave_the_console(page, live_server)
        assert drafted.advance() is JobStatus.SUCCEEDED

        page.goto(f"{live_server}/reports/{drafted.report_id()}")
        with page.expect_download() as download:
            page.click("#download-report")

        assert download.value.suggested_filename.endswith(".md")


class TestWithoutJavaScript:
    """The console must not depend on a script to show a run that is spending money."""

    @pytest.fixture
    def no_script(self, browser: Any) -> Any:
        context = browser.new_context(java_script_enabled=False)
        try:
            yield context
        finally:
            context.close()

    def test_the_steps_still_render(
        self, no_script: Any, live_server: str, waiting_run: RunFixture
    ) -> None:
        page = no_script.new_page()
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        expect(page.locator('[data-step="plan"]')).to_be_visible()
        expect(page.locator("#run-status")).to_have_text(JobStatus.AWAITING_APPROVAL.value)

    def test_the_polling_fallback_is_present_before_any_script_runs(
        self, no_script: Any, live_server: str, waiting_run: RunFixture
    ) -> None:
        page = no_script.new_page()
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        assert page.locator("#poll-fallback").count() == 1

    def test_approving_works_without_a_script(
        self, no_script: Any, live_server: str, waiting_run: RunFixture
    ) -> None:
        """A plain form post and a redirect. HTMX only changes where a response lands."""
        page = no_script.new_page()
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        page.click("#approve")

        page.wait_for_url(CONSOLE_URL)
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        expect(page.locator("#already-decided")).to_be_visible()


class TestGateOrdering:
    def test_the_final_gate_offers_nothing_before_the_draft_exists(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """Nothing has been drafted, so there is nothing to approve and the page says so."""
        response = page.goto(f"{live_server}/runs/{waiting_run.job_id}/review")

        assert response is not None
        assert response.status == 404
        expect(page.locator("#problem")).to_contain_text("drafted nothing yet")

    def test_a_run_that_does_not_exist_says_so(self, page: Page, live_server: str) -> None:
        response = page.goto(f"{live_server}/runs/{uuid.uuid4()}")

        assert response is not None
        assert response.status == 404
        expect(page.locator("#problem")).to_be_visible()


def _gate_path(job_id: uuid.UUID, gate: GateKind) -> str:
    return f"/runs/{job_id}/gates/{gate.value}"


class TestCancelling:
    """The stop button, in a real browser.

    What this catches that an HTTP test cannot: that the button is inside its form, that
    the form's CSRF input is actually rendered, and that the console the operator lands
    back on tells them the run is stopping rather than looking unchanged.
    """

    def test_the_button_stops_the_run_and_the_console_says_so(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        page.fill("#cancel-reason", "Wrong as-of date")
        page.click("#cancel-run")

        page.wait_for_url(CONSOLE_URL)
        expect(page.locator("#cancellation-requested")).to_be_visible()
        expect(page.locator("#cancellation-requested")).to_contain_text("Wrong as-of date")
        # Offered once. A second press would be a second row nobody could interpret.
        expect(page.locator("#cancel-run")).to_have_count(0)

    def test_the_run_stops_rather_than_continuing(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{waiting_run.job_id}")
        page.click("#cancel-run")
        page.wait_for_url(CONSOLE_URL)

        # The worker's next pass. It stops at the step boundary rather than carrying on
        # through the rest of the workflow.
        assert waiting_run.advance() is JobStatus.CANCELLED

        # No reload here. The console re-fetches itself when the status moves, and a
        # cancelled run whose page still offers a cancel button is the thing that would
        # go unnoticed.
        expect(page.locator("#run-status")).to_have_text(
            JobStatus.CANCELLED.value, timeout=REFETCH_TIMEOUT_MS
        )
        expect(page.locator("#cancel-run")).to_have_count(0)

    def test_a_finished_run_offers_no_cancel_button(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        # Approved through the browser, the same way the other gate tests do, so the run
        # reaches SUCCEEDED by the path an operator actually takes. The console is left
        # before each advance: a run advanced while it is open starts a re-fetch whose
        # navigation races the next `goto` — the exact "interrupted by another
        # navigation" this suite's helper exists to prevent, and which a slow machine
        # hits where a fast one never does.
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)
        leave_the_console(page, live_server)
        assert waiting_run.advance_to_the_final_gate() is JobStatus.AWAITING_APPROVAL

        page.goto(f"{live_server}/runs/{waiting_run.job_id}/review")
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)
        leave_the_console(page, live_server)
        assert waiting_run.advance() is JobStatus.SUCCEEDED

        page.goto(f"{live_server}/runs/{waiting_run.job_id}")

        expect(page.locator("#cancel-run")).to_have_count(0)


class TestCancellingIsNotADeadEnd:
    """The journey that was broken, driven end to end in a browser.

    Cancel a run and the request page offered only "open the run": no way to start again,
    no way to edit, no way to delete. The request was rubbish that could not be thrown away.
    """

    def cancel_and_stop(self, page: Page, live_server: str, run: RunFixture) -> str:
        page.goto(f"{live_server}/runs/{run.job_id}")
        page.click("#cancel-run")
        page.wait_for_url(CONSOLE_URL)
        leave_the_console(page, live_server)
        assert run.advance() is JobStatus.CANCELLED
        return run.request_path(live_server)

    def test_the_request_offers_a_fresh_run(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        request_url = self.cancel_and_stop(page, live_server, waiting_run)

        page.goto(request_url)

        expect(page.locator("#start-run")).to_be_visible()
        expect(page.locator("#start-run")).to_have_text("Start a new run")
        # Superseded, not erased: the cancelled run is still reachable.
        expect(page.locator("#open-run")).to_be_visible()

    def test_starting_again_opens_a_different_console(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        request_url = self.cancel_and_stop(page, live_server, waiting_run)
        page.goto(request_url)

        page.click("#start-run")

        page.wait_for_url(CONSOLE_URL)
        assert str(waiting_run.job_id) not in page.url
