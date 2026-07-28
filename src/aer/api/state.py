"""Process-lifetime resources shared by every request.

In its own module rather than in ``app.py`` because the dependency functions need the
type and the application factory needs the dependency functions. Putting the dataclass
where both can import it breaks that cycle without either of them reaching for a
deferred import.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aer.config import Settings

__all__ = ["AppState"]


@dataclass(slots=True)
class AppState:
    """Everything a handler might need that outlives a single request."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
