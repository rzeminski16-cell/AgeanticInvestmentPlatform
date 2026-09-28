"""The check that runs before a decision is recorded, and the surface it shares with risk.

F12's rule is **one implementation of the book's exposure arithmetic, surfaced twice**, and
its done-when is *"the same shock produces the same figure on both surfaces"*. These tests
assert that by computing both surfaces over one book and comparing the figures themselves —
not by rendering each and comparing strings, which would pass for two implementations that
happen to agree today.

Two rules the check obeys and this file pins, because both are decisions rather than gaps:

**It states the book now, and what it becomes only at a weight typed into it.** ADR 0104
refused a numeric intended size — ``decisions.size_statement`` is text precisely so no
calculation can read an intended weight off a decision and multiply it by a net asset value.
ADR 0137 adds a what-if beside it: a weight typed into the check, struck in a ledger nobody
keeps, whose source ``persist_context`` refuses to write.

**It never blocks.** No ceiling is stored anywhere in this platform, so nothing here can be
breached and no control here refuses a decision.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.core.enums import ShockKind, TransactionKind
from aer.db.models import Company
from aer.errors import ValidationError
from aer.services import risk as risk_service
from aer.services.calculations import persist_context
from tests import portfolio_fixtures
from tests.portfolio_fixtures import AS_OF, daily_bars, funded, trade

pytestmark = pytest.mark.integration

book = portfolio_fixtures.book


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


async def _two_holdings(session: AsyncSession, scene: dict[str, Any]) -> None:
    """A funded book holding both listings, each with a run of closes behind it."""
    await funded(session, scene)
    await trade(
        session,
        scene,
        kind=TransactionKind.BUY,
        security=scene["barc"],
        quantity="100",
        price="250",
        currency="GBX",
    )
    await trade(
        session,
        scene,
        kind=TransactionKind.BUY,
        security=scene["msft"],
        quantity="10",
        price="400",
        currency="USD",
    )
    await daily_bars(session, scene["barc"], until=AS_OF, days=40)
    await daily_bars(session, scene["msft"], until=AS_OF, days=40, close="410")


async def _shock_the_book(session: AsyncSession, scene: dict[str, Any], target: str = "") -> Any:
    kind = ShockKind.HOLDING if target else ShockKind.BOOK
    return await risk_service.state_scenario(
        session,
        actor=scene["user"],
        portfolio=scene["portfolio"],
        name="A fifth off",
        shocks=[risk_service.Shock(kind=kind, target=target, shock=Decimal("-0.2"))],
    )


async def _check(
    session: AsyncSession,
    context: CalculationContext,
    scene: dict[str, Any],
    *,
    security: Any = None,
    as_of: date = AS_OF,
) -> risk_service.PreTradeCheck:
    return await risk_service.check_before_recording(
        session, context, portfolio=scene["portfolio"], security=security, as_of=as_of
    )


class TestTheSameShockOnBothSurfaces:
    """F12's done-when, asserted on the figures rather than on two renderings of them."""

    async def test_the_impact_is_the_same_number_on_the_risk_page_and_the_decision_form(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)
        await _shock_the_book(db_session, book)

        page = await risk_service.risk_as_at(
            db_session, context, portfolio=book["portfolio"], as_of=AS_OF
        )
        form = await _check(db_session, context, book, security=book["msft"])

        assert len(page.scenarios) == 1
        assert len(form.scenarios) == 1
        on_the_page, on_the_form = page.scenarios[0], form.scenarios[0]
        assert on_the_page.impact is not None
        assert on_the_form.impact is not None
        assert on_the_form.impact.value == on_the_page.impact.value
        assert on_the_form.pnl is not None
        assert on_the_page.pnl is not None
        assert on_the_form.pnl.value == on_the_page.pnl.value

    async def test_the_concentration_is_the_same_number_on_both(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)

        page = await risk_service.risk_as_at(
            db_session, context, portfolio=book["portfolio"], as_of=AS_OF
        )
        form = await _check(db_session, context, book, security=book["msft"])

        assert page.exposure.top_holdings is not None
        assert form.top_holdings is not None
        assert form.top_holdings.value == page.exposure.top_holdings.value


class TestWhatTheCheckSays:
    async def test_it_names_what_the_book_already_holds_in_the_listing(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)

        check = await _check(db_session, context, book, security=book["msft"])

        assert check.held is not None
        assert check.held.security.ticker == "MSFT"
        assert check.held.weight is not None
        assert check.held.weight.value > 0

    async def test_a_listing_the_book_does_not_hold_reads_as_nothing_held(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        """And the rest of the check still reads. A first purchase is the commonest case
        this check exists for, and refusing to say anything about the book because the
        operator does not yet own the thing would answer the wrong question."""
        await funded(db_session, book)
        await trade(
            db_session,
            book,
            kind=TransactionKind.BUY,
            security=book["barc"],
            quantity="100",
            price="250",
            currency="GBX",
        )
        await daily_bars(db_session, book["barc"], until=AS_OF, days=40)

        check = await _check(db_session, context, book, security=book["msft"])

        assert check.held is None
        assert check.net_assets is not None
        assert check.top_holdings is not None

    async def test_it_names_the_share_already_in_the_listings_sector(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        company = Company(
            name="Microsoft Corporation",
            ticker="MSFT",
            cik="0000789019",
            exchange="NASDAQ",
            sic="7372",
            sic_description="Prepackaged software",
        )
        db_session.add(company)
        await db_session.flush()
        book["msft"].company_id = company.id
        await _two_holdings(db_session, book)
        await db_session.refresh(book["msft"], ["company"])

        check = await _check(db_session, context, book, security=book["msft"])

        assert check.sector is not None
        assert check.sector.label == "Prepackaged software"
        assert check.sector.share.value > 0

    async def test_an_unclassified_listing_has_no_sector_rather_than_an_empty_one(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)

        check = await _check(db_session, context, book, security=book["msft"])

        assert check.sector is None

    async def test_with_no_listing_named_it_still_reads_the_book(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        """A decision may name no listing at all — the platform may not have priced it yet
        (ADR 0104) — and the concentration and the shocks are still worth knowing."""
        await _two_holdings(db_session, book)
        await _shock_the_book(db_session, book)

        check = await _check(db_session, context, book, security=None)

        assert check.held is None
        assert check.sector is None
        assert check.net_assets is not None
        assert len(check.scenarios) == 1

    async def test_only_the_shocks_that_reach_the_listing_are_listed(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)
        await _shock_the_book(db_session, book, target="BARC")

        reaching_barc = await _check(db_session, context, book, security=book["barc"])
        reaching_msft = await _check(db_session, context, book, security=book["msft"])

        assert len(reaching_barc.scenarios) == 1
        assert reaching_msft.scenarios == ()

    async def test_a_book_with_no_denominator_says_so_rather_than_showing_a_blank(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        """Nothing bought, nothing funded: there is no whole for a weight to be a share of,
        and a concentration figure with no denominator is a blank the operator has to
        interpret rather than a warning."""
        check = await _check(db_session, context, book, security=book["msft"])

        assert check.says_nothing
        assert check.problem
        assert check.net_assets is None
        assert check.top_holdings is None
        assert check.scenarios == ()


class TestWhatTheBookBecomes:
    """ADR 0137: a weight typed into the check, set against the book by the closing
    section's own arithmetic, in a ledger nobody keeps."""

    async def test_the_holding_the_cash_and_the_five_largest_move_together(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)

        check = await risk_service.check_before_recording(
            db_session,
            context,
            portfolio=book["portfolio"],
            security=book["msft"],
            as_of=AS_OF,
            weight_after=Decimal("0.5"),
        )

        after = check.after
        assert after is not None
        assert after.weight_after.value == Decimal("0.5")
        assert check.held is not None
        assert check.held.weight is not None
        assert after.weight_before.value == check.held.weight.value
        # Funded from cash: the cash falls by exactly what the position rises by.
        moved = Decimal("0.5") - after.weight_before.value
        assert _same(after.cash_after.value, after.cash_before.value - moved)
        # Two holdings, so the five largest are both of them before and after — with this
        # one's weight replaced by the one typed.
        assert check.top_holdings is not None
        assert after.top_before is check.top_holdings
        assert _same(
            after.top_after.value,
            check.top_holdings.value - after.weight_before.value + Decimal("0.5"),
        )

    async def test_the_sector_moves_with_the_listing(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        company = Company(
            name="Microsoft Corporation",
            ticker="MSFT",
            cik="0000789019",
            exchange="NASDAQ",
            sic="7372",
            sic_description="Prepackaged software",
        )
        db_session.add(company)
        await db_session.flush()
        book["msft"].company_id = company.id
        await _two_holdings(db_session, book)
        await db_session.refresh(book["msft"], ["company"])

        check = await risk_service.check_before_recording(
            db_session,
            context,
            portfolio=book["portfolio"],
            security=book["msft"],
            as_of=AS_OF,
            weight_after=Decimal("0.3"),
        )

        after = check.after
        assert after is not None
        assert after.sector == "Prepackaged software"
        assert after.sector_before is not None
        assert after.sector_after is not None
        assert _same(
            after.sector_after.value,
            after.sector_before.value + Decimal("0.3") - after.weight_before.value,
        )

    async def test_a_weight_with_no_listing_named_says_what_it_needs(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)

        check = await risk_service.check_before_recording(
            db_session,
            context,
            portfolio=book["portfolio"],
            security=None,
            as_of=AS_OF,
            weight_after=Decimal("0.1"),
        )

        assert check.after is None
        assert "name the listing" in check.after_problem

    async def test_without_a_weight_the_check_is_as_it_was(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        await _two_holdings(db_session, book)

        check = await _check(db_session, context, book, security=book["msft"])

        assert check.after is None
        assert check.after_problem == ""

    async def test_the_ledger_a_what_if_was_struck_in_cannot_be_kept(
        self, db_session: AsyncSession, book: dict[str, Any], context: CalculationContext
    ) -> None:
        """*Nothing is recorded* enforced where every ledger is written, not promised by
        the page that happens not to write this one."""
        await _two_holdings(db_session, book)
        await risk_service.check_before_recording(
            db_session,
            context,
            portfolio=book["portfolio"],
            security=book["msft"],
            as_of=AS_OF,
            weight_after=Decimal("0.2"),
        )

        with pytest.raises(ValidationError, match="ADR 0137"):
            await persist_context(db_session, context, job_id=uuid.uuid4())


def _same(struck: Decimal, expected: Decimal) -> bool:
    """Equal to well past any displayed place. The engine carries more digits than this
    test's default decimal context, so its sums round later than the test's do."""
    return abs(struck - expected) < Decimal("1e-20")


class TestWhatTheCheckRefusesToDo:
    def test_it_says_in_words_that_the_size_is_a_sentence_and_the_what_if_is_not_kept(
        self,
    ) -> None:
        """ADR 0104 refused a numeric intended size, and ADR 0137 keeps the what-if out of
        the record. The sentence is a constant rather than template prose, so the page and
        any other reader quote the same one."""
        assert "never a number" in risk_service.NO_INTENDED_SIZE
        assert "nothing typed here is recorded" in risk_service.NO_INTENDED_SIZE

    def test_the_service_offers_no_ceiling_and_no_verdict(self) -> None:
        """ADR 0080's list, enforced by there being no function for any of it. A ceiling
        would be the platform stating the operator's risk policy for them, and a verdict on
        a decision would be the recommendation this platform exists not to make."""
        surface = set(risk_service.__all__)
        forbidden = {"ceiling", "limit", "breach", "approve_decision", "score", "rank"}
        assert not {name for name in surface if any(word in name.lower() for word in forbidden)}
        assert not [
            field
            for field in risk_service.PreTradeCheck.__dataclass_fields__
            if any(word in field for word in forbidden)
        ]
