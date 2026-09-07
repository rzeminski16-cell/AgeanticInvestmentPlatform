"""The book assembled from rows, which is where a wrong number would actually reach a person.

`tests/test_calc_portfolio.py` proves the arithmetic. This proves the *assembly*: that the
right trades are read, that a correction replaces what it corrects, that a dividend does not
end up in a share count, that a London listing's pence become pounds, and that a holding
nobody can price refuses the total rather than shrinking it.

**Two of these would be invisible in the output.** A superseded trade left in the book gives
a holding that is simply wrong by whatever the correction changed. And a net asset value
computed over the positions that happened to price is a smaller number that looks exactly
like a total — with every weight taken against it too large, in the flattering direction.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from aer.calc.attestation import Attested, Graded
from aer.calc.engine import CalculationContext
from aer.calc.units import Unit
from aer.core.enums import (
    Grade,
    TransactionKind,
)
from aer.db.models import (
    Calculation,
    Security,
)
from aer.services import portfolio as portfolio_service
from aer.web.portfolio import pages as pages_module
from aer.web.portfolio.pages import NO_LISTINGS, _resolve_security, _Unheld
from aer.web.vocabulary import Tone
from tests import portfolio_fixtures

pytestmark = pytest.mark.integration

# The book itself, and the helpers that put trades in it, live in `portfolio_fixtures`:
# `test_splits.py` needs the same book, and two fixtures of that name would drift.
AS_OF = portfolio_fixtures.AS_OF
BOUGHT_ON = portfolio_fixtures.BOUGHT_ON
funded = portfolio_fixtures.funded
trade = portfolio_fixtures.trade
view_of = portfolio_fixtures.view_of


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


class TestTheBookIsWhatTheTransactionsSay:
    async def test_a_purchase_becomes_a_holding_priced_in_the_book_s_currency(
        self, db_session, book, context
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.security.ticker == "MSFT"
        assert holding.quantity.value == Decimal("100")
        # 100 shares at $410 is $41,000, converted at 0.84645/1.0705 pounds per dollar.
        assert holding.value is not None
        assert holding.value.unit == Unit.currency("GBP")
        assert holding.value.value.quantize(Decimal("0.01")) == Decimal("32418.92")

    async def test_a_london_listing_is_marked_in_pounds_not_pence(
        self, db_session, book, context
    ) -> None:
        """The per-cent trap wearing a hat.

        A close of 250 on a London listing means £2.50, and a book that read it as £250
        would show a holding a hundred times too large — a number that is plausible, in the
        right currency, and catastrophically wrong.
        """
        await funded(db_session, book)
        # Dealt in pence, as the contract note states it: 1,000 at 240.00p is £2,400.
        await trade(
            db_session,
            book,
            security=book["barc"],
            quantity="1000",
            price="240",
            currency="GBX",
        )

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.value is not None
        assert holding.value.value == Decimal("2500")

    async def test_a_dividend_is_cash_and_not_a_share_count(
        self, db_session, book, context
    ) -> None:
        # It names the security it came from, which is right and is exactly why the grouping
        # cannot key on that: pooling it would add 50 to a holding of 100.
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        await trade(
            db_session,
            book,
            kind=TransactionKind.DIVIDEND,
            security=book["msft"],
            quantity="50",
            price=None,
        )

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.quantity.value == Decimal("100")

        balances = {row.currency: row.balance.value for row in view.cash}

        # Two balances, because the deposit was sterling and the dealing was not. The
        # dividend joined the dollars it was paid in rather than the shares it came from.
        assert balances["GBP"] == Decimal("100000")
        assert balances["USD"] == Decimal("-40950")

    async def test_a_position_sold_out_is_closed_rather_than_shown_as_nil(
        self, db_session, book, context
    ) -> None:
        await funded(db_session, book)
        # Bought at ten, sold at four. The hour is what orders two trades on one day, and
        # the pooled cost of ADR 0085 depends on that order.
        await trade(db_session, book, security=book["msft"], at_hour=10)
        await trade(
            db_session,
            book,
            kind=TransactionKind.SELL,
            security=book["msft"],
            quantity="-100",
            at_hour=16,
        )

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.problem == portfolio_service.CLOSED
        assert holding.value is None
        # And it does not drag the total down with it: the cash from the sale is the book.
        assert view.net_assets is not None

    async def test_a_trade_after_the_as_of_date_is_not_in_the_book(
        self, db_session, book, context
    ) -> None:
        await trade(db_session, book, security=book["msft"], on=date(2026, 7, 15))

        view = await view_of(db_session, context, book)

        assert view.holdings == ()


class TestACorrectionReplacesWhatItCorrects:
    async def test_the_superseded_trade_is_not_in_the_book(self, db_session, book, context) -> None:
        """ "I entered 1,000 and meant 100" was always 100.

        Superseding is a fact about the *record* rather than about the world, so it applies
        at every as-of date. A screen showing 1,000 for June because the correction came in
        July would be reporting a keystroke rather than a holding.
        """
        await funded(db_session, book)
        mistake = await trade(db_session, book, security=book["msft"], quantity="1000")
        await trade(db_session, book, security=book["msft"], quantity="100", supersedes=mistake)

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.quantity.value == Decimal("100")

    async def test_the_superseding_row_is_the_one_that_counts(
        self, db_session, book, context
    ) -> None:
        # Both rows exist and only one is in force, which is the whole shape: the mistake is
        # still readable by anybody asking what happened.
        mistake = await trade(db_session, book, security=book["msft"], quantity="1000")
        await trade(db_session, book, security=book["msft"], quantity="100", supersedes=mistake)

        trades = await portfolio_service.transactions_in_force(
            db_session, portfolio=book["portfolio"], as_of=AS_OF
        )

        assert [row.quantity for row in trades] == [Decimal("100.000000000000")]


class TestATotalMissingAPositionIsNotASmallerTotal:
    async def test_an_unpriced_holding_refuses_the_net_asset_value(
        self, db_session, book, context
    ) -> None:
        """The failure that looks exactly like an answer.

        A net asset value computed over the positions that happened to price is a smaller
        number in the right currency, and every weight taken against it is too large — in
        the direction that flatters.
        """
        unlisted = Security(
            ticker="PRIV", exchange="OTC", provider_symbol="PRIV.OTC", quote_currency="GBP"
        )
        db_session.add(unlisted)
        await db_session.flush()
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        await trade(db_session, book, security=unlisted, quantity="10", price="5", currency="GBP")

        view = await view_of(db_session, context, book)

        assert view.net_assets is None
        assert "PRIV" in view.problem
        assert all(row.weight is None for row in view.holdings)

    async def test_the_priced_rows_still_show_what_they_can(
        self, db_session, book, context
    ) -> None:
        # A refused total is not a refused page. The holdings that priced are still the
        # operator's best information about their book.
        unlisted = Security(
            ticker="PRIV", exchange="OTC", provider_symbol="PRIV.OTC", quote_currency="GBP"
        )
        db_session.add(unlisted)
        await db_session.flush()
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        await trade(db_session, book, security=unlisted, quantity="10", price="5", currency="GBP")

        view = await view_of(db_session, context, book)

        priced = [row for row in view.holdings if row.is_priced]
        assert len(priced) == 1
        assert priced[0].security.ticker == "MSFT"

    async def test_an_empty_book_states_no_value_rather_than_zero(
        self, db_session, book, context
    ) -> None:
        view = await view_of(db_session, context, book)

        assert view.net_assets is None
        assert view.problem == portfolio_service.EMPTY


class TestCashIsInTheDenominator:
    async def test_the_weights_of_the_whole_book_sum_to_one(
        self, db_session, book, context
    ) -> None:
        await funded(db_session, book)
        await trade(
            db_session, book, security=book["barc"], quantity="1000", price="240", currency="GBX"
        )

        view = await view_of(db_session, context, book)

        parts = [row.weight.value for row in view.holdings if row.weight is not None]
        parts += [row.weight.value for row in view.cash if row.weight is not None]

        assert abs(sum(parts) - Decimal(1)) < Decimal("1e-30")

    async def test_uninvested_cash_shows_as_its_own_row(self, db_session, book, context) -> None:
        # Not a footnote. Without it every weight on the page is a fraction of the wrong
        # denominator, silently.
        await trade(
            db_session,
            book,
            kind=TransactionKind.DEPOSIT,
            security=None,
            price=None,
            quantity="50000",
            currency="GBP",
        )

        view = await view_of(db_session, context, book)

        [balance] = view.cash
        assert balance.currency == "GBP"
        assert balance.balance.value == Decimal("50000")
        assert balance.weight is not None
        assert balance.weight.value == Decimal("1")


class TestTheGradeReachesTheScreenAndStopsAtTheDoor:
    async def test_a_typed_trade_makes_every_figure_above_it_attested(
        self, db_session, book, context
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], grade=Grade.ATTESTED)

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.quantity.is_attested
        assert holding.value is not None
        assert holding.value.is_attested
        assert view.rests_on_anything_typed

    async def test_a_documented_trade_does_not(self, db_session, book, context) -> None:
        # The point of the grade being a distinction rather than a blanket "operator data is
        # second class": a contract note is a document with a hash and a citation.
        # The deposit is documented too. A book is only as evidenced as its weakest row,
        # so one typed cash movement would taint the page whatever the holdings say — which
        # is the propagation working rather than a nuisance.
        await funded(db_session, book, grade=Grade.DOCUMENTED)
        await trade(db_session, book, security=book["msft"], grade=Grade.DOCUMENTED)

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert not holding.quantity.is_attested
        assert not view.rests_on_anything_typed

    async def test_the_operator_sees_their_own_typed_figures(
        self, db_session, book, context
    ) -> None:
        # Withholding a person's own book from that person would be absurd. The grade is
        # about what leaves the machine.
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], grade=Grade.ATTESTED)

        view = await view_of(db_session, context, book)

        assert view.holdings[0].quantity.value == Decimal("100")

    async def test_sharing_an_attested_figure_hands_back_no_figure(
        self, db_session, book, context
    ) -> None:
        """The export path, and it refuses by shape rather than by flag.

        There is no `quantity` on what comes back, so a renderer handed it cannot print the
        number — there is nothing to print and nothing to argue with.
        """
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], grade=Grade.ATTESTED)

        view = await view_of(db_session, context, book)
        shared = view.holdings[0].quantity.for_sharing()

        assert isinstance(shared, Attested)
        assert not hasattr(shared, "quantity")
        assert "typed" in shared.as_sentence()

    async def test_sharing_a_documented_figure_hands_back_the_figure(
        self, db_session, book, context
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], grade=Grade.DOCUMENTED)

        view = await view_of(db_session, context, book)
        shared = view.holdings[0].quantity.for_sharing()

        assert isinstance(shared, Graded)
        assert shared.quantity.value == Decimal("100")


class TestEveryFigureCarriesItsWorking:
    async def test_a_holding_resolves_to_its_formula_and_its_trades(
        self, db_session, book, context
    ) -> None:
        """ADR 0083's promise, at the surface that has to keep it.

        Click a figure, see the formula, see the inputs, see where each came from — by the
        same machinery a discounted cash flow uses. Nothing was invented for the portfolio.
        """
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="60")
        await trade(db_session, book, security=book["msft"], quantity="40", price="420")

        view = await view_of(db_session, context, book)

        record = view.holdings[0].quantity.record

        assert record.name == "quantity_held"
        assert (
            record.formula
            == "held = fold in trade-date order: a share movement adds; a ratio multiplies"
        )
        assert [row.name for row in record.inputs] == ["movements[0]", "movements[1]"]
        assert all(row.source_grade is Grade.ATTESTED for row in record.inputs)

    async def test_nothing_is_written_to_the_database_by_looking(
        self, db_session, book, context
    ) -> None:
        # A page load is not a run. Writing a few hundred `calculations` rows on every GET
        # would make a read a writer, and there is no job to hang the ledger off anyway.
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        await view_of(db_session, context, book)

        written = await db_session.scalar(select(func.count()).select_from(Calculation))

        assert written == 0
        # And yet the working exists, in the context the caller passed in — which is what a
        # run persists and a page load simply does not.
        assert len(context.records) > 0


class TestNamingTheSecurityYouMean:
    """Gap R18. The control was a `<select>` and typing into it was impossible.

    Worse at size zero, which is the state of any machine whose research runs had no
    market-data subscription: the dropdown held one option reading "cash, no security", and
    an operator could neither enter a ticker nor find out why not. The resolution rules live
    here; the control is an `<input list>` with no script behind it.
    """

    async def test_a_bare_ticker_finds_its_listing(self, db_session, book) -> None:
        found = await _resolve_security(db_session, "msft")

        assert found is book["msft"]

    async def test_the_vendor_symbol_works_too(self, db_session, book) -> None:
        """What a research run stored is what somebody copying from the run will type."""
        assert await _resolve_security(db_session, "BARC.LSE") is book["barc"]

    async def test_an_empty_box_is_a_cash_transaction_and_not_a_mistake(
        self, db_session, book
    ) -> None:
        assert await _resolve_security(db_session, "   ") is None

    async def test_a_ticker_nobody_holds_is_the_third_doors_case(self, db_session, book) -> None:
        """Since §3.1 an unknown ticker is not a dead end: resolution hands the handler
        what was typed, parsed, and the handler decides whether it can be verified at
        first sight. A bare ticker carries no venue — the door needs one."""
        unheld = await _resolve_security(db_session, "TSLA")

        assert isinstance(unheld, _Unheld)
        assert unheld.ticker == "TSLA"
        assert unheld.exchange is None

    async def test_a_named_exchange_travels_with_the_unheld_ticker(self, db_session, book) -> None:
        for typed in ("TSLA NASDAQ", "tsla.nasdaq"):
            unheld = await _resolve_security(db_session, typed)
            assert isinstance(unheld, _Unheld)
            assert unheld.ticker == "TSLA"
            assert unheld.exchange == "NASDAQ"

    async def test_an_ambiguous_ticker_names_the_choices_rather_than_picking_one(
        self, db_session, book
    ) -> None:
        """A dual listing trades at two prices in two currencies.

        Resolving it by picking the first row would put a holding in the book at a price
        from the wrong exchange, and nothing downstream would notice.
        """
        db_session.add(
            Security(
                ticker="MSFT", exchange="LSE", provider_symbol="MSFT.LSE", quote_currency="GBX"
            )
        )
        await db_session.flush()

        refusal = await _resolve_security(db_session, "MSFT")

        assert isinstance(refusal, str)
        assert "MSFT.NASDAQ" in refusal
        assert "MSFT.LSE" in refusal

    async def test_a_platform_holding_nothing_still_offers_the_third_door(
        self, db_session, book
    ) -> None:
        """The empty state is the one an operator actually meets first — and since §3.1 a
        typed ticker on an empty platform is a verification waiting to happen, not a
        refusal. The empty-state copy names both doors."""
        for security in (book["msft"], book["barc"]):
            security.is_active = False
        await db_session.flush()

        unheld = await _resolve_security(db_session, "MSFT NASDAQ")

        assert isinstance(unheld, _Unheld)
        assert "market-data subscription" in NO_LISTINGS
        assert "cash transactions" in NO_LISTINGS.lower()
        assert "verified" in NO_LISTINGS


class TestTheBookLeadsWithAVerdict:
    """Tranche 8: the page opens with a sentence composed from what the walk resolved,
    and an incomplete valuation structurally cannot wear the success tone."""

    async def test_a_fully_valued_book_says_so_and_carries_the_grade_once(
        self, db_session, book, context
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        view = await view_of(db_session, context, book)

        totals = pages_module._totals(view, book["portfolio"])
        lead = pages_module._book_verdict(view, totals=totals)

        assert lead.tone is Tone.SUCCESS
        assert "Fully valued" in lead.composed
        assert "typed" in lead.composed

    async def test_an_incomplete_book_refuses_the_all_clear(
        self, db_session, book, context
    ) -> None:
        """A GBP deposit and a franc one with no rate to join them: the four figures are
        withheld, and the verdict says so in the warning family, never the success one."""
        await funded(db_session, book)
        await trade(
            db_session,
            book,
            kind=TransactionKind.DEPOSIT,
            security=None,
            price=None,
            quantity="1000",
            currency="CHF",
        )
        view = await view_of(db_session, context, book)

        totals = pages_module._totals(view, book["portfolio"])
        lead = pages_module._book_verdict(view, totals=totals)

        assert lead.tone is not Tone.SUCCESS
        assert "withheld" in lead.composed

    async def test_an_empty_book_is_muted_rather_than_reassuring(
        self, db_session, book, context
    ) -> None:
        view = await view_of(db_session, context, book)

        totals = pages_module._totals(view, book["portfolio"])
        lead = pages_module._book_verdict(view, totals=totals)

        assert lead.tone is Tone.MUTED
        assert "Nothing is recorded yet" in lead.composed
