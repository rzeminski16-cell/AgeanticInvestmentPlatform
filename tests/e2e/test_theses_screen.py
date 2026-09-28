"""The theses tool, walked once: write a thesis, add a premise, revise it, withdraw it, retire.

`test_theses.py` proves the record and drives the pages in-process. Nothing there proves a
person can do it — that the radio decides which fields count and leads to them, that the
withdraw form on a premise row submits against that premise, that a retired thesis really
loses its forms in a browser rather than in an assertion about HTML.

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
    # Through the menu: the theses are a tab of Companies since the launcher left Today.
    page.goto(live_server)
    page.locator('nav[aria-label="Main"]').get_by_role("link", name="Companies").click()
    page.locator('nav[aria-label="Companies"]').get_by_role("link", name="Theses").click()
    page.wait_for_url("**/theses")


def _revise_the_first_premise(page: Page) -> None:
    """Revise it in place (§10.3): the sentence is the control, the reason goes beside the
    save, and the header's button submits the form it sits outside of. The old wording is
    kept in the premise's history rather than overwritten."""
    premise = page.locator('[data-premise="1"]')
    premise.locator('[data-field="statement"]').fill("Management allocates capital very well.")
    page.fill("#reason", "The buyback record held through the downturn.")
    page.click("#save-revision")
    page.wait_for_url("**/theses/*")
    premise = page.locator('[data-premise="1"]')
    expect(premise.locator('[data-field="statement"]')).to_have_value(
        "Management allocates capital very well."
    )
    premise.get_by_text("How this premise has changed").click()
    expect(premise.locator('[data-field="history"]')).to_contain_text(
        "you believed: Management allocates capital well."
    )
    expect(page.locator('[data-field="revisions"]')).to_have_text("1")


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
        expect(page.get_by_text("Nothing asserted yet")).to_be_visible()

        # Add a premise a person will review. The radio decides which fields count, and the
        # choice leads to its fields: the review branch is chosen by default, so the
        # threshold fields are off the screen until the other radio is picked, and back off
        # it when the choice returns. The record is the same either way.
        page.fill("#statement", "Management allocates capital well.")
        page.fill("#basis", "Ten years of buybacks below intrinsic value.")
        expect(page.locator("#review-fields")).to_be_visible()
        expect(page.locator("#threshold-fields")).to_be_hidden()
        page.check("#defeated_by-threshold")
        expect(page.locator("#threshold-fields")).to_be_visible()
        expect(page.locator("#review-fields")).to_be_hidden()
        page.check("#defeated_by-review")
        expect(page.locator("#review-fields")).to_be_visible()
        expect(page.locator("#threshold-fields")).to_be_hidden()
        page.fill("#review_by", "2027-03-31")
        page.click("#add")
        page.wait_for_url("**/theses/*")
        premise = page.locator('[data-premise="1"]')
        expect(premise).to_have_attribute("data-tested", "review")
        expect(premise).to_have_attribute("data-state", "by hand")
        expect(premise.locator('[data-field="defeated-by"]')).to_contain_text("31 March 2027")
        expect(page.locator('[data-field="testable"]')).to_have_text("0 of 1")

        _revise_the_first_premise(page)

        # Withdraw it, with a reason, from the header's *Withdraw a premise*. It leaves the
        # editor and stays below, struck through, with the reason.
        page.click("#withdraw-a-premise")
        withdraw = page.locator('[data-withdraw="1"]')
        withdraw.locator("input[name='reason']").fill("The FY26 guide broke it.")
        withdraw.locator("button[type='submit']").click()
        page.wait_for_url("**/theses/*")
        given_up = page.locator('#given-up [data-withdrawn="yes"]')
        expect(given_up).to_have_count(1)
        expect(given_up.locator('[data-field="withdrawn"]')).to_contain_text(
            "The FY26 guide broke it."
        )
        expect(page.locator('[data-premise="1"]')).to_have_count(0)

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
