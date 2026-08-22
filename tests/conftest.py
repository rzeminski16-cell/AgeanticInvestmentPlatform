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
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from sqlalchemy import event
from sqlalchemy.orm import Session

from aer.agents import registry as agent_registry
from aer.config import ENV_PREFIX, Settings, get_settings
from aer.db.models import ResearchRequest, WorkOrder
from aer.logging import configure_logging
from tests.agent_probes import PROBE_DEFINITIONS

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
from tests.assumption_fixtures import scene  # noqa: F401
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
from tests.workflow_fixtures import (  # noqa: F401
    provider,
    sec_client,
    workflow_settings,
    workflow_store,
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


@pytest.fixture(autouse=True, scope="session")
def probe_agent_roles() -> Iterator[None]:
    """Register the test probe agent roles for the session, and remove them after.

    The agent registry refuses an unregistered role at construction — a property several
    suites rely on — so the stand-in agents tests subclass need roles of their own. See
    ``tests/agent_probes.py``. Inserted directly rather than via monkeypatch because the
    registration must outlive any single test.
    """
    for definition in PROBE_DEFINITIONS:
        assert definition.role not in agent_registry._REGISTRY
        agent_registry._REGISTRY[definition.role] = definition
    yield
    for definition in PROBE_DEFINITIONS:
        agent_registry._REGISTRY.pop(definition.role, None)


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


# ---------------------------------------------------------------------------------------
# A hand-built research request gets the work order it is the detail row of.
#
# ADR 0068 made `work_orders` the run root: `jobs`, `approvals` and `source_documents` all
# hang off it, and `research_requests` became a 1:1 detail row sharing its id. Production
# has exactly one place that creates a request — `services.requests.create_request` — and it
# creates the work order itself.
#
# The suite does not go through that function. Seventy fixtures build a `ResearchRequest`
# directly, because what they are testing is what happens *after* a request exists, and
# routing every one of them through the service would be testing the service seventy times
# over. Those rows were always a synthesis of what the service would have written; this adds
# the row the service now also writes, so a fixture keeps meaning what it meant.
#
# **It cannot mask a production regression**, and that is the condition for it being
# acceptable rather than convenient: `TestCreateRequest::test_it_creates_the_work_order_it_
# hangs_off` in tests/test_request_api.py asserts the service does this itself, against a
# session where this listener has nothing to do because the service got there first.
@event.listens_for(Session, "before_flush")
def _anchor_requests_to_work_orders(session: Session, _context: object, _instances: object) -> None:
    pending = [obj for obj in session.new if isinstance(obj, ResearchRequest)]
    if not pending:
        return

    # Pending *and* persistent. `create_request` flushes the work order before it adds the
    # request, so by the time this runs the real one is in the identity map rather than in
    # `session.new` — and minting a second with the same key is an identity conflict, not a
    # duplicate row. That the production path trips this at all is the check working.
    known = {
        obj.id
        for obj in [*session.new, *session.identity_map.values()]
        if isinstance(obj, WorkOrder)
    }
    for request in pending:
        # The id is a server default, so it does not exist until the flush this hook runs
        # before. Assigning it here is what lets the two rows share a key.
        if request.id is None:
            request.id = uuid.uuid4()
        if request.id in known:
            continue
        session.add(
            WorkOrder(
                id=request.id,
                user_id=request.user_id,
                tool="research",
                subject_kind="company",
                subject_id=request.company_id,
                as_of_date=request.as_of_date,
                point_in_time=request.point_in_time,
                max_cost_gbp=request.max_cost_gbp,
                status=request.status,
                archived_at=request.archived_at,
            )
        )
