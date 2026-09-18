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
from datetime import date

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from aer.api.app import AppState, create_app
from aer.config import Settings, load_settings
from aer.fetch.client import FetchResult
from aer.runtime import Registers
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.sources.uk.companies_house import FilingHistory, FilingRecord
from tests.db_cleanup import delete_all

BASE_URL = "http://testserver"


class _AdmittingRegister:
    """A register that recognises whatever it is asked about, and reaches no network.

    The availability check (ADR 0128) runs wherever a run is commissioned, so *every* test
    that starts a run goes through it. Left to build real clients it would fetch EDGAR's
    ticker file from the suite, which the suite may not do — and a test about a ticker EDGAR
    does not list passes its own stub instead.
    """

    async def resolve_entity(
        self, ticker: str, *, exchange: str | None = None, name: str | None = None
    ) -> ResolvedEntity:
        return ResolvedEntity(
            identifier=ticker.upper(),
            name=name or ticker.upper(),
            ticker=ticker,
            exchange=exchange,
        )

    async def fetch_filing_history(self, company_number: str, **_: object) -> FilingHistory:
        return FilingHistory(
            company_number=company_number,
            filings=(
                FilingRecord(
                    transaction_id="stub-transaction",
                    category="accounts",
                    description="Accounts",
                    filed_on=date(2026, 1, 1),
                    document_id="stub-document",
                ),
            ),
            total=1,
        )

    async def fetch_document(self, ref: DocumentRef, *, tagged: bool = True) -> FetchResult:
        return FetchResult(
            url=ref.url,
            final_url=ref.url,
            status_code=200,
            sha256="0" * 64,
            size_bytes=1,
            media_type="application/xhtml+xml",
            declared_media_type="application/xhtml+xml",
            headers={},
            redirect_chain=(),
            elapsed_ms=0.0,
            attempts=1,
        )


def admitting_registers() -> Registers:
    """Registers that admit every subject. The default for an application under test."""
    return Registers(
        sec_client=_AdmittingRegister(),  # type: ignore[arg-type]
        companies_house_client=_AdmittingRegister(),  # type: ignore[arg-type]
    )


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
    """An engine against the migrated test database, emptied first. Requires PostgreSQL.

    Emptied for the reason `tests/db_fixtures.py` gives for `db_engine`: what an earlier
    test committed must not be what this test's application reads.
    """
    engine = create_async_engine(database_url)
    try:
        await delete_all(engine)
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


def build_app(
    settings: Settings,
    *,
    engine: AsyncEngine,
    redis: Redis,
    provider: object | None = None,
    store: object | None = None,
    registers: object | None = None,
) -> FastAPI:
    """Build an application over resources the test owns and will close itself.

    ``provider`` and ``store`` are injected for the one endpoint that spends from the web
    process — the skill dry run. Left as ``None`` the application builds the configured
    provider on first use, which needs a real key: exactly the production behaviour, and
    exactly what a test must not reach.

    ``registers`` is the same arrangement for the availability check a run is commissioned
    through (ADR 0128), and it defaults to a stub that admits every subject rather than to
    ``None``: left unset the application would build real clients and the check would reach
    EDGAR from the test suite, which is the one thing the suite may not do. A test about the
    check itself passes its own.
    """
    state = AppState(
        settings=settings,
        engine=engine,
        session_factory=async_sessionmaker(bind=engine, expire_on_commit=False),
        redis=redis,
        provider=provider,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        registers=registers if registers is not None else admitting_registers(),  # type: ignore[arg-type]
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
