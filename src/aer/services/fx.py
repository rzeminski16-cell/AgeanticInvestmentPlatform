"""Storing published exchange rates, and reading back the one an as-of date could have used.

The seam between :mod:`aer.sources.macro.ecb`, which fetches and parses, and
:mod:`aer.calc.fx`, which selects and converts. Everything database-shaped lives here so
neither of those needs a session, and so the arithmetic stays testable without one. ADR
0082.

**Nothing here decides anything.** Which observation an as-of date may use, how stale is too
stale, and what a cross-rate is are all :mod:`aer.calc.fx`'s answers, tested there and
reached from here — this module's job is to hand that module rows and to write down what it
returns. A second copy of the point-in-time rule living in a SQL ``WHERE`` clause is exactly
how two ideas of "as at" come to disagree, and the one in the query is the one nobody reads.

**Storing is idempotent on ``(pair, day, vintage)``.** A retried acquisition writes no second
copy, because duplicate rows at one vintage would make a correction appear where none
happened. A rate the ECB has genuinely restated arrives at a *later* vintage and is a new
row, never an update to the old one.

**An observation later than the response's as-of date is refused at the door.** Invariant 4
is enforced at acquisition, in code: :func:`aer.sources.macro.ecb.reference_rate_url` bounds
the request, and this bounds what gets written even if the portal answers with more than it
was asked for. :func:`aer.calc.fx.select_rate` then applies the same rule a third time over
what comes back out. Three checks for one rule is not redundancy here — it is the difference
between a control and a query parameter.

**The vintage is an audit trail, not a point-in-time filter, and this is where this module
parts company with :mod:`aer.services.macro`.** ALFRED genuinely serves a series as it stood
on a chosen date, so a macro read filters ``vintage <= as_of`` and gets what was knowable.
The ECB Data Portal is not an archive: it serves the rates as they stand, so a vintage here
records when *this platform* read them and nothing else. Filtering on it would make a run
dated to last June blind to every rate fetched since, which is a fetch-order artefact
wearing point-in-time clothes. What bounds a read is ``observed_on``, which is when the rate
was *published*, and that is filtered — in the kernel.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.calc.fx import (
    MAX_STALENESS_DAYS,
    FxRate,
    convert_at,
    cross,
    invert,
    select_rate,
)
from aer.calc.units import CalculationError, Quantity, SourceRef, Unit
from aer.db.models import FxRateRow
from aer.sources.macro.client import ReferenceRateResponse
from aer.sources.macro.ecb import BASE_CURRENCY

__all__ = [
    "StoredRates",
    "as_fx_rate",
    "convert_as_at",
    "rate_as_at",
    "record_reference_rates",
]

_log = structlog.get_logger("aer.services.fx")

# How many rows to load before asking `select_rate` to choose one.
#
# It needs the newest observation on or before the as-of date, and — when that one is too
# old — needs to *see* it in order to say so rather than reporting a missing pair. Sixteen
# covers the seven-day staleness window twice over even if every day in it carries a
# correction, and stops a decade of history being read to answer one question.
_CANDIDATE_ROWS: Final = 16


@dataclass(frozen=True, slots=True)
class StoredRates:
    """What a store of reference rates actually did."""

    inserted: int
    already_held: int

    # Observations the portal returned that are later than the as-of date the request was
    # bounded by. Counted rather than ignored: a non-zero value here means the bound in the
    # URL did not hold, which is worth knowing about the source.
    refused_after_as_of: int


async def record_reference_rates(
    session: AsyncSession,
    response: ReferenceRateResponse,
    *,
    source_document_id: uuid.UUID,
) -> StoredRates:
    """Store one currency's euro reference rates. Returns what was written.

    Every row is ``EUR`` to the response's currency, which is the direction the ECB
    publishes: an observation of ``1.0850`` for ``USD`` means one euro buys 1.0850 dollars,
    so the rate converts euros *into* dollars.

    **That direction is asserted here and checked nowhere downstream**, which is worth being
    plain about. :class:`~aer.calc.fx.FxRate` refuses a rate whose unit disagrees with its
    pair, and that is a real guard — but the unit is derived from the row's own ``base`` and
    ``quote``, so a row written the wrong way round agrees with itself and passes. What
    stands between this line and a balance sheet wrong by the square of the rate is that
    ``EUR`` is a property of the source rather than a parameter, and a test that converts a
    known amount and checks which way it moved.

    ``source_document_id`` is required, and so is the fetch it came from: the digest is read
    off ``response.fetch`` rather than passed in, because the caller could pass the hash of
    something else and the whole value of the column is that it names *these* bytes. The
    pointer may be nulled by a later purge; the digest may not (ADR 0084).
    """
    currency = response.rates.currency
    vintage = response.as_of

    # Narrowed to this vintage, so a day already held at an *earlier* reading is correctly
    # absent: a restatement is a new row, and skipping it as a duplicate would be the update
    # this table exists to avoid.
    held = {
        row.observed_on
        for row in await session.scalars(
            select(FxRateRow).where(
                FxRateRow.base == BASE_CURRENCY,
                FxRateRow.quote == currency,
                FxRateRow.vintage == vintage,
            )
        )
    }

    inserted = 0
    already = 0
    refused = 0

    for observed_on, value in response.rates.observations:
        if observed_on > response.as_of:
            refused += 1
            continue
        if observed_on in held:
            already += 1
            continue
        session.add(
            FxRateRow(
                base=BASE_CURRENCY,
                quote=currency,
                observed_on=observed_on,
                vintage=vintage,
                rate=value,
                source_document_id=source_document_id,
                artefact_sha256=response.fetch.sha256,
            )
        )
        inserted += 1

    await session.flush()
    _log.info(
        "fx.rates_recorded",
        currency=currency,
        vintage=vintage.isoformat(),
        received=len(response.rates.observations),
        inserted=inserted,
        already_held=already,
        refused_after_as_of=refused,
    )
    return StoredRates(inserted=inserted, already_held=already, refused_after_as_of=refused)


def as_fx_rate(row: FxRateRow) -> FxRate:
    """A stored row as the value :mod:`aer.calc.fx` works in.

    Sourced as a **fact**, at the row rather than at the document: two readings of one day's
    publication are two rows and one document, and a lineage that named the document could
    not say which reading a figure used. It is also the reference that survives a purge,
    which a document id is not (ADR 0084).
    """
    unit = Unit.currency(row.quote) / Unit.currency(row.base)
    return FxRate(
        base=row.base,
        quote=row.quote,
        rate=Quantity.of(
            row.rate,
            unit,
            source=SourceRef.fx_rate(
                row.id,
                label=f"{row.base}/{row.quote}@{row.observed_on.isoformat()}",
            ),
        ),
        observed_on=row.observed_on,
    )


async def rate_as_at(
    session: AsyncSession,
    context: CalculationContext,
    *,
    base: str,
    quote: str,
    as_of: date,
    max_staleness_days: int = MAX_STALENESS_DAYS,
) -> FxRate:
    """The rate for a pair as at a date, published, inverted or crossed.

    Three shapes, and which one applies follows from where the euro is:

    * ``EUR`` to anything is a **published** observation, read straight off the table.
    * anything to ``EUR`` is that observation **inverted**, keeping its source, because an
      inverted rate is one publication read backwards rather than a second one.
    * anything else is a **cross**: two published legs divided, as a recorded calculation,
      so a reader following the figure back reaches two source documents and a formula
      rather than a number that looks as though somebody published it.

    ``context`` is required even where nothing is traced. The alternative — optional, and
    only supplied for the pair that happens to need it — would make whether a conversion is
    recorded depend on which currencies a book holds.

    Raises:
        CalculationError: If the two currencies are the same, or if the legs of a cross
            resolve to different days. The second is
            :func:`aer.calc.fx.cross`'s refusal and it is kept: both legs come from one
            daily publication, so a mismatch means one series has a hole in it and the
            division would produce a rate nobody could have transacted at.
        NoRateAvailableError: If the pair has no usable observation. Its subclasses say
            which way it failed — every observation later than the as-of date, or the
            nearest one too old to use.
    """
    from_code = _currency(base)
    into_code = _currency(quote)

    if from_code == into_code:
        message = (
            f"There is no {from_code}/{into_code} rate to select. A currency converts to "
            "itself at one, and asking for that rate is asking for the number one."
        )
        raise CalculationError(message, context={"base": from_code, "quote": into_code})

    if from_code == BASE_CURRENCY:
        return await _pivot_leg(
            session, currency=into_code, as_of=as_of, max_staleness_days=max_staleness_days
        )

    base_leg = await _pivot_leg(
        session, currency=from_code, as_of=as_of, max_staleness_days=max_staleness_days
    )
    if into_code == BASE_CURRENCY:
        return invert(base_leg)

    quote_leg = await _pivot_leg(
        session, currency=into_code, as_of=as_of, max_staleness_days=max_staleness_days
    )
    return cross(context, base_leg=base_leg, quote_leg=quote_leg)


async def convert_as_at(
    session: AsyncSession,
    context: CalculationContext,
    *,
    amount: Quantity,
    into: str,
    as_of: date,
    max_staleness_days: int = MAX_STALENESS_DAYS,
) -> Quantity:
    """An amount in another currency, at the rate that date could have used.

    The form nearly every caller wants, and the reason both halves of this module exist: the
    currency converted *from* is read off the amount's own unit rather than passed in, so a
    caller cannot name a pair the figure is not in.

    The result is a traced calculation whose inputs are the amount and the rate, each with
    its own source — and where the rate is a cross, the rate is itself a calculation over
    two published legs. That chain is the whole answer to "what rate did this use, and where
    did it come from?".

    Raises:
        CalculationError: If the amount is not in a currency, or is in more than one.
    """
    held = amount.unit.currencies
    if len(held) != 1:
        message = (
            f"{amount.unit.symbol} is not an amount in one currency, so there is nothing to "
            "convert. A ratio and a share count are the same number in every currency, and a "
            "unit naming two is a figure that was already converted wrongly."
        )
        raise CalculationError(message, context={"unit": amount.unit.symbol})

    rate = await rate_as_at(
        session,
        context,
        base=held[0],
        quote=into,
        as_of=as_of,
        max_staleness_days=max_staleness_days,
    )
    return convert_at(context, amount=amount, rate=rate)


async def _pivot_leg(
    session: AsyncSession, *, currency: str, as_of: date, max_staleness_days: int
) -> FxRate:
    """The published ``EUR``-to-``currency`` observation for the as-of date.

    Every rate this platform stores has the euro on one side, so this is the only shape of
    lookup there is; a direct pair, an inversion and a cross are all built from it.
    """
    candidates = await _candidates(session, currency=currency, as_of=as_of)
    return select_rate(
        candidates,
        base=BASE_CURRENCY,
        quote=currency,
        as_of=as_of,
        max_staleness_days=max_staleness_days,
    )


async def _candidates(session: AsyncSession, *, currency: str, as_of: date) -> tuple[FxRate, ...]:
    """The rows :func:`~aer.calc.fx.select_rate` needs in order to choose or to refuse.

    Newest first, one per day — the newest *reading* of each day, since a later vintage is a
    correction to the same publication rather than a second one.

    **The as-of filter is a bound on how much is read, never the decision.** Where nothing
    was published on or before the as-of date, this falls back to the earliest row there is,
    so ``select_rate`` can say "every observation is later than the as-of date" instead of
    "there is no such pair". Those are different faults with different fixes, and the second
    one sends somebody looking for a missing acquisition that already ran.
    """
    rows = list(
        await session.scalars(
            select(FxRateRow)
            .where(
                FxRateRow.base == BASE_CURRENCY,
                FxRateRow.quote == currency,
                FxRateRow.observed_on <= as_of,
            )
            .order_by(FxRateRow.observed_on.desc(), FxRateRow.vintage.desc())
            .limit(_CANDIDATE_ROWS)
        )
    )
    if not rows:
        earliest = await session.scalar(
            select(FxRateRow)
            .where(FxRateRow.base == BASE_CURRENCY, FxRateRow.quote == currency)
            .order_by(FxRateRow.observed_on, FxRateRow.vintage.desc())
            .limit(1)
        )
        rows = [earliest] if earliest is not None else []

    return tuple(as_fx_rate(row) for row in _newest_reading_per_day(rows))


def _newest_reading_per_day(rows: Sequence[FxRateRow]) -> list[FxRateRow]:
    """One row per day, keeping the first — which the query's ordering makes the newest."""
    chosen: dict[date, FxRateRow] = {}
    for row in rows:
        chosen.setdefault(row.observed_on, row)
    return list(chosen.values())


def _currency(code: str) -> str:
    cleaned = code.strip().upper()
    # Validated through the unit system rather than against a pattern kept here, so a code
    # this module will look a rate up by is exactly one the arithmetic can be done in.
    # `aer.services.prices` normalises the same way for the same reason.
    Unit.currency(cleaned)
    return cleaned
