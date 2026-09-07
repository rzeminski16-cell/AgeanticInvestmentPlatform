"""Whether the book has done well — the arithmetic that must not read a deposit as a gain.

Roadmap §3.2. The one failure this module exists to prevent is a top-up showing as
performance, and it is a failure that looks entirely ordinary: the book is worth more, the
figure is positive, and nothing about the number says the money came from the operator's
own pocket rather than from the holdings.

So the load-bearing tests here are the pairs. `TestAFlowIsNotAGain` runs the same underlying
growth through two completely different flow schedules and demands the time-weighted return
come out identical; `TestTheTwoReturnsDisagreeOnPurpose` demands that the money-weighted one
does *not*, because a screen showing two numbers that always agree is showing one number
twice.

The property tests carry the conventions `test_calc_properties` established: decimals built
from integers and scaled, never sampled as floats; absolute tolerances, because every result
here is a rate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from aer.calc.engine import CalculationContext
from aer.calc.performance import (
    YEARS,
    exposure,
    grouped_value,
    money_weighted_return,
    time_weighted_return,
    top_holdings_share,
)
from aer.calc.units import (
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
    money,
)
from aer.calc.units import ratio as pure

SOURCE = SourceRef.attestation("performance-test", grade="attested")
GBP = Unit.currency("GBP")


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


def gbp(value: str | int) -> Quantity:
    return money(Decimal(str(value)), "GBP", source=SOURCE)


def years(value: str | int) -> Quantity:
    return Quantity.of(Decimal(str(value)), YEARS, source=SOURCE)


def share(value: str) -> Quantity:
    return pure(Decimal(value), source=SOURCE)


# -- The one that matters --------------------------------------------------------------------


class TestAFlowIsNotAGain:
    """The whole point of a time-weighted return, and the failure it exists to prevent.

    A book worth £100 that rises 10%, receives a £100 deposit, then rises 10% again is up
    21% — not 131%, which is what dividing the closing value by the opening one would say,
    and not 10.5%, which is what a naive average of the two would.
    """

    def test_the_deposit_drops_out_entirely(self, context: CalculationContext) -> None:
        chained = time_weighted_return(
            context,
            # Sub-period two opens at 210: the 110 that was there plus the 100 paid in.
            openings=[gbp(100), gbp(210)],
            # And closes at 231 — the book *before* any flow on that date.
            closings=[gbp(110), gbp(231)],
        )

        assert chained.value == Decimal("0.21")

    def test_the_same_growth_under_a_different_schedule_is_the_same_number(
        self, context: CalculationContext
    ) -> None:
        """Two books, the same two 10% moves, wildly different flows. One answer.

        This is the property in one case, and it is what makes the figure comparable to an
        index: an index has no flows, so a return that moves with them cannot be held
        against it.
        """
        no_flows = time_weighted_return(
            context, openings=[gbp(100), gbp(110)], closings=[gbp(110), gbp(121)]
        )
        large_flow = time_weighted_return(
            context, openings=[gbp(100), gbp("1110")], closings=[gbp(110), gbp("1221")]
        )

        assert no_flows.value == large_flow.value == Decimal("0.21")

    def test_a_withdrawal_is_not_a_loss_either(self, context: CalculationContext) -> None:
        """The mirror case, and the one an operator notices: taking money out must not
        make a book that grew look like a book that shrank."""
        chained = time_weighted_return(
            context, openings=[gbp(100), gbp(10)], closings=[gbp(110), gbp(11)]
        )

        assert chained.value == Decimal("0.21")

    def test_a_book_that_only_received_money_returned_nothing(
        self, context: CalculationContext
    ) -> None:
        """£100 in, then £100 more, and not a penny earned. The figure must be zero — the
        one number a screen absolutely must not report as 100%."""
        chained = time_weighted_return(
            context, openings=[gbp(100), gbp(200)], closings=[gbp(100), gbp(200)]
        )

        assert chained.value == 0


class TestTheTwoReturnsDisagreeOnPurpose:
    """If they always agreed, the page would be showing one number twice.

    A book that doubled its stake immediately before a rise earned more than the strategy
    did, and the money-weighted figure is the one that says so.
    """

    def test_good_timing_shows_up_in_one_of_them(self, context: CalculationContext) -> None:
        # £100 flat for a year, then £900 added, then everything rises 10%.
        time_weighted = time_weighted_return(
            context, openings=[gbp(100), gbp(1000)], closings=[gbp(100), gbp(1100)]
        )
        money_weighted = money_weighted_return(
            context,
            flows=[gbp(-100), gbp(-900), gbp(1100)],
            years=[years(0), years(1), years(2)],
        )

        assert time_weighted.value == Decimal("0.10")
        # The operator's own return is lower: most of the money was only at work for the
        # second year, and the first year earned nothing.
        assert 0 < money_weighted.value < time_weighted.value


# -- Time-weighted return --------------------------------------------------------------------


class TestTheChain:
    def test_one_sub_period_is_just_its_own_move(self, context: CalculationContext) -> None:
        assert time_weighted_return(
            context, openings=[gbp(200)], closings=[gbp(250)]
        ).value == Decimal("0.25")

    def test_a_flat_book_returns_nothing_rather_than_nothing_being_reported(
        self, context: CalculationContext
    ) -> None:
        assert time_weighted_return(context, openings=[gbp(100)], closings=[gbp(100)]).value == 0

    def test_losses_compound_the_same_way_gains_do(self, context: CalculationContext) -> None:
        chained = time_weighted_return(
            context, openings=[gbp(100), gbp(90)], closings=[gbp(90), gbp(81)]
        )

        assert chained.value == Decimal("-0.19")

    def test_the_result_is_a_rate_and_carries_no_currency(
        self, context: CalculationContext
    ) -> None:
        chained = time_weighted_return(context, openings=[gbp(100)], closings=[gbp(110)])

        assert chained.unit == DIMENSIONLESS

    def test_a_sub_period_needs_both_ends(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="both ends"):
            time_weighted_return(context, openings=[gbp(100), gbp(110)], closings=[gbp(110)])

    def test_no_sub_periods_is_a_question_not_an_answer(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="no sub-periods"):
            time_weighted_return(context, openings=[], closings=[])

    def test_a_sub_period_opening_at_nothing_is_refused(self, context: CalculationContext) -> None:
        """Not zero, not skipped: undefined. A book that went to nothing and was refunded
        is a real event and the reader must be told the chain cannot cross it."""
        with pytest.raises(CalculationError, match="no fraction of nothing"):
            time_weighted_return(context, openings=[gbp(100), gbp(0)], closings=[gbp(0), gbp(50)])

    def test_two_currencies_never_chain(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError, match="different units"):
            time_weighted_return(
                context,
                openings=[gbp(100)],
                closings=[money(Decimal(110), "USD", source=SOURCE)],
            )

    def test_a_share_count_in_this_position_is_refused(self, context: CalculationContext) -> None:
        counted = Quantity.of(Decimal(100), Unit.base("shares"), source=SOURCE)

        with pytest.raises(UnitMismatchError, match="not a currency"):
            time_weighted_return(context, openings=[counted], closings=[counted])


# -- Money-weighted return -------------------------------------------------------------------


class TestTheInternalRate:
    def test_a_single_year_recovers_its_own_rate(self, context: CalculationContext) -> None:
        rate = money_weighted_return(
            context, flows=[gbp(-1000), gbp(1100)], years=[years(0), years(1)]
        )

        assert abs(rate.value - Decimal("0.10")) < Decimal("1e-9")

    def test_a_staged_investment_recovers_it_too(self, context: CalculationContext) -> None:
        """£1,000 now, £1,000 in a year, £2,310 at the end — 10% on both, compounded."""
        rate = money_weighted_return(
            context,
            flows=[gbp(-1000), gbp(-1000), gbp(2310)],
            years=[years(0), years(1), years(2)],
        )

        assert abs(rate.value - Decimal("0.10")) < Decimal("1e-9")

    def test_a_loss_comes_back_negative(self, context: CalculationContext) -> None:
        rate = money_weighted_return(
            context, flows=[gbp(-1000), gbp(900)], years=[years(0), years(1)]
        )

        assert abs(rate.value - Decimal("-0.10")) < Decimal("1e-9")

    def test_part_of_a_year_annualises(self, context: CalculationContext) -> None:
        """A 10% move in six months is more than 10% a year, and the offsets are what say
        so — this is where an actual/365 basis stops being a footnote."""
        rate = money_weighted_return(
            context, flows=[gbp(-1000), gbp(1100)], years=[years(0), years("0.5")]
        )

        assert rate.value > Decimal("0.20")

    def test_scaling_every_flow_leaves_the_rate_alone(self, context: CalculationContext) -> None:
        small = money_weighted_return(
            context, flows=[gbp(-100), gbp(115)], years=[years(0), years(1)]
        )
        large = money_weighted_return(
            context, flows=[gbp(-100000), gbp(115000)], years=[years(0), years(1)]
        )

        assert abs(small.value - large.value) < Decimal("1e-15")

    def test_every_flow_is_dated_or_none_can_be(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="Every flow is dated"):
            money_weighted_return(context, flows=[gbp(-100), gbp(110)], years=[years(0)])

    def test_no_flows_is_a_question_not_an_answer(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="no flows"):
            money_weighted_return(context, flows=[], years=[])

    def test_a_period_of_no_length_has_no_rate(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="no time has passed"):
            money_weighted_return(context, flows=[gbp(-100), gbp(110)], years=[years(0), years(0)])

    def test_one_sided_flows_are_refused(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="all one sign"):
            money_weighted_return(context, flows=[gbp(-100), gbp(-100)], years=[years(0), years(1)])

    def test_a_flow_before_the_period_is_a_period_starting_in_the_wrong_place(
        self, context: CalculationContext
    ) -> None:
        with pytest.raises(CalculationError, match="begins in the wrong place"):
            money_weighted_return(context, flows=[gbp(-100), gbp(110)], years=[years(-1), years(1)])

    def test_an_offset_in_the_wrong_unit_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError, match="not years"):
            money_weighted_return(context, flows=[gbp(-100), gbp(110)], years=[years(0), gbp(1)])

    def test_a_total_loss_is_named_rather_than_approximated(
        self, context: CalculationContext
    ) -> None:
        """Everything in, nothing back. There is no rate short of -100%, and the bracket
        stops just above it — so this is refused with its reason rather than reported as
        the floor, which would look like a measurement."""
        with pytest.raises(CalculationError, match="No rate between"):
            money_weighted_return(
                context, flows=[gbp(-1000), gbp("0.000000001")], years=[years(0), years(1)]
            )


# -- Exposure and concentration --------------------------------------------------------------


class TestExposure:
    def test_a_group_is_its_share_of_the_whole_book(self, context: CalculationContext) -> None:
        value = grouped_value(context, values=[gbp(300), gbp(200)])

        assert exposure(context, value=value, net_assets=gbp(1000)).value == Decimal("0.5")

    def test_a_partition_of_the_book_sums_to_one(self, context: CalculationContext) -> None:
        """The property that makes the band readable: every slice of one cut adds to the
        whole, or the page is showing a pie that does not close."""
        total = gbp(1000)
        shares = [
            exposure(context, value=grouped_value(context, values=[part]), net_assets=total).value
            for part in (gbp(500), gbp(300), gbp(200))
        ]

        assert sum(shares) == 1

    def test_an_empty_group_is_refused_rather_than_weighted_at_nothing(
        self, context: CalculationContext
    ) -> None:
        with pytest.raises(CalculationError, match="nothing in this group"):
            grouped_value(context, values=[])

    def test_an_empty_book_has_no_fractions_of_it(self, context: CalculationContext) -> None:
        value = grouped_value(context, values=[gbp(100)])

        with pytest.raises(CalculationError, match="undefined rather than nil"):
            exposure(context, value=value, net_assets=gbp(0))

    def test_two_currencies_never_weight_against_each_other(
        self, context: CalculationContext
    ) -> None:
        value = grouped_value(context, values=[gbp(100)])

        with pytest.raises(UnitMismatchError, match="Convert first"):
            exposure(context, value=value, net_assets=money(Decimal(1000), "USD", source=SOURCE))

    def test_a_group_in_two_currencies_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError, match="different units"):
            grouped_value(context, values=[gbp(100), money(Decimal(100), "USD", source=SOURCE)])


class TestConcentration:
    def test_the_largest_five_are_the_ones_counted(self, context: CalculationContext) -> None:
        """The ranking happens inside, so a caller cannot record its own sort as the
        book's concentration."""
        weights = [
            share("0.05"),
            share("0.4"),
            share("0.1"),
            share("0.3"),
            share("0.1"),
            share("0.05"),
        ]

        assert top_holdings_share(context, weights=weights, count=5).value == Decimal("0.95")

    def test_a_smaller_book_reports_everything_it_has(self, context: CalculationContext) -> None:
        weights = [share("0.6"), share("0.4")]

        assert top_holdings_share(context, weights=weights, count=5).value == 1

    def test_more_holdings_never_reduce_the_figure(self, context: CalculationContext) -> None:
        weights = [share("0.2")] * 5

        four = top_holdings_share(context, weights=weights, count=4).value
        five = top_holdings_share(context, weights=weights, count=5).value

        assert five >= four

    def test_no_holdings_is_a_question_not_a_zero(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="no weights to rank"):
            top_holdings_share(context, weights=[], count=5)

    def test_a_top_nothing_share_is_not_a_quantity(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="at least one holding"):
            top_holdings_share(context, weights=[share("0.5")], count=0)

    def test_a_value_passed_where_a_weight_belongs_is_refused(
        self, context: CalculationContext
    ) -> None:
        """Otherwise the concentration comes out as a currency amount that reads as a
        percentage, which is the error most likely to survive a glance."""
        with pytest.raises(UnitMismatchError, match="not a fraction of the book"):
            top_holdings_share(context, weights=[gbp(500)], count=5)


# -- Properties --------------------------------------------------------------------------------

# Money exact to the penny, built from integers and scaled — never sampled as a float, which
# would test what `Decimal(float)` does to binary rounding rather than the arithmetic.
positive_money = st.integers(min_value=1, max_value=10**12).map(
    lambda pennies: money(Decimal(pennies).scaleb(-2), "GBP", source=SOURCE)
)
rates = st.integers(min_value=-9000, max_value=100_000).map(lambda basis: Decimal(basis).scaleb(-4))


class TestTheChainHolds:
    @settings(max_examples=200, deadline=None)
    @given(openings=st.lists(positive_money, min_size=1, max_size=8), factor=rates)
    def test_a_uniform_move_compounds(self, openings: list[Quantity], factor: Decimal) -> None:
        """Every sub-period moving by the same fraction compounds to that fraction over n."""
        context = CalculationContext(code_version="property")
        multiplier = Decimal(1) + factor
        closings = [money(opening.value * multiplier, "GBP", source=SOURCE) for opening in openings]

        chained = time_weighted_return(context, openings=openings, closings=closings)

        expected = multiplier ** len(openings) - 1
        assert abs(chained.value - expected) < Decimal("1e-18")

    @settings(max_examples=200, deadline=None)
    @given(openings=st.lists(positive_money, min_size=1, max_size=8))
    def test_a_book_that_never_moved_returned_nothing(self, openings: list[Quantity]) -> None:
        context = CalculationContext(code_version="property")

        chained = time_weighted_return(context, openings=openings, closings=list(openings))

        assert chained.value == 0

    @settings(max_examples=100, deadline=None)
    @given(
        openings=st.lists(positive_money, min_size=2, max_size=6),
        factor=rates,
    )
    def test_inserting_a_flow_between_two_sub_periods_changes_nothing(
        self, openings: list[Quantity], factor: Decimal
    ) -> None:
        """The property the whole figure exists for, over every book hypothesis can build:
        scaling one boundary — which is exactly what a deposit or a withdrawal does —
        leaves the chained return where it was."""
        assume(factor != -1)
        context = CalculationContext(code_version="property")
        multiplier = Decimal(1) + factor
        closings = [
            money(opening.value * Decimal("1.05"), "GBP", source=SOURCE) for opening in openings
        ]

        before = time_weighted_return(context, openings=openings, closings=closings)

        # A flow on the boundary between sub-period 0 and 1: both the opening of the second
        # and everything after scale by the same factor, which is what a flow does.
        scaled_openings = [
            openings[0],
            *(money(opening.value * multiplier, "GBP", source=SOURCE) for opening in openings[1:]),
        ]
        scaled_closings = [
            closings[0],
            *(money(closing.value * multiplier, "GBP", source=SOURCE) for closing in closings[1:]),
        ]
        assume(all(opening.value > 0 for opening in scaled_openings))

        after = time_weighted_return(context, openings=scaled_openings, closings=scaled_closings)

        assert abs(before.value - after.value) < Decimal("1e-18")


class TestTheRateSolves:
    @settings(max_examples=100, deadline=None)
    @given(rate=st.integers(min_value=-8000, max_value=50_000), span=st.integers(1, 30))
    def test_the_solver_recovers_the_rate_that_built_the_flows(self, rate: int, span: int) -> None:
        """Construct a book that earned exactly r, then ask what it earned."""
        context = CalculationContext(code_version="property")
        truth = Decimal(rate).scaleb(-4)
        stake = Decimal(1000)
        ending = stake * (Decimal(1) + truth) ** span

        solved = money_weighted_return(
            context,
            flows=[
                money(-stake, "GBP", source=SOURCE),
                money(ending, "GBP", source=SOURCE),
            ],
            years=[years(0), years(span)],
        )

        assert abs(solved.value - truth) < Decimal("1e-6")

    @settings(max_examples=100, deadline=None)
    @given(stake=positive_money, rate=st.integers(min_value=1, max_value=20_000))
    def test_the_rate_does_not_depend_on_the_size_of_the_book(
        self, stake: Quantity, rate: int
    ) -> None:
        context = CalculationContext(code_version="property")
        truth = Decimal(rate).scaleb(-4)
        ending = stake.value * (Decimal(1) + truth)

        solved = money_weighted_return(
            context,
            flows=[
                money(-stake.value, "GBP", source=SOURCE),
                money(ending, "GBP", source=SOURCE),
            ],
            years=[years(0), years(1)],
        )

        assert abs(solved.value - truth) < Decimal("1e-6")


class TestExposureHolds:
    @settings(max_examples=200, deadline=None)
    @given(parts=st.lists(positive_money, min_size=1, max_size=10))
    def test_the_slices_of_one_cut_add_to_the_whole(self, parts: list[Quantity]) -> None:
        context = CalculationContext(code_version="property")
        total = money(sum(part.value for part in parts), "GBP", source=SOURCE)

        shares = [
            exposure(context, value=grouped_value(context, values=[part]), net_assets=total).value
            for part in parts
        ]

        assert abs(sum(shares) - 1) < Decimal("1e-20")

    @settings(max_examples=200, deadline=None)
    @given(parts=st.lists(positive_money, min_size=1, max_size=10))
    def test_grouping_members_together_is_the_same_as_adding_their_shares(
        self, parts: list[Quantity]
    ) -> None:
        """A sector's weight must not depend on whether the code grouped first or divided
        first — the two orders are the same arithmetic and a difference would be drift."""
        context = CalculationContext(code_version="property")
        total = money(sum(part.value for part in parts) * 2, "GBP", source=SOURCE)

        together = exposure(
            context, value=grouped_value(context, values=parts), net_assets=total
        ).value
        apart = sum(
            exposure(context, value=grouped_value(context, values=[part]), net_assets=total).value
            for part in parts
        )

        assert abs(together - apart) < Decimal("1e-20")
