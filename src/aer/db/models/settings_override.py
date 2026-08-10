"""A setting an operator changed from the interface.

One row per overridden field, keyed by its name on :class:`~aer.config.Settings`, so an
override is traceable to what it replaces without a translation table to maintain.

**No credential is ever stored here.** The allowlist in
:mod:`aer.services.configuration` is a closed vocabulary and excludes every secret field,
for the reason recorded in ``docs/adr/0050``: this table goes into `pg_dump` output, and so
into every backup.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp

__all__ = ["SettingsOverride"]


class SettingsOverride(Base):
    __tablename__ = "settings_overrides"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[Timestamp] = created_at_column()
