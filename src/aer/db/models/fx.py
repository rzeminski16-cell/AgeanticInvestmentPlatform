"""Published exchange rates, one row per pair per day per reading.

Shaped like ``macro_observations`` and for the same reason: a rate is a dated observation
somebody published, not a number in a column that gets updated. ADR 0078.

**Two dates, and they are not the same date.** ``observed_on`` is the day the rate was
*for*; ``vintage`` is the day this platform's reading of that publication was as at. The
ECB publishes a rate once and corrects it rarely, which is an argument for the column
rather than against it — a correction applied as an ``UPDATE`` would silently rewrite an
input to arithmetic that has already run and may already have been approved. It adds a row
instead, exactly as a GDP revision does.

**The pointer to the document may be lost; the hash of what was parsed may not.** ADR 0078
made ``source_document_id`` ``NOT NULL`` to keep out a rate with no publication behind it,
and ADR 0080 moved that guarantee to ``artefact_sha256`` — because the pointer is
request-scoped and the rate is not. A research request that fetched rates is purgeable, its
documents go with it, and every rate keeps a digest naming the bytes it came from. A digest
cannot be produced for a response nobody fetched, so the door stays shut, and unlike the
pointer it stays shut afterwards. A rate somebody *typed* is not a row here at all: it is an
attestation under ADR 0069, carrying the weaker grade that propagates.

**Every row here has the euro on one side.** Not a constraint — the schema would happily
hold a GBP/USD row — but a property of the only source this platform has. The ECB publishes
the reference rates *of the euro*, so a pair that does not involve it is a cross: a division
of two of these rows, recorded as a calculation by :func:`aer.calc.fx.cross` rather than
stored here looking like something somebody published.

**The check constraints below are documentation; the migration is the enforcement.** The
test schema is built by running the real Alembic path, and autogenerate does not compare
CHECK constraints at all — so a model whose checks disagreed with the migration's would
produce no drift and no failure. Both copies are kept in step by hand, and it is
`migrations/versions/0052` that decides what the database will refuse.
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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Sha256, Timestamp, UuidFkOptional, UuidPk

__all__ = ["FxRateRow"]


class FxRateRow(Base):
    """One published rate for one pair, as read at one vintage."""

    __tablename__ = "fx_rates"

    id: Mapped[UuidPk]

    # The currency being converted *from*, and the one converted *into*. Stored as the pair
    # rather than as a series key: there is no series to look a rate up by, and inventing
    # one per pair would mean inventing one per cross too.
    base: Mapped[str] = mapped_column(String(3), nullable=False)
    quote: Mapped[str] = mapped_column(String(3), nullable=False)

    # The day the rate was for, which is the field point-in-time selection filters against.
    # `aer.calc.fx.FxRate` keeps the same distinction for the same reason.
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)

    # The day this reading was as at. See the module docstring: this is what makes two rows
    # for one day correct rather than duplicated.
    vintage: Mapped[date] = mapped_column(Date, nullable=False)

    # Units of `quote` per unit of `base`. NUMERIC at the same width as a macro observation,
    # because a rate is divided into balance-sheet figures and a rate that rounds is a
    # restatement that drifts.
    rate: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)

    # `SET NULL`, as on a macro observation and a price bar, because this is the same kind
    # of thing: external data acquired under one run and used by every other. A rate must
    # outlive the request that happened to fetch it — the portfolio needs it daily, another
    # request needs the same row, and a published report's lineage cites it (ADR 0080).
    source_document_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )

    # The digest of the bytes this rate was parsed from, which is the claim that does not
    # degrade. Not a foreign key to `artefacts.sha256`: that row is collectable and its
    # payload purgeable under ADR 0031, and what this column asserts is about the bytes
    # rather than about whether this platform still holds them.
    artefact_sha256: Mapped[Sha256] = mapped_column(nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        # The vintage is part of the identity. Two rows for one day at one vintage would
        # mean the same publication was read twice and said different things, which is a
        # bug rather than a correction.
        UniqueConstraint("base", "quote", "observed_on", "vintage", name="uq_fx_rates_point"),
        # A reading dated before the day it read about is an import error, not a correction.
        CheckConstraint("vintage >= observed_on", name="fx_vintage_not_before_observation"),
        # Zero and negative rates are parse failures. A zero rate divides into a conversion
        # and a negative one flips the sign of a holding; neither is an exchange rate.
        CheckConstraint("rate > 0", name="fx_rate_is_positive"),
        CheckConstraint("base <> quote", name="fx_pair_is_two_currencies"),
        CheckConstraint(
            "base = upper(base) AND quote = upper(quote)", name="fx_currencies_are_upper"
        ),
        CheckConstraint(
            "char_length(base) = 3 AND char_length(quote) = 3", name="fx_currencies_are_iso_codes"
        ),
        # Sixty-four lowercase hex characters or nothing. The whole point of this column is
        # that it cannot be produced for a response nobody fetched, and a blank or truncated
        # value would be exactly the hand-typed rate ADR 0078 refused wearing a digest.
        CheckConstraint("char_length(artefact_sha256) = 64", name="fx_sha256_is_full_length"),
        CheckConstraint("artefact_sha256 = lower(artefact_sha256)", name="fx_sha256_is_lowercase"),
        # The query this table exists to serve: "this pair, as at that date". Descending on
        # vintage because the answer is always the newest reading of the chosen day, and a
        # plain index would scan every reading ever stored.
        Index(
            "ix_fx_rates_pit",
            "base",
            "quote",
            "observed_on",
            text("vintage DESC"),
        ),
    )

    def __repr__(self) -> str:
        return f"<FxRateRow {self.base}/{self.quote} {self.observed_on} = {self.rate}>"
