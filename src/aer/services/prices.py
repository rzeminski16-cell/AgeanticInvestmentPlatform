"""Persisting price history, and turning it back into figures a report can quote.

The seam between :mod:`aer.sources.eodhd`, which fetches, and :mod:`aer.calc.prices`, which
computes. Everything database-shaped lives here so that neither of those has to know about a
session, and so that the pure arithmetic stays testable without one.

**Storing is idempotent, and a changed bar is a conflict rather than an update.** Re-running
an acquisition over a window this platform already holds inserts nothing and raises nothing.
A vendor that has *revised* a bar since — a corrected close, a restated volume — collides on
``(security_id, bar_date)``, and that collision is routed into the disagreement ladder built
in task 19 instead of overwriting a figure a report already cited. `price_bars` has no update
path here for the same reason `artefacts` has no delete path.

**The vendor's adjusted close is a witness, not an answer.** It is stored beside the raw bar
and :func:`vendor_divergence` compares it with this platform's own adjustment. A systematic
divergence is a bug in the arithmetic here, and it is invisible without both figures.

**Nothing in this module publishes anything.** Under ADR 0030 route 2 the EODHD subscription
is a personal-use plan with no derived-data safe harbour, so every figure downstream of these
rows is internal-only. The licence note recorded on each source document says so in the
terms' own words.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import prices as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.disagreement import DisagreementKind, Position
from aer.core.enums import SourceTier
from aer.db.models import (
    Assumption,
    CorporateAction,
    CorporateActionKind,
    PriceBar,
    Security,
)
from aer.errors import ValidationError
from aer.services import assumptions
from aer.services.disagreements import resolve_and_record
from aer.sources.eodhd.client import ActionsResponse, PriceResponse

__all__ = [
    "BETA_ASSUMPTION",
    "MAX_VENDOR_DIVERGENCE",
    "BarConflict",
    "StoredActions",
    "StoredBars",
    "VendorDivergence",
    "adjusted_series_for",
    "beta_against",
    "market_capitalisation_for",
    "price_quantity",
    "propose_computed_beta",
    "record_actions",
    "record_bars",
    "upsert_security",
    "vendor_divergence",
]

_log = structlog.get_logger("aer.services.prices")

_SHARES: Final = Unit.base("shares")

# How far this platform's total-return close may sit from the vendor's before it is worth a
# person's attention. Five parts in ten thousand: comfortably above the rounding either side
# does, comfortably below a missed dividend on anything with a yield worth mentioning.
MAX_VENDOR_DIVERGENCE: Final = Decimal("0.0005")


@dataclass(frozen=True, slots=True)
class BarConflict:
    """A bar this platform already holds, which the vendor now reports differently."""

    on: date
    stored_close: Decimal
    incoming_close: Decimal


@dataclass(frozen=True, slots=True)
class StoredBars:
    """What a store of bars actually did."""

    inserted: int
    already_held: int
    conflicts: tuple[BarConflict, ...]


@dataclass(frozen=True, slots=True)
class StoredActions:
    """What a store of corporate actions actually did."""

    splits_inserted: int
    dividends_inserted: int
    already_held: int


@dataclass(frozen=True, slots=True)
class VendorDivergence:
    """A day where this platform's adjustment and the vendor's disagree."""

    on: date
    ours: Decimal
    theirs: Decimal
    relative: Decimal


# -- Storing ---------------------------------------------------------------------------------


async def upsert_security(
    session: AsyncSession,
    *,
    ticker: str,
    exchange: str,
    provider_symbol: str,
    quote_currency: str,
    name: str | None = None,
    company_id: uuid.UUID | None = None,
) -> Security:
    """Find the listing or create it, keyed on the vendor's own symbol.

    Keyed on ``provider_symbol`` rather than on ``(ticker, exchange)`` because the vendor
    symbol is what a later fetch will be made with, and a listing found by one key and
    fetched by another is a listing that can drift into two rows.
    """
    existing = await session.scalar(
        select(Security).where(Security.provider_symbol == provider_symbol)
    )
    if existing is not None:
        if company_id is not None and existing.company_id is None:
            # A peer resolved against a registry after its prices arrived. Filled in, never
            # overwritten: a security already attached to a company is not re-pointed here.
            existing.company_id = company_id
        return existing

    security = Security(
        company_id=company_id,
        ticker=ticker.strip().upper(),
        exchange=exchange.strip().upper(),
        provider_symbol=provider_symbol,
        name=name,
        quote_currency=_require_currency(quote_currency),
    )
    session.add(security)
    await session.flush()
    return security


async def record_bars(
    session: AsyncSession,
    *,
    security: Security,
    response: PriceResponse,
    source_document_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
) -> StoredBars:
    """Insert the bars this platform does not already hold.

    A bar already held with the *same* close is skipped silently — re-running an acquisition
    is not news. One held with a different close is a **conflict**: the vendor has revised
    history, and something a report may already have cited has changed underneath it. Those
    are collected, and recorded on the disagreement ladder when a ``job_id`` is supplied.

    Nothing is ever updated. See the module docstring.
    """
    held = {
        bar.bar_date: bar
        for bar in await _bars_between(
            session,
            security,
            since=min((row.on for row in response.bars), default=response.as_of),
            until=response.as_of,
        )
    }

    inserted = 0
    already = 0
    conflicts: list[BarConflict] = []

    for row in response.bars:
        existing = held.get(row.on)
        if existing is not None:
            if existing.close == row.close:
                already += 1
            else:
                conflicts.append(
                    BarConflict(on=row.on, stored_close=existing.close, incoming_close=row.close)
                )
            continue

        session.add(
            PriceBar(
                security_id=security.id,
                bar_date=row.on,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                adjusted_close=row.adjusted_close,
                volume=row.volume,
                source_document_id=source_document_id,
            )
        )
        inserted += 1

    await session.flush()

    if conflicts and job_id is not None:
        await _record_bar_conflicts(session, security=security, conflicts=conflicts, job_id=job_id)

    _log.info(
        "prices.bars_recorded",
        security=security.provider_symbol,
        inserted=inserted,
        already_held=already,
        conflicts=len(conflicts),
        as_of=response.as_of.isoformat(),
    )
    return StoredBars(inserted=inserted, already_held=already, conflicts=tuple(conflicts))


async def record_actions(
    session: AsyncSession,
    *,
    security: Security,
    response: ActionsResponse,
    source_document_id: uuid.UUID | None = None,
) -> StoredActions:
    """Insert the splits and dividends this platform does not already hold.

    Identity follows the two partial unique indexes from migration 0018: a split is
    identified by its ex-date alone, a dividend by its ex-date *and* its amount, because an
    ordinary and a special dividend sharing an ex-date is an ordinary thing to happen.
    """
    existing = await _actions_upto(session, security, until=response.as_of)
    split_dates = {row.ex_date for row in existing if row.kind is CorporateActionKind.SPLIT}
    dividend_keys = {
        (row.ex_date, row.dividend_amount)
        for row in existing
        if row.kind is CorporateActionKind.DIVIDEND
    }

    splits = 0
    dividends = 0
    already = 0

    for split in response.splits:
        if split.ex_date in split_dates:
            already += 1
            continue
        session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.SPLIT,
                ex_date=split.ex_date,
                split_ratio=split.ratio,
                source_document_id=source_document_id,
            )
        )
        split_dates.add(split.ex_date)
        splits += 1

    for dividend in response.dividends:
        if (dividend.ex_date, dividend.amount) in dividend_keys:
            already += 1
            continue
        session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.DIVIDEND,
                ex_date=dividend.ex_date,
                pay_date=dividend.pay_date,
                record_date=dividend.record_date,
                dividend_amount=dividend.amount,
                dividend_currency=dividend.currency,
                source_document_id=source_document_id,
            )
        )
        dividend_keys.add((dividend.ex_date, dividend.amount))
        dividends += 1

    await session.flush()

    _log.info(
        "prices.actions_recorded",
        security=security.provider_symbol,
        splits=splits,
        dividends=dividends,
        already_held=already,
    )
    return StoredActions(splits_inserted=splits, dividends_inserted=dividends, already_held=already)


async def _record_bar_conflicts(
    session: AsyncSession,
    *,
    security: Security,
    conflicts: Sequence[BarConflict],
    job_id: uuid.UUID,
) -> None:
    """Put each revised bar on the ladder, as a conflict between two dates of the same feed.

    Both positions are the same tier and the same provider, so the ladder has no rule that
    settles it — which is correct. A vendor silently restating a close is exactly the thing a
    person should look at, and the ladder escalating rather than deciding is it working.
    """
    for conflict in conflicts:
        await resolve_and_record(
            session,
            job_id=job_id,
            topic=f"{security.provider_symbol} close on {conflict.on.isoformat()}",
            kind=DisagreementKind.SOURCE_CONFLICT,
            first=Position(
                reference=f"{security.id}:{conflict.on.isoformat()}:stored",
                label="Held by this platform",
                value=conflict.stored_close,
                unit=security.quote_currency,
                tier=SourceTier.T4_LICENSED_MARKET,
                filed_date=conflict.on,
            ),
            second=Position(
                reference=f"{security.id}:{conflict.on.isoformat()}:incoming",
                label="Reported now by the vendor",
                value=conflict.incoming_close,
                unit=security.quote_currency,
                tier=SourceTier.T4_LICENSED_MARKET,
                filed_date=conflict.on,
            ),
        )


# -- Reading back ----------------------------------------------------------------------------


async def adjusted_series_for(
    session: AsyncSession,
    security: Security,
    *,
    as_of: date,
    since: date | None = None,
) -> calc.AdjustedSeries:
    """The stored series, adjusted, clamped to ``as_of``.

    The clamp is in the query — ``bar_date <= as_of`` and ``ex_date <= as_of`` — and
    :func:`aer.calc.prices.adjusted_series` refuses anything later as a second line of
    defence. Two checks of one rule, because the rule is the one whose failure is invisible.
    """
    bars = await _bars_between(session, security, since=since, until=as_of)
    actions = await _actions_upto(session, security, until=as_of)

    return calc.adjusted_series(
        (
            calc.Bar(
                on=row.bar_date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                vendor_adjusted_close=row.adjusted_close,
            )
            for row in bars
        ),
        splits=[
            calc.SplitAction(ex_date=row.ex_date, ratio=_require_ratio(row))
            for row in actions
            if row.kind is CorporateActionKind.SPLIT
        ],
        dividends=[
            calc.DividendAction(
                ex_date=row.ex_date,
                amount=_require_amount(row),
                currency=row.dividend_currency or security.quote_currency,
            )
            for row in actions
            if row.kind is CorporateActionKind.DIVIDEND
        ],
        currency=security.quote_currency,
        as_of=as_of,
    )


def vendor_divergence(
    series: calc.AdjustedSeries,
    *,
    vendor: dict[date, Decimal],
    tolerance: Decimal = MAX_VENDOR_DIVERGENCE,
) -> tuple[VendorDivergence, ...]:
    """Days where this platform's total-return close differs from the vendor's.

    **Compared as a ratio, not as a difference.** The two series are on different bases: the
    vendor's is anchored so the most recent bar equals the printed close, and so is this
    one — but any dividend either side handles differently shifts the whole earlier history
    proportionally. A comparison in currency would flag every old bar of a high-priced share
    and none of a penny stock; a ratio flags the ones that are actually wrong.
    """
    out: list[VendorDivergence] = []
    for bar in series.bars:
        theirs = vendor.get(bar.on)
        if theirs is None or theirs == 0:
            continue
        relative = abs(bar.total_return_close - theirs) / theirs
        if relative > tolerance:
            out.append(
                VendorDivergence(
                    on=bar.on, ours=bar.total_return_close, theirs=theirs, relative=relative
                )
            )
    return tuple(out)


# -- The traced figures ----------------------------------------------------------------------


def price_quantity(
    series: calc.AdjustedSeries,
    *,
    on: date | None = None,
    source: SourceRef,
) -> Quantity:
    """A price from the series, as a per-share quantity in the listing's quote currency.

    Per-share, not plain currency: ``GBX/shares`` rather than ``GBX``. That is what lets
    :func:`market_capitalisation_for` reject a share count multiplied by the wrong thing,
    and it is why a market capitalisation cannot silently come out per-share.
    """
    bar = series.latest if on is None else series.on(on)
    if bar is None:
        message = (
            f"No bar for {on.isoformat() if on else 'the latest date'} in a series ending "
            f"{series.as_of.isoformat()}. The market was closed, or the window does not "
            "reach that far."
        )
        raise ValidationError(message, context={"as_of": series.as_of.isoformat()})

    unit = Unit.currency(series.currency) / _SHARES
    return Quantity.of(bar.close, unit, source=source)


def market_capitalisation_for(
    context: CalculationContext,
    *,
    series: calc.AdjustedSeries,
    shares: Quantity,
    price_source: SourceRef,
    on: date | None = None,
) -> Quantity:
    """Market capitalisation, converting out of a minor-unit quote first.

    A London listing quotes in pence, so the conversion to pounds happens here and is its own
    recorded calculation. Skipping it gives a figure a hundred times too large, which reads
    as a large company rather than as a bug.
    """
    price = price_quantity(series, on=on, source=price_source)

    if series.currency in calc.MINOR_UNITS:
        price = calc.price_in_major_units(context, quoted=price)

    return calc.market_capitalisation(context, price=price, shares=shares)


def beta_against(
    context: CalculationContext,
    *,
    subject: calc.AdjustedSeries,
    market: calc.AdjustedSeries,
    subject_source: SourceRef,
    market_source: SourceRef,
    frequency: calc.Frequency = calc.Frequency.MONTHLY,
) -> Quantity:
    """Levered beta of ``subject`` against a market proxy.

    **Monthly by default, and the default is a judgement rather than a convenience.** Daily
    beta is biased downward for anything that trades thinly, because a share that does not
    print on the same ticks as the index looks less correlated with it than it is. Five years
    of monthly returns is the classic window and what most published betas mean.

    Both series must be adjusted to the same as-of date. Returns are paired **by date**, not
    by position: a London listing and a US index keep different holidays, and zipping them
    positionally pairs a Monday with a Tuesday somewhere in the middle and produces a beta
    wrong by an amount nobody can see.
    """
    if subject.as_of != market.as_of:
        message = (
            f"The subject series ends {subject.as_of.isoformat()} and the market series "
            f"{market.as_of.isoformat()}. A beta between windows that do not match is a "
            "comparison of two different periods."
        )
        raise ValidationError(
            message,
            context={
                "subject_as_of": subject.as_of.isoformat(),
                "market_as_of": market.as_of.isoformat(),
            },
        )

    subject_returns = calc.simple_returns(
        calc.resample(subject, frequency=frequency), source=subject_source
    )
    market_returns = calc.simple_returns(
        calc.resample(market, frequency=frequency), source=market_source
    )
    paired_subject, paired_market = calc.aligned_returns(subject_returns, market_returns)

    # Both are traced, so each records the returns that went into it and the price series
    # each return came from. The `Quantity` a traced call returns already carries a source
    # pointing at its own record, which is what makes the beta's lineage a tree back to the
    # two archived responses rather than a figure with two orphan statistics under it.
    market_variance = calc.variance(context, observations=paired_market)
    joint = calc.covariance(context, subject=paired_subject, market=paired_market)

    return calc.beta(
        context,
        subject_market_covariance=joint,
        market_variance=market_variance,
        frequency=frequency,
        observations=len(paired_subject),
    )


BETA_ASSUMPTION: Final = "beta"
"""The assumption name a computed beta is proposed against.

`docs/archive/phase-3-plan.md` is explicit that **beta is a first-class assumption with an optional
computed override**, not a computed input with an assumption fallback. A documented,
human-confirmed beta with a stated justification is more defensible than a regression nobody
inspected — and it keeps the cost of capital available on a machine with no subscription.
"""


async def propose_computed_beta(
    session: AsyncSession,
    context: CalculationContext,
    *,
    request_id: uuid.UUID,
    subject: calc.AdjustedSeries,
    market: calc.AdjustedSeries,
    subject_source: SourceRef,
    market_source: SourceRef,
    market_label: str,
    frequency: calc.Frequency = calc.Frequency.MONTHLY,
    job_id: uuid.UUID | None = None,
) -> Assumption:
    """Compute a beta from the two series and put it forward as an assumption.

    **Proposed, never confirmed.** A regression is evidence for a beta, not a decision about
    one: the choice of market proxy is a judgement, the window changes the answer, and a
    thinly traded share's daily beta is biased low. So this writes a proposal a person still
    has to agree to, exactly as a model-proposed assumption does, and
    :func:`aer.services.assumptions.as_quantity` will refuse it until they have.

    The justification records the proxy, the window and the number of observations, because a
    beta quoted without those is not reproducible.
    """
    value = beta_against(
        context,
        subject=subject,
        market=market,
        subject_source=subject_source,
        market_source=market_source,
        frequency=frequency,
    )
    observations = next(
        record.parameters["observations"]
        for record in reversed(context.records)
        if record.name == "beta"
    )

    justification = (
        f"Regressed against {market_label} on {frequency.value} total returns to "
        f"{subject.as_of.isoformat()}, over {observations} paired observations. The proxy and "
        "the window are both judgements and both change the answer, which is why this is a "
        "proposal rather than an input."
    )

    return await assumptions.propose(
        session,
        request_id=request_id,
        name=BETA_ASSUMPTION,
        value=value.value,
        unit=value.unit.symbol,
        justification=justification,
        proposed_by="aer.services.prices",
        by_human=False,
        job_id=job_id,
    )


# -- Queries and guards ----------------------------------------------------------------------


async def _bars_between(
    session: AsyncSession, security: Security, *, since: date | None, until: date
) -> Sequence[PriceBar]:
    statement = (
        select(PriceBar)
        .where(PriceBar.security_id == security.id, PriceBar.bar_date <= until)
        .order_by(PriceBar.bar_date)
    )
    if since is not None:
        statement = statement.where(PriceBar.bar_date >= since)
    return list(await session.scalars(statement))


async def _actions_upto(
    session: AsyncSession, security: Security, *, until: date
) -> Sequence[CorporateAction]:
    statement = (
        select(CorporateAction)
        .where(CorporateAction.security_id == security.id, CorporateAction.ex_date <= until)
        .order_by(CorporateAction.ex_date)
    )
    return list(await session.scalars(statement))


def _require_currency(code: str) -> str:
    cleaned = code.strip().upper()
    # Validated through the unit system rather than against a private pattern, so a currency
    # this platform can store is exactly one it can do arithmetic in. `GBX` passes: it is not
    # ISO 4217, and ADR 0032 says why it is treated as a currency anyway.
    Unit.currency(cleaned)
    return cleaned


def _require_ratio(row: CorporateAction) -> Decimal:
    if row.split_ratio is not None:
        return row.split_ratio

    message = (
        f"The split on {row.ex_date.isoformat()} has no ratio. The database check constraint "
        "makes this unreachable, so reaching it means the constraint is gone."
    )
    raise ValidationError(message, context={"action_id": str(row.id)})


def _require_amount(row: CorporateAction) -> Decimal:
    if row.dividend_amount is not None:
        return row.dividend_amount

    message = (
        f"The dividend on {row.ex_date.isoformat()} has no amount. The database check "
        "constraint makes this unreachable, so reaching it means the constraint is gone."
    )
    raise ValidationError(message, context={"action_id": str(row.id)})
