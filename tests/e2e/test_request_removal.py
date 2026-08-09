"""Archiving and removing a request, driven by a real browser.

Gap B1 was a *user-interface* gap before it was anything else: the service could delete a
draft and the API exposed it, and the list page — the one place an operator actually looks
at their requests — offered no control at all. So these tests are the ones that matter for
it. An in-process HTTP client cannot tell whether a button exists on the page, whether its
form posts to the route the server serves, or whether the CSRF token it carries is the one
the cookie was set with.

The asymmetry between the two verbs is the thing under test. Archiving is one click.
Removing is a click to a page that has to state what it will destroy before it will do it.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_request_form import fill_valid

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

DETAIL_URL = re.compile(r"/requests/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def a_request(page: Page, live_server: str, *, company: str = "Microsoft Corporation") -> None:
    """Create one through the form, so the row under test is one the application made."""
    page.goto(f"{live_server}/requests/new")
    fill_valid(page, company_name=company)
    page.click("#submit")
    page.wait_for_url(DETAIL_URL)


class TestTheListHasControls:
    def test_every_row_offers_archive_and_remove(self, page: Page, live_server: str):
        """The whole of B1 in one assertion: the list page used to offer neither."""
        a_request(page, live_server)

        page.goto(f"{live_server}/requests")

        expect(page.locator(".archive-request")).to_have_count(1)
        expect(page.locator(".remove-request")).to_have_count(1)

    def test_archiving_takes_the_row_off_the_list(self, page: Page, live_server: str):
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")

        page.locator(".archive-request").first.click()

        page.wait_for_url(re.compile(r"/requests$"))
        expect(page.get_by_role("link", name="Microsoft Corporation")).to_have_count(0)

    def test_the_archive_is_reachable_and_holds_it(self, page: Page, live_server: str):
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")
        page.locator(".archive-request").first.click()
        page.wait_for_url(re.compile(r"/requests$"))

        page.click("#show-archived")

        expect(page.get_by_role("link", name="Microsoft Corporation")).to_be_visible()
        expect(page.locator(".restore-request")).to_have_count(1)

    def test_restoring_puts_it_back(self, page: Page, live_server: str):
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")
        page.locator(".archive-request").first.click()
        page.wait_for_url(re.compile(r"/requests$"))
        page.goto(f"{live_server}/requests?archived=1")

        page.locator(".restore-request").first.click()

        page.wait_for_url(re.compile(r"/requests\?archived=1$"))
        page.goto(f"{live_server}/requests")
        expect(page.get_by_role("link", name="Microsoft Corporation")).to_be_visible()

    def test_the_archive_link_is_hidden_when_there_is_nothing_in_it(
        self, page: Page, live_server: str
    ):
        """A link to an empty page is a link nobody should have been offered."""
        a_request(page, live_server)

        page.goto(f"{live_server}/requests")

        expect(page.locator("#show-archived")).to_have_count(0)


class TestRemovingAsksFirst:
    def test_the_remove_link_leads_to_a_confirmation_rather_than_acting(
        self, page: Page, live_server: str
    ):
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")

        page.locator(".remove-request").first.click()

        page.wait_for_url(re.compile(r"/remove$"))
        expect(page.get_by_role("heading", name=re.compile("Remove Microsoft"))).to_be_visible()
        expect(page.locator("#confirm-remove")).to_be_visible()

    def test_it_says_what_survives(self, page: Page, live_server: str):
        """ "Everything will be deleted" is not true here, and a warning that overstates is
        one people learn to click through."""
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")
        page.locator(".remove-request").first.click()
        page.wait_for_url(re.compile(r"/remove$"))

        expect(page.get_by_text("The audit trail.")).to_be_visible()
        expect(page.get_by_text("The spend.")).to_be_visible()
        expect(page.get_by_text("The archived documents.")).to_be_visible()

    def test_it_offers_the_reversible_option_alongside(self, page: Page, live_server: str):
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")
        page.locator(".remove-request").first.click()
        page.wait_for_url(re.compile(r"/remove$"))

        expect(page.locator("#archive-instead")).to_be_visible()

    def test_cancelling_leaves_the_request_alone(self, page: Page, live_server: str):
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")
        page.locator(".remove-request").first.click()
        page.wait_for_url(re.compile(r"/remove$"))

        page.click("#cancel-remove")

        page.wait_for_url(re.compile(r"/requests$"))
        expect(page.get_by_role("link", name="Microsoft Corporation")).to_be_visible()

    def test_confirming_removes_it(self, page: Page, live_server: str):
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")
        page.locator(".remove-request").first.click()
        page.wait_for_url(re.compile(r"/remove$"))

        page.click("#confirm-remove")

        page.wait_for_url(re.compile(r"/requests$"))
        expect(page.get_by_text("No requests yet")).to_be_visible()

    def test_archiving_from_the_confirmation_page_works_too(self, page: Page, live_server: str):
        """Somebody who reaches this page and changes their mind should not have to go
        back to the list to do the safe thing."""
        a_request(page, live_server)
        page.goto(f"{live_server}/requests")
        page.locator(".remove-request").first.click()
        page.wait_for_url(re.compile(r"/remove$"))

        page.click("#archive-instead")

        page.wait_for_url(re.compile(r"/requests$"))
        page.goto(f"{live_server}/requests?archived=1")
        expect(page.get_by_role("link", name="Microsoft Corporation")).to_be_visible()
