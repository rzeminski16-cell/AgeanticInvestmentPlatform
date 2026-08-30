"""One book, shared by every test that needs one.

A sterling portfolio, a US listing quoted in dollars, a London listing quoted in pence,
and the two ECB legs a GBP/USD cross divides — plus the helpers that put trades into it.

**Here rather than in a test module** because three suites now need the same book:
`test_portfolio_service.py` (the assembly), `test_splits.py` (ADR 0094's derivation), and
whatever comes next. Two fixtures called `book` that drifted apart would be two books
called the same thing, and a test passing against the wrong one proves nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from aer.calc.engine import CalculationContext
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
    FxRateRow,
    Portfolio,
    PriceBar,
    Security,
    SourceDocument,
    Transaction,
    User,
    WorkOrder,
)
from aer.services import portfolio as portfolio_service

__all__ = [
    "AS_OF",
    "BOUGHT_ON",
    "book",
    "funded",
    "trade",
    "view_of",
]


AS_OF = date(2026, 6, 30)
BOUGHT_ON = date(2026, 6, 15)


@pytest.fixture
async def book(db_session: Any) -> dict[str, Any]:
    """A sterling book, a US listing, a London listing, and a rate to join them."""
    user = User(email="book@example.invalid", display_name="B", role=UserRole.OWNER)
    artefact = Artefact(sha256="c" * 64, size_bytes=32, media_type="text/csv", storage_key="cc/c")
    db_session.add_all([user, artefact])
    await db_session.flush()

    portfolio = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
    order = WorkOrder(user_id=user.id, as_of_date=AS_OF, point_in_time=False)
    msft = Security(
        ticker="MSFT", exchange="NASDAQ", provider_symbol="MSFT.US", quote_currency="USD"
    )
    # Quoted in pence, which is the per-cent trap wearing a hat: a close of 250 means £2.50.
    barc = Security(ticker="BARC", exchange="LSE", provider_symbol="BARC.LSE", quote_currency="GBX")
    db_session.add_all([portfolio, order, msft, barc])
    await db_session.flush()

    document = SourceDocument(
        work_order_id=order.id,
        artefact_id=artefact.id,
        url="https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        provider=Provider.ECB,
        source_tier=SourceTier.T3_OFFICIAL_STATS,
        title="ECB euro reference rates",
        retrieved_at=datetime.now(UTC),
    )
    db_session.add(document)
    await db_session.flush()

    db_session.add_all(
        [
            PriceBar(
                security_id=msft.id,
                bar_date=AS_OF,
                open=Decimal("400"),
                high=Decimal("420"),
                low=Decimal("399"),
                close=Decimal("410"),
            ),
            PriceBar(
                security_id=barc.id,
                bar_date=AS_OF,
                open=Decimal("248"),
                high=Decimal("252"),
                low=Decimal("247"),
                close=Decimal("250"),
            ),
            # The two ECB legs a GBP/USD cross divides. 1.0705 dollars and 0.84645 pounds
            # per euro, so a dollar is 0.790705... pounds.
            FxRateRow(
                base="EUR",
                quote="USD",
                observed_on=AS_OF,
                vintage=AS_OF,
                rate=Decimal("1.0705"),
                source_document_id=document.id,
                artefact_sha256="c" * 64,
            ),
            FxRateRow(
                base="EUR",
                quote="GBP",
                observed_on=AS_OF,
                vintage=AS_OF,
                rate=Decimal("0.84645"),
                source_document_id=document.id,
                artefact_sha256="c" * 64,
            ),
        ]
    )
    await db_session.flush()

    return {
        "user": user,
        "portfolio": portfolio,
        "msft": msft,
        "barc": barc,
        "document": document,
    }


async def trade(
    session: Any,
    book: dict[str, Any],
    *,
    kind: TransactionKind = TransactionKind.BUY,
    security: Security | None = None,
    quantity: str = "100",
    price: str | None = "410",
    fees: str = "0",
    currency: str = "USD",
    on: date = BOUGHT_ON,
    at_hour: int = 10,
    grade: Grade = Grade.ATTESTED,
    supersedes: Attestation | None = None,
    recorded_at: datetime | None = None,
) -> Attestation:
    attestation = Attestation(
        kind=AttestationKind.TRANSACTION,
        grade=grade,
        effective_at=datetime(on.year, on.month, on.day, at_hour, 0, tzinfo=UTC),
        recorded_by="book@example.invalid",
        source_document_id=book["document"].id if grade is Grade.DOCUMENTED else None,
        supersedes_id=supersedes.id if supersedes is not None else None,
    )
    session.add(attestation)
    await session.flush()
    if recorded_at is not None:
        attestation.recorded_at = recorded_at
    session.add(
        Transaction(
            attestation_id=attestation.id,
            portfolio_id=book["portfolio"].id,
            kind=kind,
            security_id=security.id if security is not None else None,
            trade_date=on,
            quantity=Decimal(quantity),
            price=Decimal(price) if price is not None else None,
            fees=Decimal(fees),
            currency=currency,
        )
    )
    await session.flush()
    return attestation


async def funded(
    session: Any,
    book: dict[str, Any],
    amount: str = "100000",
    *,
    grade: Grade = Grade.ATTESTED,
) -> None:
    """Money in the account before anything is bought.

    Every test that buys needs this. Without it the book is a holding and the negative cash
    that paid for it, which nets to roughly nothing — a real state, and not the one any of
    these tests is about.
    """
    await trade(
        session,
        book,
        kind=TransactionKind.DEPOSIT,
        security=None,
        price=None,
        quantity=amount,
        currency="GBP",
        on=date(2026, 6, 1),
        grade=grade,
    )


async def view_of(session: Any, context: CalculationContext, book: dict[str, Any], **kwargs: Any):
    return await portfolio_service.book_as_at(
        session, context, portfolio=book["portfolio"], as_of=kwargs.get("as_of", AS_OF)
    )
