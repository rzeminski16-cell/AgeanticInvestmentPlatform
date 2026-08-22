"""The margin bridge: what the decomposition attributes, and what it admits it cannot.

The figures are chosen so the arithmetic is checkable by eye. The opening period runs a 40%
gross margin on revenue of 1000; the closing period runs 35% on revenue of 1200. The five
points did not come from nowhere, and the bridge has to say where.

The residual is the test that matters. A bridge whose components always sum to the movement
proves nothing — it can be made to by construction. What must hold is that a fully specified
decomposition leaves *nothing* over, and an incomplete one leaves exactly the part it could
not attribute.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from aer.calc.bridge import (
    BRIDGE_SPECS,
    BridgeSpec,
    margin_bridge,
    margin_of,
)
from aer.calc.engine import CalculationContext
from aer.calc.statements import assemble
from aer.calc.units import CALC_CONTEXT, CalculationError, Quantity, SourceRef, money
from aer.core.concepts import CANONICAL_CONCEPTS

SOURCE = SourceRef.financial_fact("fact-1")

GROSS = next(spec for spec in BRIDGE_SPECS if spec.key == "gross_margin")
OPERATING = next(spec for spec in BRIDGE_SPECS if spec.key == "operating_margin")


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def usd(value: str) -> Quantity:
    return money(value, "USD", source=SOURCE)


def statements(context: CalculationContext, **lines: str):
    return assemble(context, {concept: usd(value) for concept, value in lines.items()})


# Revenue 1000, cost 600 -> gross profit 400, margin 40%. Cost is 60% of revenue.
OPENING = {"revenue": "1000", "cost_of_revenue": "600", "gross_profit": "400"}

# Revenue 1200, cost 780 -> gross profit 420, margin 35%. Cost is 65% of revenue.
# The margin fell five points and the whole five points is the cost share rising five points.
CLOSING = {"revenue": "1200", "cost_of_revenue": "780", "gross_profit": "420"}


class TestTheSpecs:
    def test_every_spec_names_only_canonical_concepts(self):
        for spec in BRIDGE_SPECS:
            for concept in (spec.numerator, *spec.drivers):
                assert concept in CANONICAL_CONCEPTS, f"{spec.key}: {concept}"

    def test_the_operating_bridge_excludes_the_aggregate_expense_line(self):
        """`operating_expenses` is the total of SG&A and R&D for most filers.

        Including it alongside them would double-count the same cost and produce components
        that look precise and are wrong by a factor of two.
        """
        assert "operating_expenses" not in OPERATING.drivers
        assert "sg_and_a" in OPERATING.drivers
        assert "research_and_development" in OPERATING.drivers

    def test_no_driver_is_the_numerator_of_its_own_bridge(self):
        for spec in BRIDGE_SPECS:
            assert spec.numerator not in spec.drivers


class TestAFullyAttributedBridge:
    def test_the_margin_moved_by_the_figure_worked_out_by_hand(self, context):
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )

        assert bridge.opening.value == Decimal("0.4")
        assert bridge.closing.value == Decimal("0.35")
        assert bridge.movement.value == Decimal("-0.05")

    def test_the_whole_movement_is_attributed_to_the_cost_line(self, context):
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )

        assert len(bridge.components) == 1
        assert bridge.components[0].concept == "cost_of_revenue"
        assert bridge.components[0].value == Decimal("-0.05")

    def test_nothing_is_left_over(self, context):
        """The property that matters. Zero residual means the drivers really do explain it."""
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        assert bridge.residual.value == 0
        assert bridge.explained == Decimal(1)

    def test_a_component_carries_both_periods_shares(self, context):
        """ "Cost went up" and "revenue went down" produce the same contribution."""
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        component = bridge.components[0]
        assert component.opening_share.value == Decimal("0.6")
        assert component.closing_share.value == Decimal("0.65")

    def test_a_cost_line_taking_more_of_revenue_lowers_the_margin(self, context):
        """The sign. A bridge with every sign flipped still sums to the movement."""
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        assert bridge.components[0].value < 0

    def test_a_cost_line_taking_less_of_revenue_raises_it(self, context):
        improving = {"revenue": "1200", "cost_of_revenue": "660", "gross_profit": "540"}
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **improving),
        )
        assert bridge.movement.value > 0
        assert bridge.components[0].value > 0


class TestTheComponentsAndResidualSumToTheMovement:
    def test_they_do_for_a_fully_attributed_bridge(self, context):
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        with localcontext(CALC_CONTEXT):
            total = sum((c.value for c in bridge.components), Decimal(0)) + bridge.residual.value
        assert total == bridge.movement.value

    def test_they_do_for_a_partly_attributed_one(self, context):
        opening = statements(
            context, revenue="1000", cost_of_revenue="600", sg_and_a="150", operating_income="250"
        )
        closing = statements(
            context, revenue="1200", cost_of_revenue="780", sg_and_a="160", operating_income="260"
        )
        bridge = margin_bridge(context, OPERATING, opening=opening, closing=closing)

        with localcontext(CALC_CONTEXT):
            total = sum((c.value for c in bridge.components), Decimal(0)) + bridge.residual.value
        assert total == bridge.movement.value


class TestAnUnattributedBridgeSaysSo:
    def test_a_filer_reporting_only_an_aggregate_leaves_a_large_residual(self, context):
        """The honest output: the movement happened and this decomposition cannot place it."""
        opening = statements(
            context, revenue="1000", operating_expenses="150", operating_income="250"
        )
        closing = statements(
            context, revenue="1200", operating_expenses="220", operating_income="260"
        )
        bridge = margin_bridge(context, OPERATING, opening=opening, closing=closing)

        assert bridge.components == ()
        assert bridge.residual.value == bridge.movement.value
        assert bridge.explained == 0

    def test_the_drivers_it_could_not_use_are_named(self, context):
        opening = statements(
            context, revenue="1000", operating_expenses="150", operating_income="250"
        )
        closing = statements(
            context, revenue="1200", operating_expenses="220", operating_income="260"
        )
        bridge = margin_bridge(context, OPERATING, opening=opening, closing=closing)

        assert "sg_and_a" in bridge.unattributed
        assert "cost_of_revenue" in bridge.unattributed

    def test_a_driver_present_in_only_one_period_is_unattributed(self, context):
        """A line that appeared this year has no opening share to move from."""
        opening = statements(context, revenue="1000", cost_of_revenue="600", operating_income="250")
        closing = statements(
            context,
            revenue="1200",
            cost_of_revenue="780",
            restructuring_costs="40",
            operating_income="260",
        )
        bridge = margin_bridge(context, OPERATING, opening=opening, closing=closing)

        assert "restructuring_costs" in bridge.unattributed
        assert bridge.residual.value != 0

    def test_a_stated_gross_profit_that_is_not_revenue_less_cost_shows_as_residual(self, context):
        """One driver, so a residual here means the filer's own subtotal does not reconcile."""
        opening = statements(context, revenue="1000", cost_of_revenue="600", gross_profit="400")
        closing = statements(context, revenue="1200", cost_of_revenue="780", gross_profit="390")
        bridge = margin_bridge(context, GROSS, opening=opening, closing=closing)

        assert bridge.residual.value != 0


class TestWhenThereIsNoBridge:
    def test_a_margin_missing_from_one_period_gives_none(self, context):
        """No movement to explain. Inventing one from the period that has it is worse."""
        opening = statements(context, revenue="1000")
        closing = statements(context, **CLOSING)

        assert margin_bridge(context, GROSS, opening=opening, closing=closing) is None

    def test_a_margin_missing_from_both_periods_gives_none(self, context):
        empty = assemble(context, {})
        assert margin_bridge(context, GROSS, opening=empty, closing=empty) is None

    def test_zero_revenue_in_a_period_raises_rather_than_bridging(self, context):
        opening = statements(context, revenue="0", cost_of_revenue="600", gross_profit="-600")
        closing = statements(context, **CLOSING)

        with pytest.raises(CalculationError, match="no line's share"):
            margin_bridge(context, GROSS, opening=opening, closing=closing)

    def test_a_margin_that_did_not_move_explains_nothing(self, context):
        """Zero over zero. Reported as zero rather than raised: there is no question here."""
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **OPENING),
        )
        assert bridge.movement.value == 0
        assert bridge.explained == 0


class TestItAllTracesToFacts:
    def test_each_share_is_a_recorded_calculation(self, context):
        margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        recorded = [record.name for record in context.records]
        assert "margin_of" in recorded
        assert "margin_movement" in recorded

    def test_a_component_resolves_to_a_calculation(self, context):
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        component = bridge.components[0]
        assert component.contribution.source is not None
        assert component.contribution.source.kind == "calculation"

    def test_the_movement_records_the_equal_period_assumption(self, context):
        """A bridge between a half-year and a full year attributes the calendar to lines."""
        margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        record = next(r for r in context.records if r.name == "margin_movement")
        assert any("same length" in assumption for assumption in record.assumptions)

    def test_describe_names_the_lines_and_the_residual(self, context):
        bridge = margin_bridge(
            context,
            GROSS,
            opening=statements(context, **OPENING),
            closing=statements(context, **CLOSING),
        )
        described = bridge.describe()
        assert "cost_of_revenue" in described
        assert "residual" in described


class TestMarginOf:
    def test_it_is_a_share_of_revenue(self, context):
        assert margin_of(context, line=usd("400"), revenue=usd("1000")).value == Decimal("0.4")

    def test_it_is_dimensionless(self, context):
        assert margin_of(context, line=usd("400"), revenue=usd("1000")).unit.is_dimensionless

    @pytest.mark.parametrize("revenue", ["0", "-1000"])
    def test_it_refuses_a_base_that_makes_a_share_meaningless(self, context, revenue):
        with pytest.raises(CalculationError):
            margin_of(context, line=usd("400"), revenue=usd(revenue))


class TestASpecIsData:
    def test_a_new_bridge_is_a_row_rather_than_a_code_change(self, context):
        """The table is the extension point. Nothing in `margin_bridge` names a margin."""
        custom = BridgeSpec(
            key="rd_intensity",
            label="R&D intensity",
            numerator="operating_income",
            drivers=("research_and_development",),
        )
        opening = statements(
            context, revenue="1000", research_and_development="100", operating_income="250"
        )
        closing = statements(
            context, revenue="1200", research_and_development="180", operating_income="260"
        )

        bridge = margin_bridge(context, custom, opening=opening, closing=closing)
        assert bridge is not None
        assert bridge.components[0].concept == "research_and_development"
