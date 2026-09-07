"""Return and exposure over a real book — roadmap §3.2.

`test_calc_performance` proves the arithmetic. This proves the *assembly*: which
transactions count as flows, where the value series is broken, what a period is, and what
the bands do with a classification nobody has.

The one that matters is `TestATopUpIsNotPerformance`. Every other failure in this module
looks like a bug; that one looks like a good year.

The books here are cash-only wherever the subject is the flow arithmetic, deliberately: a
deposit and a withdrawal move the net asset value with no price in the way, so a figure
that came out wrong came out wrong for the reason the test is about.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.calc.units import CalculationError
from aer.core.enums import TransactionKind
from aer.db.models import Company, PriceBar
from aer.services import performance as performance_service
from aer.services import portfolio as portfolio_service
from tests import portfolio_fixtures

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

# The `book` fixture itself is registered in `conftest`; these are the helpers that put
# trades into it. Rebound rather than star-imported for the reason `test_portfolio_service`
# gives: one book, one definition, and no second name for it to drift under.
AS_OF = portfolio_fixtures.AS_OF
funded = portfolio_fixtures.funded
trade = portfolio_fixtures.trade


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


async def _returns(
    session: AsyncSession,
    context: CalculationContext,
    book: dict[str, Any],
    *,
    as_of: date = AS_OF,
) -> performance_service.ReturnView:
    return await performance_service.returns_as_at(
        session, context, portfolio=book["portfolio"], as_of=as_of
    )


async def _exposure(
    session: AsyncSession,
    context: CalculationContext,
    book: dict[str, Any],
    *,
    as_of: date = AS_OF,
) -> performance_service.ExposureView:
    return await performance_service.exposure_as_at(
        session, context, portfolio=book["portfolio"], as_of=as_of
    )


async def _deposit(session: AsyncSession, book: dict[str, Any], amount: str, on: date) -> None:
    await trade(
        session,
        book,
        kind=TransactionKind.DEPOSIT,
        security=None,
        price=None,
        quantity=amount,
        currency="GBP",
        on=on,
    )


async def _withdraw(session: AsyncSession, book: dict[str, Any], amount: str, on: date) -> None:
    await trade(
        session,
        book,
        kind=TransactionKind.WITHDRAWAL,
        security=None,
        price=None,
        quantity=amount,
        currency="GBP",
        on=on,
    )


async def _dividend(session: AsyncSession, book: dict[str, Any], amount: str, on: date) -> None:
    await trade(
        session,
        book,
        kind=TransactionKind.DIVIDEND,
        security=None,
        price=None,
        quantity=amount,
        currency="GBP",
        on=on,
    )


class TestATopUpIsNotPerformance:
    """The failure this whole item exists to prevent, end to end.

    A book that received two deposits and earned nothing is up nothing. Every naive
    reading of the same rows — closing over opening, or the change in net assets — says it
    doubled.
    """

    async def test_two_deposits_and_no_gain_is_a_return_of_nothing(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _deposit(db_session, book, "1000", date(2026, 1, 5))
        await _deposit(db_session, book, "1000", date(2026, 4, 5))

        view = await _returns(db_session, context, book)
        since = view.since_inception

        assert since is not None
        assert since.time_weighted is not None, since.problem
        assert since.time_weighted.value == 0

    async def test_and_the_money_weighted_figure_agrees_here(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """The two disagree about *timing*, never about whether money appeared from
        nowhere. A book that earned nothing earned nothing on either measure."""
        await _deposit(db_session, book, "1000", date(2026, 1, 5))
        await _deposit(db_session, book, "1000", date(2026, 4, 5))

        since = (await _returns(db_session, context, book)).since_inception

        assert since is not None
        assert since.money_weighted is not None, since.problem
        assert abs(since.money_weighted.value) < Decimal("1e-6")

    async def test_a_withdrawal_is_not_a_loss(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _deposit(db_session, book, "1000", date(2026, 1, 5))
        await _withdraw(db_session, book, "-400", date(2026, 4, 5))

        since = (await _returns(db_session, context, book)).since_inception

        assert since is not None
        assert since.time_weighted is not None, since.problem
        assert since.time_weighted.value == 0

    async def test_a_dividend_is_a_gain_rather_than_a_flow(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """The other half of the distinction, and the one that would be missed. Money the
        holdings produced belongs *inside* the return; treating it as an external flow
        would net it out and report a book that earned 10% as flat."""
        await _deposit(db_session, book, "1000", date(2026, 1, 5))
        await _dividend(db_session, book, "100", date(2026, 4, 5))

        since = (await _returns(db_session, context, book)).since_inception

        assert since is not None
        assert since.time_weighted is not None, since.problem
        assert since.time_weighted.value == Decimal("0.1")


class TestWhatCountsAsAFlow:
    async def test_only_deposits_and_withdrawals_do(self) -> None:
        assert {
            TransactionKind.DEPOSIT,
            TransactionKind.WITHDRAWAL,
        } == performance_service.EXTERNAL_KINDS

    async def test_a_book_with_no_transactions_says_so(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        view = await _returns(db_session, context, book)

        assert view.inception is None
        assert view.periods == ()
        assert "nothing to measure" in view.problem

    async def test_a_book_that_was_never_funded_says_so(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """A dividend on its own is not capital. There is nothing for a return to be a
        return *on*, and saying zero would be a figure standing in for that."""
        await _dividend(db_session, book, "100", date(2026, 1, 5))

        view = await _returns(db_session, context, book)

        assert view.inception == date(2026, 1, 5)
        assert view.periods == ()
        assert "no capital" in view.problem


class TestThePeriods:
    async def test_since_inception_leads_and_the_years_follow_newest_first(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _deposit(db_session, book, "1000", date(2024, 3, 14))

        view = await _returns(db_session, context, book)

        assert [period.label for period in view.periods] == [
            "Since inception",
            "2026 to date",
            "2025",
            "2024",
        ]

    async def test_the_current_year_is_labelled_for_where_it_stops(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """A calendar row and a part-year row are different measurements, and a reader
        comparing "2026" against a full-year index number should not be handed six months
        of it under the same label."""
        await _deposit(db_session, book, "1000", date(2026, 1, 5))

        view = await _returns(db_session, context, book)
        current = next(period for period in view.periods if period.label != "Since inception")

        assert current.label == "2026 to date"
        assert current.end == AS_OF

    async def test_a_year_runs_from_the_first_of_january_or_from_inception(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await _deposit(db_session, book, "1000", date(2025, 6, 10))

        view = await _returns(db_session, context, book)
        by_label = {period.label: period for period in view.periods}

        assert by_label["2025"].begin == date(2025, 6, 10)
        assert by_label["2025"].end == date(2025, 12, 31)
        assert by_label["2026 to date"].begin == date(2026, 1, 1)

    async def test_a_full_past_year_measures_the_growth_inside_it(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """A year the book was funded through, with a dividend inside it: the row must be
        the growth over that year, not over the whole life of the book."""
        await _deposit(db_session, book, "1000", date(2024, 6, 1))
        await _dividend(db_session, book, "100", date(2025, 6, 1))

        view = await _returns(db_session, context, book)
        by_label = {period.label: period for period in view.periods}

        assert by_label["2024"].time_weighted is not None
        assert by_label["2024"].time_weighted.value == 0
        assert by_label["2025"].time_weighted is not None
        assert by_label["2025"].time_weighted.value == Decimal("0.1")


class TestTheValuationBudget:
    async def test_it_is_stated_rather_than_hoped_about(self) -> None:
        assert performance_service.MAX_VALUATION_POINTS > 0

    async def test_a_book_past_it_loses_one_figure_and_keeps_the_other(
        self,
        db_session: AsyncSession,
        context: CalculationContext,
        book: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A time-weighted return needs a valuation per flow date and a money-weighted one
        needs two. A reader is better served by one number and a sentence than by two
        blanks, so they are refused separately."""
        monkeypatch.setattr(performance_service, "MAX_VALUATION_POINTS", 2)
        for day in (5, 6, 7, 8):
            await _deposit(db_session, book, "1000", date(2026, 1, day))

        since = (await _returns(db_session, context, book)).since_inception

        assert since is not None
        assert since.time_weighted is None
        assert "No time-weighted return" in since.problem
        assert since.money_weighted is not None


class TestExposure:
    async def test_the_bands_are_the_four_the_roadmap_names(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)

        assert [band.kind for band in view.bands] == [
            "holding",
            "sector",
            "currency",
            "country",
        ]

    async def test_a_holding_carries_its_share_and_its_value(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)
        holdings = next(band for band in view.bands if band.kind == "holding")

        assert [row.label for row in holdings.slices] == ["MSFT"]
        assert holdings.slices[0].share.value > 0
        assert holdings.slices[0].value.value > 0

    async def test_a_sector_nobody_knows_is_named_rather_than_bucketed(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """The roadmap's own words. An "other" bucket invents a category and then weights
        it, and a reader has no way to tell it from a sector the book is genuinely in."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)
        sectors = next(band for band in view.bands if band.kind == "sector")

        assert sectors.slices == ()
        assert sectors.unknown is not None
        assert sectors.unknown.label == performance_service.UNKNOWN_SECTOR
        assert sectors.unknown.known is False
        assert sectors.unknown.members == ("MSFT",)

    async def test_a_sector_a_run_resolved_is_reported(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        company = Company(
            cik="0000789019",
            name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            sic="7372",
            sic_description="Services-Prepackaged Software",
        )
        db_session.add(company)
        await db_session.flush()
        book["msft"].company_id = company.id
        await db_session.flush()
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)
        sectors = next(band for band in view.bands if band.kind == "sector")

        assert [row.label for row in sectors.slices] == ["Services-Prepackaged Software"]
        assert sectors.unknown is None

    async def test_cash_is_a_currency_exposure_like_any_other(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """Leaving it out would understate the book's own currency and overstate every
        other — the same reason a weight is taken against a total that includes it."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)
        currencies = next(band for band in view.bands if band.kind == "currency")
        labels = [row.label for row in currencies.slices]

        assert "GBP" in labels
        assert "USD" in labels

    async def test_a_pence_listing_is_exposure_to_sterling(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """`GBX` is a unit of a currency, not a currency. A band listing it beside GBP
        would split one exposure in two and understate both."""
        await funded(db_session, book)
        await trade(
            db_session, book, security=book["barc"], quantity="1000", price="240", currency="GBX"
        )

        view = await _exposure(db_session, context, book)
        currencies = next(band for band in view.bands if band.kind == "currency")

        assert "GBX" not in [row.label for row in currencies.slices]
        sterling = next(row for row in currencies.slices if row.label == "GBP")
        assert "BARC" in sterling.members

    async def test_a_listing_country_comes_from_the_venue(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        await trade(
            db_session, book, security=book["barc"], quantity="1000", price="240", currency="GBX"
        )

        view = await _exposure(db_session, context, book)
        countries = next(band for band in view.bands if band.kind == "country")

        assert {row.label for row in countries.slices} == {"United States", "United Kingdom"}

    async def test_a_venue_nobody_documented_is_named_rather_than_guessed(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """A country invented for an exchange would put a holding in a jurisdiction it does
        not trade in, and the reader would have no way to tell."""
        book["msft"].exchange = "XETRA"
        await db_session.flush()
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)
        countries = next(band for band in view.bands if band.kind == "country")

        assert countries.slices == ()
        assert countries.unknown is not None
        assert countries.unknown.label == performance_service.UNKNOWN_COUNTRY

    async def test_the_concentration_figure_covers_the_largest_five(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)

        assert performance_service.CONCENTRATION_COUNT == 5
        assert view.top_holdings is not None
        assert 0 < view.top_holdings.value <= 1

    async def test_the_shares_of_one_band_add_to_the_whole_book(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """Cash and securities together are the book, so a currency band that did not sum
        to one would be a pie that does not close."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)
        currencies = next(band for band in view.bands if band.kind == "currency")
        total = sum(row.share.value for row in currencies.slices)

        assert abs(total - 1) < Decimal("1e-12")

    async def test_it_reuses_the_book_the_caller_already_has(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """A page shows the holdings and their exposure together, and valuing the book
        twice for one screen is ADR 0083's cost paid twice for nothing."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        already = await portfolio_service.book_as_at(
            db_session, context, portfolio=book["portfolio"], as_of=AS_OF
        )
        after_one_walk = len(context.records)

        await performance_service.exposure_as_at(
            db_session, context, portfolio=book["portfolio"], as_of=AS_OF, view=already
        )
        with_reuse = len(context.records) - after_one_walk

        fresh = CalculationContext(code_version="test")
        await performance_service.exposure_as_at(
            db_session, fresh, portfolio=book["portfolio"], as_of=AS_OF
        )

        # The band arithmetic is the same either way; what the reuse saves is the walk.
        assert with_reuse < len(fresh.records)

    async def test_the_reused_book_must_come_from_the_same_ledger(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """The one constraint the parameter carries, held by a test because the type
        cannot say it: a view computed elsewhere cites calculations this context does not
        hold, and grading it would be a claim about the part that happened to be readable.
        """
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])
        elsewhere = await portfolio_service.book_as_at(
            db_session,
            CalculationContext(code_version="test"),
            portfolio=book["portfolio"],
            as_of=AS_OF,
        )

        with pytest.raises(CalculationError, match="does not hold it"):
            await performance_service.exposure_as_at(
                db_session, context, portfolio=book["portfolio"], as_of=AS_OF, view=elsewhere
            )

    async def test_a_book_that_nets_to_nothing_has_no_shares_of_it(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """A withdrawal entered without the sale that funded it. `book_as_at` reports this
        *with* a net asset value and a problem beside it, so testing completeness alone
        would let it through to a division the arithmetic refuses — and the page would show
        a stack trace where a sentence belongs."""
        await _deposit(db_session, book, "1000", date(2026, 1, 5))
        await _withdraw(db_session, book, "-2000", date(2026, 4, 5))

        view = await _exposure(db_session, context, book)

        assert view.bands == ()
        assert view.top_holdings is None
        assert "No exposure" in view.problem

    async def test_an_unpriced_book_says_why_there_is_no_exposure(
        self, db_session: AsyncSession, context: CalculationContext, book: dict[str, Any]
    ) -> None:
        """All four tiles go blank together and so does this. A band over a subtotal would
        weight every group against a denominator short a position."""
        await db_session.execute(PriceBar.__table__.delete())
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"])

        view = await _exposure(db_session, context, book)

        assert view.bands == ()
        assert view.top_holdings is None
        assert "No exposure" in view.problem
