"""Relative valuation: multiples, peer alignment, and the subject against its own history.

**A multiple is a ratio of two things measured the same way.** Almost every error here is a
failure of that sentence rather than of arithmetic: a trailing enterprise value over a forward
EBITDA, a peer whose year ends in March against one whose year ends in December, a per-share
price over a whole-company earnings figure. Each produces a number that looks like a multiple
and compares nothing.

So three things are structural here rather than conventional:

**Every multiple is dimensionless, and the unit algebra proves it.** Enterprise value is a
currency and EBITDA is a currency, so their ratio is a pure number; a price per share over
earnings per share is a pure number too. A multiple that comes out carrying a unit is one
where a total was divided by a per-share figure, and it raises instead of printing.

**A non-positive denominator has no multiple.** A company with negative EBITDA does not have a
cheap EV/EBITDA, it has none — and a negative multiple sorted into a table reads as the
cheapest peer in it. :class:`MultipleResult` carries the reason in words instead, in the same
shape :mod:`aer.calc.ratios` uses for a ratio its filing cannot support.

**Every multiple names its basis and its date.** ``12.4x`` is not a fact. ``12.4x EV/EBITDA on
a trailing twelve-month basis to 30 June 2024`` is. The basis is recorded as a calculation
parameter, not implied by which function was called.

Pure and side-effect free. It is given quantities and periods; it does not go and get them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import (
    CALC_CONTEXT,
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    UnitMismatchError,
)

__all__ = [
    "MAX_PERIOD_DRIFT_DAYS",
    "MULTIPLE_DEFINITIONS",
    "Audience",
    "CompsTable",
    "MultipleBand",
    "MultipleBasis",
    "MultipleDefinition",
    "MultipleResult",
    "PeerExclusion",
    "PeerRow",
    "align_peers",
    "book_value_per_share",
    "implied_value_per_share_from_ev_multiple",
    "implied_value_per_share_from_price_multiple",
    "market_enterprise_value",
    "median_multiple",
    "multiple",
    "multiples_for",
    "percentile_rank",
]

# How far a peer's reporting period may sit from the subject's before the comparison stops
# being one.
#
# **Six weeks, which is a fiscal-calendar allowance and not a quarter.** A 52/53-week year, a
# 30 June against a 3 July, a 31 December against a 31 January — those are the same twelve
# months with a different last day, and excluding them would empty most peer sets for no gain.
# A *quarter* of drift is different in kind: a company whose year ends in March against one
# ending in December shares nine months of twelve, and the three it does not share are the
# three that decide the comparison for anything cyclical.
#
# The first draft of this was 92 days, which permitted exactly the March-against-December case
# the sentence above says it excludes. A test of that case is what found it.
MAX_PERIOD_DRIFT_DAYS: Final = 45

# A multiple this large is arithmetic, not valuation. Nothing is rejected for it — the figure
# is real and sometimes right — but it is flagged, because a denominator approaching zero
# produces an enormous number that a median then has to survive.
IMPLAUSIBLE_MULTIPLE: Final = Decimal(500)


class MultipleBasis(StrEnum):
    """What period the denominator covers.

    Recorded on every multiple as a calculation parameter. A trailing multiple and a forward
    multiple for the same company on the same day are different numbers, and a table mixing
    them is a table comparing nothing.
    """

    TRAILING_TWELVE_MONTHS = "ttm"
    """The last four reported quarters. The default, and the only one a filing supports on
    its own."""

    LAST_FISCAL_YEAR = "lfy"
    """The most recent full year as reported. Stale by up to a year, and honest about it."""

    FORWARD = "forward"
    """A consensus or in-house estimate. **Never derived from a filing** — a forward multiple
    is an assumption wearing a multiple's clothes, and it is only ever as good as the estimate
    behind it."""


class Audience(StrEnum):
    """Who a comps table may be shown to.

    **This exists because of a licence, not a preference.** The market data these multiples
    are computed from arrives under a personal-use subscription whose terms prohibit
    displaying the information in "original or repackaged form" and contain no derived-data
    exemption. So a figure computed from a price is internal, and ADR 0030 route 2 says so.
    """

    INTERNAL = "internal"
    """The operator's own machine. Everything is shown."""

    SHAREABLE = "shareable"
    """Anything that leaves it. Nothing price-derived is shown, and the reason is."""


@dataclass(frozen=True, slots=True)
class MultipleResult:
    """One multiple: its value, or why there isn't one.

    Deliberately the same shape as :class:`aer.calc.ratios.RatioResult`. "Not meaningful" and
    "not reported" are different states and both are different from a number, and a reader
    should meet that distinction in one form rather than two.
    """

    key: str
    label: str
    quantity: Quantity | None
    basis: MultipleBasis
    period_end: date

    # Empty when the multiple computed. Otherwise the reason in words — a negative
    # denominator, a missing input, a period that could not be aligned.
    absent_because: str = ""

    missing: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.quantity is not None

    @property
    def value(self) -> Decimal | None:
        return self.quantity.value if self.quantity is not None else None

    @property
    def is_implausible(self) -> bool:
        """Whether the figure is large enough that its denominator is doing something odd."""
        return self.quantity is not None and abs(self.quantity.value) > IMPLAUSIBLE_MULTIPLE

    def describe(self) -> str:
        """The figure as it should be quoted, or the reason it cannot be.

        Written here rather than in a template, because a multiple quoted without its basis
        and its date is not a fact and must not become one by passing through a renderer.
        """
        if self.quantity is None:
            return f"{self.label}: not meaningful — {self.absent_because}"
        return (
            f"{self.label}: {self.quantity.value:.1f}x on a {self.basis.value} basis to "
            f"{self.period_end.isoformat()}"
        )


@dataclass(frozen=True, slots=True)
class MultipleDefinition:
    """A multiple, the inputs it needs, and what it commits to.

    A table rather than a function per caller, for the reason
    :class:`aer.calc.ratios.RatioDefinition` gives: "what can this platform compare, and from
    what?" should be a value somebody can inspect and count.
    """

    key: str
    label: str
    numerator: str
    denominator: str

    # What the figure means and any convention it takes for granted. Shown beside the number,
    # because "EV/EBITDA" without a definition of enterprise value is not comparable.
    note: str = ""

    # Sector keys for which this multiple is the *right* one. Empty means general purpose.
    # A REIT's P/FFO is not an ornament — earnings are the wrong denominator for a business
    # whose depreciation is an accounting artefact rather than a cost.
    specialist_for: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PeerRow:
    """One company in a comps table, with every multiple computed on one basis."""

    identifier: str
    name: str
    period_end: date
    multiples: tuple[MultipleResult, ...]

    # Why this company is in the set. Written by whoever proposed it and confirmed by a
    # person; a peer with no rationale is a peer nobody can defend.
    rationale: str = ""

    def multiple(self, key: str) -> MultipleResult | None:
        for row in self.multiples:
            if row.key == key:
                return row
        return None


@dataclass(frozen=True, slots=True)
class PeerExclusion:
    """A company that was proposed as a peer and is not in the table.

    **Recorded, never dropped.** A comparison whose exclusions are invisible is one a reader
    cannot check, and "we left out the two peers that would have moved the median" is exactly
    the thing a reader needs to be able to notice.
    """

    identifier: str
    name: str
    reason: str
    period_end: date | None = None


@dataclass(frozen=True, slots=True)
class MultipleBand:
    """One multiple for one company across time — its own history, not its peers'.

    Often the more honest comparison. A company trading at 14x against peers at 11x may be
    expensive, or may be a company that has traded at 14x for a decade against peers that have
    always been cheaper for a reason.
    """

    key: str
    label: str
    basis: MultipleBasis
    observations: tuple[tuple[date, Decimal], ...]
    low: Decimal
    median: Decimal
    high: Decimal
    current: Decimal | None

    # Where the current figure sits in its own history, 0 to 1. `None` when there is no
    # current figure to place.
    percentile: Decimal | None = None

    @property
    def count(self) -> int:
        return len(self.observations)


@dataclass(frozen=True, slots=True)
class CompsTable:
    """A subject, its confirmed peers, and everything left out.

    **Not renderable on its own.** :meth:`for_audience` is the only way to reach the rows,
    and for a shareable audience it returns them only when the licence behind the data
    permits a computed figure to be published. See :class:`Audience`, and the same argument
    ADR 0029 made for the sector block: a rule enforced by a type is one a later template
    cannot forget.

    The gate survived the determination that opened it (ADR 0030, amended 2026-08-09).
    Deleting it would have made the permission unconditional and undated, and the next
    paid feed would inherit a decision made about a different agreement.
    """

    subject: PeerRow
    peers: tuple[PeerRow, ...]
    excluded: tuple[PeerExclusion, ...]
    basis: MultipleBasis
    as_of: date

    # The peer set this was built from was confirmed by a person. False is a programming
    # error rather than a state a caller should handle — :func:`aer.services.comps.build`
    # refuses to construct one without a confirmation.
    peer_set_confirmed: bool = False

    licence_note: str = ""

    # Whether the data behind these multiples may leave the machine in computed form.
    #
    # **Passed in, never looked up.** This module is pure: it may not read a policy table,
    # a setting or a clock. So the licence determination arrives as data from
    # :func:`aer.services.comps.build`, which reads it from the provider's `FetchPolicy`.
    # That also makes the property testable at both settings without a provider existing.
    #
    # Defaults to false, so a table constructed by a caller that has not thought about the
    # licence withholds. The safe answer is the one you get by not deciding.
    derived_figures_publishable: bool = False

    def for_audience(self, audience: Audience) -> CompsTable | WithheldComps:
        """The table, or a notice standing in its place.

        **Two questions, and only one of them is about the audience.** An internal surface
        always gets the table. A shareable one gets it only if the licence behind the data
        permits a computed figure to be published — and if it does not, what comes back is
        a :class:`WithheldComps`, which *has no rows*. A renderer handed one cannot print a
        figure from it because there is no figure in it; the restriction is enforced by
        what the object contains rather than by a flag a template is trusted to read.

        The multiples are the only thing in here. A :class:`PeerRow` carries an identifier,
        a name, a period and ratios — no price, no market capitalisation, nothing that is
        the vendor's series in another shape. So "may a derived figure be published?" is
        the whole question for this object, and a source whose answer is yes has nothing
        left to withhold.
        """
        if audience is Audience.INTERNAL or self.derived_figures_publishable:
            return self
        return WithheldComps(
            peer_count=len(self.peers),
            excluded_count=len(self.excluded),
            as_of=self.as_of,
            licence_note=self.licence_note,
        )

    def median_of(self, key: str) -> Decimal | None:
        """The peer median for one multiple, ignoring the subject and the not-meaningfuls.

        **One guard, not two.** The first draft filtered on `found.present` and then filtered
        the resulting list for `None` again — so deleting either left the other doing the job,
        and neither could be pinned by a test. A sabotage pass found it by removing the first
        and watching nothing fail. A guard a test cannot reach is a guard that is not there.
        """
        values: list[Decimal] = []
        for row in self.peers:
            found = row.multiple(key)
            if found is not None and found.quantity is not None:
                values.append(found.quantity.value)

        if not values:
            return None
        return _median(values)


@dataclass(frozen=True, slots=True)
class WithheldComps:
    """What a shareable surface gets instead of a comps table.

    Carries no multiple, no peer name and no price-derived figure of any kind. The counts are
    of *companies*, which were chosen by a person rather than supplied by the data vendor, so
    they disclose the shape of the work without disclosing the licensed data.
    """

    peer_count: int
    excluded_count: int
    as_of: date
    licence_note: str = ""

    def as_paragraph(self) -> str:
        """The disclosure, written here so it cannot vary by template.

        Two states, because they make two different claims. With peers in the table there
        is an analysis, and this paragraph withholds its figures under the licence. With
        none — every proposed peer excluded, nothing priced, nothing computed — there is
        no analysis *anywhere*, and the first live report proved what the single wording
        does with that: it promised an analysis "available in full on the operator's own
        copy" that no copy holds (gap A53). A disclosure may withhold figures; it may not
        invent them to withhold.

        House style throughout (gap R10): small counts are spelled, plurals agree, and
        the attempted-and-empty state names no "operator's own copy" — in a personal
        research tool that is the document the reader is already holding, and the clause
        earns its place only where a fuller version genuinely exists.
        """
        if self.peer_count == 0:
            if self.excluded_count == 1:
                excluded = "its single proposed peer was excluded for want of usable data"
            elif self.excluded_count:
                excluded = (
                    f"every one of the {_spelled(self.excluded_count)} proposed peers "
                    "was excluded for want of usable data"
                )
            else:
                excluded = "no peer survived to be compared"
            return (
                f"A comparable-company analysis was attempted as at {self.as_of.isoformat()}, "
                f"but {excluded}, so no comparable figure was computed and there is no "
                "fuller version elsewhere."
            )
        peers = "one peer" if self.peer_count == 1 else f"{_spelled(self.peer_count)} peers"
        if self.excluded_count == 0:
            excluded_clause = "none of the proposed peers excluded"
        elif self.excluded_count == 1:
            excluded_clause = "one proposed peer excluded"
        else:
            excluded_clause = f"{_spelled(self.excluded_count)} proposed peers excluded"
        return (
            f"A comparable-company analysis was performed against {peers} "
            f"as at {self.as_of.isoformat()}, with {excluded_clause}. "
            "**The figures are withheld from this version.** They derive from "
            "market data licensed for internal use only, under terms that grant no "
            "derived-data exemption, so no multiple computed from it appears in anything "
            "shareable. The analysis is available in full on the operator's own copy."
        )


# Counts up to twelve are spelled, as prose spells them; beyond that the digits carry
# better. Zero never reaches this — each branch above phrases absence in words.
_COUNT_WORDS: Final[tuple[str, ...]] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
)


def _spelled(count: int) -> str:
    return _COUNT_WORDS[count] if 0 <= count < len(_COUNT_WORDS) else str(count)


# -- The definitions --------------------------------------------------------------------------

MULTIPLE_DEFINITIONS: Final[tuple[MultipleDefinition, ...]] = (
    MultipleDefinition(
        key="ev_ebitda",
        label="EV/EBITDA",
        numerator="enterprise_value",
        denominator="ebitda",
        note=(
            "Enterprise value is market capitalisation plus net debt. Minority interests, "
            "preferred stock and associates are not adjusted for, so a company with material "
            "amounts of any of them is overstated or understated by that much."
        ),
    ),
    MultipleDefinition(
        key="ev_sales",
        label="EV/Sales",
        numerator="enterprise_value",
        denominator="revenue",
        note=(
            "The multiple that still works when earnings do not. It says nothing about "
            "whether the revenue is profitable, which is the whole of its weakness."
        ),
    ),
    MultipleDefinition(
        key="pe",
        label="P/E",
        numerator="price_per_share",
        denominator="earnings_per_share",
        note=(
            "Both figures per share, so the multiple is unaffected by the share count. "
            "Diluted earnings where the filing reports them."
        ),
    ),
    MultipleDefinition(
        key="p_b",
        label="P/B",
        numerator="price_per_share",
        denominator="book_value_per_share",
        note=(
            "Book value is total equity as reported, goodwill and intangibles included — "
            "the general measure. A bank is properly read on P/TBV instead, which is why "
            "both exist."
        ),
    ),
    MultipleDefinition(
        key="p_tbv",
        label="P/TBV",
        numerator="price_per_share",
        denominator="tangible_book_value_per_share",
        note=(
            "Tangible book excludes goodwill and intangibles. The standard measure for a "
            "bank, where book value is the business rather than a historical cost."
        ),
        specialist_for=("banks",),
    ),
    MultipleDefinition(
        key="p_ffo",
        label="P/FFO",
        numerator="price_per_share",
        denominator="ffo_per_share",
        note=(
            "Funds from operations adds back real-estate depreciation, which for a property "
            "company is an accounting artefact rather than a cost. P/E is the wrong "
            "denominator for a REIT and this is why."
        ),
        specialist_for=("reits",),
    ),
)


# -- The traced calculations ------------------------------------------------------------------


@traced(
    name="market_enterprise_value",
    formula="EV = market capitalisation + net debt",
    assumptions=(
        "Minority interests, preferred stock and associates are not adjusted for.",
        "Net debt is the period-end balance, not an average.",
        "The market capitalisation is as at the as-of date, not the period end.",
    ),
)
def market_enterprise_value(
    _context: CalculationContext, *, market_capitalisation: Quantity, net_debt: Quantity
) -> Quantity:
    """What the market says the whole business is worth, debt included.

    **Not the same figure as :func:`aer.calc.dcf.enterprise_value`**, which is the present
    value of forecast cash flows. This one is observed and that one is derived; comparing them
    is most of the point of running both, and giving them one name would have hidden it.

    Raises:
        UnitMismatchError: If the two are in different currencies.
    """
    if market_capitalisation.unit != net_debt.unit:
        message = (
            f"Market capitalisation is in {market_capitalisation.unit.symbol} and net debt "
            f"in {net_debt.unit.symbol}. Adding them needs a rate, and adding them without "
            "one produces a figure that is wrong by it."
        )
        raise UnitMismatchError(
            message,
            context={
                "market_capitalisation": market_capitalisation.unit.symbol,
                "net_debt": net_debt.unit.symbol,
            },
        )

    return market_capitalisation + net_debt


@traced(
    name="book_value_per_share",
    formula="book value per share = total equity / shares outstanding",
    assumptions=(
        "Total equity as reported, goodwill and intangibles included.",
        "The period-end share count, not a weighted average.",
    ),
)
def book_value_per_share(
    _context: CalculationContext, *, equity: Quantity, shares: Quantity
) -> Quantity:
    """The balance sheet per share, for the P/B denominator.

    Negative equity passes through: a company that has bought back more than it ever
    earned has a negative book value, and :func:`multiple` is the place that says a
    multiple over it is not meaningful — here it is simply the number the filing implies.

    Raises:
        CalculationError: If the share count is not positive, which is a data error
            rather than a state a company can be in.
    """
    if shares.value <= 0:
        message = f"The share count is {shares.value}; a listed company has shares."
        raise CalculationError(message, context={"shares": str(shares.value)})
    return equity / shares


@traced(
    name="implied_value_per_share_from_ev_multiple",
    formula="value per share = (multiple * denominator - net debt) / shares",
    assumptions=(
        "The peer multiple prices the subject's whole enterprise the way it prices the "
        "peer's; differences in growth, margin and risk are exactly what it ignores.",
        "Net debt is the period-end balance the enterprise-value bridge uses.",
    ),
)
def implied_value_per_share_from_ev_multiple(
    _context: CalculationContext,
    *,
    multiple_observed: Quantity,
    denominator: Quantity,
    net_debt: Quantity,
    shares: Quantity,
) -> Quantity:
    """What a peer's enterprise multiple would say one of the subject's shares is worth.

    The arithmetic behind a comps band on a football field: an observed EV/EBITDA (or
    EV/Sales) applied to the subject's own denominator, bridged back to equity and spread
    over the share count. The unit algebra carries the proof — a pure multiple times a
    currency, less a currency, over shares, is a currency per share.

    Raises:
        CalculationError: If the share count is not positive.
    """
    if shares.value <= 0:
        message = f"The share count is {shares.value}; a listed company has shares."
        raise CalculationError(message, context={"shares": str(shares.value)})
    return (multiple_observed * denominator - net_debt) / shares


@traced(
    name="implied_value_per_share_from_price_multiple",
    formula="value per share = multiple * per-share denominator",
    assumptions=(
        "The peer multiple prices the subject's per-share figure the way it prices the "
        "peer's; differences in growth, margin and risk are exactly what it ignores.",
    ),
)
def implied_value_per_share_from_price_multiple(
    _context: CalculationContext,
    *,
    multiple_observed: Quantity,
    denominator_per_share: Quantity,
) -> Quantity:
    """What a peer's per-share multiple — a P/E, a P/B — implies for the subject."""
    return multiple_observed * denominator_per_share


@traced(
    name="multiple",
    formula="multiple = numerator / denominator",
    assumptions=(
        "Both figures cover the same period and the same entity.",
        "A non-positive denominator has no multiple and is refused rather than returned.",
    ),
)
def multiple(
    _context: CalculationContext,
    *,
    numerator: Quantity,
    denominator: Quantity,
    basis: MultipleBasis,
) -> Quantity:
    """One multiple, dimensionless, on a stated basis.

    **The denominator guard is the important line in this module.** A company with negative
    EBITDA has no EV/EBITDA; the arithmetic yields a negative number, and a negative number
    sorted into a table of multiples reads as the cheapest company in it. Callers get a
    refusal they must handle, which :func:`multiples_for` turns into "not meaningful" with the
    reason attached.

    Raises:
        CalculationError: If the denominator is not positive, or the basis is not a
            :class:`MultipleBasis`.
        UnitMismatchError: If the ratio is not dimensionless — which means a total was
            divided by a per-share figure, or two currencies were mixed.
    """
    _require_basis(basis)

    if denominator.value <= 0:
        message = (
            f"The denominator is {denominator.value}. A multiple over a nil or negative "
            "figure is not a low multiple, it is not a multiple — and a negative one sorted "
            "into a table reads as the cheapest company in it."
        )
        raise CalculationError(
            message,
            context={"denominator": str(denominator.value), "unit": denominator.unit.symbol},
        )

    result = numerator / denominator
    if not result.unit.is_dimensionless:
        message = (
            f"{numerator.unit.symbol} over {denominator.unit.symbol} is "
            f"{result.unit.symbol}, and a multiple is a pure number. A unit here means a "
            "whole-company figure was divided by a per-share one, or two currencies were "
            "mixed."
        )
        raise UnitMismatchError(
            message,
            context={
                "numerator": numerator.unit.symbol,
                "denominator": denominator.unit.symbol,
                "result": result.unit.symbol,
            },
        )
    return result


@traced(
    name="median_multiple",
    formula="median of the observations, averaging the middle pair when the count is even",
    assumptions=(
        "Every observation is on the same basis; mixing bases makes the median meaningless.",
        "The median rather than the mean, because one peer at 90x moves a mean and not this.",
    ),
)
def median_multiple(_context: CalculationContext, *, observations: Sequence[Quantity]) -> Quantity:
    """The middle multiple.

    **The median, never the mean.** A peer set of eight trading between 9x and 13x plus one at
    140x has a mean of 27x, which describes none of them. The outlier is usually a company
    whose denominator is nearly zero rather than a company anybody would pay that for.

    Raises:
        CalculationError: If there are no observations.
        UnitMismatchError: If they are not all dimensionless.
    """
    if not observations:
        message = "A median over no observations is undefined."
        raise CalculationError(message, context={"observations": 0})

    for index, item in enumerate(observations):
        if not item.unit.is_dimensionless:
            message = (
                f"Observation {index} is in {item.unit.symbol}. A multiple is a pure number, "
                "so a median of multiples is too."
            )
            raise UnitMismatchError(message, context={"unit": item.unit.symbol})

    with localcontext(CALC_CONTEXT):
        return Quantity.of(_median([item.value for item in observations]), DIMENSIONLESS)


@traced(
    name="percentile_rank",
    formula="rank = count of observations at or below the value / total count",
    assumptions=(
        "Every observation is on the same basis as the value being placed.",
        "Ties count as at-or-below, so an unchanged multiple ranks at 1 rather than at 0.",
    ),
)
def percentile_rank(
    _context: CalculationContext, *, value: Quantity, observations: Sequence[Quantity]
) -> Quantity:
    """Where a figure sits in its own history, from 0 to 1.

    The honest form of "is this expensive?". A company at 14x against peers at 11x may simply
    be a company that has always traded at 14x, and this is the number that says so.

    Raises:
        CalculationError: If there are no observations.
    """
    if not observations:
        message = "A percentile over no observations is undefined."
        raise CalculationError(message, context={"observations": 0})

    with localcontext(CALC_CONTEXT):
        at_or_below = sum(1 for item in observations if item.value <= value.value)
        return Quantity.of(Decimal(at_or_below) / Decimal(len(observations)), DIMENSIONLESS)


# -- Assembly ---------------------------------------------------------------------------------


def multiples_for(
    context: CalculationContext,
    *,
    inputs: Mapping[str, Quantity],
    basis: MultipleBasis,
    period_end: date,
    definitions: Sequence[MultipleDefinition] = MULTIPLE_DEFINITIONS,
) -> tuple[MultipleResult, ...]:
    """Every multiple the inputs support, and a stated reason for each one they do not.

    Never raises for a missing input or a non-positive denominator: both are ordinary
    conditions and both are reported. It *does* raise on a unit mismatch, for the reason
    :mod:`aer.calc.ratios` gives — a currency mix is a mapping error, and swallowing it would
    hide it in exactly the place somebody is looking for problems.
    """
    results: list[MultipleResult] = []

    for definition in definitions:
        missing = tuple(
            name for name in (definition.numerator, definition.denominator) if name not in inputs
        )
        if missing:
            results.append(
                MultipleResult(
                    key=definition.key,
                    label=definition.label,
                    quantity=None,
                    basis=basis,
                    period_end=period_end,
                    absent_because=(
                        f"the filing does not report {' and '.join(missing)} for this period"
                    ),
                    missing=missing,
                )
            )
            continue

        try:
            value = multiple(
                context,
                numerator=inputs[definition.numerator],
                denominator=inputs[definition.denominator],
                basis=basis,
            )
        except CalculationError as exc:
            if isinstance(exc, UnitMismatchError):
                raise
            results.append(
                MultipleResult(
                    key=definition.key,
                    label=definition.label,
                    quantity=None,
                    basis=basis,
                    period_end=period_end,
                    absent_because=str(exc).split(".")[0].strip().lower() or "not meaningful",
                )
            )
            continue

        results.append(
            MultipleResult(
                key=definition.key,
                label=definition.label,
                quantity=value,
                basis=basis,
                period_end=period_end,
            )
        )

    return tuple(results)


def align_peers(
    candidates: Sequence[tuple[str, str, date]],
    *,
    subject_period_end: date,
    tolerance_days: int = MAX_PERIOD_DRIFT_DAYS,
) -> tuple[tuple[tuple[str, str, date], ...], tuple[PeerExclusion, ...]]:
    """Split proposed peers into those comparable with the subject and those not.

    ``candidates`` are ``(identifier, name, period_end)`` triples.

    **A peer whose year ends in a different quarter is excluded, not adjusted.** Aligning it
    would need quarterly data for both companies and an assumption about seasonality; without
    that, including it compares three months of a different economy, and for anything cyclical
    those three months are the whole story. The exclusion is recorded with its reason, so a
    reader can see the peer that was left out and disagree.
    """
    kept: list[tuple[str, str, date]] = []
    excluded: list[PeerExclusion] = []

    for identifier, name, period_end in candidates:
        drift = abs((period_end - subject_period_end).days)
        if drift <= tolerance_days:
            kept.append((identifier, name, period_end))
            continue
        excluded.append(
            PeerExclusion(
                identifier=identifier,
                name=name,
                period_end=period_end,
                reason=(
                    f"reports to {period_end.isoformat()}, {drift} days from the subject's "
                    f"{subject_period_end.isoformat()}. Beyond {tolerance_days} days the two "
                    "cover materially different periods, and aligning them would need "
                    "quarterly data and an assumption about seasonality."
                ),
            )
        )

    return tuple(kept), tuple(excluded)


# -- Guards -----------------------------------------------------------------------------------


def _median(values: Sequence[Decimal]) -> Decimal:
    """The middle value, averaging the middle pair on an even count."""
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    with localcontext(CALC_CONTEXT):
        if len(ordered) % 2 == 1:
            return ordered[midpoint]
        return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _require_basis(value: object) -> None:
    """Refuse anything but a :class:`MultipleBasis`.

    The annotation is enough for every caller mypy checks, and this catches the ones it does
    not. ``"trailing"`` would otherwise be recorded verbatim as the basis a multiple was
    computed on, which reads as a specification and is a string.
    """
    if isinstance(value, MultipleBasis):
        return

    message = (
        f"basis is {value!r}, which is not a MultipleBasis. A trailing multiple and a "
        "forward multiple are different numbers, so the record has to say which in a form "
        "code can read back."
    )
    raise CalculationError(message, context={"basis": repr(value)})
