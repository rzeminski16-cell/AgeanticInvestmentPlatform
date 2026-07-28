"""Shared pytest fixtures.

The autouse fixture below is load-bearing for test correctness, not a convenience. See
its docstring.

One thing still to be added, noted so it is not forgotten: a socket-blocking autouse
fixture, so the default suite provably cannot reach the network. It arrives with the HTTP
fetch layer. Until then the "no network in tests" rule in ``CLAUDE.md`` is a convention
rather than an enforced invariant.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from aer.config import ENV_PREFIX, Settings, get_settings
from aer.logging import configure_logging

# Fixtures live in their own modules to keep this one readable; re-exported here so
# pytest discovers them. See tests/db_fixtures.py for the transactional isolation
# strategy, tests/api_fixtures.py for how the application is driven, and
# tests/fetch_fixtures.py for the socket guard that keeps the fetch tests offline.
from tests.api_fixtures import (  # noqa: F401
    api_engine,
    api_settings,
    broken_engine,
    broken_redis,
    fake_redis,
)
from tests.db_fixtures import (  # noqa: F401
    anyio_backend,
    database_url,
    db_engine,
    db_session,
    repo_root,
)
from tests.fetch_fixtures import (  # noqa: F401
    artefact_store,
    breaker,
    clock,
    fetch_settings,
    limiter,
    no_real_sockets,
    redis_client,
    sleeper,
)

# A User-Agent is the one required setting, so almost every settings test needs it.
VALID_USER_AGENT = "Test Runner test@example.invalid"


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate every test from the developer's real environment and ``.env`` file.

    ``pydantic-settings`` reads ``.env`` from the working directory by default. Without
    this fixture the suite would inherit whatever the developer happens to have
    configured, so tests would pass or fail depending on whose machine ran them, and a
    test process would read real credentials into memory. Both are unacceptable, and
    neither fails loudly — they just produce results you cannot trust.

    Also clears the ``get_settings`` cache on both sides, so a cached object cannot leak
    between tests in either direction.
    """
    for name in list(os.environ):
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)

    # Belt and braces: even with the environment clean, a `.env` on disk would still be
    # read. Disable dotenv loading for the whole suite. monkeypatch restores the original
    # value afterwards, so the production default is untouched.
    monkeypatch.setitem(Settings.model_config, "env_file", None)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def valid_user_agent() -> str:
    """The User-Agent that :func:`settings_env` installs."""
    return VALID_USER_AGENT


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Set the minimum environment for a valid ``Settings``, and hand back monkeypatch.

    Tests use the returned object to add or override individual variables.
    """
    monkeypatch.setenv(f"{ENV_PREFIX}HTTP_USER_AGENT", VALID_USER_AGENT)
    return monkeypatch


@pytest.fixture
def fake_anthropic_key() -> str:
    """A syntactically plausible but entirely fake Anthropic key.

    Used to prove that redaction works. It must never be a real key, and it must look
    enough like one to exercise the matching patterns.
    """
    return "sk-ant-api03-FAKEFAKEFAKE"


@pytest.fixture
def bridged_logging() -> Iterator[None]:
    """Route structlog through stdlib logging, so ``caplog`` can see events.

    Structlog's out-of-the-box configuration prints straight to stdout and never touches
    stdlib logging, which means ``caplog`` sees nothing until :func:`configure_logging`
    has run. A test asserting on log output without this fixture does not fail — it finds
    an empty record list and quietly proves nothing.

    Restores structlog's defaults afterwards so a configured pipeline does not leak into
    tests that expect the default one.
    """
    configure_logging(level="DEBUG", json_output=False)
    try:
        yield
    finally:
        structlog.reset_defaults()
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_aer_logging_handler", False):
                root.removeHandler(handler)


@pytest.fixture
def isolated_paths(tmp_path: Path) -> dict[str, Path]:
    """Two definitely-separate directories, for the Obsidian containment tests."""
    return {
        "vault": tmp_path / "generated-vault",
        "personal": tmp_path / "personal-notes",
    }
