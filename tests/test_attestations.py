"""The fourth record kind, and the promise it had to make to get in.

`SourceKind` said "three, and the list is deliberately closed — a fourth kind would be a way
in for a number with no story". ADR 0073 read that as the bar rather than the wall: a fill
price is none of the three, and what it had to show was a story of its own — two times, a
named assertor, and a grade of evidence that propagates.

**The grade propagating is the whole decision, and it is the thing that would be quietly
lost.** A flag would have been easier and would have failed the first time somebody under
time pressure decided *this* surface was fine. So the tests that matter here are the ones
that would notice a typed figure reaching a shareable surface: `TestTheGradeTravelsUpwards`
walks a lineage three deep, and `TestNothingCanPrintAWithheldFigure` asserts the absence of
a field rather than the value of a flag.

The schema tests are the other half. Each one is a way a portfolio could hold a number that
looks ordinary and is wrong: a sell that adds shares, a documented row with no document, a
correction that forked the history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aer.calc.attestation import Attested, Graded, grade_of, weakest_grade
from aer.calc.basic import growth_rate
from aer.calc.engine import CalculationContext, traced
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    SourceTable,
    UnsourcedValueError,
    money,
)
from aer.core.enums import (
    AttestationKind,
    Grade,
    Provider,
    SourceTier,
    TransactionKind,
    UserRole,
)
from aer.db.models import (
    Artefact,
    Attestation,
    Portfolio,
    Security,
    SourceDocument,
    Transaction,
    User,
    WorkOrder,
)
from aer.services import calculations as calculation_service

pytestmark = pytest.mark.integration

DEALT_ON = date(2026, 6, 15)
EFFECTIVE = datetime(2026, 6, 15, 16, 30, tzinfo=UTC)


# -- The pure half. No database, because the containment must not need one. -------------------


def attested(label: str) -> Quantity:
    return money(
        Decimal("100"),
        "USD",
        source=SourceRef.attestation(uuid.uuid4(), grade=Grade.ATTESTED, label=label),
    )


def documented(label: str) -> Quantity:
    return money(
        Decimal("100"),
        "USD",
        source=SourceRef.attestation(uuid.uuid4(), grade=Grade.DOCUMENTED, label=label),
    )


def filed(value: str = "100") -> Quantity:
    return money(
        Decimal(value), "USD", source=SourceRef.financial_fact(uuid.uuid4(), label="revenue")
    )


@traced(name="total", formula="total = left + right")
def total(_context: CalculationContext, *, left: Quantity, right: Quantity) -> Quantity:
    return left + right


class TestAReferenceCannotBeVagueAboutEvidence:
    def test_an_attestation_without_a_grade_is_refused(self) -> None:
        """The one way an unmarked operator figure could enter the lineage.

        A grade that defaulted would be a decision made once, invisibly, for every call
        site that did not think about it — and whichever way it fell it would be wrong.
        """
        with pytest.raises(UnsourcedValueError, match="has no grade"):
            SourceRef(
                kind=SourceKind.ATTESTATION,
                identifier="x",
                table=SourceTable.ATTESTATIONS,
            )

    def test_a_filing_carrying_a_grade_is_refused(self) -> None:
        # The other direction, and it matters as much: a grade on a financial fact is a
        # claim about evidence that nothing checks, and it would read as though something
        # had.
        with pytest.raises(CalculationError, match="carries a grade"):
            SourceRef(
                kind=SourceKind.FACT,
                identifier="x",
                table=SourceTable.FINANCIAL_FACTS,
                grade=Grade.DOCUMENTED,
            )

    def test_the_constructor_makes_the_grade_impossible_to_forget(self) -> None:
        made = SourceRef.attestation("x", grade=Grade.ATTESTED, label="a trade")

        assert made.kind is SourceKind.ATTESTATION
        assert made.table is SourceTable.ATTESTATIONS
        assert made.grade is Grade.ATTESTED


class TestTheWeakestGradeWins:
    def test_one_typed_input_among_documented_ones_taints_the_set(self) -> None:
        # Not the most common grade and not the newest. Averaging evidence is how a book of
        # one typed holding and nineteen documented ones comes to read as documented.
        mixed = [documented("a").source, documented("b").source, attested("c").source]

        assert weakest_grade([source for source in mixed if source]) is Grade.ATTESTED

    def test_a_set_with_no_attestation_in_it_is_documented(self) -> None:
        assert weakest_grade([filed().source]) is Grade.DOCUMENTED  # type: ignore[list-item]

    def test_an_empty_set_is_documented(self) -> None:
        # A calculation over nothing but constants has nothing to withhold.
        assert weakest_grade([]) is Grade.DOCUMENTED


class TestTheGradeTravelsUpwards:
    """ADR 0073's promise: the *lineage*, not the node.

    One typed exchange rate three levels down taints the net asset value computed above it,
    because a figure is only as evidenced as its weakest input. A grade that stopped at the
    first level would pass every test written about one calculation and fail on the first
    real book.
    """

    def test_a_figure_three_levels_above_a_typed_input_is_withheld(self) -> None:
        context = CalculationContext(code_version="test")
        first = total(context, left=attested("a fill nobody documented"), right=filed())
        second = total(context, left=first, right=filed())
        third = total(context, left=second, right=filed())

        graded = grade_of(context, context.records[-1])

        assert isinstance(graded, Attested)
        assert graded.attested_inputs == ("a fill nobody documented",)
        assert third.value == Decimal("400")  # the arithmetic happened; only sharing is refused

    def test_a_documented_lineage_carries_its_figure(self) -> None:
        context = CalculationContext(code_version="test")
        first = total(context, left=documented("a contract note"), right=filed())
        total(context, left=first, right=filed())

        graded = grade_of(context, context.records[-1])

        assert isinstance(graded, Graded)
        assert graded.grade is Grade.DOCUMENTED
        assert graded.quantity.value == Decimal("300")

    def test_every_typed_input_is_named_once_however_the_walk_ordered_them(self) -> None:
        context = CalculationContext(code_version="test")
        left = total(context, left=attested("zeta"), right=attested("alpha"))
        right = total(context, left=attested("alpha"), right=filed())
        total(context, left=left, right=right)

        graded = grade_of(context, context.records[-1])

        assert isinstance(graded, Attested)
        # Sorted and deduplicated: a disclosure that reworded itself between two runs over
        # identical data is one a reader learns to distrust.
        assert graded.attested_inputs == ("alpha", "zeta")

    def test_a_lineage_with_a_hole_in_it_refuses_rather_than_grading_the_readable_part(
        self,
    ) -> None:
        orphan = CalculationContext(code_version="test")
        inner = total(orphan, left=filed(), right=filed())
        total(orphan, left=inner, right=filed())

        # The record exists and cites a calculation. The ledger being graded does not hold
        # that child, so the only honest answers are "refuse" and "grade the part I could
        # read" — and the second is a claim about the readable part wearing the whole
        # figure's name.
        with pytest.raises(CalculationError, match="does not hold it"):
            grade_of(CalculationContext(code_version="test"), orphan.records[-1])


class TestNothingCanPrintAWithheldFigure:
    def test_the_withheld_object_has_no_figure_in_it_at_all(self) -> None:
        """The mechanism, asserted as an absence.

        A flag would leave the number in the object with a boolean beside it saying not to
        show it, and every future template is one `if` away from showing it anyway. Nobody
        argues with a field that does not exist.
        """
        withheld = Attested(label="net asset value", attested_inputs=("a typed rate",))

        assert not hasattr(withheld, "quantity")
        assert not hasattr(withheld, "value")
        assert Attested.__slots__ == ("label", "attested_inputs")

    def test_the_disclosure_says_what_is_missing_and_where_to_see_it(self) -> None:
        withheld = Attested(label="Net asset value", attested_inputs=("a typed rate",))

        sentence = withheld.as_sentence()

        assert "1 figure(s) the operator typed" in sentence
        assert "a typed rate" in sentence
        assert "operator's own copy" in sentence

    def test_a_long_list_is_counted_rather_than_recited(self) -> None:
        withheld = Attested(label="NAV", attested_inputs=("a", "b", "c", "d", "e"))

        assert "and 2 other(s)" in withheld.as_sentence()


# -- The schema half. Each of these is a way to hold a wrong number that looks right. ---------


@pytest.fixture
async def book(db_session: Any) -> dict[str, Any]:
    user = User(email="book@example.invalid", display_name="B", role=UserRole.OWNER)
    security = Security(
        ticker="MSFT", exchange="NASDAQ", provider_symbol="MSFT.US", quote_currency="USD"
    )
    artefact = Artefact(
        sha256="f" * 64, size_bytes=64, media_type="application/pdf", storage_key="ff/f"
    )
    db_session.add_all([user, security, artefact])
    await db_session.flush()

    portfolio = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
    order = WorkOrder(user_id=user.id, as_of_date=DEALT_ON, point_in_time=False)
    db_session.add_all([portfolio, order])
    await db_session.flush()

    note = SourceDocument(
        work_order_id=order.id,
        artefact_id=artefact.id,
        url="file:///contract-notes/2026-06-15.pdf",
        provider=Provider.USER_SUPPLIED,
        source_tier=SourceTier.T5_SECONDARY,
        title="Contract note, 15 June 2026",
        retrieved_at=datetime.now(UTC),
    )
    db_session.add(note)
    await db_session.flush()

    return {"user": user, "portfolio": portfolio, "security": security, "note": note}


def an_attestation(**overrides: Any) -> Attestation:
    fields: dict[str, Any] = {
        "kind": AttestationKind.TRANSACTION,
        "grade": Grade.ATTESTED,
        "effective_at": EFFECTIVE,
        "recorded_by": "book@example.invalid",
    }
    fields.update(overrides)
    return Attestation(**fields)


def a_trade(book: dict[str, Any], attestation: Attestation, **overrides: Any) -> Transaction:
    fields: dict[str, Any] = {
        "attestation_id": attestation.id,
        "portfolio_id": book["portfolio"].id,
        "kind": TransactionKind.BUY,
        "security_id": book["security"].id,
        "trade_date": DEALT_ON,
        "quantity": Decimal("100"),
        "price": Decimal("410.25"),
        "fees": Decimal("9.95"),
        "currency": "USD",
    }
    fields.update(overrides)
    return Transaction(**fields)


class TestTheGradeCannotDriftFromTheEvidence:
    async def test_a_documented_row_must_name_its_document(self, db_session, book) -> None:
        # Otherwise "documented" is a word somebody typed, which is precisely the figure the
        # grade exists to distinguish itself from.
        db_session.add(an_attestation(grade=Grade.DOCUMENTED))

        with pytest.raises(IntegrityError, match="attestation_grade_matches_its_evidence"):
            await db_session.flush()

    async def test_a_typed_row_must_not_name_one(self, db_session, book) -> None:
        # A document attached to a row still marked attested is a document nobody read, and
        # the figure above it would be withheld for a reason that no longer holds.
        db_session.add(an_attestation(grade=Grade.ATTESTED, source_document_id=book["note"].id))

        with pytest.raises(IntegrityError, match="attestation_grade_matches_its_evidence"):
            await db_session.flush()

    async def test_a_documented_row_with_its_note_is_accepted(self, db_session, book) -> None:
        row = an_attestation(grade=Grade.DOCUMENTED, source_document_id=book["note"].id)
        db_session.add(row)
        await db_session.flush()

        assert row.is_documented


class TestACorrectionIsANewRow:
    async def test_a_row_can_be_superseded(self, db_session, book) -> None:
        original = an_attestation(note="entered 1,000 and meant 100")
        db_session.add(original)
        await db_session.flush()

        correction = an_attestation(supersedes_id=original.id)
        db_session.add(correction)
        await db_session.flush()

        assert correction.supersedes_id == original.id

    async def test_the_history_cannot_fork(self, db_session, book) -> None:
        """Two corrections of one entry, which is what a UNIQUE on `supersedes_id` refuses.

        With both stored, "the current value" would depend on which branch a reader walked
        — and a portfolio has no way to choose between them.
        """
        original = an_attestation()
        db_session.add(original)
        await db_session.flush()
        db_session.add(an_attestation(supersedes_id=original.id))
        await db_session.flush()

        db_session.add(an_attestation(supersedes_id=original.id))

        with pytest.raises(IntegrityError, match="uq_attestations_supersedes_once"):
            await db_session.flush()

    async def test_a_row_cannot_supersede_itself(self, db_session, book) -> None:
        row = an_attestation()
        db_session.add(row)
        await db_session.flush()

        row.supersedes_id = row.id

        with pytest.raises(IntegrityError, match="does_not_supersede_itself"):
            await db_session.flush()


class TestASellCannotAddShares:
    """The check that stops a mis-signed entry producing an ordinary-looking position.

    A sell entered as +100 would *add* to a holding, and nothing downstream would look
    wrong: the quantity is a plausible number, the price is right, the security exists. The
    database refuses it rather than leaving the negation to whichever form was used.
    """

    async def test_a_sell_entered_positive_is_refused(self, db_session, book) -> None:
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        db_session.add(
            a_trade(book, attestation, kind=TransactionKind.SELL, quantity=Decimal("100"))
        )

        with pytest.raises(IntegrityError, match="transaction_sign_matches_its_kind"):
            await db_session.flush()

    async def test_a_sell_entered_negative_is_accepted(self, db_session, book) -> None:
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        row = a_trade(book, attestation, kind=TransactionKind.SELL, quantity=Decimal("-100"))
        db_session.add(row)
        await db_session.flush()

        assert row.quantity == Decimal("-100")

    async def test_a_fee_entered_positive_is_refused(self, db_session, book) -> None:
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        db_session.add(
            a_trade(
                book,
                attestation,
                kind=TransactionKind.FEE,
                security_id=None,
                price=None,
                quantity=Decimal("9.95"),
                currency="GBP",
            )
        )

        with pytest.raises(IntegrityError, match="transaction_sign_matches_its_kind"):
            await db_session.flush()

    async def test_a_transaction_that_moves_nothing_is_refused(self, db_session, book) -> None:
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        db_session.add(a_trade(book, attestation, quantity=Decimal("0")))

        with pytest.raises(IntegrityError, match="transaction_moves_something"):
            await db_session.flush()


class TestOnlyDealingHasAPrice:
    async def test_a_dividend_with_a_price_is_refused(self, db_session, book) -> None:
        # Cash has no price in its own currency, and a "price" on a dividend is a number
        # nothing downstream could interpret.
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        db_session.add(
            a_trade(
                book,
                attestation,
                kind=TransactionKind.DIVIDEND,
                quantity=Decimal("50"),
                price=Decimal("1"),
            )
        )

        with pytest.raises(IntegrityError, match="transaction_price_is_for_dealing_only"):
            await db_session.flush()

    async def test_a_buy_without_one_is_refused(self, db_session, book) -> None:
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        db_session.add(a_trade(book, attestation, price=None))

        with pytest.raises(IntegrityError, match="transaction_price_is_for_dealing_only"):
            await db_session.flush()

    async def test_a_dividend_may_still_name_the_holding_it_came_from(
        self, db_session, book
    ) -> None:
        # The distinction the two checks together are for: a dividend has a security and no
        # price, which a single "priced things have securities" rule would have forbidden.
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        row = a_trade(
            book,
            attestation,
            kind=TransactionKind.DIVIDEND,
            quantity=Decimal("50"),
            price=None,
        )
        db_session.add(row)
        await db_session.flush()

        assert row.security_id == book["security"].id


class TestATransactionIsAnAttestationSeenFromBelow:
    async def test_it_shares_the_attestation_s_key(self, db_session, book) -> None:
        # A separate id with a unique foreign key would allow a trade with no attestation
        # above it — no assertor, no grade, no two times.
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()
        db_session.add(a_trade(book, attestation))
        await db_session.flush()

        found = await db_session.scalar(select(Transaction))

        assert found.attestation_id == attestation.id

    async def test_deleting_the_attestation_takes_the_transaction(self, db_session, book) -> None:
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()
        db_session.add(a_trade(book, attestation))
        await db_session.flush()

        await db_session.delete(attestation)
        await db_session.flush()

        assert await db_session.scalar(select(Transaction)) is None

    async def test_the_two_times_are_stored_separately(self, db_session, book) -> None:
        """Dealt Monday, entered Thursday, and both dates are the answer to a question.

        A portfolio shown as at Tuesday must include it; a record of what was *known* on
        Tuesday must not. One column could serve neither.
        """
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()

        assert attestation.effective_at == EFFECTIVE
        assert attestation.recorded_at != EFFECTIVE


class TestAnAttestationIsALeafSomebodyCanWalkTo:
    async def test_the_node_names_the_grade_the_arithmetic_stood_on(self, db_session, book) -> None:
        """Not the grade the row carries now.

        A trade entered from memory and documented a month later is documented today, and
        was not when the figure above it was struck. A node reporting the current grade
        would retroactively evidence arithmetic that ran on somebody's word.
        """
        attestation = an_attestation()
        db_session.add(attestation)
        await db_session.flush()
        db_session.add(a_trade(book, attestation))
        await db_session.flush()

        stored = calculation_service._StoredInput.of(
            {
                "name": "quantity",
                "value": "100",
                "unit": "shares",
                "source": {
                    "kind": SourceKind.ATTESTATION.value,
                    "id": str(attestation.id),
                    "table": SourceTable.ATTESTATIONS.value,
                    "label": "MSFT buy",
                    "grade": Grade.ATTESTED.value,
                },
            }
        )
        attestation.grade = Grade.DOCUMENTED
        attestation.source_document_id = book["note"].id
        await db_session.flush()

        node = await calculation_service._attestation_node(db_session, stored)

        assert node is not None
        assert node.kind == "attestation"
        assert node.detail["grade"] == Grade.ATTESTED.value
        assert node.detail["grade_now"] == Grade.DOCUMENTED.value
        assert node.detail["recorded_by"] == "book@example.invalid"

    async def test_a_reference_to_a_row_that_is_gone_is_reported_rather_than_hidden(
        self, db_session, book
    ) -> None:
        stored = calculation_service._StoredInput.of(
            {
                "name": "quantity",
                "value": "100",
                "unit": "shares",
                "source": {
                    "kind": SourceKind.ATTESTATION.value,
                    "id": str(uuid.uuid4()),
                    "table": SourceTable.ATTESTATIONS.value,
                    "grade": Grade.ATTESTED.value,
                },
            }
        )

        assert await calculation_service._attestation_node(db_session, stored) is None


class TestTheLedgerWritesTheGradeDown:
    def test_a_stored_input_carries_the_grade_into_the_json(self) -> None:
        # The property that makes a persisted lineage gradeable without a second query, and
        # replayable without asking the row what it says today.
        context = CalculationContext(code_version="test")
        total(context, left=attested("a typed fill"), right=filed())

        stored = context.records[0].as_dict()
        graded = [row for row in stored["inputs"] if row["source"].get("grade")]

        assert len(graded) == 1
        assert graded[0]["source"]["grade"] == Grade.ATTESTED.value

    def test_a_filing_writes_no_grade_at_all(self) -> None:
        # Absent rather than null, so a stored row says "not applicable" by silence and
        # nobody is invited to wonder what a null grade on a 10-K would mean.
        context = CalculationContext(code_version="test")
        growth_rate(context, start=filed("100"), end=filed("110"))

        stored = context.records[0].as_dict()

        assert all("grade" not in row["source"] for row in stored["inputs"])
