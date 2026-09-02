"""The theses tool, walked once: write a thesis, add a premise, withdraw it, retire the thesis.

`test_theses.py` proves the record and drives the pages in-process. Nothing there proves a
person can do it — that the radio decides which fields count, that the withdraw form on a
premise row submits against that premise, that a retired thesis really loses its forms in a
browser rather than in an assertion about HTML.

**No worker and no model.** A thesis is a document a person writes; there is nothing to
approve and nothing to spend.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.db_fixtures import run_async

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


async def _seed_a_company(database_url: str) -> None:
    """A company the thesis can be about. The reset seeds a user and nothing else, and a
    thesis is only ever about a company the platform can resolve."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO companies (name, ticker, exchange, company_number) "
                    "VALUES ('Contoso plc', 'CTSO', 'LSE', '01234567')"
                )
            )
    finally:
        await engine.dispose()


def _open_the_tool(page: Page, live_server: str) -> None:
    page.goto(live_server)
    page.locator('[data-tool="theses"] [data-field="action"]').click()
    page.wait_for_url("**/theses")


class TestAThesisFromNothing:
    def test_write_add_withdraw_retire(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        """The one test that would notice the theses tool stopped working. Long on purpose:
        split into four, each would pass against a product where the steps no longer lead to
        one another."""
        run_async(_seed_a_company(database_url))
        _open_the_tool(page, live_server)

        # Write it. The company select offers the one company the platform can resolve.
        page.fill("#title", "Contoso holds its pricing power")
        page.select_option("#company_id", index=0)
        page.click("#write")
        page.wait_for_url("**/theses/*")
        expect(page.locator("#subject")).to_have_text("Contoso plc (CTSO)")
        expect(page.get_by_text("Nothing is asserted yet")).to_be_visible()

        # Add a premise a person will review. The radio decides which fields count, so the
        # threshold fields are left empty and the review date is what is recorded.
        page.fill("#statement", "Management allocates capital well.")
        page.fill("#basis", "Ten years of buybacks below intrinsic value.")
        page.check("#defeated_by-review")
        page.fill("#review_by", "2027-03-31")
        page.click("#add")
        page.wait_for_url("**/theses/*")
        premise = page.locator('[data-premise="1"]')
        expect(premise).to_have_attribute("data-tested", "review")
        expect(premise.locator('[data-field="defeated-by"]')).to_contain_text("31 March 2027")
        expect(page.get_by_text("1 premise held")).to_be_visible()

        # Withdraw it, with a reason. The row stays, struck through, with the reason.
        premise.locator("input[name='reason']").fill("The FY26 guide broke it.")
        premise.locator("button[type='submit']").click()
        page.wait_for_url("**/theses/*")
        premise = page.locator('[data-premise="1"]')
        expect(premise).to_have_attribute("data-withdrawn", "yes")
        expect(premise.locator('[data-field="withdrawn"]')).to_contain_text(
            "The FY26 guide broke it."
        )
        expect(page.get_by_text("1 withdrawn, with the reason kept")).to_be_visible()

        # Retire it. The forms go; the record stays, on the retired list.
        page.fill("#retire-reason", "Replaced by a narrower thesis.")
        page.click("#retire")
        page.wait_for_url("**/theses/*")
        expect(page.locator("#retired-notice")).to_be_visible()
        expect(page.locator("#add-premise")).to_have_count(0)
        expect(page.locator("#retire-thesis")).to_have_count(0)

        page.goto(f"{live_server}/theses")
        expect(page.get_by_text("Contoso holds its pricing power")).to_have_count(0)
        page.click("#show-retired")
        expect(page.get_by_text("Contoso holds its pricing power")).to_be_visible()
