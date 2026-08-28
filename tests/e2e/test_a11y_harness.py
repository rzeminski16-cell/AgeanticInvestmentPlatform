"""The accessibility harness itself, before anything relies on it.

A harness nobody has watched work is a harness whose green result means nothing. These tests
are about `tests/a11y.py` rather than about any page: that the vendored bytes are the ones
recorded, that axe loads and runs against a real page in a real browser, that a genuine
failure is actually caught, and that the library never reaches an operator.

The last one is the reason this file exists at all rather than the pin living beside the
font pins. axe is 568 kB. Serving it would be the suite charging its own convenience to every
first paint in the product.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from aer.web.templating import STATIC_DIR
from tests import a11y

pytestmark = pytest.mark.integration

# A one-pixel transparent GIF with no `alt`, which axe rates `critical` under `image-alt`.
# Injected into a real rendered page rather than asserted against a fixture string, so the
# harness is exercised on the same path a page test will use it on.
_UNLABELLED_IMAGE = """() => {
    const broken = document.createElement("img");
    broken.src =
        "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
    broken.id = "deliberately-unlabelled";
    document.body.appendChild(broken);
}"""


class TestTheVendoredBytesAreTheOnesRecorded:
    def test_the_file_is_present(self) -> None:
        assert a11y.AXE_SOURCE.is_file(), (
            f"axe-core is not vendored at {a11y.AXE_SOURCE}. Run `just vendor-axe`, then "
            "record the new hash in `tests/a11y.py`."
        )

    def test_its_hash_is_the_pinned_one(self) -> None:
        """A swapped minified file reviews as one changed line. Here it is a red build."""
        assert a11y.digest() == a11y.PINNED_SHA256, (
            "The vendored axe-core is not the file that was reviewed. If this is a "
            f"deliberate upgrade, record {a11y.digest()} in `tests/a11y.py` and say which "
            "version it is in the commit."
        )


class TestTheHarnessIsNotShipped:
    """It is a test asset. The static tree is what an operator's browser fetches."""

    def test_it_lives_outside_the_served_tree(self) -> None:
        assert not a11y.AXE_SOURCE.is_relative_to(STATIC_DIR)

    def test_no_axe_file_is_in_the_static_tree(self) -> None:
        stray = [p.name for p in STATIC_DIR.rglob("*.js") if "axe" in p.name.lower()]
        assert not stray, (
            f"A testing library is in the served tree: {stray}. axe is injected from "
            "`tests/fixtures/` by the harness; it is never served."
        )

    def test_the_page_never_asks_for_it(self, page: Page, live_server: str) -> None:
        """The complementary check, from the browser's side rather than the filesystem's."""
        asked: list[str] = []
        page.on("request", lambda request: asked.append(request.url))
        page.goto(live_server)
        assert not [url for url in asked if "axe" in url.lower()]


class TestTheHarnessRuns:
    def test_axe_loads_and_reports_on_a_real_page(self, page: Page, live_server: str) -> None:
        """The whole point: it injects, it executes, and it returns a verdict.

        The main menu, because it is the one page that renders in every state the platform
        can be in and is therefore the one always available to check.
        """
        page.goto(live_server)
        violations = a11y.audit(page)
        assert isinstance(violations, list)

    def test_it_reports_the_version_it_was_pinned_at(self, page: Page, live_server: str) -> None:
        """Read from the running library rather than scraped from the bundle's text."""
        page.goto(live_server)
        page.add_script_tag(path=str(a11y.AXE_SOURCE))
        assert page.evaluate("() => window.axe.version") == "4.13.0"

    def test_a_real_violation_is_caught(self, page: Page, live_server: str) -> None:
        """A guard that has never been seen to fail is a guard nobody should trust.

        An image with no alternative text is `critical` under `image-alt`, which is squarely
        inside `BLOCKING_IMPACTS`. Injected into a real rendered page rather than a fixture
        string, so this exercises the same path a page test will.
        """
        page.goto(live_server)
        page.evaluate(_UNLABELLED_IMAGE)
        violations = a11y.audit(page)
        assert "image-alt" in {found.rule for found in violations}, (
            "axe did not catch an image with no alternative text, which is a `critical` "
            f"finding. The harness is not working. {a11y.describe(violations)}"
        )

    def test_the_description_names_the_element(self, page: Page, live_server: str) -> None:
        """A failure message has to be actionable without opening a browser."""
        page.goto(live_server)
        page.evaluate(_UNLABELLED_IMAGE)
        described = a11y.describe(a11y.audit(page))
        assert "image-alt" in described
        assert "deliberately-unlabelled" in described


class TestTheFragmentExemption:
    """`region` is exempt for fragments — and today that exemption changes nothing.

    Worth stating plainly rather than leaving as a trap for whoever promotes the impact
    threshold. `region` is a `moderate` finding, `BLOCKING_IMPACTS` is `critical` and
    `serious`, so the rule is already never blocking regardless of the exemption. The
    exemption starts mattering the day `moderate` joins the threshold, which is tranche 9's
    to do — and on that day a fragment rendered on its own would otherwise fail a
    whole-page landmark rule it cannot possibly satisfy.
    """

    def test_region_is_not_blocking_at_the_current_threshold(self) -> None:
        assert "moderate" not in a11y.BLOCKING_IMPACTS

    def test_the_exemption_is_ready_for_when_it_is(self) -> None:
        assert "region" in a11y.FRAGMENT_EXEMPT_RULES

    def test_a_fragment_audit_still_catches_a_blocking_finding(
        self, page: Page, live_server: str
    ) -> None:
        """The exemption narrows one rule; it must not quietly narrow the whole audit."""
        page.goto(live_server)
        page.evaluate(_UNLABELLED_IMAGE)
        assert "image-alt" in {found.rule for found in a11y.audit(page, is_fragment=True)}
