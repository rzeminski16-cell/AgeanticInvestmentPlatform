"""One position, in full (page specification §3).

Two traced calculations — what the disposals made against the pool, and what each share
still held cost — and the service that reads one listing out of the same walk the book
makes. Property-based on the arithmetic, because ``calc/`` is where a wrong number would
come from; scene-based on the service, because a position is a view over transactions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aer.calc.engine import CalculationContext
from aer.calc.portfolio import SHARES, average_cost, realised_gain
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
    money,
)
from aer.core.enums import TransactionKind
from aer.services import positions as positions_service
from aer.services.portfolio import CLOSED
from tests.portfolio_fixtures import AS_OF, BOUGHT_ON, book, funded, trade

__all__ = ["book"]

SOURCE = SourceRef.financial_fact("positions-test")


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


def shares(value: str) -> Quantity:
    return Quantity.of(Decimal(value), SHARES, source=SOURCE)


def cash(value: str, currency: str = "USD") -> Quantity:
    return money(Decimal(value), currency, source=SOURCE)


NIL = cash("0")

pence = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("5000"), places=2)
whole_shares = st.integers(min_value=1, max_value=10_000)


# -- What the disposals made -----------------------------------------------------------------


class TestRealisedGain:
    @settings(max_examples=60, deadline=None)
    @given(bought=whole_shares, sold_share=st.fractions(0, 1), paid=pence, fetched=pence)
    def test_one_sale_realises_the_spread_on_what_was_sold(
        self, bought: int, sold_share: Any, paid: Decimal, fetched: Decimal
    ) -> None:
        """Buy at one price, sell part at another: the gain is the sold units times the
        difference, and the pool's average is the one price paid."""
        context = CalculationContext(code_version="test")
        sold = max(1, int(bought * sold_share))
        gain = realised_gain(
            context,
            movements=[shares(str(bought)), shares(str(-sold))],
            acquisition_costs=[cash(str(Decimal(bought) * paid)), NIL],
            proceeds=[NIL, cash(str(Decimal(sold) * fetched))],
        )
        assert gain.value == Decimal(sold) * (fetched - paid)
        assert gain.unit == Unit.currency("USD")

    def test_it_walks_the_pool_in_order(self, context: CalculationContext) -> None:
        """Two purchases at different prices, then a sale: the cost removed is the pool's
        average at the moment of sale, which is what ADR 0085 says a disposal costs."""
        gain = realised_gain(
            context,
            movements=[shares("100"), shares("100"), shares("-50")],
            acquisition_costs=[cash("1000"), cash("3000"), NIL],
            proceeds=[NIL, NIL, cash("1500")],
        )
        # The pool held 200 shares at 4000, so 50 of them cost 1000; sold for 1500.
        assert gain.value == Decimal("500")

    def test_a_sale_below_the_average_is_a_loss(self, context: CalculationContext) -> None:
        gain = realised_gain(
            context,
            movements=[shares("10"), shares("-10")],
            acquisition_costs=[cash("1000"), NIL],
            proceeds=[NIL, cash("900")],
        )
        assert gain.value == Decimal("-100")

    def test_it_refuses_a_ledger_whose_rows_disagree(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError):
            realised_gain(
                context, movements=[shares("10")], acquisition_costs=[cash("1")], proceeds=[]
            )
        with pytest.raises(CalculationError):
            realised_gain(context, movements=[], acquisition_costs=[], proceeds=[])

    def test_it_refuses_a_purchase_with_proceeds_and_a_sale_with_a_cost(
        self, context: CalculationContext
    ) -> None:
        with pytest.raises(CalculationError):
            realised_gain(
                context,
                movements=[shares("10")],
                acquisition_costs=[cash("100")],
                proceeds=[cash("5")],
            )
        with pytest.raises(CalculationError):
            realised_gain(
                context,
                movements=[shares("10"), shares("-5")],
                acquisition_costs=[cash("100"), cash("1")],
                proceeds=[NIL, cash("60")],
            )

    def test_it_refuses_selling_more_than_the_pool_holds(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError):
            realised_gain(
                context,
                movements=[shares("10"), shares("-11")],
                acquisition_costs=[cash("100"), NIL],
                proceeds=[NIL, cash("110")],
            )

    def test_it_refuses_mixed_currencies(self, context: CalculationContext) -> None:
        with pytest.raises((CalculationError, UnitMismatchError)):
            realised_gain(
                context,
                movements=[shares("10"), shares("-5")],
                acquisition_costs=[cash("100", "USD"), cash("0", "GBP")],
                proceeds=[cash("0", "USD"), cash("60", "GBP")],
            )


# -- What each share cost --------------------------------------------------------------------


class TestAverageCost:
    @settings(max_examples=60, deadline=None)
    @given(cost=pence, held=whole_shares)
    def test_it_is_the_pooled_cost_over_the_shares_held(self, cost: Decimal, held: int) -> None:
        context = CalculationContext(code_version="test")
        each = average_cost(context, cost=cash(str(cost)), quantity=shares(str(held)))
        # The engine divides at a wider precision than the default context; the product
        # is what a reader could check, to the penny.
        assert (each.value * Decimal(held)).quantize(Decimal("0.01")) == cost
        assert each.unit == Unit.currency("USD") / SHARES

    def test_it_refuses_nothing_held(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError):
            average_cost(context, cost=cash("100"), quantity=shares("0"))

    def test_it_refuses_a_quantity_that_is_not_shares(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError):
            average_cost(context, cost=cash("100"), quantity=cash("10"))

    def test_it_refuses_a_cost_that_is_not_money(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError):
            average_cost(context, cost=shares("100"), quantity=shares("10"))


# -- The position, read from the book --------------------------------------------------------


class TestThePosition:
    async def test_a_partly_closed_position_shows_both_halves(
        self, db_session: Any, book: dict[str, Any]
    ) -> None:
        """Buy a hundred, sell forty: sixty held at the pool's average, and the forty's
        gain stated on its own — neither a remainder of the other."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="100", price="400")
        await trade(
            db_session,
            book,
            kind=TransactionKind.SELL,
            security=book["msft"],
            quantity="-40",
            price="420",
            on=date(2026, 6, 20),
        )

        detail = await positions_service.position_as_at(
            db_session, user=book["user"], security_id=book["msft"].id
        )

        assert detail is not None
        assert detail.as_of == AS_OF
        assert detail.is_partly_closed
        assert not detail.is_closed
        assert detail.holding.quantity is not None
        assert detail.holding.quantity.value == Decimal("60")
        assert detail.realised is not None
        assert detail.realised.value == Decimal("800")  # forty sold at 420, bought at 400
        assert detail.average is not None
        assert detail.holding.cost is not None
        # Per share of the pooled cost, in the book's currency as the cost is.
        assert detail.average.unit == Unit.currency("GBP") / SHARES
        assert (detail.average.value * Decimal(60)).quantize(Decimal("0.01")) == (
            detail.holding.cost.value.quantize(Decimal("0.01"))
        )
        assert [trade.kind for trade in detail.ledger] == [
            TransactionKind.BUY,
            TransactionKind.SELL,
        ]
        assert detail.ledger[0].trade_date == BOUGHT_ON

    async def test_a_closed_position_realises_and_holds_nothing(
        self, db_session: Any, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="100", price="400")
        await trade(
            db_session,
            book,
            kind=TransactionKind.SELL,
            security=book["msft"],
            quantity="-100",
            price="410",
            on=date(2026, 6, 20),
        )

        detail = await positions_service.position_as_at(
            db_session, user=book["user"], security_id=book["msft"].id
        )

        assert detail is not None
        assert detail.is_closed
        assert detail.holding.problem == CLOSED
        assert detail.realised is not None
        assert detail.realised.value == Decimal("1000")
        assert detail.average is None

    async def test_a_position_never_sold_from_has_realised_nothing(
        self, db_session: Any, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")

        detail = await positions_service.position_as_at(
            db_session, user=book["user"], security_id=book["msft"].id
        )

        assert detail is not None
        assert detail.realised is None
        assert not detail.is_partly_closed
        assert detail.holding.average is not None
        assert detail.holding.average.unit == Unit.currency("GBP") / SHARES

    async def test_a_listing_the_book_never_dealt_is_not_a_position(
        self, db_session: Any, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")

        assert (
            await positions_service.position_as_at(
                db_session, user=book["user"], security_id=book["barc"].id
            )
            is None
        )
