"""Macro series and their observations, with the vintage on the observation.

**The vintage is on the observation, not on the series.** GDP for the first quarter of 2020
has one period and many values: the advance estimate, the second estimate, the third, the
annual revision, and every rebasing since. Those are not a value and its corrections — they
are five different facts about what was known at five different times, and a schema that kept
only the latest could not answer "what did this analysis have available?" at all.

So the primary key of an observation is ``(series, period, vintage)``. Two rows for the same
period at different vintages are expected and correct; two at the same vintage are a bug, and
the unique constraint says so.

**``is_archived`` records how strong the vintage claim is.** ALFRED genuinely returns a series
as it stood on a chosen date. The ONS returns the current series and says when it was
released, which is a weaker thing — and a UK figure that silently inherited a US figure's
point-in-time guarantee would be the whole problem this table exists to prevent. The column
carries the difference so a report can too.

**The check constraints below are documentation; the migration is the enforcement.** The test
schema is built by running the real Alembic path rather than ``metadata.create_all``, and
Alembic's autogenerate does not compare CHECK constraints at all — so a model whose checks
disagreed with the migration's would produce no drift and no failure. Both copies are kept in
step by hand, and it is `migrations/versions/0016` that decides what the database will refuse.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import Provider
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

__all__ = ["MacroObservationRow", "MacroSeriesRow"]


class MacroSeriesRow(Base):
    """A series this platform has retrieved at least once."""

    __tablename__ = "macro_series"

    id: Mapped[UuidPk]

    # The registry key, not the provider's identifier. Two providers could publish a series
    # under the same code, and the key is what a caller asks for.
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    provider: Mapped[Provider] = mapped_column(
        SaEnum(Provider, name="provider", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    identifier: Mapped[str] = mapped_column(String(64), nullable=False)

    # The ONS dataset, empty for providers that need none.
    dataset: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")

    label: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pure")
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)

    # Who produced the numbers, as distinct from who distributed them. The copyright question
    # is about this, and a stored column means a report can attribute correctly without
    # re-deriving it from a registry that may since have changed.
    originator: Mapped[str] = mapped_column(Text, nullable=False)

    # Why this may be used and redistributed, in the words that go into a report's sources
    # appendix. Copied onto the row so the claim travels with the data.
    licence_note: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    observations: Mapped[list[MacroObservationRow]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("char_length(btrim(key)) > 0", name="macro_series_key_is_not_blank"),
        CheckConstraint(
            "char_length(btrim(identifier)) > 0", name="macro_series_identifier_is_not_blank"
        ),
        CheckConstraint(
            "char_length(btrim(licence_note)) > 0", name="macro_series_licence_is_not_blank"
        ),
        Index("ix_macro_series_provider", "provider"),
    )

    def __repr__(self) -> str:
        return f"<MacroSeriesRow {self.key} ({self.provider.value}:{self.identifier})>"


class MacroObservationRow(Base):
    """One value of one series, as it stood at one vintage."""

    __tablename__ = "macro_observations"

    id: Mapped[UuidPk]

    series_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("macro_series.id", ondelete="CASCADE"), nullable=False
    )

    # The period the figure describes.
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)

    # The date the archive was read as at. See the module docstring: this is what makes two
    # rows for one period correct rather than duplicated.
    vintage: Mapped[date] = mapped_column(Date, nullable=False)

    value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)

    # Whether the provider genuinely served an archived vintage, or whether this is the
    # current series stamped with its release date. `True` for ALFRED, `False` for the ONS.
    # Stored per observation because a series can be retrieved from either over its life.
    is_archived: Mapped[bool] = mapped_column(nullable=False)

    # The document this came from, so a macro figure is traceable the same way a filing
    # figure is. Nullable only because a fixture-loaded observation has no fetch behind it.
    source_document_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    series: Mapped[MacroSeriesRow] = relationship(back_populates="observations")

    __table_args__ = (
        # The vintage is part of the identity. Two rows for one period at one vintage would
        # mean the same archive answered twice with different figures, which is a bug rather
        # than a revision.
        UniqueConstraint("series_id", "observed_on", "vintage", name="uq_macro_observations_point"),
        # A vintage before the period it describes is a figure published before the period it
        # measures had happened. Not a revision, an import error.
        CheckConstraint("vintage >= observed_on", name="macro_vintage_not_before_period"),
        Index("ix_macro_observations_series_id", "series_id"),
        # The query this table exists to serve: "this series, as at that date". Composite and
        # descending on vintage, because the answer is always the newest vintage not after a
        # cutoff and a plain index would scan every vintage ever stored.
        Index(
            "ix_macro_observations_pit",
            "series_id",
            "observed_on",
            text("vintage DESC"),
        ),
    )

    def __repr__(self) -> str:
        return f"<MacroObservationRow {self.observed_on} = {self.value} (vintage {self.vintage})>"
