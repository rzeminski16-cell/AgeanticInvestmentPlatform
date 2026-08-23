"""What a book is worth, and what it cost — the arithmetic ADR 0083 refused to store.

There is no `positions` table and there will not be one, so every figure a portfolio screen
shows is computed here from transactions. That makes this module the place where a wrong
number costs actual money, and the tests are arranged around the ways one could look
entirely ordinary:

* a **cost basis under the wrong convention** — plausible, right units, 33% out (ADR 0085);
* a **weight over securities alone**, which overstates every holding silently;
* a **holding that went negative** because a disposal was entered before its acquisition;
* a **disposal's dealing costs folded into the pool**, inflating what the remainder cost.

`TestTheConventionIsPooledAndNotFirstIn` is the load-bearing one. The three cost conventions
agree on almost every book and disagree on the one in ADR 0085, which is why that example is
here and also in the golden corpus: two independent chances to notice pooling being replaced.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aer.calc.engine import CalculationContext
from aer.calc.portfolio import (
    SHARES,
    acquisition_cost,
    cash_balance,
    cash_movement,
    dealt_cash_effect,
    holding_value,
    net_assets,
    pooled_cost,
    quantity_held,
    unrealised,
    weight,
)
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
    money,
)

SOURCE = SourceRef.financial_fact("portfolio-test")


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


def shares(value: str) -> Quantity:
    return Quantity.of(Decimal(value), SHARES, source=SOURCE)


def price(value: str, currency: str = "GBP") -> Quantity:
    return Quantity.of(Decimal(value), Unit.currency(currency) / SHARES, source=SOURCE)


def cash(value: str, currency: str = "GBP") -> Quantity:
    return money(Decimal(value), currency, source=SOURCE)


NIL = cash("0")


class TestHowMuchIsHeld:
    def test_it_is_the_signed_sum_of_the_movements(self, context) -> None:
        held = quantity_held(context, movements=[shares("100"), shares("50"), shares("-30")])

        assert held.value == Decimal("120")
        assert held.unit == SHARES

    def test_each_trade_is_its_own_recorded_input(self, context) -> None:
        # "Why does this say 120 shares?" resolves to the list of trades rather than to a
        # total, which is the property a stored column cannot have.
        quantity_held(context, movements=[shares("100"), shares("-30")])

        names = [row.name for row in context.records[0].inputs]

        assert names == ["movements[0]", "movements[1]"]

    def test_a_holding_that_nets_below_zero_is_refused(self, context) -> None:
        """The failure that would price and weight as though it were ordinary.

        A disposal entered before its acquisition gives a negative holding, a negative
        market value and a negative weight — three numbers, none of them obviously wrong.
        """
        with pytest.raises(CalculationError, match="less than nothing held"):
            quantity_held(context, movements=[shares("100"), shares("-140")])

    def test_no_trades_at_all_is_not_a_holding_of_zero(self, context) -> None:
        # "No trades" and "trades that net to nothing" are different states, and only the
        # second is an answer.
        with pytest.raises(CalculationError, match="no movements to add"):
            quantity_held(context, movements=[])

    def test_movements_in_two_units_are_refused(self, context) -> None:
        with pytest.raises(UnitMismatchError):
            quantity_held(context, movements=[shares("100"), cash("100")])


class TestTheConventionIsPooledAndNotFirstIn:
    """ADR 0085's worked example, which is where the three conventions disagree.

    Buy 100 at £10, buy 100 at £20, sell 100. Pooled leaves £1,500; first-in-first-out
    leaves £2,000; specific identification leaves anything between. Nothing in the output
    says which rule made the number, so this is the test that says it.
    """

    def test_the_remaining_hundred_cost_fifteen_hundred(self, context) -> None:
        remaining = pooled_cost(
            context,
            movements=[shares("100"), shares("100"), shares("-100")],
            acquisition_costs=[cash("1000"), cash("2000"), NIL],
        )

        assert remaining.value == Decimal("1500")
        assert remaining.unit == Unit.currency("GBP")

    def test_the_order_of_the_same_three_trades_changes_the_answer(self, context) -> None:
        """Which is why this module walks rather than sums.

        Buy at £10, sell, then buy at £20 disposes of the cheap shares at their own average
        and leaves the expensive ones. A cost basis is a walk through history.
        """
        remaining = pooled_cost(
            context,
            movements=[shares("100"), shares("-100"), shares("100")],
            acquisition_costs=[cash("1000"), NIL, cash("2000")],
        )

        assert remaining.value == Decimal("2000")

    def test_dealing_costs_on_a_purchase_are_part_of_what_it_cost(self, context) -> None:
        bought = acquisition_cost(
            context, quantity=shares("100"), price=price("10"), fees=cash("9.95")
        )

        assert bought.value == Decimal("1009.95")

    def test_a_disposal_carrying_a_cost_is_refused(self, context) -> None:
        """The mistake that inflates a cost basis by the cost of selling something else.

        A sale's dealing costs reduce cash and would reduce a chargeable gain; they are not
        part of what the remaining shares cost.
        """
        with pytest.raises(CalculationError, match="removes cost at the pool's average"):
            pooled_cost(
                context,
                movements=[shares("100"), shares("-50")],
                acquisition_costs=[cash("1000"), cash("9.95")],
            )

    def test_an_acquisition_cost_cannot_be_asked_for_a_disposal(self, context) -> None:
        # The other end of the same rule, refused at the function that would compute it.
        with pytest.raises(CalculationError, match="ADR 0085"):
            acquisition_cost(context, quantity=shares("-100"), price=price("10"), fees=NIL)

    def test_selling_more_than_is_held_is_refused(self, context) -> None:
        with pytest.raises(CalculationError, match="the pool holds"):
            pooled_cost(
                context,
                movements=[shares("100"), shares("-150")],
                acquisition_costs=[cash("1000"), NIL],
            )

    def test_selling_everything_leaves_exactly_nothing(self, context) -> None:
        # Computed as a share of the pool rather than as `average * sold`, so a fully
        # disposed holding returns to zero rather than to a rounding residue that would
        # render as "£0.00 cost" on a position that no longer exists.
        remaining = pooled_cost(
            context,
            movements=[shares("3"), shares("-3")],
            acquisition_costs=[cash("1000"), NIL],
        )

        assert remaining.value == Decimal("0")

    def test_a_mismatched_pairing_is_refused(self, context) -> None:
        with pytest.raises(CalculationError, match="paired by position"):
            pooled_cost(
                context,
                movements=[shares("100"), shares("-50")],
                acquisition_costs=[cash("1000")],
            )

    def test_two_currencies_in_one_pool_are_refused(self, context) -> None:
        # Averaging pounds with dollars needs a conversion date nobody chose.
        with pytest.raises(UnitMismatchError):
            pooled_cost(
                context,
                movements=[shares("100"), shares("100")],
                acquisition_costs=[cash("1000"), cash("1000", "USD")],
            )

    def test_the_formula_records_that_it_is_not_a_tax_computation(self, context) -> None:
        """The claim a surface inherits, asserted where it is written down.

        A pooled average without the same-day rule, the thirty-day rule or share
        reorganisations answers "what did I pay for what I hold" and not "what do I owe".
        """
        pooled_cost(
            context,
            movements=[shares("100")],
            acquisition_costs=[cash("1000")],
        )

        assumptions = " ".join(context.records[0].assumptions)

        assert "Not a tax computation" in assumptions
        assert "thirty-day rule" in assumptions


class TestWhatCashDid:
    def test_a_purchase_takes_the_consideration_and_the_fees(self, context) -> None:
        effect = dealt_cash_effect(
            context, quantity=shares("100"), price=price("10"), fees=cash("9.95")
        )

        assert effect.value == Decimal("-1009.95")

    def test_a_sale_brings_cash_in_and_still_pays_the_fees(self, context) -> None:
        # One function and not two: the sign falls out of the signed quantity, so there is
        # no branch for a caller to get on the wrong side of.
        effect = dealt_cash_effect(
            context, quantity=shares("-100"), price=price("10"), fees=cash("9.95")
        )

        assert effect.value == Decimal("990.05")

    def test_a_deposit_is_itself_and_a_charge_is_negative(self, context) -> None:
        assert cash_movement(context, amount=cash("100"), fees=NIL).value == Decimal("100")
        assert cash_movement(context, amount=cash("-9.95"), fees=NIL).value == Decimal("-9.95")

    def test_a_dividend_net_of_withholding(self, context) -> None:
        received = cash_movement(context, amount=cash("50"), fees=cash("7.50"))

        assert received.value == Decimal("42.50")

    def test_a_balance_is_the_sum_of_the_effects(self, context) -> None:
        balance = cash_balance(context, effects=[cash("1000"), cash("-1009.95"), cash("50")])

        assert balance.value == Decimal("40.05")

    def test_a_balance_across_two_currencies_is_refused(self, context) -> None:
        # A balance in two currencies needs a rate and a date, which makes it a conversion.
        with pytest.raises(UnitMismatchError):
            cash_balance(context, effects=[cash("100"), cash("100", "USD")])

    def test_a_fee_in_another_currency_is_refused(self, context) -> None:
        with pytest.raises(UnitMismatchError):
            dealt_cash_effect(
                context, quantity=shares("100"), price=price("10"), fees=cash("9.95", "USD")
            )


class TestWhatItIsWorth:
    def test_a_holding_is_its_quantity_at_the_mark(self, context) -> None:
        value = holding_value(context, quantity=shares("120"), price=price("12.50"))

        assert value.value == Decimal("1500")
        assert value.unit == Unit.currency("GBP")

    def test_a_total_rather_than_a_per_share_price_is_refused(self, context) -> None:
        # The error that produces a number a hundred million times too large.
        with pytest.raises(UnitMismatchError, match="not a price per share"):
            holding_value(context, quantity=shares("120"), price=cash("12.50"))

    def test_cash_is_not_valued_at_a_price(self, context) -> None:
        with pytest.raises(UnitMismatchError, match="not shares"):
            holding_value(context, quantity=cash("120"), price=price("12.50"))

    def test_the_ledger_calls_it_a_holding_and_not_a_market_capitalisation(self, context) -> None:
        # Arithmetically identical to `prices.market_capitalisation` and deliberately its own
        # function: the name is part of the record, and a portfolio row reading
        # "market_capitalisation" would say something untrue about what was valued.
        holding_value(context, quantity=shares("1"), price=price("1"))

        assert context.records[0].name == "holding_value"


class TestWhatTheBookIsWorth:
    def test_cash_is_part_of_it(self, context) -> None:
        total = net_assets(context, holdings=[cash("9000")], cash=[cash("1000")])

        assert total.value == Decimal("10000")

    def test_a_weight_is_a_fraction_of_everything_including_cash(self, context) -> None:
        """The denominator that makes the difference, stated as a number.

        Nine thousand of securities against a ten-thousand book is 90%. Over securities
        alone it would be 100%, and every holding on the page would read as larger than it
        is — silently, and in the flattering direction.
        """
        total = net_assets(context, holdings=[cash("9000")], cash=[cash("1000")])

        share = weight(context, value=cash("9000"), net_assets=total)

        assert share.value == Decimal("0.9")
        assert share.unit.is_dimensionless

    def test_an_empty_book_has_no_value_to_state(self, context) -> None:
        # Not zero. Zero is a figure somebody could act on.
        with pytest.raises(CalculationError, match="needs something in it"):
            net_assets(context, holdings=[], cash=[])

    def test_a_weight_against_nothing_is_undefined_rather_than_nil(self, context) -> None:
        with pytest.raises(CalculationError, match="undefined rather than nil"):
            weight(context, value=cash("100"), net_assets=cash("0"))

    def test_unrealised_is_the_mark_less_the_pool(self, context) -> None:
        profit = unrealised(context, value=cash("1500"), cost=cash("1009.95"))

        assert profit.value == Decimal("490.05")

    def test_a_loss_is_a_negative_number_rather_than_an_error(self, context) -> None:
        # Being down on a holding is an ordinary state of the world.
        assert unrealised(context, value=cash("900"), cost=cash("1000")).value == Decimal("-100")


class TestEveryFigureIsARecordedCalculation:
    def test_each_one_carries_a_formula_and_its_inputs(self, context) -> None:
        """ADR 0083's claim, checked against the ledger rather than the prose.

        Click a figure, see the formula, see the inputs — by the same machinery a discounted
        cash flow already uses. Nothing was invented for the portfolio.
        """
        held = quantity_held(context, movements=[shares("100")])
        value = holding_value(context, quantity=held, price=price("12.50"))
        cost = pooled_cost(context, movements=[shares("100")], acquisition_costs=[cash("1000")])
        unrealised(context, value=value, cost=cost)

        assert [record.name for record in context.records] == [
            "quantity_held",
            "holding_value",
            "pooled_cost",
            "unrealised",
        ]
        for record in context.records:
            assert record.formula
            assert record.inputs
            assert record.code_version == "test"

    def test_a_figure_traces_back_through_the_ones_it_was_built_from(self, context) -> None:
        held = quantity_held(context, movements=[shares("100")])
        holding_value(context, quantity=held, price=price("12.50"))

        upper = context.records[-1]
        cited = [source.identifier for source in upper.input_sources]

        assert str(context.records[0].id) in cited


# -- Properties: statements that must hold for every book, not five of them -------------------

# Share counts and prices exact to the smallest sensible unit, built from integers rather than
# sampled as floats — a float converted to Decimal would test binary rounding, which is not
# the subject. The same convention as `tests/test_calc_properties.py`.
quantities = st.integers(min_value=1, max_value=10**9).map(Decimal)
prices = st.integers(min_value=1, max_value=10**8).map(lambda pence: Decimal(pence).scaleb(-2))


class TestPropertiesOfTheBook:
    @given(bought=quantities, cost=prices)
    @settings(max_examples=200, deadline=None)
    def test_disposing_of_everything_always_leaves_exactly_nothing(
        self, bought: Decimal, cost: Decimal
    ) -> None:
        """Whatever was paid and however much was bought, an empty pool costs zero.

        A residue here would render as a cost basis on a position that no longer exists,
        which is the shape of error that survives review because the number is tiny. One
        quantity drawn and used on both sides rather than two filtered down to the equal
        pairs: `assume(a == b)` over the integers discards essentially everything.
        """
        context = CalculationContext(code_version="test")

        remaining = pooled_cost(
            context,
            movements=[shares(str(bought)), shares(f"-{bought}")],
            acquisition_costs=[cash(str(cost)), NIL],
        )

        assert remaining.value == Decimal("0")

    @given(first=quantities, second=quantities, cost=prices)
    @settings(max_examples=200, deadline=None)
    def test_the_pool_never_costs_more_than_was_paid_into_it(
        self, first: Decimal, second: Decimal, cost: Decimal
    ) -> None:
        # A disposal can only take cost out. A pool that grew on a sale would mean the
        # average was computed against the wrong denominator.
        context = CalculationContext(code_version="test")

        remaining = pooled_cost(
            context,
            movements=[shares(str(first)), shares(str(second)), shares(f"-{first}")],
            acquisition_costs=[cash(str(cost)), cash(str(cost)), NIL],
        )

        assert remaining.value <= cost * 2
        assert remaining.value >= 0

    @given(units=quantities, mark=prices)
    @settings(max_examples=200, deadline=None)
    def test_a_single_holding_is_the_whole_book(self, units: Decimal, mark: Decimal) -> None:
        # The degenerate case, which is also the first book anybody has: one position, no
        # cash, and a weight of exactly one. An arithmetic drift shows up here as 0.9999.
        context = CalculationContext(code_version="test")

        value = holding_value(context, quantity=shares(str(units)), price=price(str(mark)))
        total = net_assets(context, holdings=[value], cash=[])
        share = weight(context, value=value, net_assets=total)

        assert share.value == Decimal("1")

    @given(held=quantities, deposit=prices, mark=prices)
    @settings(max_examples=200, deadline=None)
    def test_the_weights_of_everything_in_the_book_sum_to_one(
        self, held: Decimal, deposit: Decimal, mark: Decimal
    ) -> None:
        """Including cash, which is the whole point of counting it.

        If cash were left out of the denominator the securities would sum to one on their
        own and the book would appear fully invested whatever was sitting uninvested.
        """
        context = CalculationContext(code_version="test")

        value = holding_value(context, quantity=shares(str(held)), price=price(str(mark)))
        balance = cash_balance(context, effects=[cash(str(deposit))])
        total = net_assets(context, holdings=[value], cash=[balance])

        parts = [
            weight(context, value=value, net_assets=total).value,
            weight(context, value=balance, net_assets=total).value,
        ]

        # An absolute bound in the last places: the two divisions each round at 34 digits,
        # and a sum of two fractions has nowhere to hide a real error above that.
        assert abs(sum(parts) - Decimal(1)) < Decimal("1e-30")

    @given(units=quantities, mark=prices, paid=prices)
    @settings(max_examples=200, deadline=None)
    def test_unrealised_plus_cost_reconstructs_the_mark(
        self, units: Decimal, mark: Decimal, paid: Decimal
    ) -> None:
        # True by definition, and the definition is what a reader checks when the profit
        # column looks wrong.
        context = CalculationContext(code_version="test")

        value = holding_value(context, quantity=shares(str(units)), price=price(str(mark)))
        profit = unrealised(context, value=value, cost=cash(str(paid)))

        assert profit.value + paid == value.value

    @given(units=quantities, mark=prices, fees=prices)
    @settings(max_examples=200, deadline=None)
    def test_buying_and_selling_at_the_same_price_loses_exactly_the_dealing_costs(
        self, units: Decimal, mark: Decimal, fees: Decimal
    ) -> None:
        """The round trip, which is the cheapest sanity check a person can do on a book.

        Cash out on the way in, cash in on the way out, at the same price — the difference
        is the two lots of dealing costs and nothing else. A sign error anywhere in the cash
        path shows up here immediately.
        """
        context = CalculationContext(code_version="test")

        out = dealt_cash_effect(
            context,
            quantity=shares(str(units)),
            price=price(str(mark)),
            fees=cash(str(fees)),
        )
        back = dealt_cash_effect(
            context,
            quantity=shares(f"-{units}"),
            price=price(str(mark)),
            fees=cash(str(fees)),
        )

        assert out.value + back.value == -(fees * 2)
