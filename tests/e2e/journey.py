"""The journey harness, browser half: a real page, driven as an operator drives it.

Everything the harness asserts lives in `tests/journey_harness.py`; this module supplies the
one thing that half cannot — a surface backed by a browser, where "visible" means rendered
and "press" means a click. `tests/e2e/test_journey.py` is the collected entry point.
"""

from __future__ import annotations

import contextlib
import re

from playwright.sync_api import Page

from tests.journey_harness import Control

__all__ = ["BrowserSurface"]


class BrowserSurface:
    """A Playwright page behind the harness's :class:`~tests.journey_harness.Surface`."""

    def __init__(self, page: Page) -> None:
        self._page = page
        page.set_default_timeout(10_000)

    @property
    def url(self) -> str:
        return self._page.url

    def goto(self, url: str) -> None:
        self._page.goto(url)

    def text(self) -> str:
        main = self._page.locator("main")
        return main.first.inner_text() if main.count() else self._page.locator("body").inner_text()

    def controls(self) -> list[Control]:
        page = self._page
        scope = page.locator("main") if page.locator("main").count() else page.locator("body")
        offered: list[Control] = []
        for kind in ("link", "button"):
            for control in scope.get_by_role(kind).all():
                with contextlib.suppress(Exception):
                    if control.is_visible() and control.is_enabled():
                        label = " ".join(control.inner_text().split())
                        offered.append(Control(label=label, kind=kind, handle=control))
        return offered

    def press(self, control: Control, *, expect_url: re.Pattern[str] | None = None) -> None:
        control.handle.click()
        self._settle(expect_url)

    def press_by_id(self, element_id: str, *, expect_url: re.Pattern[str] | None = None) -> None:
        self._page.click(f"#{element_id}")
        self._settle(expect_url)

    def _settle(self, expect_url: re.Pattern[str] | None) -> None:
        if expect_url is not None:
            self._page.wait_for_url(expect_url)
            return
        with contextlib.suppress(Exception):
            self._page.wait_for_load_state("load")

    def fill(self, name: str, value: str) -> None:
        self._page.fill(f"[name='{name}']", value)

    def set_hidden(self, name: str, value: str) -> None:
        self._page.evaluate(
            "([name, value]) => { document.querySelector(`input[name='${name}']`).value = value }",
            [name, value],
        )

    def has(self, element_id: str) -> bool:
        return self._page.locator(f"#{element_id}").count() > 0
