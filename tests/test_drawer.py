"""The drawer's shape, asserted where a browser is not needed.

What a browser proves — focus, Escape, the scroll lock — is in `tests/e2e/test_shell.py`.
What is here is everything that can be wrong before a browser is involved: semantics the
server has to send rather than a script set, a trigger that stops being a link, and a
second implementation of a focus trap appearing somewhere.
"""

from __future__ import annotations

import re

import pytest

from aer.web.templating import STATIC_DIR, TEMPLATES_DIR

DRAWER_JS = STATIC_DIR / "js" / "drawer.js"
DRAWER_HTML = TEMPLATES_DIR / "_shell" / "drawer.html"
BASE = TEMPLATES_DIR / "base.html"


@pytest.fixture(scope="module")
def markup() -> str:
    return DRAWER_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script() -> str:
    return DRAWER_JS.read_text(encoding="utf-8")


class TestTheSemanticsAreMarkup:
    """Sent by the server, not set on open.

    A panel that only becomes a dialogue when a script gets round to it is a panel that is
    not one in the DOM a reader inspects, and is not one at all if the script failed to
    load. What a server genuinely cannot send is the *behaviour*, and that is all the
    script owns.
    """

    @pytest.mark.parametrize(
        "attribute",
        ['role="dialog"', 'aria-modal="true"', 'aria-labelledby="aer-drawer-title"'],
    )
    def test_the_panel_declares_itself(self, markup: str, attribute: str) -> None:
        assert attribute in markup

    def test_the_panel_can_hold_focus(self, markup: str) -> None:
        # `tabindex="-1"` is what makes a trap possible: with nothing focusable inside,
        # focus has to have somewhere to rest that is still in the panel.
        assert 'tabindex="-1"' in markup

    def test_it_is_closed_by_an_attribute_rather_than_a_class(self, markup: str) -> None:
        # `hidden` is the one assistive technology already understands, so a stylesheet
        # that failed to load leaves the drawer closed rather than covering the page.
        assert re.search(r'id="aer-drawer"\s+hidden', markup)

    def test_the_script_sets_no_aria(self, script: str) -> None:
        """The other half of the same rule, and the one that rots.

        A script that set `aria-modal` on open would make the markup above decorative, and
        the two would drift the first time somebody edited one of them.
        """
        assert "aria-modal" not in script
        assert 'role", "dialog"' not in script


class TestTheDrawerLivesInTheShell:
    def test_the_page_shell_includes_it(self) -> None:
        assert '{% include "_shell/drawer.html" %}' in BASE.read_text(encoding="utf-8")

    def test_the_script_is_loaded_once_and_deferred(self) -> None:
        body = BASE.read_text(encoding="utf-8")

        assert body.count("/js/drawer.js") == 1
        assert re.search(r"/js/drawer\.js'\) \}\}\" defer", body)

    def test_no_page_writes_its_own_focus_trap(self) -> None:
        """ADR 0073's rule, as a grep: written once, in one file, never per page.

        A second implementation is how a chrome layer becomes a framework, and the copy
        that gets the trap subtly wrong is always the one nobody remembers exists.
        """
        offenders = []
        for path in (*TEMPLATES_DIR.rglob("*.html"), *(STATIC_DIR / "js").glob("*.js")):
            if path in (DRAWER_JS, DRAWER_HTML):
                continue
            body = path.read_text(encoding="utf-8")
            if "aria-modal" in body or "shiftKey" in body:
                offenders.append(str(path.name))

        assert not offenders, (
            f"overlay or focus-trap code outside the chrome layer: {offenders}. The drawer "
            "is written once (ADR 0073)."
        )


class TestATriggerIsALinkFirst:
    def test_the_preview_trigger_keeps_a_real_href(self) -> None:
        """ADR 0006's binding rule, at the one place this slice could have broken it.

        htmx intercepts the click when it is there to; when it is not, the browser follows
        the `href`. A trigger with `hx-get` and no `href` is a control that does nothing at
        all with scripting off, which is what the drawer must not become.
        """
        body = (TEMPLATES_DIR / "overview" / "index.html").read_text(encoding="utf-8")

        trigger = re.search(r"<a\b[^>]*hx-target=\"#aer-drawer-body\"[^>]*>", body, re.DOTALL)
        assert trigger is not None, "no drawer trigger found on the Overview screen"
        assert "href=" in trigger.group(0)

    def test_the_trigger_names_the_panel(self) -> None:
        body = (TEMPLATES_DIR / "overview" / "index.html").read_text(encoding="utf-8")

        assert "data-drawer-title=" in body

    def test_the_fragment_extends_nothing(self) -> None:
        # It is swapped into a page that is already rendered. A template extending
        # `base.html` would put a second navigation and a second disclaimer inside the first.
        for name in ("_run_preview.html", "_missing.html"):
            body = (TEMPLATES_DIR / "overview" / name).read_text(encoding="utf-8")
            assert "{% extends" not in body, name


class TestTheDrawerOpensBecauseContentArrived:
    def test_it_binds_to_the_swap_rather_than_to_a_click(self, script: str) -> None:
        """The decision that makes an empty drawer impossible.

        Opening on click leaves a moment between "opened" and "filled" — long enough for a
        failed request to leave an empty panel on screen with nothing to say.
        """
        assert "htmx:afterSwap" in script
        # The selector form rather than the bare name: the file's own comment explains why
        # there is no `data-drawer-open`, and a check that could not tell prose from code
        # would forbid saying so.
        assert "[data-drawer-open]" not in script

    def test_it_empties_the_panel_on_close(self, script: str) -> None:
        # A panel that kept the last run's numbers would show them for the instant before
        # the next request landed, and a reader who opened the wrong row would see the
        # right-looking answer to the wrong question.
        assert 'body.innerHTML = ""' in script

    def test_it_computes_nothing(self, script: str) -> None:
        """JavaScript may own chrome, never a figure (ADR 0073).

        Not a proof, and not meant to be — it is the cheap version of the rule, catching
        the arithmetic and the number formatting somebody reaches for first.
        """
        for forbidden in ("toFixed", "parseFloat", "toLocaleString", "Intl.NumberFormat"):
            assert forbidden not in script, forbidden
