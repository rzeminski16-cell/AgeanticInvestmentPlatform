"""The hardening sweep: §8.3 of testing-by-hand, driven rather than clicked.

`docs/developers/testing-by-hand.md` §8.3 names four passes the suite cannot make — the
keyboard alone, a narrow window, both schemes, 200% zoom — and tranche 9's exit adds a
fifth, scripting off. This module drives all five through the same Chromium the browser
suite uses, over every surface the overhaul rebuilt, so that the pass recorded in
`docs/plan/interface-overhaul.md` names an instrument anybody can re-run rather than a
claim they have to take on trust.

**Not part of any suite.** The filename dodges ``python_files`` deliberately: this is the
instrument of a deliberate pass, run when the interface has changed —

    uv run pytest tests/e2e/sweep.py -q

— in its own pytest process, like everything else in this directory.

**What a green run means, and what it cannot.** It is the automatable floor of §8.3: no
surface scrolls the page sideways at 320px, 768px or the viewport 200% zoom produces;
both schemes actually answer; every surface stands with scripting off; the tab order
follows the document and every stop shows a ring; the whole request journey — form, theme
— works from the keyboard alone. What it cannot see stays §8.3's job by hand: a real
screen reader, engines other than Chromium's, and the judgement of whether a page that
technically reflows still *reads*. The drawer's focus trap and Escape-return are already
browser-proved in `test_evidence_surfaces.py` and are not repeated here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final

import pytest
from playwright.sync_api import Browser, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from aer.core.enums import GateKind, JobStatus
from tests.e2e.test_run_console import RunFixture

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# WCAG 2.2's reflow floor, a tablet, and what 200% zoom does to a 1440px desktop: browser
# zoom halves the CSS viewport, and Playwright cannot drive the real zoom control, so the
# halved viewport is the same thing as far as reflow is concerned.
PHONE: Final = 320
TABLET: Final = 768
ZOOMED: Final = 720

# Every parameterless surface the overhaul rebuilt. The seeded ones — console and the
# three widest gates, which §8.3 names as the most likely to fail a narrow window — are
# yielded by `_surfaces` once a run exists to give them addresses.
STATIC_SURFACES: Final = (
    "/",
    "/requests",
    "/requests/new",
    "/portfolio",
    "/reports",
    "/knowledge",
    "/knowledge/graph",
    "/skills",
    "/skills/new",
    "/skills/examples",
    "/skills/import",
    # Once the one planned tool's placeholder; the watchlist is a working tool now.
    "/watchlist",
    "/settings",
    "/costs",
)

REQUEST_URL: Final = re.compile(r"/requests/[0-9a-f-]{36}$")


def _seeded_run(database_url: str) -> RunFixture:
    run = RunFixture(database_url)
    assert run.advance() is JobStatus.AWAITING_APPROVAL
    return run


def _surfaces(run: RunFixture) -> Iterator[tuple[str, str]]:
    """Every surface, static ones first, advancing the run between gate stages.

    The plan gate is visited while it is genuinely waiting, so its live form is what gets
    measured; `advance_until` then clears it and carries the run to the next gate.
    """
    for path in STATIC_SURFACES:
        yield path, path
    yield "run console", f"/runs/{run.job_id}"
    yield "plan gate", f"/runs/{run.job_id}/plan"
    run.advance_until(GateKind.ASSUMPTIONS)
    yield "assumptions gate", f"/runs/{run.job_id}/assumptions"
    run.advance_until(GateKind.FINAL)
    yield "review gate", f"/runs/{run.job_id}/review"


_OVERFLOW: Final = "document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"


class TestANarrowWindow:
    """§8.3: the page body never scrolls sideways. A wide table scrolls inside its own box."""

    def test_no_surface_scrolls_the_page_sideways(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run = _seeded_run(database_url)
        findings: list[str] = []
        for label, path in _surfaces(run):
            for width, height in ((PHONE, 640), (TABLET, 1024)):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(f"{live_server}{path}")
                overflow = page.evaluate(_OVERFLOW)
                if overflow > 1:
                    findings.append(f"{label} at {width}px: body scrolls sideways ({overflow}px)")
        assert not findings, "\n".join(findings)


class TestTwoHundredPercentZoom:
    """§8.3: the page reflows and everything remains reachable."""

    def test_every_surface_reflows_and_keeps_its_heading(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run = _seeded_run(database_url)
        page.set_viewport_size({"width": ZOOMED, "height": 512})
        findings: list[str] = []
        for label, path in _surfaces(run):
            page.goto(f"{live_server}{path}")
            overflow = page.evaluate(_OVERFLOW)
            if overflow > 1:
                findings.append(f"{label} zoomed: the body scrolls sideways by {overflow}px")
            heading = page.locator("h1").first
            if heading.count() == 0 or not heading.inner_text().strip():
                findings.append(f"{label} zoomed: no readable page heading")
        assert not findings, "\n".join(findings)


class TestBothSchemes:
    """§8.3: light and dark both answer, from the system preference and from the stamp."""

    def test_every_surface_answers_the_system_preference(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run = _seeded_run(database_url)
        findings: list[str] = []
        for label, path in _surfaces(run):
            page.emulate_media(color_scheme="light")
            page.goto(f"{live_server}{path}")
            light = page.evaluate("getComputedStyle(document.body).backgroundColor")
            page.emulate_media(color_scheme="dark")
            page.reload()
            dark = page.evaluate("getComputedStyle(document.body).backgroundColor")
            if light == dark:
                findings.append(f"{label}: body background is {light} in both schemes")
        assert not findings, "\n".join(findings)

    def test_the_explicit_stamp_overrides_the_preference(
        self, page: Page, live_server: str
    ) -> None:
        """`data-theme` is rendered from the cookie, so the override must beat the media
        query in both directions — the pairing `tests/e2e/test_contrast.py` measures."""
        page.emulate_media(color_scheme="light")
        page.goto(f"{live_server}/")
        light = page.evaluate("getComputedStyle(document.body).backgroundColor")

        with page.expect_navigation():
            page.click("#aer-theme-dark")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        stamped = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert stamped != light, "the dark stamp did not change the rendered scheme"


class TestScriptingOff:
    """Tranche 9's fifth pass: every surface is server-rendered and stands without JS."""

    def test_every_surface_stands(
        self,
        browser: Browser,
        browser_context_args: dict,
        live_server: str,
        database_url: str,
    ) -> None:
        run = _seeded_run(database_url)
        context = browser.new_context(**browser_context_args, java_script_enabled=False)
        page = context.new_page()
        findings: list[str] = []
        try:
            for label, path in _surfaces(run):
                page.goto(f"{live_server}{path}")
                heading = page.locator("h1").first
                if heading.count() == 0 or not heading.inner_text().strip():
                    findings.append(f"{label}: no page heading with scripting off")
                if page.locator("#aer-main").count() == 0:
                    findings.append(f"{label}: no main landmark with scripting off")
            # The console's no-script fallback: a waiting run still refreshes by meta tag.
            page.goto(f"{live_server}/runs/{run.job_id}")
            if page.locator('meta[http-equiv="refresh"]').count() == 0:
                findings.append("run console: no meta-refresh fallback while the run waits")
        finally:
            context.close()
        assert not findings, "\n".join(findings)


# What one tab stop looks like from the page: what has focus, whether the ring is visible,
# and whether the stop follows the previous one in document order. `outline` or a
# `box-shadow` both count as a ring — the design uses outlines, but refusing a shadow ring
# would fail a legitimate technique rather than a defect.
_STOP: Final = """() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const prev = window.__sweepPrev ?? null;
    window.__sweepPrev = el;
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    return {
        desc: el.tagName.toLowerCase()
            + (el.id ? "#" + el.id : "")
            + (el.name ? "[name=" + el.name + "]" : ""),
        ring: (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0)
            || style.boxShadow !== "none",
        visible: box.width > 0 && box.height > 0,
        follows: prev === null
            || Boolean(prev.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING),
        tag: el.tagName.toLowerCase(),
        name: el.name ?? "",
        type: el.type ?? "",
    };
}"""


def _walk(page: Page, *, limit: int = 80) -> list[dict]:
    page.evaluate("window.__sweepPrev = null")
    stops: list[dict] = []
    for _ in range(limit):
        page.keyboard.press("Tab")
        stop = page.evaluate(_STOP)
        if stop is None:
            break
        if stops and stop["desc"] == stops[-1]["desc"]:
            break
        stops.append(stop)
    return stops


def _keyboard_findings(label: str, stops: list[dict]) -> list[str]:
    findings = []
    for stop in stops:
        if not stop["visible"]:
            findings.append(f"{label}: {stop['desc']} takes focus while invisible")
        if not stop["ring"]:
            findings.append(f"{label}: {stop['desc']} shows no focus ring")
        if not stop["follows"]:
            findings.append(f"{label}: {stop['desc']} breaks reading order")
    if not stops:
        findings.append(f"{label}: nothing is reachable by keyboard")
    return findings


class TestTheKeyboardAlone:
    """§8.3 with the mouse down: the skip link, the order, the ring, the whole journey."""

    def test_the_skip_link_is_first_and_visible(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}/")
        page.keyboard.press("Tab")
        first = page.evaluate(
            """() => {
                const el = document.activeElement;
                const box = el.getBoundingClientRect();
                return {href: el.getAttribute("href"), onscreen: box.width > 0 && box.height > 0};
            }"""
        )
        assert first["href"] == "#aer-main", "the first tab stop is not the skip link"
        assert first["onscreen"], "the skip link takes focus while still off-canvas"

    def test_focus_is_visible_and_ordered_on_the_widest_surfaces(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run = _seeded_run(database_url)
        findings: list[str] = []
        for label, path in (
            ("main menu", "/"),
            ("request form", "/requests/new"),
            ("portfolio", "/portfolio"),
            ("plan gate", f"/runs/{run.job_id}/plan"),
        ):
            page.goto(f"{live_server}{path}")
            findings.extend(_keyboard_findings(label, _walk(page)))
        assert not findings, "\n".join(findings)

    def test_the_request_journey_works_from_the_keyboard(
        self, page: Page, live_server: str
    ) -> None:
        """Fill the form and submit it with Tab, letters and Enter — no clicks.

        Each field is answered when the walk reaches it, so this proves the fields are
        reachable in order as well as fillable. A select is answered with the arrow keys,
        which is how a keyboard user actually drives one.
        """
        answers = {
            "company_name": "Microsoft Corporation",
            "ticker": "MSFT",
            "as_of_date": "31122025",
            "max_cost_gbp": "5",
            # The refine-mandate disclosure stays closed, as a first-time reader would
            # leave it: the blank form now seeds the defaults its hints promise, and the
            # sweep proving that is the point — a required field behind the closed
            # disclosure once blocked this very submission.
        }
        answered: set[str] = set()
        page.goto(f"{live_server}/requests/new")
        for _ in range(60):
            page.keyboard.press("Tab")
            stop = page.evaluate(_STOP)
            if stop is None:
                pytest.fail("focus left the page before the form was submitted")
            if stop["tag"] == "input" and stop["name"] in answers and stop["name"] not in answered:
                # A date input keeps focus across several Tab presses (its segments are
                # one activeElement), so an answer is given once, not per stop.
                answered.add(stop["name"])
                page.keyboard.type(answers[stop["name"]])
            elif stop["tag"] == "select":
                for _ in range(3):
                    page.keyboard.press("ArrowDown")
                    if page.evaluate("document.activeElement.value") != "":
                        break
            elif stop["desc"] == "button#submit":
                page.keyboard.press("Enter")
                break
        else:
            pytest.fail("the submit button was never reached by Tab")
        try:
            page.wait_for_url(REQUEST_URL, timeout=10_000)
        except PlaywrightTimeoutError:
            state = page.evaluate(
                """() => Array.from(document.querySelectorAll("input, select"), (el) => ({
                    name: el.name, value: el.value, valid: el.checkValidity(),
                })).filter((f) => f.name)"""
            )
            pytest.fail(f"the form did not submit from the keyboard; field state: {state}")

    def test_the_theme_changes_from_the_keyboard(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}/")
        for _ in range(80):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.id") == "aer-theme-dark":
                with page.expect_navigation():
                    page.keyboard.press("Enter")
                assert page.evaluate("document.documentElement.dataset.theme") == "dark"
                return
        pytest.fail("the dark-theme control was never reached by Tab")
