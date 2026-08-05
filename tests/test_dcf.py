"""The discounted cash flow: a worked example, the invariants, and what it refuses.

The forecast below was worked out on paper before it was run, and every line of it is an
exact decimal — 1,000 growing at 10%, 8% and 6%, at a 20% margin, 25% tax, 5% depreciation,
8% capital intensity and 10% working capital. The per-year figures are asserted exactly. The
discounted aggregates are asserted to 0.01%, which is what `docs/phase-3-plan.md` asks for
and as tight as a figure quoted to eight significant figures can honestly be checked.

The worked example is also a demonstration. Its two terminal methods disagree by 81% — 12.09
a share against 21.95 — and the reason is visible in the cross-checks: 2% perpetual growth
implies an exit multiple of 5.8x, and the 10x exit multiple implies 5.19% perpetual growth.
Neither number is wrong. Presenting either one alone would be.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
from itertools import pairwise

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from aer.calc.dcf import (
    HIGH_TERMINAL_SHARE_CAVEAT,
    MAX_FORECAST_YEARS,
    METHOD_DISAGREEMENT_CAVEAT,
    NARROW_SPREAD_CAVEAT,
    NEGATIVE_EQUITY_CAVEAT,
    BridgeItem,
    DcfInputs,
    DriverPath,
    GridAxis,
    GridMeasure,
    TerminalMethod,
    discount_factor,
    discounted_cash_flow,
    enterprise_value,
    exit_multiple_terminal_value,
    gordon_terminal_value,
    project,
    projected_capex,
    projected_ebit,
    projected_revenue,
    projected_working_capital,
    sensitivity_grid,
    value_per_share,
)
from aer.calc.engine import CalculationContext
from aer.calc.units import (
    CALC_CONTEXT,
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    UnitMismatchError,
    money,
)
from aer.core.sectors import ValuationModel, unclassified_mandate

ASSUMPTION = SourceRef.assumption("assumption-1")
FACT = SourceRef.fact("fact-1")

# An ordinary company: nobody classified it into a specialist sector, so the standard model
# applies. The block itself is tested in `test_sectors_enforcement.py`; here the mandate is
# the permission the arithmetic requires, and every call has to carry one.
MANDATE = unclassified_mandate(ValuationModel.DCF_FCFF, subject="TESTCO")


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def rate(value: str) -> Quantity:
    return Quantity.of(Decimal(value), source=ASSUMPTION)


def usd(value: str) -> Quantity:
    return money(value, "USD", source=FACT)


def shares(value: str) -> Quantity:
    return Quantity.of(Decimal(value), "shares", source=FACT)


def flat(name: str, value: str, *, years: int = 3) -> DriverPath:
    return DriverPath.flat(name, rate(value), years=years)


def base_inputs(**overrides) -> DcfInputs:
    """The worked example. Every figure round, every intermediate exact."""
    inputs = DcfInputs(
        base_revenue=usd("1000"),
        revenue_growth=DriverPath("revenue_growth", (rate("0.10"), rate("0.08"), rate("0.06"))),
        ebit_margin=flat("ebit_margin", "0.20"),
        capex_intensity=flat("capex_intensity", "0.08"),
        depreciation_intensity=flat("depreciation_intensity", "0.05"),
        working_capital_intensity=flat("working_capital_intensity", "0.10"),
        opening_working_capital=usd("100"),
        tax_rate=rate("0.25"),
        wacc=rate("0.10"),
        terminal_growth=rate("0.02"),
        exit_multiple=rate("10"),
        net_debt=usd("500"),
        shares_outstanding=shares("100"),
        non_operating=(),
    )
    return replace(inputs, **overrides) if overrides else inputs


# The plan's tolerance. Tight enough that a wrong discount convention or an off-by-one in the
# exponent fails; loose enough that a figure quoted to eight significant figures passes.
TOLERANCE = Decimal("0.0001")


def close(actual: Quantity, expected: str) -> bool:
    wanted = Decimal(expected)
    return abs(actual.value - wanted) <= abs(wanted) * TOLERANCE


# -- The worked forecast ---------------------------------------------------------------------


class TestTheForecast:
    """Every line exact. These were computed by hand from the drivers above."""

    @pytest.mark.parametrize(
        ("year", "revenue", "ebit", "nopat", "depreciation", "capex", "nwc", "movement", "flow"),
        [
            # 1,000 x 1.10 = 1,100. EBIT 220, NOPAT 165, D&A 55, capex 88, NWC 110 from 100,
            # so free cash flow is 165 plus 55 less 88 less 10.
            (1, "1100", "220", "165", "55", "88", "110", "10", "122"),
            # 1,100 x 1.08 = 1,188, and 178.2 plus 59.4 less 95.04 less 8.8.
            (2, "1188", "237.6", "178.2", "59.4", "95.04", "118.8", "8.8", "133.76"),
            # 1,188 x 1.06 = 1,259.28, and 188.892 plus 62.964 less 100.7424 less 7.128.
            (
                3,
                "1259.28",
                "251.856",
                "188.892",
                "62.964",
                "100.7424",
                "125.928",
                "7.128",
                "143.9856",
            ),
        ],
    )
    def test_each_year(
        self, context, year, revenue, ebit, nopat, depreciation, capex, nwc, movement, flow
    ):
        projected = project(context, base_inputs(), mandate=MANDATE)[year - 1]

        assert projected.revenue.value == Decimal(revenue)
        assert projected.ebit.value == Decimal(ebit)
        assert projected.nopat.value == Decimal(nopat)
        assert projected.depreciation.value == Decimal(depreciation)
        assert projected.capex.value == Decimal(capex)
        assert projected.working_capital.value == Decimal(nwc)
        assert projected.change_in_working_capital.value == Decimal(movement)
        assert projected.free_cash_flow.value == Decimal(flow)

    def test_ebitda_adds_depreciation_back(self, context):
        years = project(context, base_inputs(), mandate=MANDATE)
        assert years[-1].ebitda.value == Decimal("314.82")

    def test_the_discounted_years(self, context):
        # 122/1.1, 133.76/1.21, 143.9856/1.331
        years = project(context, base_inputs(), mandate=MANDATE)
        assert close(years[0].present_value, "110.90909091")
        assert close(years[1].present_value, "110.54545455")
        assert close(years[2].present_value, "108.17851240")

    def test_the_first_year_is_discounted_one_year_not_none(self, context):
        """Year one is a year away. Discounting it at t=0 overstates every valuation."""
        years = project(context, base_inputs(), mandate=MANDATE)
        assert years[0].discount_factor.value < 1
        assert close(years[0].discount_factor, "0.90909091")

    def test_working_capital_moves_from_the_opening_balance(self, context):
        """Year one's movement is against the balance sheet, not against nil."""
        years = project(context, base_inputs(opening_working_capital=usd("60")), mandate=MANDATE)
        assert years[0].change_in_working_capital.value == Decimal("50")


# -- The worked valuation --------------------------------------------------------------------


class TestBothTerminalValues:
    def test_gordon_growth(self, context):
        # 143.9856 x 1.02 / (0.10 - 0.02) = 146.865312 / 0.08
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        assert result.gordon.terminal_value.value == Decimal("1835.8164")
        assert close(result.gordon.discounted_terminal_value, "1379.27603306")
        assert close(result.gordon.enterprise_value, "1708.90909091")
        assert close(result.gordon.equity_value, "1208.90909091")
        assert close(result.gordon.value_per_share, "12.08909091")

    def test_the_exit_multiple(self, context):
        # 314.82 x 10 = 3,148.2, discounted by 1.331
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        assert result.exit_multiple.terminal_value.value == Decimal("3148.2")
        assert close(result.exit_multiple.discounted_terminal_value, "2365.28925620")
        assert close(result.exit_multiple.enterprise_value, "2694.92231405")
        assert close(result.exit_multiple.equity_value, "2194.92231405")
        assert close(result.exit_multiple.value_per_share, "21.94922314")

    def test_both_are_always_returned(self, context):
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        assert result.outcomes[0].method is TerminalMethod.GORDON_GROWTH
        assert result.outcomes[1].method is TerminalMethod.EXIT_MULTIPLE
        assert result.outcome(TerminalMethod.EXIT_MULTIPLE) is result.exit_multiple

    def test_the_forecast_years_are_shared_rather_than_recomputed(self, context):
        """Both methods value the same business. Only the terminal assumption differs."""
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        difference = (
            result.exit_multiple.enterprise_value.value - result.gordon.enterprise_value.value
        )
        terminal_difference = (
            result.exit_multiple.discounted_terminal_value.value
            - result.gordon.discounted_terminal_value.value
        )
        assert difference == terminal_difference


class TestTheCrossChecks:
    """Each method reports the other's parameter. This is where a DCF gives itself away."""

    def test_gordon_growth_reports_the_multiple_it_implies(self, context):
        # 1,835.8164 / 314.82. A 2% perpetuity is a 5.8x exit, which is the argument.
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        assert result.gordon.implied_exit_multiple is not None
        assert close(result.gordon.implied_exit_multiple, "5.83132075")

    def test_the_exit_multiple_reports_the_growth_it_implies(self, context):
        # (3,148.2 x 0.10 - 143.9856) / (3,148.2 + 143.9856)
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        assert result.exit_multiple.implied_terminal_growth is not None
        assert close(result.exit_multiple.implied_terminal_growth, "0.05189088")

    def test_neither_restates_its_own_input_as_a_finding(self, context):
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        assert result.gordon.implied_terminal_growth is None
        assert result.exit_multiple.implied_exit_multiple is None

    def test_the_implied_growth_round_trips_to_the_same_terminal_value(self, context):
        """The cross-check is a rearrangement, so it has to invert exactly."""
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)
        implied = result.exit_multiple.implied_terminal_growth
        assert implied is not None

        rebuilt = gordon_terminal_value(
            context,
            final_cash_flow=result.years[-1].free_cash_flow,
            wacc=base_inputs().wacc,
            terminal_growth=implied,
        )
        assert close(rebuilt, str(result.exit_multiple.terminal_value.value))


class TestTheTerminalShare:
    def test_it_appears_on_every_outcome(self, context):
        """The acceptance criterion: on every result, not on request."""
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        for outcome in result.outcomes:
            assert outcome.terminal_share.unit.is_dimensionless
            assert 0 < outcome.terminal_share.value < 1

    def test_it_is_the_discounted_terminal_value_over_enterprise_value(self, context):
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        assert close(result.gordon.terminal_share, "0.80710907")
        assert close(result.exit_multiple.terminal_share, "0.87768365")

    def test_a_longer_forecast_lowers_it(self, context):
        short = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)
        long = discounted_cash_flow(
            context,
            base_inputs(
                revenue_growth=flat("revenue_growth", "0.06", years=8),
                ebit_margin=flat("ebit_margin", "0.20", years=8),
                capex_intensity=flat("capex_intensity", "0.08", years=8),
                depreciation_intensity=flat("depreciation_intensity", "0.05", years=8),
                working_capital_intensity=flat("working_capital_intensity", "0.10", years=8),
            ),
            mandate=MANDATE,
        )

        assert long.gordon.terminal_share.value < short.gordon.terminal_share.value


# -- What the result has to say about itself -------------------------------------------------


class TestCaveats:
    def test_a_high_terminal_share_is_stated(self, context):
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)
        assert HIGH_TERMINAL_SHARE_CAVEAT in result.caveats

    def test_methods_that_disagree_materially_say_so(self, context):
        # 12.09 against 21.95 is 81% apart, and that is the honest width of the answer.
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)
        assert METHOD_DISAGREEMENT_CAVEAT in result.caveats

    def test_a_terminal_growth_rate_near_the_discount_rate_is_flagged(self, context):
        result = discounted_cash_flow(
            context, base_inputs(terminal_growth=rate("0.095")), mandate=MANDATE
        )
        assert NARROW_SPREAD_CAVEAT in result.caveats

    def test_a_comfortable_spread_is_not_flagged(self, context):
        result = discounted_cash_flow(
            context, base_inputs(terminal_growth=rate("0.02")), mandate=MANDATE
        )
        assert NARROW_SPREAD_CAVEAT not in result.caveats

    def test_equity_value_below_zero_is_stated_rather_than_printed(self, context):
        result = discounted_cash_flow(context, base_inputs(net_debt=usd("9000")), mandate=MANDATE)
        assert NEGATIVE_EQUITY_CAVEAT in result.caveats
        assert result.gordon.value_per_share.value < 0

    def test_a_valuation_with_nothing_to_flag_says_nothing(self, context):
        """Otherwise the caveats become boilerplate and a reader stops reading them."""
        result = discounted_cash_flow(
            context,
            base_inputs(
                revenue_growth=flat("revenue_growth", "0.04", years=10),
                ebit_margin=flat("ebit_margin", "0.20", years=10),
                capex_intensity=flat("capex_intensity", "0.08", years=10),
                depreciation_intensity=flat("depreciation_intensity", "0.05", years=10),
                working_capital_intensity=flat("working_capital_intensity", "0.10", years=10),
                wacc=rate("0.14"),
                terminal_growth=rate("0.01"),
                exit_multiple=rate("4.5"),
            ),
            mandate=MANDATE,
        )

        assert result.caveats == ()


# -- Refusals --------------------------------------------------------------------------------


class TestItRefusesRatherThanProducingAVastNumber:
    def test_terminal_growth_equal_to_the_discount_rate(self, context):
        with pytest.raises(CalculationError, match="unbounded"):
            discounted_cash_flow(
                context, base_inputs(terminal_growth=rate("0.10")), mandate=MANDATE
            )

    def test_terminal_growth_above_the_discount_rate(self, context):
        with pytest.raises(CalculationError, match="perpetuity denominator"):
            discounted_cash_flow(
                context, base_inputs(terminal_growth=rate("0.12")), mandate=MANDATE
            )

    def test_a_perpetuity_of_a_negative_cash_flow(self, context):
        with pytest.raises(CalculationError, match="Growing a"):
            gordon_terminal_value(
                context,
                final_cash_flow=usd("-40"),
                wacc=rate("0.10"),
                terminal_growth=rate("0.02"),
            )

    def test_an_exit_multiple_on_negative_ebitda(self, context):
        with pytest.raises(CalculationError, match="negative price"):
            exit_multiple_terminal_value(context, terminal_ebitda=usd("-100"), multiple=rate("8"))

    def test_a_nil_exit_multiple(self, context):
        with pytest.raises(CalculationError, match="not a multiple"):
            exit_multiple_terminal_value(context, terminal_ebitda=usd("100"), multiple=rate("0"))

    def test_a_per_share_value_with_no_shares(self, context):
        with pytest.raises(CalculationError, match="needs shares"):
            value_per_share(context, equity_value=usd("1000"), shares=shares("0"))

    def test_a_share_count_that_is_not_in_shares(self, context):
        with pytest.raises(UnitMismatchError, match="not shares"):
            value_per_share(context, equity_value=usd("1000"), shares=usd("100"))

    def test_a_discount_rate_that_was_never_converted_from_per_cent(self, context):
        with pytest.raises(CalculationError, match="rate_from_percent"):
            discount_factor(context, wacc=rate("10"), year=1)

    def test_discounting_starts_at_year_one(self, context):
        with pytest.raises(CalculationError, match="year one"):
            discount_factor(context, wacc=rate("0.10"), year=0)

    def test_a_growth_rate_entered_as_a_percentage(self, context):
        with pytest.raises(CalculationError, match="never divided by a hundred"):
            projected_revenue(context, prior_revenue=usd("1000"), growth=rate("10"))

    def test_a_margin_above_one_hundred_per_cent(self, context):
        with pytest.raises(CalculationError, match="arithmetically impossible"):
            projected_ebit(context, revenue=usd("1000"), margin=rate("1.5"))

    def test_negative_capex_belongs_in_the_bridge(self, context):
        with pytest.raises(CalculationError, match="disposal"):
            projected_capex(context, revenue=usd("1000"), intensity=rate("-0.05"))

    def test_negative_working_capital_intensity_is_allowed(self, context):
        """A supermarket runs one. Growth releases cash rather than consuming it."""
        result = projected_working_capital(context, revenue=usd("1000"), intensity=rate("-0.05"))
        assert result.value == Decimal("-50")

    def test_drivers_covering_different_numbers_of_years(self, context):
        with pytest.raises(CalculationError, match="different numbers of years"):
            project(
                context,
                base_inputs(ebit_margin=flat("ebit_margin", "0.20", years=4)),
                mandate=MANDATE,
            )

    def test_a_forecast_longer_than_the_ceiling(self, context):
        years = MAX_FORECAST_YEARS + 1
        with pytest.raises(CalculationError, match="explicit forecast is outside"):
            project(
                context,
                base_inputs(
                    revenue_growth=flat("revenue_growth", "0.04", years=years),
                    ebit_margin=flat("ebit_margin", "0.20", years=years),
                    capex_intensity=flat("capex_intensity", "0.08", years=years),
                    depreciation_intensity=flat("depreciation_intensity", "0.05", years=years),
                    working_capital_intensity=flat(
                        "working_capital_intensity", "0.10", years=years
                    ),
                ),
                mandate=MANDATE,
            )

    def test_a_driver_path_with_no_values(self):
        with pytest.raises(CalculationError, match="no values"):
            DriverPath("revenue_growth", ())

    def test_an_enterprise_value_with_no_forecast_years(self, context):
        with pytest.raises(CalculationError, match="terminal value wearing"):
            enterprise_value(context, discounted_flows=[], discounted_terminal_value=usd("1000"))

    def test_an_unsourced_driver(self, context):
        with pytest.raises(CalculationError, match="no source"):
            projected_revenue(
                context, prior_revenue=usd("1000"), growth=Quantity.of(Decimal("0.1"))
            )

    def test_a_cash_flow_with_no_currency(self, context):
        with pytest.raises(UnitMismatchError, match="not a currency amount"):
            projected_revenue(context, prior_revenue=rate("1000"), growth=rate("0.1"))


# -- The bridge to equity --------------------------------------------------------------------


class TestTheBridgeToEquityValue:
    def test_non_operating_items_are_signed_and_named(self, context):
        # Deliberately not netting to nil. Items that cancel would let a bridge that ignored
        # them entirely produce the right answer, which is how a test passes while the code
        # it covers does nothing.
        adjusted = discounted_cash_flow(
            context,
            base_inputs(
                non_operating=(
                    BridgeItem("Associates at carrying value", usd("150")),
                    BridgeItem("Pension deficit, net of tax", usd("-90")),
                    BridgeItem("Minority interests", usd("-25")),
                )
            ),
            mandate=MANDATE,
        )
        plain = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        # 1,708.909... - 500 + 150 - 90 - 25
        assert close(adjusted.gordon.equity_value, "1243.90909091")
        assert adjusted.gordon.equity_value.value != plain.gordon.equity_value.value

    def test_a_single_positive_adjustment_raises_the_equity_value(self, context):
        plain = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)
        adjusted = discounted_cash_flow(
            context,
            base_inputs(non_operating=(BridgeItem("Listed investments", usd("300")),)),
            mandate=MANDATE,
        )

        assert adjusted.gordon.equity_value.value - plain.gordon.equity_value.value == Decimal(
            "300"
        )

    def test_a_single_negative_adjustment_lowers_it(self, context):
        plain = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)
        adjusted = discounted_cash_flow(
            context,
            base_inputs(non_operating=(BridgeItem("Minority interests", usd("-200")),)),
            mandate=MANDATE,
        )

        assert adjusted.gordon.equity_value.value - plain.gordon.equity_value.value == Decimal(
            "-200"
        )

    def test_enterprise_value_less_net_debt_is_equity_value(self, context):
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        # Checked in the calculation context. Python's default 28 digits would round the
        # 34-digit enterprise value while subtracting, and the identity would fail on the
        # arithmetic doing the checking rather than on the arithmetic under test.
        with localcontext(CALC_CONTEXT):
            expected = result.gordon.enterprise_value.value - Decimal("500")
        assert result.gordon.equity_value.value == expected

    def test_the_bridge_is_one_recorded_calculation_per_method(self, context):
        discounted_cash_flow(context, base_inputs(), mandate=MANDATE)
        assert len(context.named("equity_value")) == 2


# -- Provenance ------------------------------------------------------------------------------


class TestProvenance:
    def test_every_line_of_every_year_is_recorded(self, context):
        discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        for name in (
            "projected_revenue",
            "projected_ebit",
            "nopat_from_ebit",
            "projected_depreciation",
            "projected_capex",
            "projected_working_capital",
            "change_in_working_capital",
            "forecast_ebitda",
            "free_cash_flow",
            "discount_factor",
        ):
            assert len(context.named(name)) == 3, name

    def test_the_per_share_figure_traces_back_to_a_driver(self, context):
        """The acceptance criterion: complete lineage to a fact or a confirmed assumption."""
        result = discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        seen: set[str] = set()
        frontier = [result.gordon.value_per_share.source]
        leaves: set[SourceKind] = set()

        while frontier:
            source = frontier.pop()
            assert source is not None
            if source.kind is not SourceKind.CALCULATION:
                leaves.add(source.kind)
                continue
            if source.identifier in seen:
                continue
            seen.add(source.identifier)
            record = context.find(source.identifier)
            assert record is not None, source.identifier
            frontier.extend(record.input_sources)

        assert leaves == {SourceKind.FACT, SourceKind.ASSUMPTION}

    def test_each_forecast_year_is_recorded_as_its_own_input_to_the_enterprise_value(self, context):
        """A total nobody can decompose is a total nobody can check."""
        discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        record = context.named("enterprise_value")[0]
        names = [entry.name for entry in record.inputs]
        assert names == [
            "discounted_flows[0]",
            "discounted_flows[1]",
            "discounted_flows[2]",
            "discounted_terminal_value",
        ]

    def test_the_discounting_convention_is_on_the_record(self, context):
        """End-of-year rather than mid-year is a choice worth a quarter of a year's rate."""
        discounted_cash_flow(context, base_inputs(), mandate=MANDATE)

        (record, *_) = context.named("discount_factor")
        assert any("end of each year" in note for note in record.assumptions)
        assert record.parameters["year"] == 1


# -- The sensitivity grid --------------------------------------------------------------------


class TestTheSensitivityGrid:
    def test_every_cell_is_a_complete_valuation(self, context):
        grid = sensitivity_grid(
            context,
            base_inputs(),
            rows=GridAxis("wacc", (rate("0.09"), rate("0.10"), rate("0.11"))),
            columns=GridAxis("terminal_growth", (rate("0.01"), rate("0.02"), rate("0.03"))),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=MANDATE,
        )

        assert len(grid.cells) == 9
        # Nine complete forecasts, not one forecast and eight interpolations.
        assert len(context.named("free_cash_flow")) == 27

    def test_the_base_case_cell_matches_the_base_case(self, context):
        grid = sensitivity_grid(
            context,
            base_inputs(),
            rows=GridAxis("wacc", (rate("0.09"), rate("0.10"))),
            columns=GridAxis("terminal_growth", (rate("0.02"), rate("0.03"))),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=MANDATE,
        )

        centre = next(
            cell
            for cell in grid.cells
            if cell.row_value.value == Decimal("0.10")
            and cell.column_value.value == Decimal("0.02")
        )
        assert close(centre.result, "12.08909091")

    def test_every_cell_names_the_calculation_that_produced_it(self, context):
        grid = sensitivity_grid(
            context,
            base_inputs(),
            rows=GridAxis("wacc", (rate("0.09"), rate("0.11"))),
            columns=GridAxis("exit_multiple", (rate("8"), rate("12"))),
            method=TerminalMethod.EXIT_MULTIPLE,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=MANDATE,
        )

        identifiers = {cell.calculation_id for cell in grid.cells}
        assert len(identifiers) == 4
        for cell in grid.cells:
            assert context.find(cell.calculation_id) is not None

    def test_value_falls_across_the_discount_rate_axis(self, context):
        grid = sensitivity_grid(
            context,
            base_inputs(),
            rows=GridAxis("wacc", (rate("0.09"), rate("0.10"), rate("0.11"))),
            columns=GridAxis("terminal_growth", (rate("0.01"), rate("0.02"))),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=MANDATE,
        )

        for growth in (Decimal("0.01"), Decimal("0.02")):
            column = [cell.result.value for cell in grid.cells if cell.column_value.value == growth]
            # Strictly, not merely weakly. A grid that ignored its axes and repeated the base
            # case in every cell would satisfy a non-strict ordering, and that is exactly the
            # failure a sensitivity grid is worth having tests for.
            assert all(later < earlier for earlier, later in pairwise(column))

    def test_value_rises_across_the_terminal_growth_axis(self, context):
        grid = sensitivity_grid(
            context,
            base_inputs(),
            rows=GridAxis("wacc", (rate("0.09"), rate("0.11"))),
            columns=GridAxis("terminal_growth", (rate("0.01"), rate("0.02"), rate("0.03"))),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=MANDATE,
        )

        for wacc in (Decimal("0.09"), Decimal("0.11")):
            row = [cell.result.value for cell in grid.cells if cell.row_value.value == wacc]
            assert all(later > earlier for earlier, later in pairwise(row))

    def test_no_two_cells_hold_the_same_figure(self, context):
        """Both axes move the answer. A grid varying one of them is a table of one variable."""
        grid = sensitivity_grid(
            context,
            base_inputs(),
            rows=GridAxis("wacc", (rate("0.09"), rate("0.10"), rate("0.11"))),
            columns=GridAxis("terminal_growth", (rate("0.01"), rate("0.02"), rate("0.03"))),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=MANDATE,
        )

        assert len({cell.result.value for cell in grid.cells}) == 9

    def test_the_grid_reports_its_own_shape(self, context):
        grid = sensitivity_grid(
            context,
            base_inputs(),
            rows=GridAxis("wacc", (rate("0.09"), rate("0.11"))),
            columns=GridAxis("terminal_growth", (rate("0.01"), rate("0.02"))),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.ENTERPRISE_VALUE,
            mandate=MANDATE,
        )

        assert grid.output_name == "enterprise_value_gordon_growth"
        assert grid.output_unit == "USD"

    def test_an_axis_against_itself_is_refused(self, context):
        with pytest.raises(CalculationError, match="Only the diagonal"):
            sensitivity_grid(
                context,
                base_inputs(),
                rows=GridAxis("wacc", (rate("0.09"), rate("0.11"))),
                columns=GridAxis("wacc", (rate("0.09"), rate("0.11"))),
                method=TerminalMethod.GORDON_GROWTH,
                measure=GridMeasure.VALUE_PER_SHARE,
                mandate=MANDATE,
            )

    def test_an_axis_over_something_that_is_not_a_scalar_is_refused(self):
        with pytest.raises(CalculationError, match="not an input a sensitivity grid may vary"):
            GridAxis("revenue_growth", (rate("0.05"), rate("0.06")))

    def test_a_single_valued_axis_is_not_a_sensitivity(self):
        with pytest.raises(CalculationError, match="not a sensitivity"):
            GridAxis("wacc", (rate("0.10"),))

    def test_an_axis_beyond_the_ceiling_is_refused(self):
        with pytest.raises(CalculationError, match="complete valuation"):
            GridAxis("wacc", tuple(rate(f"0.{n:02d}") for n in range(5, 20)))


# -- Properties ------------------------------------------------------------------------------
#
# The invariants `docs/PLAN.md` names for a discounted cash flow, bounded to inputs where the
# final year's free cash flow stays positive — outside that the Gordon terminal value refuses,
# which the refusal tests above cover and which hypothesis would otherwise spend its budget
# rediscovering.

waccs = st.decimals(min_value=Decimal("0.05"), max_value=Decimal("0.20"), places=4)
terminal_growths = st.decimals(min_value=Decimal(0), max_value=Decimal("0.03"), places=4)
margins = st.decimals(min_value=Decimal("0.20"), max_value=Decimal("0.40"), places=4)
growths = st.decimals(min_value=Decimal(0), max_value=Decimal("0.15"), places=4)
capex_intensities = st.decimals(min_value=Decimal("0.02"), max_value=Decimal("0.10"), places=4)
nwc_intensities = st.decimals(min_value=Decimal(0), max_value=Decimal("0.20"), places=4)
revenues = st.decimals(min_value=Decimal(100), max_value=Decimal("1e6"), places=2)
multiples = st.decimals(min_value=Decimal(4), max_value=Decimal(20), places=2)
debts = st.decimals(min_value=Decimal(0), max_value=Decimal("1e5"), places=2)


def built(
    *,
    revenue=Decimal(1000),
    growth=Decimal("0.05"),
    margin=Decimal("0.25"),
    capex=Decimal("0.06"),
    nwc=Decimal("0.10"),
    wacc=Decimal("0.10"),
    terminal_growth=Decimal("0.02"),
    multiple=Decimal(10),
    net_debt=Decimal(500),
    years=3,
) -> DcfInputs:
    """An input set from scalars, with opening working capital consistent with the intensity.

    Consistent because year one's working-capital movement would otherwise be the whole
    balance rather than the change in it, which can drive the first year's cash flow negative
    for reasons that have nothing to do with the property under test.
    """
    return DcfInputs(
        base_revenue=usd(str(revenue)),
        revenue_growth=flat("revenue_growth", str(growth), years=years),
        ebit_margin=flat("ebit_margin", str(margin), years=years),
        capex_intensity=flat("capex_intensity", str(capex), years=years),
        depreciation_intensity=flat("depreciation_intensity", "0.05", years=years),
        working_capital_intensity=flat("working_capital_intensity", str(nwc), years=years),
        opening_working_capital=usd(str(revenue * nwc)),
        tax_rate=rate("0.25"),
        wacc=rate(str(wacc)),
        terminal_growth=rate(str(terminal_growth)),
        exit_multiple=rate(str(multiple)),
        net_debt=usd(str(net_debt)),
        shares_outstanding=shares("100"),
        non_operating=(),
    )


class TestProperties:
    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        wacc=waccs,
        increase=st.decimals(min_value=Decimal("0.005"), max_value=Decimal("0.05"), places=4),
        margin=margins,
        growth=growths,
        capex=capex_intensities,
        multiple=multiples,
    )
    def test_enterprise_value_falls_as_the_discount_rate_rises(
        self, context, wacc, increase, margin, growth, capex, multiple
    ):
        assume(wacc + increase <= Decimal("0.30"))
        common = {"margin": margin, "growth": growth, "capex": capex, "multiple": multiple}

        lower = discounted_cash_flow(context, built(wacc=wacc, **common), mandate=MANDATE)
        higher = discounted_cash_flow(
            context, built(wacc=wacc + increase, **common), mandate=MANDATE
        )

        for method in TerminalMethod:
            assert (
                higher.outcome(method).enterprise_value.value
                < lower.outcome(method).enterprise_value.value
            )

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        margin=margins,
        increase=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.20"), places=4),
        wacc=waccs,
        growth=growths,
        capex=capex_intensities,
    )
    def test_enterprise_value_rises_with_margin(
        self, context, margin, increase, wacc, growth, capex
    ):
        assume(margin + increase <= Decimal("0.60"))
        common = {"wacc": wacc, "growth": growth, "capex": capex}

        lower = discounted_cash_flow(context, built(margin=margin, **common), mandate=MANDATE)
        higher = discounted_cash_flow(
            context, built(margin=margin + increase, **common), mandate=MANDATE
        )

        for method in TerminalMethod:
            assert (
                higher.outcome(method).enterprise_value.value
                > lower.outcome(method).enterprise_value.value
            )

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        terminal_growth=terminal_growths,
        increase=st.decimals(min_value=Decimal("0.001"), max_value=Decimal("0.01"), places=4),
        wacc=waccs,
        margin=margins,
    )
    def test_enterprise_value_rises_with_terminal_growth(
        self, context, terminal_growth, increase, wacc, margin
    ):
        common = {"wacc": wacc, "margin": margin}

        lower = discounted_cash_flow(
            context, built(terminal_growth=terminal_growth, **common), mandate=MANDATE
        )
        higher = discounted_cash_flow(
            context, built(terminal_growth=terminal_growth + increase, **common), mandate=MANDATE
        )

        assert higher.gordon.enterprise_value.value > lower.gordon.enterprise_value.value
        # The exit multiple does not know about perpetual growth, and must not move.
        assert (
            higher.exit_multiple.enterprise_value.value
            == lower.exit_multiple.enterprise_value.value
        )

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        revenue=revenues,
        scale=st.decimals(min_value=Decimal("0.1"), max_value=Decimal(100), places=2),
        wacc=waccs,
        margin=margins,
        capex=capex_intensities,
        nwc=nwc_intensities,
    )
    def test_scaling_every_cash_flow_scales_enterprise_value(
        self, context, revenue, scale, wacc, margin, capex, nwc
    ):
        """Homogeneity of degree one. A valuation in thousands is the same valuation."""
        common = {"wacc": wacc, "margin": margin, "capex": capex, "nwc": nwc}

        plain = discounted_cash_flow(context, built(revenue=revenue, **common), mandate=MANDATE)
        scaled = discounted_cash_flow(
            context, built(revenue=revenue * scale, **common), mandate=MANDATE
        )

        for method in TerminalMethod:
            expected = plain.outcome(method).enterprise_value.value * scale
            actual = scaled.outcome(method).enterprise_value.value
            assert abs(actual - expected) <= abs(expected) * Decimal("1e-25")

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(wacc=waccs, margin=margins, net_debt=debts, capex=capex_intensities)
    def test_enterprise_value_less_net_debt_is_equity_value(
        self, context, wacc, margin, net_debt, capex
    ):
        result = discounted_cash_flow(
            context,
            built(wacc=wacc, margin=margin, net_debt=net_debt, capex=capex),
            mandate=MANDATE,
        )

        # In the calculation context, not Python's 28-digit default: subtracting even zero
        # under the narrower context rounds a 34-digit enterprise value and the identity
        # fails on the arithmetic used to check it rather than on the arithmetic under test.
        with localcontext(CALC_CONTEXT):
            for method in TerminalMethod:
                outcome = result.outcome(method)
                assert outcome.equity_value.value == outcome.enterprise_value.value - net_debt

    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        margin=st.decimals(min_value=Decimal("0.22"), max_value=Decimal("0.35"), places=4),
        wacc=st.decimals(min_value=Decimal("0.08"), max_value=Decimal("0.16"), places=4),
        terminal_growth=st.decimals(
            min_value=Decimal("0.005"), max_value=Decimal("0.02"), places=4
        ),
    )
    def test_bear_is_never_worth_more_than_base_is_never_worth_more_than_bull(
        self, context, margin, wacc, terminal_growth
    ):
        """Three cases differing only in directions where the ordering actually holds.

        Not revenue growth: enterprise value is *not* monotone in it. Where capital intensity
        exceeds the operating margin, faster growth consumes more cash than it produces and a
        bull case built by raising growth alone is worth less than the base. That is the
        correct answer and it is why this property is stated over margin, the discount rate
        and terminal growth instead.
        """
        bear = built(
            margin=margin - Decimal("0.02"),
            wacc=wacc + Decimal("0.02"),
            terminal_growth=terminal_growth - Decimal("0.005"),
        )
        base = built(margin=margin, wacc=wacc, terminal_growth=terminal_growth)
        bull = built(
            margin=margin + Decimal("0.02"),
            wacc=wacc - Decimal("0.02"),
            terminal_growth=terminal_growth + Decimal("0.005"),
        )

        values = [
            discounted_cash_flow(context, case, mandate=MANDATE).gordon.value_per_share.value
            for case in (bear, base, bull)
        ]
        assert values == sorted(values)

    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(wacc=waccs, margin=margins, multiple=multiples)
    def test_the_terminal_share_is_always_a_share(self, context, wacc, margin, multiple):
        result = discounted_cash_flow(
            context, built(wacc=wacc, margin=margin, multiple=multiple), mandate=MANDATE
        )

        for method in TerminalMethod:
            share = result.outcome(method).terminal_share.value
            assert 0 < share < 1
