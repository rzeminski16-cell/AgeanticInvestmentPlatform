"""Database layer: engine, session management, base classes and ORM models."""

from __future__ import annotations

from aer.db.base import Base, metadata
from aer.db.engine import (
    create_engine,
    create_session_factory,
    dispose_engine,
    get_engine,
    get_session,
    session_scope,
)

__all__ = [
    "Base",
    "create_engine",
    "create_session_factory",
    "dispose_engine",
    "get_engine",
    "get_session",
    "metadata",
    "session_scope",
]
