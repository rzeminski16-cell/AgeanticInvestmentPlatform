"""The request form, driven by a real browser.

These tests exist to catch what an in-process HTTP client structurally cannot: that the
form's fields are actually reachable and named as the server expects, that submitting it
navigates where it should, and that HTMX is wired to a target that exists. A test that
POSTs a hand-built dictionary passes happily against a form with a misspelt ``name``
attribute or a submit button outside the ``<form>``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# A created request, not the form. A glob of "/requests/**" also matches
# "/requests/new", so wait_for_url would return instantly without the submission
# having happened — and the next navigation would abort the POST still in flight.
DETAIL_URL = re.compile(r"/requests/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def fill_valid(page: Page, **overrides: str) -> None:
    """Fill the whole form with a valid submission, minus any overrides.

    ``exchange`` and ``base_currency`` are handled separately because they are
    ``<select>`` elements: Playwright's ``fill`` refuses them, which is a better outcome
    than silently doing nothing.
    """
    typed = {
        "company_name": "Microsoft Corporation",
        "ticker": "msft",
        "as_of_date": "2026-07-01",
        "investment_horizon_months": "36",
        "current_weight_percent": "2.5",
        "maximum_weight_percent": "5",
        "benchmark": "MSCI World",
        "max_cost_gbp": "2.00",
        "focus_questions": "How durable is the Azure gross margin?",
    }
    selected = {"exchange": "NASDAQ", "base_currency": "USD"}

    for name, value in overrides.items():
        target = selected if name in selected else typed
        target[name] = value

    for name, value in typed.items():
        page.fill(f"#{name}", value)
    for name, value in selected.items():
        page.select_option(f"#{name}", value)


class TestHappyPath:
    def test_filling_the_form_creates_a_request_and_lands_on_its_page(
        self, page: Page, live_server: str
    ):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page)
        page.click("#submit")

        page.wait_for_url(DETAIL_URL)
        expect(page.locator("#company-name")).to_have_text("Microsoft Corporation")
        expect(page.locator("#ticker")).to_have_text("MSFT")
        expect(page.locator("#exchange")).to_have_text("NASDAQ")
        expect(page.locator("#as-of-date")).to_have_text("2026-07-01")
        expect(page.locator("#status")).to_have_text("DRAFT")

    def test_the_values_it_shows_are_the_ones_submitted(self, page: Page, live_server: str):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page)
        page.click("#submit")
        page.wait_for_url(DETAIL_URL)

        expect(page.locator("#focus-questions")).to_contain_text(
            "How durable is the Azure gross margin?"
        )
        # Entered as 2.5%, stored as the fraction 0.025, displayed as 2.5% again.
        expect(page.get_by_text("2.5%")).to_be_visible()

    def test_the_new_request_is_reachable_from_the_list(self, page: Page, live_server: str):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page)
        page.click("#submit")
        page.wait_for_url(DETAIL_URL)

        page.goto(f"{live_server}/requests")
        expect(page.get_by_role("link", name="Microsoft Corporation")).to_be_visible()

    def test_reaching_the_form_from_the_landing_page(self, page: Page, live_server: str):
        page.goto(live_server)
        page.get_by_role("link", name="Start a research request").click()

        expect(page).to_have_url(f"{live_server}/requests/new")


class TestRejection:
    def test_a_future_as_of_date_shows_an_inline_error_and_creates_nothing(
        self, page: Page, live_server: str
    ):
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, as_of_date=tomorrow)
        page.click("#submit")

        expect(page.locator("#error-summary")).to_be_visible()
        expect(page.locator("#error-summary")).to_contain_text("in the future")
        # Still on the form. Nothing was created, and the URL not having changed is the
        # visible proof of that.
        expect(page).to_have_url(f"{live_server}/requests/new")

        page.goto(f"{live_server}/requests")
        expect(page.get_by_text("No requests yet")).to_be_visible()

    def test_an_etf_is_refused_with_an_explanation(self, page: Page, live_server: str):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, ticker="SPY", company_name="SPDR S&P 500 ETF Trust", exchange="NYSE")
        page.click("#submit")

        expect(page.locator("#error-summary")).to_contain_text(
            "fund rather than an operating company"
        )

    def test_the_operators_answers_survive_a_rejection(self, page: Page, live_server: str):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, ticker="NOT A TICKER")
        page.click("#submit")

        expect(page.locator("#error-summary")).to_be_visible()
        expect(page.locator("#company_name")).to_have_value("Microsoft Corporation")
        expect(page.locator("#focus_questions")).to_have_value(
            "How durable is the Azure gross margin?"
        )
        expect(page.locator("#benchmark")).to_have_value("MSCI World")

    def test_correcting_the_error_and_resubmitting_works(self, page: Page, live_server: str):
        # The token issued with the rejected page has to be usable, or the operator is
        # stuck in a loop that looks like the form being broken.
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, ticker="NOT A TICKER")
        page.click("#submit")
        expect(page.locator("#error-summary")).to_be_visible()

        page.fill("#ticker", "MSFT")
        page.click("#submit")

        page.wait_for_url(DETAIL_URL)
        expect(page.locator("#ticker")).to_have_text("MSFT")

    def test_the_error_summary_links_to_the_offending_input(self, page: Page, live_server: str):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, ticker="NOT A TICKER")
        page.click("#submit")

        page.locator("#error-summary a").first.click()
        expect(page.locator("#ticker")).to_be_visible()


class TestWithoutJavaScript:
    """The form must work with scripting off.

    HTMX is an enhancement. A form whose validation depends on a script is a form that
    accepts anything the moment the script fails to load — and this one commissions
    spending.
    """

    @pytest.fixture
    def no_js_page(self, browser, browser_context_args):
        context = browser.new_context(**browser_context_args, java_script_enabled=False)
        page = context.new_page()
        yield page
        context.close()

    def test_it_still_submits_and_redirects(self, no_js_page: Page, live_server: str):
        no_js_page.goto(f"{live_server}/requests/new")
        fill_valid(no_js_page)
        no_js_page.click("#submit")

        no_js_page.wait_for_url(DETAIL_URL)
        expect(no_js_page.locator("#ticker")).to_have_text("MSFT")

    def test_it_still_enforces_the_rules(self, no_js_page: Page, live_server: str):
        no_js_page.goto(f"{live_server}/requests/new")
        fill_valid(no_js_page, ticker="SPY", company_name="SPDR S&P 500 ETF Trust")
        no_js_page.click("#submit")

        expect(no_js_page.locator("#error-summary")).to_be_visible()
        no_js_page.goto(f"{live_server}/requests")
        expect(no_js_page.get_by_text("No requests yet")).to_be_visible()


class TestDisclaimer:
    def test_every_page_carries_it(self, page: Page, live_server: str):
        # In the footer specifically. It lives in the page shell rather than in any
        # individual template, which is what makes it impossible for a page to ship
        # without it.
        for path in ("/", "/requests", "/requests/new"):
            page.goto(f"{live_server}{path}")
            expect(
                page.locator("footer").get_by_text("not regulated investment advice")
            ).to_be_visible()

    def test_the_header_badge_is_present_too(self, page: Page, live_server: str):
        page.goto(live_server)
        expect(page.get_by_text("Not investment advice", exact=True)).to_be_visible()
