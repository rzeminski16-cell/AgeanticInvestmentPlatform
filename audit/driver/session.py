"""The production bundle, opened the way `aer step` and the worker open it.

One engine, one session factory, one Redis client and one resolved settings object for the
life of the driver — the same reads the worker makes (ADR 0050), so a driven run is the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.api.deps import current_user_or_none
from aer.config import Settings, load_settings
from aer.db.engine import create_engine, create_session_factory
from aer.db.models import User
from aer.errors import AerError
from aer.runtime import ServiceBundle, build_services
from aer.services.configuration import effective_settings

__all__ = ["AuditRuntime"]


@dataclass
class AuditRuntime:
    """Everything the driver shares across a run."""

    settings: Settings
    resolved: Settings
    engine: Any
    factory: async_sessionmaker[AsyncSession]
    redis: Redis
    bundle: ServiceBundle | None

    @classmethod
    async def open(cls, *, with_bundle: bool = True) -> AuditRuntime:
        settings = load_settings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        async with factory() as session:
            resolved = await effective_settings(session, settings)
        bundle = build_services(resolved, redis=redis) if with_bundle else None
        return cls(
            settings=settings,
            resolved=resolved,
            engine=engine,
            factory=factory,
            redis=redis,
            bundle=bundle,
        )

    async def close(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.factory()

    async def operator(self, session: AsyncSession) -> User:
        actor = await current_user_or_none(session)
        if actor is None:
            message = "No user exists; run `uv run aer seed-user --email …` first."
            raise AerError(message)
        return actor
