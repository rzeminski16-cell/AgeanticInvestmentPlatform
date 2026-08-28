"""Every component, in a real browser, in both schemes.

`tests/test_components.py` asserts the markup. This asserts the half a file cannot: what the
browser *computes*. A focus ring declared in a stylesheet and overridden two rules later is
still declared; a hover fill that resolves to the same colour as its rest state is still a
hover rule. Both look completely correct in the source.

**The fixture is routed onto the live server rather than served by it.** The Components page
is never a route (decision B6), and a page in the navigation is one that gets linked to and
eventually carries a state nobody rendered anywhere else. Intercepting one URL gives the
markup a real origin — so `/static/css/app.css` and the eight woff2 resolve exactly as they do
in the product — without the application ever offering it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.a11y import audit, describe
from tests.test_components import render_components

if TYPE_CHECKING:
    from playwright.sync_api import Page

PATH = "/components-under-test"


def _open(page: Page, live_server: str, theme: str, width: int = 1280) -> None:
    page.set_viewport_size({"width": width, "height": 1400})
    page.route(
        f"**{PATH}",
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=render_components(theme)
        ),
    )
    page.goto(f"{live_server}{PATH}")
    page.evaluate("document.fonts.ready")


def _colour(page: Page, selector: str, property_name: str) -> str:
    return page.evaluate(
        "([sel, prop]) => getComputedStyle(document.querySelector(sel))[prop]",
        [selector, property_name],
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
class TestEveryComponentInBothSchemes:
    def test_the_whole_set_passes_axe(self, page: Page, live_server: str, theme: str) -> None:
        """One page carrying every component in every state, checked in both schemes.

        Worth more than the same check on a product page: a product page renders the states it
        happens to be in, and the states nobody has seen are exactly where a missing label or
        an unassociated error survives.
        """
        _open(page, live_server, theme)

        violations = audit(page)

        assert not violations, describe(violations)

    def test_a_focused_control_has_a_visible_ring(
        self, page: Page, live_server: str, theme: str
    ) -> None:
        """The one affordance a keyboard user has. A control that removes the outline on
        `:focus` and forgets to add it back is the commonest defect there is, and it is
        invisible to everyone who does not navigate that way."""
        _open(page, live_server, theme)
        page.focus("#buttons button:first-of-type")

        style = page.evaluate(
            """() => {
                const s = getComputedStyle(document.querySelector('#buttons button'));
                return {width: s.outlineWidth, style: s.outlineStyle, colour: s.outlineColor};
            }"""
        )

        assert style["style"] != "none"
        assert float(style["width"].rstrip("px")) >= 2

    def test_hovering_a_button_changes_its_fill(
        self, page: Page, live_server: str, theme: str
    ) -> None:
        """A hover rule that resolves to the rest colour is a hover rule in name only."""
        _open(page, live_server, theme)
        rest = _colour(page, "#buttons button:first-of-type", "backgroundColor")
        page.hover("#buttons button:first-of-type")
        hovered = _colour(page, "#buttons button:first-of-type", "backgroundColor")

        assert rest != hovered

    def test_a_disabled_button_does_not_answer_the_pointer(
        self, page: Page, live_server: str, theme: str
    ) -> None:
        """Disabled uses the sunken fill and the control boundary rather than opacity: a
        translucent button sits at whatever contrast its background happens to give it, which
        is a different number on every surface and measured on none of them."""
        _open(page, live_server, theme)
        disabled = "#buttons button[disabled]"
        rest = _colour(page, disabled, "backgroundColor")
        page.hover(disabled, force=True)

        assert _colour(page, disabled, "backgroundColor") == rest
        assert _colour(page, disabled, "opacity") == "1"

    def test_an_invalid_field_is_bounded_in_the_failure_family(
        self, page: Page, live_server: str, theme: str
    ) -> None:
        _open(page, live_server, theme)

        assert float(_colour(page, "#ceiling", "borderTopWidth").rstrip("px")) >= 2
        assert _colour(page, "#ceiling", "borderTopColor") != _colour(
            page, "#company", "borderTopColor"
        )

    def test_the_empty_sheet_keeps_the_populated_geometry(
        self, page: Page, live_server: str, theme: str
    ) -> None:
        """So the page does not rearrange when a state changes underneath it. A surface that
        shrinks when it empties moves everything below it, and the reader loses their place at
        the moment the page is telling them something changed."""
        _open(page, live_server, theme)
        widths = page.evaluate(
            """() => [...document.querySelectorAll('#sheets > section')]
                    .map(s => Math.round(s.getBoundingClientRect().width))"""
        )

        assert len(set(widths)) == 1, f"sheets render at different widths: {widths}"

    def test_the_running_dot_is_the_only_thing_that_moves(
        self, page: Page, live_server: str, theme: str
    ) -> None:
        """§6.3. No value count-ups, no shimmer skeletons, no entrance cascades. A page of
        figures that animates is a page where the reader waits to find out what a number is."""
        _open(page, live_server, theme)
        animated = page.evaluate(
            """() => [...document.querySelectorAll('main *')]
                    .filter(el => getComputedStyle(el).animationName !== 'none')
                    .map(el => el.className.toString().slice(0, 60))"""
        )

        assert not animated, f"these elements animate: {animated}"


class TestTheSetSurvivesANarrowWindow:
    def test_nothing_makes_the_page_scroll_sideways_at_320px(
        self, page: Page, live_server: str
    ) -> None:
        """320px is the WCAG 2.2 reflow floor, and the width a table of five columns fails at.

        The record list exists because of this: a run waiting for a decision carries a name, a
        sentence, a status, a waiting time and an action, and as a table those either scroll
        sideways or truncate the sentence that is the only part worth reading.
        """
        _open(page, live_server, "light", width=320)

        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )

        assert overflow <= 0, f"the page scrolls {overflow}px sideways at 320px"

    def test_the_evidence_spine_wraps_rather_than_scrolling(
        self, page: Page, live_server: str
    ) -> None:
        """Below 960px the spine becomes a horizontal sequence. It wraps between nodes and
        preserves DOM order; it never hides an intermediate node behind a tooltip."""
        _open(page, live_server, "light", width=320)

        spine = page.evaluate(
            """() => {
                const el = document.querySelector('#spine-ok ol');
                return {scroll: el.scrollWidth, client: el.clientWidth,
                        nodes: el.querySelectorAll('li').length};
            }"""
        )

        assert spine["nodes"] == 3, "a node is missing at 320px"
        assert spine["scroll"] <= spine["client"] + 1
