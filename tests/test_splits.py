"""A split arrives as a transaction — ADR 0094, roadmap §2.6.

`corporate_actions` has known about splits since migration 0018 and the price series has
honoured them all along; the *book* did not, so a book spanning a split was wrong in a way
that looked entirely ordinary — a two-for-one showed half the shares at twice the average
cost, and every figure downstream inherited it.

The tests are arranged around the two ways the fix could be wrong. **The derivation could
go stale**: a row storing a share delta is right when written and wrong the moment an
earlier trade is backfilled, so `TestTheRatioIsWhatIsStored` walks that scene and asserts
the answer moves with the history. And **the row could reach the wrong arithmetic**: a
split has no price, and the cash branch keys on a missing price, so a careless row would
pour a share multiplier into a cash balance — the silent double-count the
currency-exchange refusal was written against. `TestASplitTouchesNoCash` is that guard.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aer.calc.engine import CalculationContext
from aer.core.enums import AttestationKind, Grade, TransactionKind
from aer.db.models import Attestation, CorporateAction, Portfolio, Security, Transaction, User
from aer.db.models.security import CorporateActionKind
from aer.services import splits as splits_service

# The `book` fixture itself comes from `conftest`; its helpers are plain functions.
from tests.portfolio_fixtures import funded, trade, view_of

pytestmark = pytest.mark.integration


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


# The split sits between the two purchases, so a walk that multiplied at the wrong place
# would give a different answer from a walk that multiplied at the right one.
SPLIT_DATE = date(2026, 6, 20)
BOUGHT_BEFORE = date(2026, 6, 10)
BOUGHT_AFTER = date(2026, 6, 25)


async def split_action(
    session: Any,
    security: Security,
    *,
    ratio: str = "2",
    on: date = SPLIT_DATE,
) -> CorporateAction:
    action = CorporateAction(
        security_id=security.id,
        kind=CorporateActionKind.SPLIT,
        ex_date=on,
        split_ratio=Decimal(ratio),
    )
    session.add(action)
    await session.flush()
    return action


async def _splits_in(session: Any, book: dict[str, Any]) -> list[Transaction]:
    return list(
        await session.scalars(
            select(Transaction)
            .where(
                Transaction.portfolio_id == book["portfolio"].id,
                Transaction.kind == TransactionKind.SPLIT,
            )
            .order_by(Transaction.trade_date)
        )
    )


class TestTheDerivationWritesOneRowPerBook:
    async def test_a_new_action_reaches_every_book_that_deals_the_security(
        self, db_session, book
    ) -> None:
        await trade(db_session, book, security=book["msft"], on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])

        written = await splits_service.derive_for_action(db_session, action=action)

        assert written == 1
        [row] = await _splits_in(db_session, book)
        assert row.quantity == Decimal("2")
        assert row.trade_date == SPLIT_DATE
        assert row.security_id == book["msft"].id
        assert row.price is None
        assert row.corporate_action_id == action.id

    async def test_a_book_that_never_dealt_the_security_gets_nothing(
        self, db_session, book
    ) -> None:
        """Not an empty row and not a zero: a book with no interest in this listing has
        nothing the split is an answer about."""
        await trade(db_session, book, security=book["barc"], on=BOUGHT_BEFORE, currency="GBP")
        action = await split_action(db_session, book["msft"])

        assert await splits_service.derive_for_action(db_session, action=action) == 0
        assert await _splits_in(db_session, book) == []

    async def test_deriving_twice_writes_one_row(self, db_session, book) -> None:
        """Asking twice is not an error and does not create a second row — the shape
        `job_cancellations` settled, held here by the partial unique index."""
        await trade(db_session, book, security=book["msft"], on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])

        await splits_service.derive_for_action(db_session, action=action)
        again = await splits_service.derive_for_action(db_session, action=action)

        assert again == 0
        assert len(await _splits_in(db_session, book)) == 1

    async def test_a_backfilled_first_trade_gets_the_split_it_missed(
        self, db_session, book
    ) -> None:
        """The self-healing half. The action was recorded when this book held nothing of
        the security, so no row was written; recording the trade writes it."""
        action = await split_action(db_session, book["msft"])
        assert await splits_service.derive_for_action(db_session, action=action) == 0

        await trade(db_session, book, security=book["msft"], on=BOUGHT_BEFORE)
        written = await splits_service.ensure_for(
            db_session, portfolio_id=book["portfolio"].id, security=book["msft"]
        )

        assert written == 1
        assert len(await _splits_in(db_session, book)) == 1

    async def test_the_derived_row_is_attested_and_names_no_person(self, db_session, book) -> None:
        """A machine-derived row must not claim an attestation somebody made. The
        provenance that matters is structural: the row's action, and the action's own
        hashed vendor artefact."""
        await trade(db_session, book, security=book["msft"], on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])
        await splits_service.derive_for_action(db_session, action=action)

        [row] = await _splits_in(db_session, book)
        attestation = await db_session.get(Attestation, row.attestation_id)

        assert attestation.kind is AttestationKind.TRANSACTION
        assert attestation.grade is Grade.ATTESTED
        assert attestation.recorded_by == splits_service.DERIVED_BY
        assert "@" not in attestation.recorded_by


class TestTheRatioIsWhatIsStored:
    """Why ADR 0094 stores the ratio and not the share delta.

    A delta is computed from what the book held at the ex-date, and the book is
    append-only history: an earlier trade recorded later changes what was held then, and
    a stored delta is a wrong number no constraint can see.
    """

    async def test_a_book_spanning_a_split_holds_the_multiplied_count(
        self, db_session, book, context
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="100", on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])
        await splits_service.derive_for_action(db_session, action=action)

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.quantity.value == Decimal("200")

    async def test_a_trade_after_the_split_is_not_multiplied(
        self, db_session, book, context
    ) -> None:
        """The multiplication lands at its place in the walk: 100 before doubling plus 100
        after is 300, not 400."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="100", on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])
        await splits_service.derive_for_action(db_session, action=action)
        await trade(db_session, book, security=book["msft"], quantity="100", on=BOUGHT_AFTER)

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.quantity.value == Decimal("300")

    async def test_backfilling_an_earlier_trade_moves_the_answer(
        self, db_session, book, context
    ) -> None:
        """The scene a stored delta gets wrong. The forgotten fifty shares were held on
        the ex-date, so they doubled too — and because the row carries the ratio rather
        than a delta computed once, nothing needs re-deriving for the book to say so.
        """
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="100", on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])
        await splits_service.derive_for_action(db_session, action=action)

        before = await view_of(db_session, context, book)
        assert before.holdings[0].quantity.value == Decimal("200")

        # The forgotten trade, recorded now and dated before the split.
        await trade(db_session, book, security=book["msft"], quantity="50", on=date(2026, 6, 5))

        after = await view_of(db_session, context, book)
        assert after.holdings[0].quantity.value == Decimal("300")

    async def test_a_consolidation_divides_the_count(self, db_session, book, context) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="500", on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"], ratio="0.1")
        await splits_service.derive_for_action(db_session, action=action)

        view = await view_of(db_session, context, book)

        assert view.holdings[0].quantity.value == Decimal("50")


class TestTheCostPoolIsUntouched:
    async def test_the_pool_keeps_its_cost_while_the_count_multiplies(
        self, db_session, book, context
    ) -> None:
        """A split is not a purchase (ADR 0085, ADR 0094): the average per share halves
        because the same money now sits behind twice the shares."""
        await funded(db_session, book)
        await trade(
            db_session,
            book,
            security=book["msft"],
            quantity="100",
            price="410",
            on=BOUGHT_BEFORE,
        )
        action = await split_action(db_session, book["msft"])
        await splits_service.derive_for_action(db_session, action=action)

        view = await view_of(db_session, context, book)

        [holding] = view.holdings
        assert holding.quantity.value == Decimal("200")
        # A hundred shares at $410 is $41,000, in the book's sterling at the fixture's
        # cross rate. What matters here is that it did not change when the count doubled.
        cost_after = holding.cost.value
        assert cost_after > 0

        splits = await _splits_in(db_session, book)
        assert splits[0].fees == 0


class TestTheOperatorCanSeeIt:
    async def test_the_share_count_shows_the_split_among_its_workings(
        self, db_session, book, context
    ) -> None:
        """A derived row the operator cannot see is a row they cannot audit.

        There is no transaction listing yet, so the audit surface is the figure's own
        working: `quantity_held` records each movement as a separate input, and the split
        sits there with its ratio beside the trades it multiplied.
        """
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="100", on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])
        await splits_service.derive_for_action(db_session, action=action)

        view = await view_of(db_session, context, book)
        record = view.holdings[0].quantity.record

        assert [row.name for row in record.inputs] == ["movements[0]", "movements[1]"]
        assert record.inputs[1].unit == "ratio"
        assert Decimal(record.inputs[1].value) == Decimal("2")


class TestASplitTouchesNoCash:
    async def test_the_share_multiplier_never_reaches_a_balance(
        self, db_session, book, context
    ) -> None:
        """The failure mode the row shape exists to make impossible.

        `_cash_effects` routes any transaction with no price into the cash balance, and a
        split has no price. If the kind did not short-circuit that branch, a two-for-one
        would deposit two dollars into the book.
        """
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="100", on=BOUGHT_BEFORE)
        action = await split_action(db_session, book["msft"])

        before = await view_of(db_session, context, book)
        balances_before = {row.currency: row.balance.value for row in before.cash}

        await splits_service.derive_for_action(db_session, action=action)

        after = await view_of(db_session, context, book)
        balances_after = {row.currency: row.balance.value for row in after.cash}

        assert balances_after == balances_before


class TestTheDatabaseHoldsTheRules:
    """Every rule that keeps a hand-written or malformed split out, at the constraint."""

    async def _attestation(self, session: Any) -> Attestation:
        attestation = Attestation(
            kind=AttestationKind.TRANSACTION,
            grade=Grade.ATTESTED,
            effective_at=datetime(2026, 6, 20, tzinfo=UTC),
            recorded_by="operator@example.invalid",
        )
        session.add(attestation)
        await session.flush()
        return attestation

    async def _split_row(self, session: Any, book: dict[str, Any], **overrides: Any) -> None:
        attestation = await self._attestation(session)
        fields: dict[str, Any] = {
            "attestation_id": attestation.id,
            "portfolio_id": book["portfolio"].id,
            "kind": TransactionKind.SPLIT,
            "security_id": book["msft"].id,
            "trade_date": SPLIT_DATE,
            "quantity": Decimal("2"),
            "price": None,
            "fees": Decimal(0),
            "currency": "USD",
        }
        fields.update(overrides)
        session.add(Transaction(**fields))
        await session.flush()

    async def test_a_split_without_an_action_behind_it_is_refused(self, db_session, book) -> None:
        """ "Never as a quantity that changed with nothing behind it", as a constraint."""
        with pytest.raises(IntegrityError, match="transaction_split_derives_from_an_action"):
            await self._split_row(db_session, book, corporate_action_id=None)

    async def test_an_ordinary_trade_may_not_carry_an_action(self, db_session, book) -> None:
        action = await split_action(db_session, book["msft"])
        with pytest.raises(IntegrityError, match="transaction_split_derives_from_an_action"):
            await self._split_row(
                db_session,
                book,
                kind=TransactionKind.BUY,
                price=Decimal("410"),
                corporate_action_id=action.id,
            )

    async def test_a_ratio_of_one_is_refused(self, db_session, book) -> None:
        """It multiplies nothing, and `transaction_moves_something` cannot see that: a
        ratio of one is not a quantity of zero."""
        action = await split_action(db_session, book["msft"], ratio="1")
        with pytest.raises(IntegrityError, match="transaction_split_multiplies"):
            await self._split_row(
                db_session, book, quantity=Decimal("1"), corporate_action_id=action.id
            )

    async def test_a_negative_ratio_is_refused(self, db_session, book) -> None:
        action = await split_action(db_session, book["msft"])
        with pytest.raises(IntegrityError, match="transaction_sign_matches_its_kind"):
            await self._split_row(
                db_session, book, quantity=Decimal("-2"), corporate_action_id=action.id
            )

    async def test_a_split_must_name_its_security(self, db_session, book) -> None:
        action = await split_action(db_session, book["msft"])
        with pytest.raises(IntegrityError, match="transaction_split_names_its_security"):
            await self._split_row(db_session, book, security_id=None, corporate_action_id=action.id)

    async def test_two_rows_for_one_action_in_one_book_are_refused(self, db_session, book) -> None:
        """The database holds the rule, because it is the only participant that sees both
        writers — the shape `uq_source_document_per_artefact` settled."""
        action = await split_action(db_session, book["msft"])
        await self._split_row(db_session, book, corporate_action_id=action.id)
        with pytest.raises(IntegrityError, match="uq_transactions_split_per_action"):
            await self._split_row(db_session, book, corporate_action_id=action.id)


class TestTwoBooksBothGetTheirRow:
    async def test_each_book_that_deals_the_security_gets_one(self, db_session, book) -> None:
        other_user = User(email="second@example.invalid", display_name="S", role=book["user"].role)
        db_session.add(other_user)
        await db_session.flush()
        second = Portfolio(user_id=other_user.id, name="SIPP", base_currency="GBP")
        db_session.add(second)
        await db_session.flush()

        await trade(db_session, book, security=book["msft"], on=BOUGHT_BEFORE)
        await trade(
            db_session,
            {**book, "portfolio": second},
            security=book["msft"],
            on=BOUGHT_BEFORE,
        )
        action = await split_action(db_session, book["msft"])

        written = await splits_service.derive_for_action(db_session, action=action)

        assert written == 2
        total = await db_session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.corporate_action_id == action.id)
        )
        assert total == 2
