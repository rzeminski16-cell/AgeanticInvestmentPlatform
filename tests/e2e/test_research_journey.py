"""The research tool, end to end, from the front door to a finished report.

Every other browser test in this directory owns one surface. `test_request_form.py` proves
the form submits; `test_run_console.py` proves the console renders a run and the gates take
a decision. Nothing proved they join up — and the shell underneath them has been rebuilt
four times in a fortnight, each time with a green suite.

So this is the seam, walked once: front page, launcher, form, request, run, plan gate,
final gate, report. One browser session, no URLs typed that an operator would not have
clicked their way to.

**The model is fake and the browser is real.** There is no worker here, so the run is
advanced from the test process through `worker.py` — which is what the worker does minus
the queue — and every step a *person* takes is a click. That split is the whole value: a
journey that drove the workflow from the test would prove the test can run a workflow, not
that the product can.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.core.enums import JobStatus
from aer.db.models import Job
from tests.db_fixtures import run_async
from tests.e2e.test_request_form import fill_valid
from tests.e2e.worker import Worker
from tests.workflow_fixtures import DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

CONSOLE_URL = re.compile(r"/runs/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Microsoft, because that is the company `StubSecClient` has filings for. A journey that
# commissioned something else would be testing what a fake provider does with a scene it
# has never seen.
COMPANY = "Microsoft Corporation"

# What the *report* calls it, which is not what the form was told. `acquire` resolves the
# request against the regulator's registry and the filing's registered name wins from then
# on — so a document says "MICROSOFT CORP" however politely the operator typed it. Correct,
# and the reason this constant exists rather than reusing the one above.
REGISTERED = "MICROSOFT CORP"
TICKER = "MSFT"


def _commission(page: Page, live_server: str) -> None:
    """Everything a person does to get from the front page to a request that exists.

    The filling itself is ``fill_valid``, which knows the form's shape — four decisions,
    then refinement behind a disclosure — so this walks the navigation and owns only the
    one value the journey needs different: the platform-default ceiling rather than the
    form test's £2.00. A run driven past the draft step needs a ceiling that step's
    estimate does not exceed, and a journey that stopped at BUDGET_EXCEEDED would be
    testing the cap rather than the tool.
    """
    page.goto(live_server)
    page.locator('[data-tool="research"] [data-field="action"]').click()
    page.wait_for_url("**/requests/new")

    fill_valid(page, max_cost_gbp=str(DEFAULT_PER_RUN_BUDGET_GBP))
    page.click("#submit")


def _leave_the_console(page: Page, live_server: str) -> None:
    """Navigate away before advancing a run from the test.

    The console re-fetches itself when the run's status moves, so a run advanced while it
    is open starts a navigation of its own — which races the test's next `goto` and
    surfaces as "interrupted by another navigation". `test_run_console.py` learned this
    first and says so at more length.
    """
    page.goto(f"{live_server}/requests")


def _job_id(database_url: str) -> uuid.UUID:
    """The run the browser just started, read back the way a worker would find it."""

    async def newest() -> uuid.UUID:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                job = await session.scalar(select(Job).order_by(Job.started_at.desc()).limit(1))
                assert job is not None, "the browser started no run"
                return job.id
        finally:
            await engine.dispose()

    return run_async(newest())  # type: ignore[no-any-return]


class TestTheWholeThing:
    def test_from_the_front_page_to_a_finished_report(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        """The one test that would notice the research tool stopped working.

        Long on purpose. Split into eight, each would pass against a product where the
        steps no longer lead to one another — which is the only failure mode this is here
        to catch, the surfaces having tests of their own already.
        """
        _commission(page, live_server)

        # 1. The request exists and the page for it says so.
        page.wait_for_url(re.compile(r"/requests/[0-9a-f-]{36}$"))
        expect(page.get_by_role("heading", name=COMPANY).first).to_be_visible()

        # 2. Starting the run is a button on that page, not a URL anybody has to know.
        page.click("#start-run")
        page.wait_for_url(CONSOLE_URL)
        expect(page.locator("#run-steps")).to_be_visible()

        job_id = _job_id(database_url)
        worker = Worker(database_url)

        # 3. The worker reaches the first gate and stops. A run that ran to completion
        #    without pausing would mean the gates had stopped gating.
        assert worker.advance(job_id) is JobStatus.AWAITING_APPROVAL

        # 4. The plan is reviewable, and approving it is a form carrying the hash of what
        #    was on screen — which is what makes it an approval *of this plan*.
        page.goto(f"{live_server}/runs/{job_id}")
        page.click("#review-plan")
        expect(page.locator("#plan-summary")).not_to_be_empty()
        digest = page.locator("#payload-hash").get_attribute("value")
        assert digest is not None
        assert len(digest) == 64
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)

        # 5. The rest of the run, with whatever conditional gates this scene raises cleared
        #    on the way — an operator would clear them and they are not what this is about.
        assert worker.advance_to_the_final_gate(job_id) is JobStatus.AWAITING_APPROVAL

        # 6. The draft is a document a person can read before deciding on it — under the
        #    name the filing registers, not the one the form was told.
        page.goto(f"{live_server}/runs/{job_id}/review")
        expect(page.locator("#draft-markdown")).to_contain_text(REGISTERED)
        expect(page.locator("#draft-markdown")).to_contain_text(TICKER)

        # 7. Approving the final gate clears it. It does not publish anything: the report
        #    is written by the step *after* the gate, which is the worker's job and not the
        #    approval's — a decision and its consequence are separate rows for a reason.
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)
        _leave_the_console(page, live_server)

        assert worker.advance(job_id) is JobStatus.SUCCEEDED

        # 8. And the report is reachable by clicking, which is the whole point of a console.
        page.goto(f"{live_server}/runs/{job_id}")
        page.click("#view-report")

        expect(page.locator("#immutable-badge")).to_be_visible()
        expect(page.locator("#report-markdown")).to_contain_text(REGISTERED)
        # The one sentence that has to be on every surface this platform produces.
        expect(page.get_by_text("not regulated investment advice").first).to_be_visible()

    def test_the_finished_run_shows_up_on_the_front_page(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        """The other half of the loop, and the reason the front page is a work list.

        A run stopped at a gate is the operator's to clear, and the main menu is where they
        find out. Before this slice the feed lived at `/overview`; it lives at `/` now, and
        this is what says the move did not lose the connection.
        """
        _commission(page, live_server)
        page.wait_for_url(re.compile(r"/requests/[0-9a-f-]{36}$"))
        page.click("#start-run")
        page.wait_for_url(CONSOLE_URL)

        job_id = _job_id(database_url)
        assert Worker(database_url).advance(job_id) is JobStatus.AWAITING_APPROVAL

        page.goto(live_server)

        expect(page.locator(f'[data-attention="research.gate.{job_id}"]')).to_be_visible()
        expect(page.get_by_text(f"{COMPANY} is waiting for you")).to_be_visible()

    def test_a_request_nobody_ran_is_listed_as_idle(self, page: Page, live_server: str) -> None:
        # The cheapest of the three feed states, and the one an operator hits most: a
        # request written and then forgotten. It has to appear without a run existing.
        _commission(page, live_server)
        page.wait_for_url(re.compile(r"/requests/[0-9a-f-]{36}$"))

        page.goto(live_server)

        expect(page.get_by_text(f"{COMPANY} has never been run")).to_be_visible()
