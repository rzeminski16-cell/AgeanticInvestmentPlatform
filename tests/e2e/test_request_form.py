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


def open_refinement(page: Page) -> None:
    """Open the optional-refinement disclosure so the fields inside it can be filled.

    The form leads with four decisions and keeps everything that refines a mandate behind
    a native disclosure that ships closed. A closed ``<details>`` opens on a summary click
    with scripting on or off — that is what native buys — so this works on the no-JS page
    too. Guarded on the ``open`` attribute rather than clicking blind, because a click on
    an already-open disclosure would close it.
    """
    details = page.locator("details", has_text="Refine this mandate")
    if details.get_attribute("open") is None:
        details.locator("summary").click()


def fill_valid(page: Page, **overrides: str) -> None:
    """Fill the whole form with a valid submission, minus any overrides.

    ``exchange`` and ``base_currency`` are handled separately because they are
    ``<select>`` elements: Playwright's ``fill`` refuses them, which is a better outcome
    than silently doing nothing. Half of what this fills sits behind the refinement
    disclosure, which is opened first — ``fill`` waits for visibility, and a field inside
    a closed ``<details>`` is attached and invisible for ever.
    """
    open_refinement(page)
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
        # The human label from the vocabulary, not the enum: raw domain values stopped
        # reaching templates in tranche 1 of the overhaul.
        expect(page.locator("#status")).to_contain_text("Draft")

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
        # `exact`, because every row now carries a "Remove Microsoft Corporation" link
        # beside the company link, and a substring match resolves to both.
        expect(page.get_by_role("link", name="Microsoft Corporation", exact=True)).to_be_visible()

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
        expect(page.get_by_text("No active requests")).to_be_visible()

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
        expect(no_js_page.get_by_text("No active requests")).to_be_visible()


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

    def test_it_is_said_once_and_not_twice(self, page: Page, live_server: str):
        """The shell's footer owns it and nothing duplicates it.

        This replaced an assertion that a second "Not investment advice" badge sat beside the
        wordmark. That badge was a second copy of one claim, and the design system is explicit
        that the footer carries it once and no page repeats it — a disclaimer people meet twice
        on every screen is one they stop reading, which is the opposite of why it is there.

        The claim itself is asserted three ways and did not weaken: the footer test above
        checks it is visible on three routes, and `tests/test_web_pages.py` checks the exact
        constant is present and that it appears exactly once.
        """
        page.goto(live_server)

        expect(page.get_by_text("not regulated investment advice")).to_have_count(1)


class TestEditingADraft:
    """The edit journey, in a real browser.

    The value of doing this here rather than with an HTTP client: the edit form is the
    *same* template as the create form, rendered with different values and a different
    action. An in-process test that POSTs a dictionary would pass against a page whose
    inputs were never prefilled at all.
    """

    def create(self, page: Page, live_server: str, **overrides: str) -> None:
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, **overrides)
        page.click("#submit")
        page.wait_for_url(DETAIL_URL)

    def test_the_form_opens_prefilled_with_what_was_saved(self, page: Page, live_server: str):
        self.create(page, live_server)

        page.click("#edit-request")

        expect(page.locator("#company_name")).to_have_value("Microsoft Corporation")
        expect(page.locator("#ticker")).to_have_value("MSFT")
        expect(page.locator("#exchange")).to_have_value("NASDAQ")
        # The percentage as typed, not the fraction as stored. Getting this wrong divides
        # the weight by a hundred every time the form is saved, and nothing errors.
        expect(page.locator("#current_weight_percent")).to_have_value("2.5")
        # Two radios rather than a checkbox since tranche 5, so each state has a name;
        # the id carries the value it selects.
        expect(page.locator("#point_in_time-true")).to_be_checked()

    def test_changing_a_value_and_saving_updates_the_request(self, page: Page, live_server: str):
        self.create(page, live_server)
        page.click("#edit-request")

        page.fill("#company_name", "Microsoft Corp.")
        page.click("#submit")

        page.wait_for_url(DETAIL_URL)
        expect(page.locator("#company-name")).to_have_text("Microsoft Corp.")

    def test_a_rejected_edit_keeps_what_was_typed(self, page: Page, live_server: str):
        self.create(page, live_server)
        page.click("#edit-request")

        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        page.fill("#as_of_date", tomorrow)
        open_refinement(page)
        page.fill("#horizon_label", "Kept, please")
        page.click("#submit")

        expect(page.locator("#error-summary")).to_be_visible()
        expect(page.locator("#horizon_label")).to_have_value("Kept, please")

    def test_the_edit_and_delete_controls_disappear_once_a_run_starts(
        self, page: Page, live_server: str
    ):
        self.create(page, live_server)
        detail = page.url

        page.click("#start-run")
        page.wait_for_url(re.compile(r"/runs/"))
        page.goto(detail)

        expect(page.locator("#edit-request")).to_have_count(0)
        expect(page.locator("#delete-request")).to_have_count(0)
        expect(page.locator("#immutable-reason")).to_be_visible()


class TestDeletingADraft:
    """The `confirm()` dialogue went in tranche 5, deliberately.

    The control on the detail page is now a link to a confirmation *page* that states what
    will be destroyed and what survives — a browser dialogue holds one sentence, cannot be
    reached with scripting off, and is dismissed by reflex. These tests walk that page from
    the detail page's own control; `test_request_removal.py` walks it from the list.
    """

    def test_confirming_on_the_page_deletes_it(self, page: Page, live_server: str):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, company_name="Deletable Holdings plc")
        page.click("#submit")
        page.wait_for_url(DETAIL_URL)

        page.click("#delete-request")
        page.wait_for_url(re.compile(r"/remove$"))
        page.click("#confirm-remove")

        page.wait_for_url(f"{live_server}/requests")
        expect(page.get_by_role("link", name="Deletable Holdings plc")).to_have_count(0)

    def test_keeping_it_from_the_page_deletes_nothing(self, page: Page, live_server: str):
        page.goto(f"{live_server}/requests/new")
        fill_valid(page, company_name="Kept Holdings plc")
        page.click("#submit")
        page.wait_for_url(DETAIL_URL)
        detail = page.url

        page.click("#delete-request")
        page.wait_for_url(re.compile(r"/remove$"))
        page.click("#cancel-remove")

        page.wait_for_url(detail)
        expect(page.locator("#company-name")).to_have_text("Kept Holdings plc")
