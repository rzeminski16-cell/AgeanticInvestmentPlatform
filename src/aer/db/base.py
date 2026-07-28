"""Declarative base and shared column conventions.

The naming convention below is the important part. Without it, PostgreSQL invents names
for indexes, unique constraints and check constraints, those names differ between the
database and the model metadata, and Alembic's autogenerate produces spurious
drop-and-recreate migrations for constraints that never changed. Setting it once, before
any table exists, avoids a class of migration noise that is genuinely painful to unpick
later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = ["Base", "created_at_column", "metadata"]

# `ix` deliberately includes the column list so multi-column indexes get distinct names.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    metadata = metadata

    def __repr__(self) -> str:
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


def created_at_column(**kwargs: Any) -> Mapped[datetime]:
    """A timezone-aware creation timestamp defaulted by the database.

    ``server_default`` rather than a Python default: the database clock is the single
    authority for when a row appeared. Application clocks drift, differ between the web
    process and the worker, and cannot be trusted in an audit trail.
    """
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        **kwargs,
    )
