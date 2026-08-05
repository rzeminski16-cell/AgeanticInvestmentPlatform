"""Price series arithmetic: adjustment, returns, market capitalisation and beta.

**The adjusted series is computed here and stored nowhere.** ADR 0032 says why: an adjusted
close changes retroactively for the whole history every time a company splits or pays a
dividend, so a table of them is a table that rewrites itself. `price_bars` holds what the
exchange printed, `corporate_actions` holds the events, and this module is the rule that
turns the two into a comparable series.

**Splits and dividends adjust different series, and mixing them is a real error.** A split
restates the *price* series — the share is physically different afterwards, and a chart that
does not adjust shows a 50% crash that never happened. A dividend does not restate the price
series at all: the price genuinely fell by the dividend on the ex-date, and the holder got the
cash. Adjusting prices for dividends produces a *total-return* series, which is the right
input for a return, a volatility or a beta, and the wrong figure to quote as "the share price
in 2019". Both are produced, separately and by name.

**Which figures are traced, and which are not.** A five-year daily series is 1,250 bars, and
tracing each adjusted close as its own calculation would write 1,250 rows nobody reads to
answer a question nobody asks. What reaches a report is a handful of derived figures — a spot
price, a market capitalisation, a beta, a return over a period — and those are traced, each
with its inputs. The series in between is a pure function with unit tests, deterministic and
reproducible from the archived response plus the recorded actions, which is what the audit
trail actually needs.

**Look-ahead is refused rather than filtered.** :func:`adjusted_series` raises on a bar or an
action later than the as-of date instead of quietly dropping it. A caller who passed an
unclamped query would otherwise get the right answer by luck and never learn the query was
wrong — and the failure mode is a September split silently restating a June valuation, which
looks exactly like a correct number.

Pure and side-effect free, like everything in :mod:`aer.calc`. It is given bars and actions;
it does not go and get them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from enum import StrEnum
from itertools import pairwise
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import (
    CALC_CONTEXT,
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
)

__all__ = [
    "MINOR_UNITS",
    "MIN_RETURN_OBSERVATIONS",
    "AdjustedBar",
    "AdjustedSeries",
    "Bar",
    "CurrencyMismatchError",
    "DividendAction",
    "Frequency",
    "InsufficientHistoryError",
    "LookAheadPriceError",
    "SplitAction",
    "adjusted_series",
    "aligned_returns",
    "beta",
    "covariance",
    "cumulative_split_factor",
    "market_capitalisation",
    "price_in_major_units",
    "ratios_after",
    "resample",
    "simple_returns",
    "total_return",
    "variance",
]

_SHARES: Final = Unit.base("shares")


class LookAheadPriceError(CalculationError):
    """A bar or a corporate action postdates the as-of date.

    Its own class because this is the failure that matters. A split announced in September
    restates every price before it, so applying one to a valuation dated to June produces a
    figure that looks entirely ordinary and is derived from information nobody had.
    """

    code = "look_ahead_price"


class CurrencyMismatchError(UnitMismatchError):
    """A dividend is declared in a currency the share is not quoted in.

    Ordinary, not exotic: a London listing quoted in pence can declare a dollar dividend.
    The adjustment needs the two in one currency, which needs a rate — and this platform
    has no rate source wired in (ADR 0026), so the honest answer is a refusal naming both
    currencies rather than an adjustment off by the exchange rate.
    """

    code = "dividend_currency_mismatch"


class InsufficientHistoryError(CalculationError):
    """Not enough overlapping observations to compute the statistic asked for."""

    code = "insufficient_price_history"


# Currencies quoted in a minor unit, mapped to the major unit and how many minor units go in
# one. Deliberately just the one entry: `GBX` is the case this platform meets, and a table
# that accepted any three letters would let a typo become a silent hundredfold error. South
# African cents (`ZAC`) and Israeli agorot (`ILA`) work the same way and are not seeded,
# because nothing here trades on those exchanges yet.
MINOR_UNITS: Final[Mapping[str, tuple[str, Decimal]]] = {
    "GBX": ("GBP", Decimal(100)),
}

# Below this many overlapping observations, a covariance is noise wearing a number's clothes.
# Twenty-four is two years of monthly returns, the shortest window in common professional use.
MIN_RETURN_OBSERVATIONS: Final = 24

# A variance and a covariance are second moments: they need a deviation from a mean, and one
# observation has none. This is the arithmetic floor, well below the useful floor above.
_MIN_FOR_A_SECOND_MOMENT: Final = 2


class Frequency(StrEnum):
    """How often a return is measured.

    **This is a choice with consequences, so it is recorded as a parameter rather than
    hidden in a default.** Daily beta is biased downward for anything that trades thinly,
    because a stock that does not print on the same ticks as the index looks less correlated
    with it than it is. Monthly is the classic five-year window; weekly is the compromise.
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class Bar:
    """One trading day as the exchange printed it.

    Plain ``Decimal`` rather than :class:`~aer.calc.units.Quantity`: a series is thousands of
    these, the unit is the same for every one of them and is carried on the series, and a
    per-bar source reference would be a thousand copies of one fact.
    """

    on: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None

    # The vendor's own adjusted figure, where one was supplied. Never used in the arithmetic
    # below — it is here so `aer.services.prices` can compare it with this platform's answer
    # and surface a divergence, which is impossible if only one of the two is kept.
    vendor_adjusted_close: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SplitAction:
    """A change in the share count, dated by the day the price stepped.

    ``ratio`` is what the share count is multiplied by: 2 for a two-for-one, 0.1 for a
    one-for-ten consolidation.
    """

    ex_date: date
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class DividendAction:
    """Cash paid out, dated by the day the price stepped.

    ``currency`` is carried because a share quoted in one currency can pay a dividend in
    another, and the adjustment is wrong by the exchange rate if nobody checks.
    """

    ex_date: date
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class AdjustedBar:
    """One bar, with both adjusted closes and the factors that produced them."""

    on: date
    close: Decimal
    """What the exchange printed. Unchanged, always."""

    split_adjusted_close: Decimal
    """Comparable across splits. The figure to quote as "the share price back then"."""

    total_return_close: Decimal
    """Comparable across splits *and* dividends. The figure to compute a return from."""

    split_factor: Decimal
    """The product of every split ratio with an ex-date after this bar."""

    total_return_factor: Decimal
    """The split factor combined with every dividend's price-drop ratio."""


@dataclass(frozen=True, slots=True)
class AdjustedSeries:
    """A whole adjusted series, and what was applied to produce it."""

    bars: tuple[AdjustedBar, ...]
    currency: str
    as_of: date

    splits_applied: tuple[SplitAction, ...]
    dividends_applied: tuple[DividendAction, ...]

    # Dividends inside the window that adjusted nothing, because no bar precedes their
    # ex-date. Reported rather than dropped: a series that silently ignored a payment would
    # be indistinguishable from one that had none.
    dividends_without_a_prior_close: tuple[DividendAction, ...] = ()

    @property
    def latest(self) -> AdjustedBar:
        """The most recent bar.

        Raises:
            InsufficientHistoryError: If the series is empty.
        """
        if not self.bars:
            message = "This series has no bars, so it has no latest price."
            raise InsufficientHistoryError(message, context={"as_of": self.as_of.isoformat()})
        return self.bars[-1]

    def on(self, when: date) -> AdjustedBar | None:
        """The bar for a given day, or ``None`` if the market did not trade."""
        for bar in self.bars:
            if bar.on == when:
                return bar
        return None


# -- Adjustment ------------------------------------------------------------------------------


def adjusted_series(
    bars: Iterable[Bar],
    *,
    splits: Iterable[SplitAction] = (),
    dividends: Iterable[DividendAction] = (),
    currency: str,
    as_of: date,
) -> AdjustedSeries:
    """Apply the recorded actions to the printed series.

    Not traced, and deliberately so — see the module docstring. This is the rule; the figures
    a report quotes are traced individually, each with the factor this produced as an input.

    **The split adjustment.** A bar is divided by the product of every split ratio with an
    ex-date *after* it. A two-for-one split leaves prices before it at half, which is what
    makes the series comparable across the event.

    **The dividend adjustment.** For a dividend of ``D`` with ex-date ``e``, let ``C`` be the
    close on the last trading day before ``e``. Every bar before ``e`` is multiplied by
    ``(C - D) / C``, the fraction of its value the share kept when it went ex. This is the
    standard construction and it produces a total-return series, not a price series.

    Args:
        bars: The printed bars, in any order. Sorted here.
        splits: Splits and consolidations.
        dividends: Cash dividends, each in a stated currency.
        currency: What the bars are quoted in. ``GBX`` for a London listing in pence.
        as_of: The run's as-of date.

    Raises:
        LookAheadPriceError: If any bar or action postdates ``as_of``.
        CurrencyMismatchError: If a dividend is not in the quote currency.
        CalculationError: On a non-positive split ratio, a duplicate bar date, or a dividend
            that would take a price to or below zero.
    """
    ordered = tuple(sorted(bars, key=lambda bar: bar.on))
    split_list = tuple(sorted(splits, key=lambda action: action.ex_date))
    dividend_list = tuple(sorted(dividends, key=lambda action: action.ex_date))

    _require_no_look_ahead(ordered, split_list, dividend_list, as_of=as_of)
    _require_distinct_dates(ordered)
    _require_usable_splits(split_list)
    _require_matching_currency(dividend_list, currency=currency)

    closes = {bar.on: bar.close for bar in ordered}

    with localcontext(CALC_CONTEXT):
        # Each dividend's price-drop ratio, keyed by ex-date. Computed against the *printed*
        # close on the preceding trading day, which is what the market actually dropped from.
        drop_ratios: dict[date, Decimal] = {}
        unapplied: list[DividendAction] = []
        for dividend in dividend_list:
            prior = _last_close_before(ordered, dividend.ex_date, closes)
            if prior is None:
                unapplied.append(dividend)
                continue
            if dividend.amount >= prior:
                message = (
                    f"A dividend of {dividend.amount} on {dividend.ex_date.isoformat()} is not "
                    f"less than the {prior} close before it. That is not a dividend this "
                    "arithmetic can adjust for: the factor would be zero or negative, and "
                    "every earlier price would collapse or change sign."
                )
                raise CalculationError(
                    message,
                    context={
                        "ex_date": dividend.ex_date.isoformat(),
                        "amount": str(dividend.amount),
                        "prior_close": str(prior),
                    },
                )
            drop_ratios[dividend.ex_date] = (prior - dividend.amount) / prior

        applied_dividends = tuple(d for d in dividend_list if d.ex_date in drop_ratios)

        # Walked backwards so each factor is the running product of everything still ahead of
        # the bar. Walking forwards would mean re-multiplying the whole tail per bar, which is
        # quadratic and, more to the point, easy to get subtly wrong at the boundary.
        adjusted: list[AdjustedBar] = []
        split_factor = Decimal(1)
        dividend_factor = Decimal(1)
        for bar in reversed(ordered):
            adjusted.append(
                AdjustedBar(
                    on=bar.on,
                    close=bar.close,
                    split_adjusted_close=bar.close / split_factor,
                    total_return_close=bar.close * dividend_factor / split_factor,
                    split_factor=split_factor,
                    total_return_factor=dividend_factor / split_factor,
                )
            )
            # Applied *after* this bar is written, because an action's ex-date adjusts the
            # bars strictly before it: the bar on the ex-date has already stepped.
            for split in split_list:
                if split.ex_date == bar.on:
                    split_factor *= split.ratio
            for ex_date, ratio in drop_ratios.items():
                if ex_date == bar.on:
                    dividend_factor *= ratio

        # An action whose ex-date fell on a non-trading day never met a bar in the loop above,
        # so it is folded in here against the first bar that precedes it.
        adjusted.reverse()
        adjusted = _apply_actions_off_calendar(
            adjusted,
            ordered,
            splits=split_list,
            drop_ratios=drop_ratios,
        )

    return AdjustedSeries(
        bars=tuple(adjusted),
        currency=currency,
        as_of=as_of,
        splits_applied=split_list,
        dividends_applied=applied_dividends,
        dividends_without_a_prior_close=tuple(unapplied),
    )


def _apply_actions_off_calendar(
    adjusted: list[AdjustedBar],
    ordered: Sequence[Bar],
    *,
    splits: Sequence[SplitAction],
    drop_ratios: Mapping[date, Decimal],
) -> list[AdjustedBar]:
    """Fold in actions whose ex-date was not itself a trading day.

    An ex-date on a public holiday, or on a day the vendor has no bar for, would otherwise
    adjust nothing at all — the loop in :func:`adjusted_series` only meets an action when a
    bar shares its date. That is a hole big enough to lose a whole split through, and a split
    that fails to apply halves or doubles every historical price silently.
    """
    trading_days = {bar.on for bar in ordered}
    stray_splits = [split for split in splits if split.ex_date not in trading_days]
    stray_drops = {when: ratio for when, ratio in drop_ratios.items() if when not in trading_days}
    if not stray_splits and not stray_drops:
        return adjusted

    rebuilt: list[AdjustedBar] = []
    for bar in adjusted:
        split_factor = bar.split_factor
        dividend_factor = bar.total_return_factor * bar.split_factor
        for split in stray_splits:
            if split.ex_date > bar.on:
                split_factor *= split.ratio
        for when, ratio in stray_drops.items():
            if when > bar.on:
                dividend_factor *= ratio
        rebuilt.append(
            AdjustedBar(
                on=bar.on,
                close=bar.close,
                split_adjusted_close=bar.close / split_factor,
                total_return_close=bar.close * dividend_factor / split_factor,
                split_factor=split_factor,
                total_return_factor=dividend_factor / split_factor,
            )
        )
    return rebuilt


def _last_close_before(
    ordered: Sequence[Bar], when: date, closes: Mapping[date, Decimal]
) -> Decimal | None:
    """The printed close on the last trading day strictly before ``when``."""
    previous: Decimal | None = None
    for bar in ordered:
        if bar.on >= when:
            break
        previous = closes[bar.on]
    return previous


def _require_no_look_ahead(
    bars: Sequence[Bar],
    splits: Sequence[SplitAction],
    dividends: Sequence[DividendAction],
    *,
    as_of: date,
) -> None:
    late_bars = [bar.on for bar in bars if bar.on > as_of]
    late_actions = [split.ex_date for split in splits if split.ex_date > as_of]
    late_actions += [dividend.ex_date for dividend in dividends if dividend.ex_date > as_of]
    if not late_bars and not late_actions:
        return

    message = (
        f"This series carries {len(late_bars)} bar(s) and {len(late_actions)} corporate "
        f"action(s) dated after {as_of.isoformat()}. Point-in-time filtering belongs to the "
        "query that fetched them; reaching this function with them still present means it "
        "did not happen, and a split dated after the as-of date restates every price before "
        "it."
    )
    raise LookAheadPriceError(
        message,
        context={
            "as_of": as_of.isoformat(),
            "late_bars": [d.isoformat() for d in late_bars[:5]],
            "late_actions": [d.isoformat() for d in late_actions[:5]],
        },
    )


def _require_distinct_dates(bars: Sequence[Bar]) -> None:
    for earlier, later in pairwise(bars):
        if earlier.on == later.on:
            message = (
                f"Two bars for {later.on.isoformat()}. One of them is a correction, and "
                "which one is a question for the disagreement ladder rather than for "
                "whichever happened to sort first."
            )
            raise CalculationError(message, context={"bar_date": later.on.isoformat()})


def _require_usable_splits(splits: Sequence[SplitAction]) -> None:
    for split in splits:
        if split.ratio <= 0:
            message = (
                f"A split ratio of {split.ratio} on {split.ex_date.isoformat()} is not a "
                "ratio. A share count is multiplied by it, and dividing prices by zero or a "
                "negative is not an adjustment."
            )
            raise CalculationError(
                message,
                context={"ex_date": split.ex_date.isoformat(), "ratio": str(split.ratio)},
            )


def _require_matching_currency(dividends: Sequence[DividendAction], *, currency: str) -> None:
    mismatched = [d for d in dividends if d.currency.upper() != currency.upper()]
    if not mismatched:
        return

    first = mismatched[0]
    message = (
        f"A dividend on {first.ex_date.isoformat()} is declared in {first.currency} and the "
        f"share is quoted in {currency}. Subtracting one from the other would be wrong by "
        "the exchange rate. Converting needs a rate at the ex-date, and no rate source is "
        "wired in — see ADR 0026."
    )
    raise CurrencyMismatchError(
        message,
        context={
            "quote_currency": currency,
            "dividend_currency": first.currency,
            "ex_date": first.ex_date.isoformat(),
            "count": len(mismatched),
        },
    )


# -- Returns ---------------------------------------------------------------------------------


def resample(series: AdjustedSeries, *, frequency: Frequency) -> tuple[AdjustedBar, ...]:
    """The last bar of each period, which is what a periodic return is measured between.

    Daily returns pass through unchanged. Weekly groups by ISO week, monthly by calendar
    month — both taking the *last* observation, so a period is represented by where it closed
    rather than by an average nobody trades at.
    """
    if frequency is Frequency.DAILY:
        return series.bars

    last_of_period: dict[tuple[int, int], AdjustedBar] = {}
    for bar in series.bars:
        if frequency is Frequency.WEEKLY:
            iso = bar.on.isocalendar()
            key = (iso.year, iso.week)
        else:
            key = (bar.on.year, bar.on.month)
        last_of_period[key] = bar
    return tuple(last_of_period[key] for key in sorted(last_of_period))


def simple_returns(
    bars: Sequence[AdjustedBar], *, source: SourceRef
) -> tuple[tuple[date, Quantity], ...]:
    """Period-on-period simple returns from the total-return series.

    The total-return close is the right input: a price series alone reports a dividend as a
    loss, because the share genuinely fell by it on the ex-date.

    **Each return carries a source**, because a return is what
    :func:`variance` and :func:`covariance` are traced over, and
    :mod:`aer.calc.engine` refuses to compute from a number that cannot say where it came
    from. ``source`` is the provenance of the *price series* — the security, its archived
    response — since that is what every return in it derives from.

    Raises:
        CalculationError: If any observation is non-positive, which no traded price is.
    """
    with localcontext(CALC_CONTEXT):
        out: list[tuple[date, Quantity]] = []
        for earlier, later in pairwise(bars):
            if earlier.total_return_close <= 0:
                message = (
                    f"The adjusted close on {earlier.on.isoformat()} is "
                    f"{earlier.total_return_close}. A return measured from it is undefined."
                )
                raise CalculationError(message, context={"bar_date": earlier.on.isoformat()})
            value = (
                later.total_return_close - earlier.total_return_close
            ) / earlier.total_return_close
            out.append((later.on, Quantity.of(value, DIMENSIONLESS, source=source)))
        return tuple(out)


def aligned_returns(
    subject: Sequence[tuple[date, Quantity]],
    market: Sequence[tuple[date, Quantity]],
) -> tuple[tuple[Quantity, ...], tuple[Quantity, ...]]:
    """The two return series restricted to the dates they share, in date order.

    **Alignment is not optional and it is not free.** A London listing and a US index do not
    trade on the same days: each has holidays the other does not. Zipping two series by
    position pairs a Monday with a Tuesday somewhere in the middle and produces a beta that
    is wrong by an amount nobody can see. Pairing by date is the whole of the fix.

    Raises:
        InsufficientHistoryError: If fewer than :data:`MIN_RETURN_OBSERVATIONS` dates are
            shared.
    """
    market_by_date = dict(market)
    shared = [(when, value) for when, value in subject if when in market_by_date]
    shared.sort()

    if len(shared) < MIN_RETURN_OBSERVATIONS:
        message = (
            f"The two series share {len(shared)} observation(s), and a covariance needs at "
            f"least {MIN_RETURN_OBSERVATIONS}. A beta from fewer is noise with a number's "
            "confidence — check the two series cover the same window and trade on "
            "overlapping calendars."
        )
        raise InsufficientHistoryError(
            message,
            context={
                "shared": len(shared),
                "subject": len(subject),
                "market": len(market),
                "minimum": MIN_RETURN_OBSERVATIONS,
            },
        )

    return tuple(value for _, value in shared), tuple(market_by_date[when] for when, _ in shared)


# -- The traced figures ----------------------------------------------------------------------


@traced(
    name="price_in_major_units",
    formula="price = quoted / minor units per major",
    assumptions=(
        "The quote convention is a property of the listing, not of the day.",
        "The divisor is definitional and exact — it is not an exchange rate.",
    ),
)
def price_in_major_units(_context: CalculationContext, *, quoted: Quantity) -> Quantity:
    """Convert a price quoted in a minor unit to the major one — pence to pounds.

    **Traced, for a division by one hundred.** A Barclays quote of 250 means £2.50, and the
    number carries no marker saying so. The same argument as ADR 0027 made for percentages:
    the conversion that lives in a developer's head is the one that gets skipped on the path
    nobody tested, and the failure is a market capitalisation a hundred times too large,
    which looks like a large company rather than like a bug.

    Deliberately **not** routed through :mod:`aer.calc.fx`. That module refuses a rate later
    than the as-of date and one more than a week stale, both of which are the right questions
    to ask of an observed exchange rate and neither of which means anything here: one pound
    has been one hundred pence since 1971, and treating a definition as an observation would
    demand a source for it.

    Raises:
        UnitMismatchError: If the quantity is not in a currency this platform knows to be
            quoted in a minor unit.
    """
    currencies = quoted.unit.currencies
    if len(currencies) != 1 or currencies[0] not in MINOR_UNITS:
        message = (
            f"{quoted.unit.symbol} is not a minor-unit quote. This converts a price quoted "
            f"in a fraction of a currency — {', '.join(sorted(MINOR_UNITS))} — into the "
            "currency itself, and anything else is either already in major units or needs "
            "an exchange rate rather than a division."
        )
        raise UnitMismatchError(
            message, context={"unit": quoted.unit.symbol, "known": sorted(MINOR_UNITS)}
        )

    minor = currencies[0]
    major, per_major = MINOR_UNITS[minor]
    rate = Quantity.of(Decimal(1) / per_major, Unit.currency(major) / Unit.currency(minor))
    return quoted * rate


def ratios_after(bar_date: date, splits: Sequence[SplitAction]) -> tuple[SplitAction, ...]:
    """The splits whose ex-date falls after a bar, which are the ones that restate it.

    Strictly after: the bar printed on an ex-date has already stepped, so adjusting it would
    count the split twice. Pure and untraced — it selects rows, it does not compute a figure.
    """
    return tuple(split for split in splits if split.ex_date > bar_date)


@traced(
    name="cumulative_split_factor",
    formula="factor = product of every split ratio with an ex-date after the bar",
    assumptions=(
        "Only splits and consolidations restate the share count.",
        "The ratios given are exactly those after the bar; see `ratios_after`.",
    ),
)
def cumulative_split_factor(
    _context: CalculationContext, *, ratios: Sequence[Quantity]
) -> Quantity:
    """What a price must be divided by to be comparable with a later one.

    Traced because it is the number that explains an adjusted price. "Why is the 2019 close
    £4.20 when the exchange printed £8.40?" is answered by one factor of 2, and **each ratio
    is a separate recorded input carrying the corporate action it came from** — so the answer
    names the split rather than merely stating the factor.

    Raises:
        UnitMismatchError: If a ratio is not dimensionless. A share count multiplied by a
            price is not a split.
        CalculationError: If a ratio is not positive.
    """
    with localcontext(CALC_CONTEXT):
        factor = Decimal(1)
        for index, ratio in enumerate(ratios):
            if not ratio.unit.is_dimensionless:
                message = (
                    f"Split ratio {index} is in {ratio.unit.symbol}. A ratio of share counts "
                    "is a pure number."
                )
                raise UnitMismatchError(message, context={"unit": ratio.unit.symbol})
            if ratio.value <= 0:
                message = (
                    f"Split ratio {index} is {ratio.value}. A share count is multiplied by "
                    "it, and dividing prices by zero or a negative is not an adjustment."
                )
                raise CalculationError(message, context={"ratio": str(ratio.value)})
            factor *= ratio.value
    return Quantity.of(factor)


@traced(
    name="market_capitalisation",
    formula="market cap = price per share * shares outstanding",
    assumptions=(
        "The share count is the one in issue at the price date, not a later restatement.",
        "One class only; a company with several needs each priced separately and summed.",
    ),
)
def market_capitalisation(
    _context: CalculationContext, *, price: Quantity, shares: Quantity
) -> Quantity:
    """The equity's market value.

    The unit algebra does the checking that matters. A price is a currency *per share*, so
    multiplying by a share count gives a currency and multiplying by anything else does not —
    which is how an inverted or mis-scaled input fails here rather than in a report.

    Raises:
        UnitMismatchError: If the price is not per-share, or the count is not in shares.
        CalculationError: If the share count is not positive.
    """
    if shares.unit != _SHARES:
        message = (
            f"The share count is in {shares.unit.symbol}, not shares. A market "
            "capitalisation multiplies a per-share price by a number of shares."
        )
        raise UnitMismatchError(message, context={"unit": shares.unit.symbol})

    if dict(price.unit.dimensions).get("shares") != -1:
        message = (
            f"The price is in {price.unit.symbol}, which is not a price per share. A figure "
            "in plain currency is a total, and multiplying a total by a share count gives "
            "a number a hundred million times too large."
        )
        raise UnitMismatchError(message, context={"unit": price.unit.symbol})

    if shares.value <= 0:
        message = f"The share count is {shares.value}. A market capitalisation needs shares."
        raise CalculationError(message, context={"shares": str(shares.value)})

    return price * shares


@traced(
    name="total_return",
    formula="return = end / start - 1, on the dividend-adjusted series",
    assumptions=(
        "Dividends are reinvested at the ex-date close, which is the standard construction.",
        "No tax and no dealing costs.",
    ),
)
def total_return(_context: CalculationContext, *, start: Quantity, end: Quantity) -> Quantity:
    """The return between two adjusted closes, as a fraction.

    Both must come from the *total-return* series. Measured on the price series instead, every
    dividend reads as a loss on its ex-date, which understates the return of exactly the
    companies whose case rests on paying one.

    Raises:
        UnitMismatchError: If the two are not in the same unit.
        CalculationError: If the starting value is not positive.
    """
    if start.unit != end.unit:
        message = (
            f"Cannot measure a return from {start.unit.symbol} to {end.unit.symbol}. Both "
            "ends of a return are the same measure at two dates."
        )
        raise UnitMismatchError(
            message, context={"start": start.unit.symbol, "end": end.unit.symbol}
        )

    if start.value <= 0:
        message = f"A return measured from {start.value} is undefined. A traded price is positive."
        raise CalculationError(message, context={"start": str(start.value)})

    return end / start - Quantity.of(1, end.unit / start.unit)


@traced(
    name="variance",
    formula="variance = Σ(x - mean)² / (n - 1)",
    assumptions=("Sample variance, so the denominator is n - 1 rather than n.",),
)
def variance(_context: CalculationContext, *, observations: Sequence[Quantity]) -> Quantity:
    """Sample variance of a return series.

    Each observation is a sourced quantity and is recorded as its own input, which is what
    makes the figure reproducible from the record rather than only from the code.

    Raises:
        InsufficientHistoryError: If there are fewer than two observations.
        UnitMismatchError: If the observations are not all in one unit.
    """
    if len(observations) < _MIN_FOR_A_SECOND_MOMENT:
        message = (
            f"A variance over {len(observations)} observation(s) is undefined. Two is the "
            "minimum, and two is not enough to mean anything."
        )
        raise InsufficientHistoryError(message, context={"observations": len(observations)})

    unit = _require_one_unit(observations, what="variance")
    values = [item.value for item in observations]

    # Every average here is `Decimal` inside `CALC_CONTEXT` rather than `statistics.mean`,
    # which converts through `float` for some inputs. A beta that differs in the fourth place
    # between runs is a beta nobody can reproduce.
    with localcontext(CALC_CONTEXT):
        mean = sum(values, Decimal(0)) / Decimal(len(values))
        total = sum(((value - mean) ** 2 for value in values), Decimal(0))
        # Squared, because a variance is: the unit algebra carries it, so a beta comes out
        # dimensionless only when the two series are in the same unit.
        return Quantity.of(total / Decimal(len(values) - 1), unit**2)


@traced(
    name="covariance",
    formula="covariance = Σ(x - mean x)(y - mean y) / (n - 1)",
    assumptions=(
        "Sample covariance, so the denominator is n - 1.",
        "The two series are already paired by date, not by position.",
    ),
)
def covariance(
    _context: CalculationContext,
    *,
    subject: Sequence[Quantity],
    market: Sequence[Quantity],
) -> Quantity:
    """Sample covariance of two aligned return series.

    Raises:
        InsufficientHistoryError: If the series differ in length or are shorter than two.
        UnitMismatchError: If either series mixes units.
    """
    if len(subject) != len(market):
        message = (
            f"The two series have {len(subject)} and {len(market)} observations. A covariance "
            "pairs them, and pairing series of different lengths means they were not aligned "
            "by date."
        )
        raise InsufficientHistoryError(
            message, context={"subject": len(subject), "market": len(market)}
        )

    if len(subject) < _MIN_FOR_A_SECOND_MOMENT:
        message = f"A covariance over {len(subject)} observation(s) is undefined."
        raise InsufficientHistoryError(message, context={"observations": len(subject)})

    subject_unit = _require_one_unit(subject, what="covariance")
    market_unit = _require_one_unit(market, what="covariance")
    subject_values = [item.value for item in subject]
    market_values = [item.value for item in market]

    with localcontext(CALC_CONTEXT):
        n = Decimal(len(subject_values))
        subject_mean = sum(subject_values, Decimal(0)) / n
        market_mean = sum(market_values, Decimal(0)) / n
        total = sum(
            (
                (a - subject_mean) * (b - market_mean)
                for a, b in zip(subject_values, market_values, strict=True)
            ),
            Decimal(0),
        )
        return Quantity.of(total / (n - Decimal(1)), subject_unit * market_unit)


@traced(
    name="beta",
    formula="beta = covariance(subject, market) / variance(market)",
    assumptions=(
        "The market series is a proxy for the market, and which proxy is a judgement.",
        "Beta measured over one window is an estimate of it, not a property of the company.",
    ),
)
def beta(
    _context: CalculationContext,
    *,
    subject_market_covariance: Quantity,
    market_variance: Quantity,
    frequency: Frequency,
    observations: int,
) -> Quantity:
    """Levered beta against a market proxy.

    ``frequency`` and ``observations`` are parameters, recorded because they change the
    answer. A daily beta and a monthly beta over the same five years are different numbers
    for the same company, and a beta quoted without its window is not reproducible. Neither
    enters the arithmetic; ``frequency`` is checked rather than merely accepted so that an
    untyped caller passing a free-text label fails here instead of writing a record whose
    window means nothing.

    Raises:
        InsufficientHistoryError: If fewer than :data:`MIN_RETURN_OBSERVATIONS` observations
            went into it.
        CalculationError: If the market's variance is zero, or the frequency is not one.
    """
    _require_frequency(frequency)

    if observations < MIN_RETURN_OBSERVATIONS:
        message = (
            f"A beta from {observations} observation(s) is not an estimate, it is noise. At "
            f"least {MIN_RETURN_OBSERVATIONS} are needed."
        )
        raise InsufficientHistoryError(
            message,
            context={"observations": observations, "minimum": MIN_RETURN_OBSERVATIONS},
        )

    if market_variance.value == 0:
        message = (
            "The market proxy's variance is zero — it did not move over the window. Beta "
            "against a series that does not move is undefined, not infinite."
        )
        raise CalculationError(message, context={"observations": observations})

    return subject_market_covariance / market_variance


def _require_one_unit(observations: Sequence[Quantity], *, what: str) -> Unit:
    """The single unit every observation shares, or a refusal.

    A series that mixes units is a series somebody built by concatenating two things. The
    arithmetic below would average across them and produce a number, which is worse than
    the error.
    """
    units = {item.unit for item in observations}
    if len(units) == 1:
        return units.pop()

    message = (
        f"A {what} needs one unit and these observations carry "
        f"{', '.join(sorted(unit.symbol for unit in units))}. A series measuring two "
        "different things is not a series."
    )
    raise UnitMismatchError(message, context={"units": sorted(unit.symbol for unit in units)})


def _require_frequency(value: object) -> None:
    """Refuse anything but a :class:`Frequency`.

    The type annotation is enough for every caller mypy checks, and this is what catches the
    ones it does not. ``"5y monthly"`` would otherwise be recorded verbatim as the window a
    beta was measured over, which reads as a specification and is a string.
    """
    if isinstance(value, Frequency):
        return

    message = (
        f"frequency is {value!r}, which is not a Frequency. A beta's window changes its "
        "value, so the record has to say which one it was in a form code can read back."
    )
    raise CalculationError(message, context={"frequency": repr(value)})
