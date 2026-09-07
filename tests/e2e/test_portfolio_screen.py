"""The portfolio tool, walked once: an empty book, a deposit, a trade, and a total.

Every other test of this tool owns one layer. `test_calc_portfolio.py` proves the
arithmetic; `test_portfolio_service.py` proves the assembly. Nothing proved they reach a
person — that the form submits what the columns want, that a date control returns a book at
a date, that the grade an operator has to read is actually on the screen.

**No worker and no model.** A portfolio is not a run: there is nothing to approve and
nothing to spend. Everything here is a person clicking, which is the point — a journey that
drove the tool from the test would prove the test can enter a trade, not that the product
can.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# What `tests/e2e/conftest.py` seeds prices for, which is what a holding needs to be
# valued at all. A ticker with no bars would exercise the unpriced path, which has its own
# test in the service suite.
TRADE_DATE = "2026-06-15"


def _open_the_book(page: Page, live_server: str) -> None:
    """Everything a person does to get from the front page to a book that exists."""
    page.goto(live_server)
    page.locator('[data-tool="portfolio"] [data-field="action"]').click()
    page.wait_for_url("**/portfolio")

    # A book is one row and the table has held it since day one, so this is a create form
    # rather than a wizard.
    page.click("#create-book")
    page.wait_for_url("**/portfolio")


def _record(page: Page, **fields: str) -> None:
    for name, value in fields.items():
        # `kind` is the one remaining `<select>`. `security` used to be one too and is now
        # an `<input list>` — a native typeable combobox, because a dropdown of every
        # listing is unusable at any real size and offered nothing at all at size zero.
        if name == "kind":
            page.select_option(f"#{name}", value)
        else:
            page.fill(f"#{name}", value)
    page.click("#record")
    page.wait_for_url("**/portfolio")


class TestTheBookFromNothing:
    def test_a_deposit_and_a_trade_become_a_priced_book(self, page: Page, live_server: str) -> None:
        """The one test that would notice the portfolio tool stopped working.

        Long on purpose. Split into five, each would pass against a product where the steps
        no longer lead to one another — which is the only failure this is here to catch,
        the layers below having tests of their own.
        """
        _open_the_book(page, live_server)

        # 1. An empty book says what to do rather than reporting the absence.
        expect(page.get_by_text("Nothing recorded yet")).to_be_visible()

        # 2. Money in. Cash is a position, so it appears in the table rather than as a
        #    footnote — and the weight of a book that is all cash is all of it.
        _record(page, kind="deposit", quantity="50000", currency="GBP", trade_date=TRADE_DATE)

        expect(page.locator('[data-cash="GBP"]')).to_be_visible()
        expect(page.locator('[data-cash="GBP"] [data-field="weight"]')).to_contain_text("100.0%")

        # 3. Every figure that came from something typed says so. The chip is what an
        #    operator reads; the containment is the type an export receives.
        expect(page.locator('[data-grade="attested"]').first).to_be_visible()
        expect(page.locator("#attested-notice")).to_be_visible()

        # 4. The date control is a GET, so a view is a link somebody can keep.
        page.fill("#as_of", "2026-06-01")
        page.click("#reprice")
        page.wait_for_url(re.compile(r"as_of=2026-06-01"))
        # Before the deposit, so there is nothing in it — and it says so rather than
        # showing a total of zero, which is a figure somebody could act on.
        expect(page.locator("#as-of-date")).to_have_text("2026-06-01")
        expect(page.get_by_text("Nothing recorded yet")).to_be_visible()

    def test_the_tool_is_reachable_from_the_front_page(self, page: Page, live_server: str) -> None:
        # The launcher's claim, checked: portfolio works now, and its row carries the one
        # thing you most often want to do with it.
        page.goto(live_server)

        row = page.locator('[data-tool="portfolio"]')

        expect(row).to_have_attribute("data-status", "Working")
        expect(row.locator('[data-field="action"]')).to_contain_text("Record a transaction")

    def test_a_sale_entered_positive_is_refused_rather_than_adding_shares(
        self, page: Page, live_server: str
    ) -> None:
        """The form signs a disposal, so the operator never has to type a minus.

        A book that required one would fill with additions that look like disposals — and a
        holding that grew on a sale is a plausible number nothing downstream would question.
        """
        _open_the_book(page, live_server)
        _record(page, kind="withdrawal", quantity="100", currency="GBP", trade_date=TRADE_DATE)

        expect(page.locator('[data-cash="GBP"] [data-field="balance"]')).to_contain_text("-")


class TestASubtotalIsNeverShownAsATotal:
    def test_every_tile_goes_blank_when_any_row_cannot_be_valued(
        self, page: Page, live_server: str
    ) -> None:
        """Found by looking at the screen, which no passing test had done.

        The first draft showed a refused net asset value beside a cash tile reading
        £50,000 — a book whose dollars could not be converted, with its sterling summed and
        stated as though that were the cash. Every tile is a sum over the rows that
        resolved, so if any row did not, none of them may be shown.
        """
        _open_the_book(page, live_server)
        _record(page, kind="deposit", quantity="50000", currency="GBP", trade_date=TRADE_DATE)
        # No rate for this pair, so it cannot reach sterling and the book has no total.
        _record(page, kind="deposit", quantity="20000", currency="USD", trade_date=TRADE_DATE)

        expect(page.locator("#portfolio-problem")).to_be_visible()
        for tile in ("net-assets", "securities", "cash", "unrealised"):
            expect(page.locator(f"#tile-{tile}")).to_contain_text("—")
            expect(page.locator(f"#tile-{tile}")).not_to_contain_text("50,000")

        # The row that did resolve still says what it says. A refused total is not a
        # refused page.
        expect(page.locator('[data-cash="GBP"] [data-field="in-base"]')).to_contain_text(
            "50,000.00"
        )
        expect(page.locator('[data-cash="USD"] [data-field="problem"]')).to_contain_text(
            "No EUR/USD rate"
        )


class TestTheThirdDoor:
    """Roadmap §3.1: a ticker the platform has never seen, typed into the form.

    This server has no market-data key, so the wiring is provable without a network: the
    unknown ticker reaches the verifier and the answer is the configuration — named, with
    what to do about it — never a dead end and never a guess.
    """

    def test_an_unknown_ticker_reaches_the_verifier_and_the_refusal_names_the_key(
        self, page: Page, live_server: str
    ) -> None:
        _open_the_book(page, live_server)
        page.select_option("#kind", "buy")
        page.fill("#security", "TSLA NASDAQ")
        page.fill("#trade_date", TRADE_DATE)
        page.fill("#quantity", "10")
        page.fill("#price", "100")
        page.fill("#currency", "USD")

        page.click("#record")

        expect(page.get_by_text("No market-data subscription is configured")).to_be_visible()

    def test_a_bare_unknown_ticker_is_told_to_name_the_exchange(
        self, page: Page, live_server: str
    ) -> None:
        """Verifying against a guessed venue could resolve a different company's listing
        somewhere else, so a bare ticker gets the ask rather than a guess."""
        _open_the_book(page, live_server)
        page.select_option("#kind", "buy")
        page.fill("#security", "TSLA")
        page.fill("#trade_date", TRADE_DATE)
        page.fill("#quantity", "10")
        page.fill("#price", "100")
        page.fill("#currency", "USD")

        page.click("#record")

        expect(page.get_by_text("Name the exchange too")).to_be_visible()
