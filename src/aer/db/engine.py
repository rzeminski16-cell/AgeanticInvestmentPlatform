"""Async engine and session management.

One engine per process, created lazily and disposed on shutdown. The engine owns a
connection pool, so constructing one per request would defeat pooling entirely; creating
it at import time would make the module impossible to import without a configured
database, which breaks tests and tooling.

Sessions do **not** autocommit. Every write path commits explicitly, because in this
system a partially-written job or a report row without its artefacts is worse than a
failed run — a failure is visible, a half-written audit trail is not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aer.config import Settings, get_settings

__all__ = [
    "create_engine",
    "create_session_factory",
    "dispose_engine",
    "get_engine",
    "get_session",
    "session_scope",
]

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build a new async engine. Callers are responsible for disposing of it."""
    resolved = settings or get_settings()
    return create_async_engine(
        resolved.database_url,
        # Verify a pooled connection before handing it out. Without this, a connection
        # dropped by a database restart or an idle timeout surfaces as a confusing error
        # in the middle of a long research run rather than being replaced transparently.
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        # SQL echoing is controlled by log level, not a separate flag, so there is one
        # place to turn up verbosity.
        echo=resolved.log_level == "DEBUG",
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        # Attributes stay usable after commit. Without this, reading any field on a
        # committed object triggers a lazy refresh, which raises outside an async context
        # and produces bewildering errors in request handlers.
        expire_on_commit=False,
        autoflush=False,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine  # noqa: PLW0603 -- one engine per process is the intended lifecycle
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory, creating it on first use."""
    global _session_factory  # noqa: PLW0603 -- see get_engine
    if _session_factory is None:
        _session_factory = create_session_factory(get_engine())
    return _session_factory


async def dispose_engine() -> None:
    """Close the pool and reset module state. Call on application shutdown and in tests."""
    global _engine, _session_factory  # noqa: PLW0603 -- see get_engine
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional session: commits on success, rolls back on any exception.

    Use for background work such as workflow steps. Request handlers should depend on
    :func:`get_session` instead so the framework owns the lifecycle.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session for dependency injection.

    Deliberately does **not** commit: the caller decides when a unit of work is complete,
    so a handler that raises halfway through cannot leave a partial write behind.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session
