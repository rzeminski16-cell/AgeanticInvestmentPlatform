"""Fixtures for exercising the HTTP application.

The application is driven in-process through ``httpx.ASGITransport`` rather than over a
socket. That keeps the default suite free of network access, and it means an exception
raised inside a handler is the same object the test can inspect.

``raise_app_exceptions=False`` is the one setting worth understanding. Starlette's
``ServerErrorMiddleware`` sends the 500 response and then re-raises, so that a real server
still logs the crash. Left at its default, ``httpx`` would honour that re-raise and the
test would never see the response we care about asserting on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from aer.api.app import AppState, create_app
from aer.config import Settings, load_settings

BASE_URL = "http://testserver"


@pytest.fixture
def api_settings(settings_env, tmp_path) -> Settings:
    """Settings for an application under test: real shape, throwaway paths."""
    settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
    settings_env.setenv("AER_SECRET_KEY", "test-signing-key-not-a-real-one")
    return load_settings()


@pytest.fixture
def fake_redis() -> Redis:
    """An in-process Redis.

    A real implementation of the protocol rather than a stub that returns ``True``: if
    the readiness probe called a method that did not exist, a stub would happily pass and
    the endpoint would be broken in production only.
    """
    client: Redis = fake_aioredis.FakeRedis(decode_responses=True)
    return client


@pytest.fixture
async def broken_redis() -> AsyncIterator[Redis]:
    """A client pointed at a port nothing is listening on."""
    client: Redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.25)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def api_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """An engine against the migrated test database. Requires PostgreSQL."""
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def broken_engine() -> AsyncIterator[AsyncEngine]:
    """An engine pointed at a port nothing is listening on.

    Deliberately a genuinely unreachable database rather than a mocked failure: what is
    being tested is that a driver-level connection error is caught, classified and
    reported, and a raised ``Mock`` would not exercise any of that.
    """
    engine = create_async_engine(
        "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/nowhere",  # pragma: allowlist secret
        connect_args={"timeout": 0.25},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


def build_app(settings: Settings, *, engine: AsyncEngine, redis: Redis) -> FastAPI:
    """Build an application over resources the test owns and will close itself."""
    state = AppState(
        settings=settings,
        engine=engine,
        session_factory=async_sessionmaker(bind=engine, expire_on_commit=False),
        redis=redis,
    )
    return create_app(settings, state=state)


async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Run the lifespan and yield a client bound to the application."""
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=BASE_URL,
        ) as client,
    ):
        yield client
