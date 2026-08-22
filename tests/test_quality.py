"""Earnings quality: the questions that get asked, and the ones this module admits it cannot.

The fixture is a company whose profit and cash have visibly parted company — net income 200
against operating cash flow of 60 — because a suite that is only tested on healthy filings
never finds out whether it can see an unhealthy one.

The flags are the point, and so is their restraint. Every threshold is a judgement, and a
test that asserted "this company is bad" would be encoding that judgement twice. What is
asserted here is that a figure past its stated threshold flags and one short of it does not,
which is the only thing the code actually claims.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from aer.calc import quality as quality_module
from aer.calc.engine import CalculationContext
from aer.calc.quality import (
    ACCRUALS_CONCERN,
    CASH_CONVERSION_CONCERN,
    QUALITY_DEFINITIONS,
    UNAVAILABLE_SIGNALS,
    Direction,
    accruals_ratio,
    assess_quality,
    capex_to_depreciation,
    cash_conversion,
    depreciation_rate,
    interest_capitalisation_gap,
    rate_change,
    working_capital_intensity,
)
from aer.calc.statements import assemble
from aer.calc.units import (
    CALC_CONTEXT,
    CalculationError,
    Quantity,
    SourceRef,
    UnitMismatchError,
    money,
)
from aer.core.concepts import CANONICAL_CONCEPTS

SOURCE = SourceRef.fact("fact-1")


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def usd(value: str) -> Quantity:
    return money(value, "USD", source=SOURCE)


def a_third_off() -> Decimal:
    """A third, at the precision the calculation context uses.

    Python's default 28 digits and the calculation context's 34 produce different
    Decimals for the same division, and comparing across the two fails on the rounding
    rather than on the arithmetic.
    """
    with localcontext(CALC_CONTEXT):
        return Decimal("-1") / Decimal(3)


# A company reporting a profit its cash flow does not support:
#
#   net income 200, operating cash flow 60, assets 2000 -> accruals ratio 7%
#   cash conversion 60 / 200 = 0.3
#   AR 300 + inventory 200 - AP 100 = 400 over revenue 1000 -> intensity 40%
#   capex 40 against D&A 100 -> capex cover 0.4
#   interest paid 60 against interest expense 50 -> gap 20%
#   D&A 100 against PP&E 1000 -> depreciation rate 10%
STRAINED = {
    "revenue": "1000",
    "cost_of_revenue": "600",
    "net_income": "200",
    "operating_cash_flow": "60",
    "assets": "2000",
    "accounts_receivable": "300",
    "inventory": "200",
    "accounts_payable": "100",
    "capital_expenditure": "40",
    "depreciation_and_amortisation": "100",
    "property_plant_and_equipment": "1000",
    "interest_paid": "60",
    "interest_expense": "50",
}

# The same company a year earlier, healthier on every count that has a prior comparison:
#
#   D&A 150 against PP&E 1000 -> depreciation rate 15%, so the rate fell by a third
#   AR 200 + inventory 150 - AP 150 = 200 over revenue 1000 -> intensity 20%, so drift +20pts
PRIOR = {
    **STRAINED,
    "depreciation_and_amortisation": "150",
    "accounts_receivable": "200",
    "inventory": "150",
    "accounts_payable": "150",
}


def facts(source: dict[str, str], **overrides: str) -> dict[str, Quantity]:
    return {concept: usd(value) for concept, value in {**source, **overrides}.items()}


def signals(context: CalculationContext, values: dict[str, Quantity], **kwargs: object) -> dict:
    prior = kwargs.get("prior")
    assessed = assess_quality(
        context,
        assemble(context, values),
        prior=assemble(context, facts(prior)) if isinstance(prior, dict) else None,
    )
    return {signal.key: signal for signal in assessed}


class TestTheTableItself:
    def test_every_signal_needs_only_canonical_concepts(self):
        for definition in QUALITY_DEFINITIONS:
            for concept in definition.needs:
                assert concept in CANONICAL_CONCEPTS, f"{definition.key}: {concept}"

    def test_every_signal_states_what_it_is_asking(self):
        """A number labelled "accruals ratio: 0.07" tells a reader nothing they can use."""
        for definition in QUALITY_DEFINITIONS:
            assert definition.question.endswith("?"), definition.key

    def test_the_da_labels_say_what_the_ratio_measures(self):
        """Gap R16: the numerator is all D&A — intangible amortisation included — over
        net PP&E, so an asset-light company legitimately shows 0.65 to 0.88. A label
        promising a fixed-asset "depreciation rate" made a defensible figure read as
        alarming; the label now names the ratio it actually is, and the stored key stays
        so the ledger's history remains comparable."""
        labels = {d.key: d.label for d in (*QUALITY_DEFINITIONS, *quality_module._PAIRED)}
        assert labels["depreciation_rate"] == "D&A to net PP&E"
        assert labels["depreciation_rate_change"] == "Change in D&A to net PP&E"
        assert "Depreciation rate" not in labels.values()

    def test_every_key_is_unique(self):
        assessed = assess_quality(CalculationContext(code_version="t"), assemble_empty())
        keys = [signal.key for signal in assessed]
        assert len(keys) == len(set(keys))

    def test_what_cannot_be_derived_is_listed_rather_than_omitted(self):
        """ "We checked and it was fine" must be distinguishable from "we never looked"."""
        keys = {signal.key for signal in UNAVAILABLE_SIGNALS}
        assert "rd_capitalisation" in keys

    def test_each_unavailable_signal_says_where_to_look_instead(self):
        for signal in UNAVAILABLE_SIGNALS:
            assert signal.why
            assert signal.where_to_look


def assemble_empty():
    return assemble(CalculationContext(code_version="t"), {})


class TestKnownAnswers:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("accruals_ratio", "0.07"),
            ("cash_conversion", "0.3"),
            ("working_capital_intensity", "0.4"),
            ("capex_to_depreciation", "0.4"),
            ("interest_capitalisation_gap", "0.2"),
            ("depreciation_rate", "0.1"),
        ],
    )
    def test_it_computes_the_figure_worked_out_by_hand(self, context, key, expected):
        result = signals(context, facts(STRAINED))[key]
        assert result.present, result.absent_because
        assert result.value == Decimal(expected)

    def test_the_depreciation_rate_change_is_the_proportional_fall(self, context):
        """15% to 10% is a third off the rate, not five points off it."""
        result = signals(context, facts(STRAINED), prior=PRIOR)["depreciation_rate_change"]
        assert result.present
        assert result.value == a_third_off()

    def test_the_working_capital_drift_is_the_movement_in_the_level(self, context):
        result = signals(context, facts(STRAINED), prior=PRIOR)["working_capital_drift"]
        assert result.present
        assert result.value == Decimal("0.2")


class TestTheFlags:
    def test_a_figure_past_its_threshold_flags(self, context):
        result = signals(context, facts(STRAINED))["accruals_ratio"]
        assert result.value > ACCRUALS_CONCERN or result.flagged is False
        # 7% is inside the 10% threshold, so this company does not flag on accruals.
        assert result.value == Decimal("0.07")
        assert not result.flagged

    def test_accruals_above_the_threshold_do_flag(self, context):
        values = facts(STRAINED, operating_cash_flow="-100")
        result = signals(context, values)["accruals_ratio"]
        assert result.value > ACCRUALS_CONCERN
        assert result.flagged

    def test_cash_conversion_below_one_flags(self, context):
        result = signals(context, facts(STRAINED))["cash_conversion"]
        assert result.value < CASH_CONVERSION_CONCERN
        assert result.flagged

    def test_cash_conversion_above_one_does_not(self, context):
        values = facts(STRAINED, operating_cash_flow="260")
        result = signals(context, values)["cash_conversion"]
        assert not result.flagged

    def test_capex_below_the_depreciation_charge_flags(self, context):
        assert signals(context, facts(STRAINED))["capex_to_depreciation"].flagged

    def test_capex_above_it_does_not(self, context):
        values = facts(STRAINED, capital_expenditure="150")
        assert not signals(context, values)["capex_to_depreciation"].flagged

    def test_cash_interest_running_well_above_the_charge_flags(self, context):
        assert signals(context, facts(STRAINED))["interest_capitalisation_gap"].flagged

    def test_cash_interest_matching_the_charge_does_not(self, context):
        values = facts(STRAINED, interest_paid="50")
        assert not signals(context, values)["interest_capitalisation_gap"].flagged

    def test_a_falling_depreciation_rate_flags(self, context):
        """The observable end of a useful-life extension."""
        assert signals(context, facts(STRAINED), prior=PRIOR)["depreciation_rate_change"].flagged

    def test_a_steady_depreciation_rate_does_not(self, context):
        result = signals(context, facts(STRAINED), prior=STRAINED)["depreciation_rate_change"]
        assert result.value == 0
        assert not result.flagged

    def test_an_absent_signal_never_flags_and_that_is_not_a_pass(self, context):
        """Flagged is false for an absent signal. A caller must read `present` first."""
        result = signals(context, {"revenue": usd("1000")})["accruals_ratio"]
        assert not result.present
        assert not result.flagged

    def test_the_direction_says_which_way_is_concerning(self):
        directions = {d.key: d.direction for d in QUALITY_DEFINITIONS}
        assert directions["accruals_ratio"] is Direction.HIGHER_IS_CONCERNING
        assert directions["cash_conversion"] is Direction.LOWER_IS_CONCERNING


class TestAnAbsentInputGivesAnAbsentSignal:
    def test_a_missing_concept_produces_no_value(self, context):
        values = {c: q for c, q in facts(STRAINED).items() if c != "operating_cash_flow"}
        result = signals(context, values)["cash_conversion"]
        assert not result.present
        assert result.missing == ("operating_cash_flow",)

    def test_other_signals_are_unaffected(self, context):
        values = {c: q for c, q in facts(STRAINED).items() if c != "operating_cash_flow"}
        assert signals(context, values)["capex_to_depreciation"].present

    def test_nothing_silently_becomes_zero(self, context):
        assessed = assess_quality(context, assemble(context, {}))
        assert all(signal.value is None for signal in assessed)

    def test_the_suite_returns_one_row_per_signal_always(self, context):
        assessed = assess_quality(context, assemble(context, {}))
        assert len(assessed) == len(QUALITY_DEFINITIONS) + 2


class TestASignalNeedingTwoPeriodsSaysSo:
    def test_without_a_prior_period_the_movement_is_absent(self, context):
        result = signals(context, facts(STRAINED))["depreciation_rate_change"]
        assert not result.present
        assert "only one period" in result.absent_because

    def test_the_level_is_still_reported(self, context):
        """A depreciation rate is a number; a falling one is the question. Both are shown."""
        assessed = signals(context, facts(STRAINED))
        assert assessed["depreciation_rate"].present
        assert not assessed["depreciation_rate_change"].present

    def test_a_concept_missing_from_either_period_makes_the_movement_absent(self, context):
        prior = {c: v for c, v in PRIOR.items() if c != "property_plant_and_equipment"}
        result = signals(context, facts(STRAINED), prior=prior)["depreciation_rate_change"]
        assert not result.present
        assert "property_plant_and_equipment" in result.missing

    def test_an_undefined_level_in_one_period_makes_the_movement_absent(self, context):
        prior = {**PRIOR, "property_plant_and_equipment": "0"}
        result = signals(context, facts(STRAINED), prior=prior)["depreciation_rate_change"]
        assert not result.present
        assert "undefined" in result.absent_because


class TestAnUndefinedSignalIsAbsentWithTheGuardsReason:
    def test_cash_conversion_against_a_loss_is_refused(self, context):
        """It inverts: a loss-making, cash-burning company scores positively."""
        values = facts(STRAINED, net_income="-200", operating_cash_flow="-100")
        result = signals(context, values)["cash_conversion"]
        assert not result.present
        assert "inverts its sign" in result.absent_because

    def test_capex_cover_with_no_depreciation_charge_is_refused(self, context):
        result = signals(context, facts(STRAINED, depreciation_and_amortisation="0"))
        assert not result["capex_to_depreciation"].present

    def test_an_interest_gap_with_no_interest_charge_is_refused(self, context):
        result = signals(context, facts(STRAINED, interest_expense="0"))
        assert not result["interest_capitalisation_gap"].present

    def test_an_accruals_ratio_with_no_asset_base_is_refused(self, context):
        result = signals(context, facts(STRAINED, assets="0"))
        assert not result["accruals_ratio"].present

    def test_a_negative_accruals_ratio_is_an_answer_not_a_refusal(self, context):
        """Cash ahead of profit is a good thing and a real number."""
        result = signals(context, facts(STRAINED, operating_cash_flow="400"))["accruals_ratio"]
        assert result.present
        assert result.value < 0


class TestAMappingErrorIsNeverSwallowed:
    def test_two_currencies_in_one_signal_raise(self, context):
        values = facts(STRAINED)
        values["operating_cash_flow"] = money("60", "GBP", source=SOURCE)

        with pytest.raises(UnitMismatchError):
            assess_quality(context, assemble(context, values))

    def test_a_mismatch_between_periods_raises(self, context):
        prior = facts(PRIOR)
        prior["property_plant_and_equipment"] = money("1000", "GBP", source=SOURCE)

        with pytest.raises(UnitMismatchError):
            assess_quality(
                context, assemble(context, facts(STRAINED)), prior=assemble(context, prior)
            )


class TestItAllTracesToFacts:
    def test_every_computed_signal_is_a_recorded_calculation(self, context):
        assessed = assess_quality(context, assemble(context, facts(STRAINED)))

        for signal in assessed:
            if signal.present:
                assert signal.quantity.source is not None
                assert signal.quantity.source.kind == "calculation"

    def test_each_signal_records_itself_by_name(self, context):
        assess_quality(context, assemble(context, facts(STRAINED)))
        recorded = {record.name for record in context.records}

        assert "accruals_ratio" in recorded
        assert "cash_conversion" in recorded
        assert "interest_capitalisation_gap" in recorded

    def test_the_movement_is_a_recorded_calculation_not_an_inline_subtraction(self, context):
        assess_quality(
            context,
            assemble(context, facts(STRAINED)),
            prior=assemble(context, facts(PRIOR)),
        )
        recorded = {record.name for record in context.records}
        assert "level_change" in recorded
        assert "rate_change" in recorded

    def test_the_accruals_ratio_names_its_scaling_choice(self, context):
        """A vendor scaling by average net operating assets reports a different number."""
        assess_quality(context, assemble(context, facts(STRAINED)))
        record = next(r for r in context.records if r.name == "accruals_ratio")
        assert any("net operating assets" in a for a in record.assumptions)

    def test_capex_cover_names_the_sign_convention_it_relies_on(self, context):
        assess_quality(context, assemble(context, facts(STRAINED)))
        record = next(r for r in context.records if r.name == "capex_to_depreciation")
        assert any("positive magnitude" in a for a in record.assumptions)


class TestThePrimitives:
    def test_accruals_are_profit_that_did_not_arrive_as_cash(self, context):
        result = accruals_ratio(
            context,
            net_income=usd("200"),
            operating_cash_flow=usd("60"),
            assets=usd("2000"),
        )
        assert result.value == Decimal("0.07")

    def test_cash_conversion_is_cash_over_profit(self, context):
        result = cash_conversion(context, operating_cash_flow=usd("60"), net_income=usd("200"))
        assert result.value == Decimal("0.3")

    def test_working_capital_intensity_can_be_negative(self, context):
        """A supermarket is funded by its suppliers. That is the business, not a problem."""
        result = working_capital_intensity(
            context,
            accounts_receivable=usd("10"),
            inventory=usd("100"),
            accounts_payable=usd("400"),
            revenue=usd("1000"),
        )
        assert result.value < 0

    def test_capex_cover_uses_the_magnitude_convention(self, context):
        """Capex is a payment reported positive, so cover is a positive-over-positive ratio."""
        result = capex_to_depreciation(
            context, capital_expenditure=usd("40"), depreciation=usd("100")
        )
        assert result.value == Decimal("0.4")

    def test_the_depreciation_rate_is_the_charge_over_the_base(self, context):
        result = depreciation_rate(
            context, depreciation=usd("100"), property_plant_and_equipment=usd("1000")
        )
        assert result.value == Decimal("0.1")

    def test_the_interest_gap_is_zero_when_cash_matches_the_charge(self, context):
        result = interest_capitalisation_gap(
            context, interest_paid=usd("50"), interest_expense=usd("50")
        )
        assert result.value == 0

    def test_a_rate_change_from_zero_is_undefined(self, context):
        with pytest.raises(CalculationError, match="opening rate is zero"):
            rate_change(context, opening=usd("0"), closing=usd("10"))

    def test_a_rate_change_is_proportional_to_the_opening_level(self, context):
        result = rate_change(context, opening=usd("0.15"), closing=usd("0.10"))
        assert result.value == a_third_off()

    def test_the_denominator_is_the_magnitude_so_a_rise_from_a_negative_base_reads_as_a_rise(
        self, context
    ):
        """The `abs()` in the formula, which nothing exercised.

        Dividing by a signed opening inverts the answer whenever that opening is negative: a
        margin moving from -10% to -5% is an improvement, and the signed form reports it as
        a fall of a half. Today's only caller measures a depreciation rate, which cannot be
        negative — so the mutation that removes the `abs()` survives every test. The function
        is general, the formula says `|opening|`, and a signal that reports an improvement as
        a deterioration is the kind of wrong that reads as analysis.
        """
        improving = rate_change(context, opening=usd("-0.10"), closing=usd("-0.05"))
        worsening = rate_change(context, opening=usd("-0.10"), closing=usd("-0.20"))

        assert improving.value == Decimal("0.5")
        assert worsening.value == Decimal(-1)
