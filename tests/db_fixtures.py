"""Database fixtures.

Two decisions here are worth stating explicitly.

**A separate test database.** Everything runs against ``aer_test``, never the development
database. A test suite that can drop your working data is a test suite you eventually stop
running.

**Transactional isolation, not truncation.** Each test gets a connection inside an outer
transaction that is rolled back afterwards, with the session joining it via savepoints.
Tests can therefore call ``commit()`` and observe its effects — including constraint
violations, which only fire at flush time — while leaving the database untouched. Deleting
rows between tests instead would be slower and would silently mask ordering bugs.

The whole module skips when PostgreSQL is unreachable, so ``uv run pytest`` still works on
a machine with nothing running. It reports the reason rather than passing quietly.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent

# Read at import time, before the hermetic-environment fixture strips AER_* variables.
_DEFAULT_TEST_URL = (
    "postgresql+asyncpg://aer:aer_local_dev@127.0.0.1:5432/aer_test"  # pragma: allowlist secret
)
TEST_DATABASE_URL = os.environ.get("AER_TEST_DATABASE_URL", _DEFAULT_TEST_URL)
TEST_USER_AGENT = "Test Runner test@example.invalid"


def in_worker_thread(work: Callable[[], Any]) -> Any:
    """Run ``work`` on a dedicated thread and return its result.

    ``asyncio.run`` raises if a loop is already running in the calling thread, and one
    often is: Playwright's synchronous API drives its own loop, so setup code that starts
    a loop fails inside a browser test with "cannot be called from a running event loop" —
    a message that points at asyncio rather than at the thing that needs fixing.

    A fresh thread has no running loop, which sidesteps the question rather than trying to
    detect it. Slightly slower and completely reliable, which is the right trade for setup.
    Exceptions are re-raised on the calling thread so a failure still fails the test.
    """
    outcome: dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["value"] = work()
        except BaseException as exc:  # re-raised on the calling thread below
            outcome["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()

    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def run_async(coroutine: Any) -> Any:
    """Run a coroutine to completion from synchronous code, safely. See
    :func:`in_worker_thread`."""
    return in_worker_thread(lambda: asyncio.run(coroutine))


def _admin_url(url: str) -> str:
    """The same server, but pointed at the default ``postgres`` database."""
    base, _, _ = url.rpartition("/")
    return f"{base}/postgres"


def _database_name(url: str) -> str:
    return url.rpartition("/")[2]


async def _server_is_reachable(url: str) -> tuple[bool, str]:
    engine = create_async_engine(_admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect():
            return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        await engine.dispose()


async def _recreate_database(url: str) -> None:
    """Drop and recreate the test database, so every run starts from a known state."""
    name = _database_name(url)
    engine = create_async_engine(_admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def _run_migrations(url: str) -> None:
    """Apply migrations in-process.

    Deliberately the real Alembic path rather than ``metadata.create_all``: the migration
    is what production runs, so it is what the tests must exercise. ``create_all`` would
    happily pass against a migration that does not actually work.
    """
    previous = {
        "AER_DATABASE_URL": os.environ.get("AER_DATABASE_URL"),
        "AER_HTTP_USER_AGENT": os.environ.get("AER_HTTP_USER_AGENT"),
    }
    os.environ["AER_DATABASE_URL"] = url
    os.environ["AER_HTTP_USER_AGENT"] = TEST_USER_AGENT
    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        command.upgrade(config, "head")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def database_url() -> str:
    """A migrated test database, or skip the whole suite with the reason."""
    reachable, reason = run_async(_server_is_reachable(TEST_DATABASE_URL))
    if not reachable:
        pytest.skip(
            f"PostgreSQL is not reachable at {_admin_url(TEST_DATABASE_URL)} ({reason}). "
            "Start it with `just up`, or set AER_TEST_DATABASE_URL."
        )

    run_async(_recreate_database(TEST_DATABASE_URL))
    # Alembic's async env starts its own event loop, so this needs the same treatment as
    # the calls above rather than only looking synchronous from here.
    in_worker_thread(lambda: _run_migrations(TEST_DATABASE_URL))
    return TEST_DATABASE_URL


@pytest.fixture
async def db_engine(database_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(database_url, poolclass=None)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: Any) -> AsyncIterator[AsyncSession]:
    """A session whose writes are rolled back when the test ends.

    ``join_transaction_mode="create_savepoint"`` lets the test call ``commit()`` — which
    is what triggers flush-time constraint checks — without escaping the outer
    transaction that gets rolled back.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def repo_root() -> Iterator[Path]:
    return REPO_ROOT
