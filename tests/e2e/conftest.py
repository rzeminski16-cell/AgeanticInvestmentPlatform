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
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
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


async def _reset(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            await connection.execute(
                text("TRUNCATE research_requests, audit_events, users RESTART IDENTITY CASCADE")
            )
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            session.add(User(email="e2e@example.invalid", display_name="E2E", role=UserRole.OWNER))
            await session.commit()
    finally:
        with contextlib.suppress(Exception):
            await engine.dispose()
