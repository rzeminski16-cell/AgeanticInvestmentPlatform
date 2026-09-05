"""The watchlist, walked once: follow, commission from the row, stop following.

`test_watchlist.py` proves the service and drives the pages in-process. Nothing there
proves a person can do it — that the launcher leads to the form, that following puts a
row on the queue and a row on the work list, that commissioning from the row lands on the
run it started, and that stopping following takes the row away.

**No worker.** The run the commission starts is queued for a worker that is not running;
what is under test is that the page started it and where it sent the operator.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


class TestFollowThenCommission:
    def test_follow_commission_and_stop(self, page: Page, live_server: str) -> None:
        # From the launcher to the form.
        page.goto(live_server)
        page.locator('[data-tool="watchlist"] [data-field="action"]').click()
        page.wait_for_url("**/watchlist")
        expect(page.get_by_text("Nothing followed")).to_be_visible()

        page.fill("#company_name", "Contoso plc")
        page.fill("#ticker", "CTSO")
        page.fill("#exchange", "LSE")
        page.fill("#why", "The FY25 margin bridge looks too good.")
        page.click("#follow")
        page.wait_for_url("**/watchlist")
        row = page.locator("[data-entry]")
        expect(row).to_have_count(1)
        expect(row).to_have_attribute("data-state", "queued")
        expect(row.locator('[data-field="why"]')).to_contain_text("margin bridge")

        # The work list knows the queue is not started.
        page.goto(live_server)
        waiting = page.locator('[data-tool="watchlist"][data-attention]')
        expect(waiting).to_have_count(1)
        expect(waiting).to_contain_text("waiting to be researched")

        # Commissioning from the row starts a run and lands on it.
        page.goto(f"{live_server}/watchlist")
        page.locator("[data-commission] button").click()
        page.wait_for_url("**/runs/*")
        run_url = page.url

        page.goto(f"{live_server}/watchlist")
        row = page.locator("[data-entry]")
        expect(row).to_have_attribute("data-state", "commissioned")
        expect(row).to_contain_text("researched as at")
        expect(page.locator('[data-figure="reserved"]')).to_be_visible()
        assert run_url.endswith(
            page.locator("[data-entry] a[href^='/runs/']").get_attribute("href") or ""
        )

        # And the work list no longer asks about the queue.
        page.goto(live_server)
        expect(page.locator('[data-tool="watchlist"][data-attention]')).to_have_count(0)

        # Stopping following takes the row away, with a reason.
        page.goto(f"{live_server}/watchlist")
        page.locator("[data-stop] input[name='reason']").fill("Researched by hand.")
        page.locator("[data-stop] button").click()
        page.wait_for_url("**/watchlist")
        expect(page.locator("[data-entry]")).to_have_count(0)
