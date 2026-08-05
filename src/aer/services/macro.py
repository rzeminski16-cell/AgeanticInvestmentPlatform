"""Storing macro observations by vintage, and reading back the one an as-of date had.

Two operations, and the second is the whole point.

:func:`record_series` writes observations keyed by ``(series, period, vintage)``, so a
revision adds rows rather than replacing them. Storing the same vintage twice is idempotent,
because a retried step must not produce a second copy of a series and make a revision appear
where none happened.

:func:`observation_as_at` answers "what did this series say about that period, to somebody
standing on the as-of date?" — the newest vintage **not after** the as-of date, of the newest
period not after it. Both halves matter and they are different dates: a valuation as at 30
June wants the March GDP figure as it was published in, say, May, not the March figure as
revised the following year.

**Nothing here falls back to the current series.** A period with no vintage at or before the
as-of date returns ``None`` and the caller decides; it does not quietly return the newest row
it can find. That fallback is the entire error this table exists to prevent, and it would be
invisible in the output because a GDP figure looks like a GDP figure whichever year it was
published in.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import Quantity, SourceRef, Unit
from aer.db.models import MacroObservationRow, MacroSeriesRow
from aer.sources.macro.client import MacroResponse
from aer.sources.macro.series import MacroSeries, risk_free_series_for, series_for

__all__ = [
    "as_quantity",
    "observation_as_at",
    "observations_for_series",
    "record_series",
    "risk_free_rate_as_at",
    "upsert_series",
]

_log = structlog.get_logger("aer.services.macro")


async def upsert_series(session: AsyncSession, series: MacroSeries) -> MacroSeriesRow:
    """The row for a registry entry, created on first use.

    The registry's licence and originator are **copied onto the row** rather than looked up
    at read time. A report published today must be able to state the terms it was published
    under, and a registry edited next year would otherwise rewrite the attribution on work
    already delivered.
    """
    existing = await session.scalar(select(MacroSeriesRow).where(MacroSeriesRow.key == series.key))
    if existing is not None:
        return existing

    row = MacroSeriesRow(
        key=series.key,
        provider=series.provider,
        identifier=series.identifier,
        dataset=series.dataset,
        label=series.label,
        unit=series.unit,
        frequency=series.frequency.value,
        originator=series.originator,
        licence_note=series.licence,
    )
    session.add(row)
    await session.flush()
    return row


async def record_series(
    session: AsyncSession,
    response: MacroResponse,
    *,
    source_document_id: uuid.UUID | None = None,
) -> int:
    """Store a retrieved series. Returns how many observations were new.

    **Idempotent on ``(series, period, vintage)``.** A retried acquisition step must not
    write a second copy: duplicated rows at one vintage would make a revision appear where
    none happened, and the disagreement ladder would then have two identical positions to
    arbitrate between.
    """
    row = await upsert_series(session, response.series)

    existing = {
        (observation.observed_on, observation.vintage)
        for observation in await session.scalars(
            select(MacroObservationRow).where(MacroObservationRow.series_id == row.id)
        )
    }

    written = 0
    for observation in response.observations:
        if (observation.observed_on, observation.vintage) in existing:
            continue
        session.add(
            MacroObservationRow(
                series_id=row.id,
                observed_on=observation.observed_on,
                vintage=observation.vintage,
                value=observation.value,
                is_archived=response.is_archived,
                source_document_id=source_document_id,
            )
        )
        written += 1

    await session.flush()
    _log.info(
        "macro.recorded",
        series=response.series.key,
        vintage=response.vintage.isoformat(),
        received=len(response.observations),
        written=written,
        archived=response.is_archived,
    )
    return written


async def observation_as_at(
    session: AsyncSession, *, key: str, as_of: date
) -> MacroObservationRow | None:
    """The value this series had for its latest period, as somebody on ``as_of`` would see it.

    Two filters, on two different dates:

    * ``vintage <= as_of`` — the figure must have been *published* by the as-of date.
    * ``observed_on <= as_of`` — the period it describes must have *happened* by then.

    Then the newest period, and within that period the newest vintage. A query that took the
    newest vintage first would find the latest revision of an old period and miss a newer
    period entirely.

    ``None`` where nothing qualifies. **Never the current value**: falling back would put a
    figure published years later into an analysis dated before it existed, and nothing
    downstream could tell.
    """
    series = series_for(key)
    found: MacroObservationRow | None = await session.scalar(
        select(MacroObservationRow)
        .join(MacroSeriesRow, MacroSeriesRow.id == MacroObservationRow.series_id)
        .where(
            MacroSeriesRow.key == series.key,
            MacroObservationRow.vintage <= as_of,
            # Redundant while `macro_vintage_not_before_period` holds: a period cannot
            # postdate its own vintage, so a vintage at or before the as-of date cannot carry
            # a later period. Kept as defence in depth, because the day that constraint is
            # relaxed for a forecast series this query would start reaching forward silently.
            # `test_macro_service.py` tests the constraint directly, since with it in place no
            # test can distinguish this line's presence from its absence.
            MacroObservationRow.observed_on <= as_of,
        )
        .order_by(
            MacroObservationRow.observed_on.desc(),
            MacroObservationRow.vintage.desc(),
        )
        .limit(1)
    )
    return found


async def risk_free_rate_as_at(
    session: AsyncSession, *, currency: str, as_of: date
) -> MacroObservationRow | None:
    """The risk-free rate for a currency, at the vintage the as-of date had.

    Which series that is, is a documented decision rather than a lookup — see
    :data:`~aer.sources.macro.series.RISK_FREE_SERIES`. A currency with no documented series
    raises rather than substituting another one's government yield, because that error is
    the whole rate differential and looks entirely ordinary in the output.

    Raises:
        SeriesRefusedError: If no series is documented for the currency.
    """
    series = risk_free_series_for(currency)
    return await observation_as_at(session, key=series.key, as_of=as_of)


def as_quantity(observation: MacroObservationRow, *, series: MacroSeries) -> Quantity:
    """An observation as something a calculation can take.

    Sourced as a **fact**, not an assumption: a published statistic is an observation
    somebody made, and the vintage in the label is what distinguishes this reading of it from
    a later one. A calculation using it therefore resolves to a row that names the period,
    the vintage and the document it came from.
    """
    return Quantity.of(
        observation.value,
        Unit.parse(series.unit),
        source=SourceRef.fact(
            observation.id,
            label=f"{series.key}@{observation.observed_on.isoformat()}"
            f" (vintage {observation.vintage.isoformat()})",
        ),
    )


async def observations_for_series(
    session: AsyncSession, *, key: str, as_of: date
) -> Sequence[MacroObservationRow]:
    """Every period of a series, each at the newest vintage the as-of date had.

    The series a chart would draw: one value per period, chosen the way
    :func:`observation_as_at` chooses one. Periods with nothing published by the as-of date
    are absent rather than carried forward.
    """
    series = series_for(key)
    rows = list(
        await session.scalars(
            select(MacroObservationRow)
            .join(MacroSeriesRow, MacroSeriesRow.id == MacroObservationRow.series_id)
            .where(
                MacroSeriesRow.key == series.key,
                MacroObservationRow.vintage <= as_of,
                MacroObservationRow.observed_on <= as_of,
            )
            .order_by(
                MacroObservationRow.observed_on,
                MacroObservationRow.vintage.desc(),
            )
        )
    )

    # One per period, keeping the first — which the ordering above makes the newest vintage.
    chosen: dict[date, MacroObservationRow] = {}
    for row in rows:
        chosen.setdefault(row.observed_on, row)
    return [chosen[period] for period in sorted(chosen)]
