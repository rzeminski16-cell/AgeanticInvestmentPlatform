"""A real server and a real browser.

Everything else in the suite drives the application in-process through
``httpx.ASGITransport``, which is fast and proves the server behaves. It cannot prove the
*page* behaves: that the form actually submits, that HTMX is wired to the right target,
that a browser's own date input does not quietly reject what the server would accept.
Those need a browser, and a browser needs a socket.

So this module starts uvicorn on an ephemeral port in a background thread. Ephemeral
rather than fixed, because a hard-coded port collides with a development server the
moment someone runs both — and the failure looks like a mysterious test failure rather
than a port conflict.

**Run these in their own pytest process.** Playwright's synchronous API drives an asyncio
loop on the main thread and keeps it running for the life of its session fixture, so any
asyncio-based fixture that runs after a browser test in the same process fails with
"Runner.run() cannot be called from a running event loop". ``just test`` excludes this
directory and ``just test-e2e`` runs it alone, which is why ``just test-all`` is two
commands rather than one ``pytest`` invocation.
"""

from __future__ import annotations

import contextlib
import gc
import os
import socket
import threading
import time
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aer.api.app import create_app
from aer.config import load_settings
from aer.core.enums import UserRole
from aer.db.models import User
from tests.db_fixtures import run_async

STARTUP_TIMEOUT_SECONDS = 20.0

# What the console's meta-refresh fallback is set to for browser tests. See `live_server`.
E2E_POLL_SECONDS = 3600


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# Some environments ship a Chromium that Playwright did not download itself — a CI image
# with the browser baked in, for instance. Playwright looks for an exact build number and
# tells you to run `playwright install` when it does not find one, which in an image with
# no network is advice that cannot be followed. Pointing at the installed binary is both
# faster and the only thing that works there.
_PREINSTALLED_CHROMIUM = Path(
    os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "/opt/pw-browsers/chromium")
)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    args = dict(browser_type_launch_args)
    if _PREINSTALLED_CHROMIUM.exists():
        args["executable_path"] = str(_PREINSTALLED_CHROMIUM)
    # Chromium's sandbox needs privileges a container usually does not grant, and the
    # failure is an unhelpful crash on launch. Safe here: the only page loaded is our own.
    args.setdefault("args", []).append("--no-sandbox")
    return args


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    # A fixed locale and timezone: "today" in the date input and in the server's UTC
    # comparison must not drift apart because the machine running the tests is elsewhere.
    return {**browser_context_args, "locale": "en-GB", "timezone_id": "Europe/London"}


@pytest.fixture
def live_server(settings_env, tmp_path, database_url) -> Iterator[str]:
    """Serve the application on 127.0.0.1 and yield its base URL.

    The database is emptied and re-seeded per test. These tests commit for real — the
    browser is a separate client, so there is no transaction to roll back — and a test
    that inherits the previous one's rows is a test whose result depends on ordering.
    """
    # The console's no-JavaScript fallback is a `<meta http-equiv="refresh">`, and five
    # seconds is right for a person watching a run. In a browser test it is a timer armed
    # on any page the test leaves parked — including while the test does slow work of its
    # own — and when it fires into a navigation Playwright reports "interrupted by another
    # navigation" against whichever test was unlucky. The tests assert that the fallback
    # is *present* (and absent on a finished run), never that it fires, so an interval no
    # test outlives keeps the feature under test and takes the race away.
    settings_env.setattr("aer.web.pages.POLL_SECONDS", E2E_POLL_SECONDS)
    settings_env.setenv("AER_DATABASE_URL", database_url)
    settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
    settings_env.setenv("AER_SECRET_KEY", "e2e-signing-key-not-a-real-one")
    settings = load_settings()

    run_async(_reset(database_url))

    port = _free_port()
    # No injected state, unlike the in-process tests. The connection pool must be created
    # and disposed on the loop that uses it — asyncpg connections belong to their loop —
    # and the only loop that qualifies is the server thread's own. Letting the
    # application's lifespan own the engine is both simpler and the only correct option
    # here; injecting one built out here produced "attached to a different loop" on
    # teardown.
    app = create_app(settings)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        # asyncio rather than uvloop. uvloop leaves transports open when a server is shut
        # down from another thread, and `filterwarnings = ["error"]` turns the resulting
        # ResourceWarning into a test failure that has nothing to do with the test. What
        # is under test here is the page, not the event-loop implementation.
        loop="asyncio",
        # Do not wait for keep-alive connections to drain on shutdown. The browser holds
        # them open, so the default would stall every teardown until it times out.
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            pytest.fail(f"the test server did not start within {STARTUP_TIMEOUT_SECONDS}s")
        time.sleep(0.05)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=STARTUP_TIMEOUT_SECONDS)
        del app, config, server
        _finalise_abandoned_connections()


def _finalise_abandoned_connections() -> None:
    """Collect this server's leftovers here, rather than during some later test.

    Stopping the server cancels whatever requests are still in flight. A request
    cancelled mid-query cannot hand its connection back to the pool — closing one is
    itself an ``await`` and the loop is going away — so the connection is left to the
    garbage collector. For a process that is exiting, which is what a real shutdown is,
    that is harmless. In this suite the process carries on, the collector fires during
    some later test, and ``filterwarnings = ["error"]`` turns asyncpg's ResourceWarning
    into a failure of a test that had nothing to do with it. That is the whole of the
    long-standing browser-suite flake: the failures rotated because the collector did.

    So the collection is forced here, where the only objects being finalised are the ones
    the server just torn down left behind — asyncpg reports them as an unclosed
    connection, asyncio as an unclosed transport, and the kernel as an unclosed socket to
    port 5432, all of them the same abandoned connection seen from three levels. Silencing
    ``ResourceWarning`` for the duration of this one collection is therefore narrow in the
    way that matters: it covers this server's leftovers and nothing that happens during a
    test.

    The leak that matters in production — a browser navigating away mid-poll, on a server
    that keeps running — is fixed in :mod:`aer.api.sse` and never reaches this function.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()


async def _reset(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            # Everything a run produces, by cascade. `section_definitions` is deliberately
            # absent: those rows come from the migration and are what a report is built
            # from, so truncating them would empty every report rather than reset the test.
            await connection.execute(
                text(
                    "TRUNCATE research_requests, audit_events, users, artefacts, prompts, "
                    "companies RESTART IDENTITY CASCADE"
                )
            )
            # Skills are the one thing a test can create that the truncate above cannot
            # reach: they own `section_definitions` rows, which stay for the reason given
            # there. A skill left behind gives the next test a library that is not empty
            # — and "the library is empty" is exactly what the editor's first test says.
            await connection.execute(text("DELETE FROM section_definitions WHERE origin = 'skill'"))
            await connection.execute(text("DELETE FROM skill_versions"))
            await connection.execute(text("DELETE FROM skills"))
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            session.add(User(email="e2e@example.invalid", display_name="E2E", role=UserRole.OWNER))
            await session.commit()
    finally:
        with contextlib.suppress(Exception):
            await engine.dispose()
