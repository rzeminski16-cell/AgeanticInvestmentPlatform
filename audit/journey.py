"""The journey harness, shape half: every stopped state, in-process, no browser, no model.

    uv run python -m audit.smoke --journey

The same inventory and the same builders and assertions as the browser half
(`tests/journey_harness.py`), driven through an HTTP client bound to the application in this
process rather than through a browser. What it cannot see is what only a browser can — that
a form actually submits, that a script wired something — and what it buys is a verdict on
every commit for the cost of the suite's fake scene: the console and the gate pages are
rendered by the real handlers, the controls are read off the real HTML, and pressing one
posts the real form with the real CSRF token.

One verdict per row, written to ``audit/out/smoke-journey/verdicts.json``. The process exits
non-zero if any row's verdict is not the one `tests/journey_inventory.py` records for it: a
row outside ``STILL_RED`` and ``UNCONSTRUCTED`` that fails, or a row inside either that
does not fail exactly as it says.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin

import httpx
from asgi_lifespan import LifespanManager
from selectolax.parser import HTMLParser, Node
from tests.journey_harness import Control

from aer.api.app import create_app
from aer.config import Settings, load_settings

__all__ = ["HtmlSurface", "InProcessServer", "Verdict", "run_journey"]

BASE_URL: Final = "http://journey.invalid"
_REQUEST_TIMEOUT_SECONDS: Final = 60.0
# Rendered but not readable: what a browser leaves out of a page's text and out of what is
# pressable. `hidden` is both the attribute and the utility class the templates use;
# a closed `<details>` shows its summary and nothing else.
_INVISIBLE: Final = "script, style, template, noscript, [hidden], .hidden"


class SurfaceError(RuntimeError):
    """The surface could not do what it was asked: no such control, a press that ended
    somewhere other than where it had to. The harness reports it as a control that did not
    lead on."""


class InProcessServer:
    """The application, alive on a loop of its own, answering an HTTP client in this thread.

    The builders and the assertions are synchronous and open a fresh event loop per database
    read (`tests.db_fixtures.run_async`), which rules out serving the application on the
    caller's loop: there is none, and there must not be one running. So the application
    lives on a background thread's loop for the life of one row — its engine and its Redis
    client are created and disposed there, where asyncpg requires — and every request is
    handed across and waited for.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="journey-server", daemon=True
        )
        self._client: httpx.AsyncClient | None = None
        self._lifespan: LifespanManager | None = None

    def __enter__(self) -> InProcessServer:
        self._thread.start()
        self._call(self._start())
        return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(Exception):
            self._call(self._stop())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=_REQUEST_TIMEOUT_SECONDS)
        self._loop.close()

    async def _start(self) -> None:
        app = create_app(self._settings)
        self._lifespan = LifespanManager(app)
        await self._lifespan.__aenter__()
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=BASE_URL,
            follow_redirects=True,
        )

    async def _stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._lifespan is not None:
            await self._lifespan.__aexit__(None, None, None)

    def _call(self, coroutine: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=_REQUEST_TIMEOUT_SECONDS)

    def get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        assert self._client is not None
        response: httpx.Response = self._call(self._client.get(url, params=params))
        return response

    def post(self, url: str, data: dict[str, str]) -> httpx.Response:
        assert self._client is not None
        response: httpx.Response = self._call(self._client.post(url, data=data))
        return response


class HtmlSurface:
    """A page as an in-process client sees it: parsed HTML, forms posted by hand.

    The visibility model is the browser's, approximated: script, style and template content,
    anything hidden by attribute or utility class, and the body of a closed disclosure are
    neither read nor pressable. Where the approximation and the browser disagree, the two
    halves report different verdicts for the same row, which is the harness saying so.
    """

    def __init__(self, server: InProcessServer) -> None:
        self._server = server
        self._url = ""
        self._tree: HTMLParser | None = None
        self._overrides: dict[str, str] = {}

    @property
    def url(self) -> str:
        return self._url

    def goto(self, url: str) -> None:
        self._land(self._server.get(url))

    def _land(self, response: httpx.Response) -> None:
        self._url = str(response.url)
        self._tree = HTMLParser(response.text)
        self._overrides = {}
        for node in self._tree.css(_INVISIBLE):
            node.decompose()
        for details in self._tree.css("details:not([open])"):
            for child in list(details.iter()):
                if child.tag != "summary":
                    child.decompose()

    def _main(self) -> Node:
        assert self._tree is not None, "no page has been opened"
        node = self._tree.css_first("main") or self._tree.css_first("body")
        assert node is not None, f"{self._url} rendered no body"
        return node

    def text(self) -> str:
        return " ".join(self._main().text(separator=" ").split())

    def controls(self) -> list[Control]:
        offered: list[Control] = []
        for node in self._main().css("a[href], button, input[type='submit']"):
            if "disabled" in node.attributes:
                continue
            if node.tag == "input":
                label = node.attributes.get("value") or ""
            else:
                label = " ".join(node.text(separator=" ").split())
            kind = "link" if node.tag == "a" else "button"
            offered.append(Control(label=label, kind=kind, handle=node))
        return offered

    def press(self, control: Control, *, expect_url: re.Pattern[str] | None = None) -> None:
        node: Node = control.handle
        if node.tag == "a":
            response = self._server.get(urljoin(self._url, node.attributes.get("href") or ""))
        else:
            response = self._submit(node)
        self._land(response)
        if expect_url is not None and not expect_url.search(self._url):
            message = (
                f"pressing {control.label!r} ended at {self._url}, not at {expect_url.pattern}"
            )
            raise SurfaceError(message)

    def press_by_id(self, element_id: str, *, expect_url: re.Pattern[str] | None = None) -> None:
        assert self._tree is not None, "no page has been opened"
        node = self._tree.css_first(f"#{element_id}")
        if node is None:
            message = f"no control #{element_id} on {self._url}"
            raise SurfaceError(message)
        label = " ".join(node.text(separator=" ").split())
        kind = "link" if node.tag == "a" else "button"
        self.press(Control(label=label, kind=kind, handle=node), expect_url=expect_url)

    def _submit(self, button: Node) -> httpx.Response:
        form: Node | None = button
        while form is not None and form.tag != "form":
            form = form.parent
        if form is None:
            message = f"the control {button.attributes.get('id') or button.tag!r} is in no form"
            raise SurfaceError(message)
        data = self._fields(form)
        name = button.attributes.get("name")
        if name:
            data[name] = button.attributes.get("value") or ""
        data.update(self._overrides)
        action = urljoin(self._url, form.attributes.get("action") or self._url)
        if (form.attributes.get("method") or "get").lower() == "post":
            return self._server.post(action, data=data)
        return self._server.get(action, params=data)

    @staticmethod
    def _fields(form: Node) -> dict[str, str]:
        data: dict[str, str] = {}
        for node in form.css("input, textarea, select"):
            name = node.attributes.get("name")
            if not name:
                continue
            if node.tag == "input":
                kind = (node.attributes.get("type") or "text").lower()
                if kind in {"submit", "button", "reset", "image", "file"}:
                    continue
                if kind in {"checkbox", "radio"} and "checked" not in node.attributes:
                    continue
                data[name] = node.attributes.get("value") or ""
            elif node.tag == "textarea":
                data[name] = node.text()
            else:
                chosen = node.css_first("option[selected]") or node.css_first("option")
                if chosen is not None:
                    data[name] = chosen.attributes.get("value") or chosen.text()
        return data

    def fill(self, name: str, value: str) -> None:
        self._overrides[name] = value

    def set_hidden(self, name: str, value: str) -> None:
        self._overrides[name] = value

    def has(self, element_id: str) -> bool:
        assert self._tree is not None, "no page has been opened"
        return self._tree.css_first(f"#{element_id}") is not None


@dataclass(frozen=True, slots=True)
class Verdict:
    key: str
    expected: str
    measured: str
    ok: bool
    detail: str
    seconds: float


@contextlib.contextmanager
def _environment(overrides: dict[str, str]) -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _expected(key: str) -> str:
    from tests.journey_inventory import STILL_RED, UNCONSTRUCTED  # noqa: PLC0415

    if key in UNCONSTRUCTED:
        return "unconstructed"
    if key in STILL_RED:
        return "red"
    return "green"


def _measure(state: Any, database_url: str) -> Verdict:
    from tests.db_fixtures import run_async  # noqa: PLC0415
    from tests.journey_harness import (  # noqa: PLC0415
        DeadEndError,
        NoPathConstructedError,
        Scene,
        build,
        check,
        environment_for,
        reset_scene,
    )

    started = time.monotonic()
    with _environment(environment_for(state)):
        run_async(reset_scene(database_url))
        with InProcessServer(load_settings()) as server:
            scene = Scene(
                surface=HtmlSurface(server), live_server=BASE_URL, database_url=database_url
            )
            try:
                job_id = build(state, scene)
                check(state, scene, job_id)
            except NoPathConstructedError as unbuilt:
                measured, detail = "unconstructed", str(unbuilt)
            except DeadEndError as dead_end:
                measured, detail = "red", str(dead_end)
            except Exception as broke:  # the harness itself broke, and the verdict says so
                measured, detail = "error", f"{type(broke).__name__}: {broke}"
            else:
                measured, detail = "green", ""
    expected = _expected(state.key)
    return Verdict(
        key=state.key,
        expected=expected,
        measured=measured,
        ok=measured == expected,
        detail=detail,
        seconds=round(time.monotonic() - started, 1),
    )


def run_journey(database_url: str, *, out_dir: Path, only: str | None = None) -> list[Verdict]:
    """Every row, one after another, on one database emptied between rows."""
    from tests.journey_inventory import inventory  # noqa: PLC0415

    verdicts: list[Verdict] = []
    for state in inventory():
        if only and not re.search(only, state.key):
            continue
        verdict = _measure(state, database_url)
        verdicts.append(verdict)
        mark = "ok " if verdict.ok else "!! "
        print(
            f"{mark}{verdict.key:<44} {verdict.expected:<13} {verdict.measured:<13} {verdict.seconds:>5}s"
        )
        if not verdict.ok:
            print(f"    {verdict.detail[:600]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verdicts.json").write_text(
        json.dumps([asdict(verdict) for verdict in verdicts], indent=2, ensure_ascii=False)
    )
    return verdicts
