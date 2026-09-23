"""One holding, in full: how it was built, what it is worth, and what it does to the book.

Page specification §3. Everything on the position page is computed on the way to it from
the same walk the portfolio screen makes (ADR 0083): the book as at the date, the trades in
force, and the exposure cuts — so a figure here is the figure the book shows, never a
second reading of it. **Nothing is recomputed client-side and nothing is stored**; a
position is a view over the transactions, and the realised half of a partly closed one is
one more traced calculation over the same rows (:func:`aer.calc.portfolio.realised_gain`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import portfolio as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import CalculationError, Quantity, Unit
from aer.core.enums import DecisionAction, TransactionKind
from aer.db.models import Company, Decision, Portfolio, Security, Thesis, Transaction, User
from aer.services import calculations as calculation_service
from aer.services import decisions as decision_service
from aer.services import performance as performance_service
from aer.services import portfolio as portfolio_service
from aer.services import theses as thesis_service
from aer.services.portfolio import CLOSED, Figure, HoldingRow, graded_figure

__all__ = ["PositionDetail", "position_as_at"]

_log = structlog.get_logger("aer.services.positions")


@dataclass(frozen=True, slots=True)
class PositionDetail:
    """One security the book holds or held, as at the date, with everything behind it."""

    portfolio: Portfolio
    as_of: date
    security: Security
    company: Company | None
    holding: HoldingRow
    # The trades that built it, oldest first — the ledger the figures walk.
    ledger: tuple[Transaction, ...]
    # What the disposals made or lost against the pool's average; ``None`` where nothing
    # was ever sold, since a position nobody has sold from has realised nothing to show.
    realised: Figure | None
    # The sector cut the listing sits in, and how much of the book that cut is.
    sector: performance_service.ExposureSlice | None
    # The largest holdings' share of the book, which this position may or may not be in.
    top_holdings: Figure | None
    in_top_holdings: bool
    # What the operator has written about the company, and decided.
    theses: tuple[Thesis, ...]
    decisions: tuple[Decision, ...]

    @property
    def is_closed(self) -> bool:
        return self.holding.problem == CLOSED

    @property
    def is_partly_closed(self) -> bool:
        return self.realised is not None and not self.is_closed

    @property
    def average(self) -> Figure | None:
        """The pool's cost per share, as the book's own row carries it."""
        return self.holding.average


async def position_as_at(
    session: AsyncSession,
    *,
    user: User,
    security_id: uuid.UUID,
    as_of: date | None = None,
) -> PositionDetail | None:
    """The position in ``user``'s book for one listing, or ``None`` for no such holding.

    ``None`` for a listing the book has never dealt as much as for one that does not
    exist: a position page is reached from the book's own rows, and an id from elsewhere
    is not a position of this person's.
    """
    book = await portfolio_service.default_book(session, user_id=user.id)
    if book is None:
        return None
    security = await session.get(Security, security_id)
    if security is None:
        return None
    dated = as_of or await portfolio_service.latest_close(session, portfolio=book)
    context = calculation_service.new_context()
    view = await portfolio_service.book_as_at(session, context, portfolio=book, as_of=dated)
    holding = next((row for row in view.holdings if row.security.id == security.id), None)
    if holding is None:
        return None

    trades = [
        trade
        for trade in await portfolio_service.transactions_in_force(
            session, portfolio=book, as_of=dated
        )
        if trade.security_id == security.id and trade.kind not in portfolio_service.CASH_KINDS
    ]
    exposure = await performance_service.exposure_as_at(
        session, context, portfolio=book, as_of=dated, view=view
    )
    company = (
        await session.get(Company, security.company_id) if security.company_id is not None else None
    )
    theses: tuple[Thesis, ...] = ()
    decisions: tuple[Decision, ...] = ()
    if company is not None:
        theses = tuple(
            thesis
            for thesis in await thesis_service.theses_for(session, user_id=user.id)
            if thesis.subject_kind == "company" and thesis.subject_id == company.id
        )
        thesis_ids = {thesis.id for thesis in theses}
        decisions = tuple(
            decision
            for decision in await decision_service.decisions_for(session, user_id=user.id)
            if decision.thesis_id in thesis_ids and decision.action is not DecisionAction.PASS
        )

    return PositionDetail(
        portfolio=book,
        as_of=dated,
        security=security,
        company=company,
        holding=holding,
        ledger=tuple(trades),
        realised=_realised(context, trades),
        sector=_sector_of(exposure, ticker=security.ticker),
        top_holdings=exposure.top_holdings,
        in_top_holdings=_in_top_holdings(exposure, ticker=security.ticker),
        theses=theses,
        decisions=decisions,
    )


def _realised(context: CalculationContext, trades: list[Transaction]) -> Figure | None:
    """The disposals' gain against the pool, or ``None`` where nothing was ever sold."""
    if not any(trade.kind is TransactionKind.SELL for trade in trades):
        return None
    try:
        movements = [portfolio_service.movement_of(trade) for trade in trades]
        costs = [portfolio_service.acquisition_cost_of(context, trade) for trade in trades]
        proceeds = [_proceeds(context, trade) for trade in trades]
        gain = calc.realised_gain(
            context, movements=movements, acquisition_costs=costs, proceeds=proceeds
        )
    except CalculationError as problem:
        _log.info("positions.realised_unreadable", reason=str(problem))
        return None
    return graded_figure(context, gain)


def _proceeds(context: CalculationContext, trade: Transaction) -> Quantity:
    """What a disposal fetched net of its dealing costs, or a sourced nil for anything else."""
    money = Unit.currency(trade.currency)
    if trade.kind is not TransactionKind.SELL or trade.price is None:
        return Quantity.of(
            Decimal(0), money, source=portfolio_service.source_of(trade, "no proceeds")
        )
    # The cash a sale brought in is exactly the dealt cash effect, which is positive for a
    # disposal: the traced calculation the cash balance already rests on.
    return calc.dealt_cash_effect(
        context,
        quantity=portfolio_service.movement_of(trade),
        price=Quantity.of(
            trade.price, money / calc.SHARES, source=portfolio_service.source_of(trade, "price")
        ),
        fees=Quantity.of(trade.fees, money, source=portfolio_service.source_of(trade, "fees")),
    )


def _sector_of(
    exposure: performance_service.ExposureView, *, ticker: str
) -> performance_service.ExposureSlice | None:
    band = next((band for band in exposure.bands if band.kind == "sector"), None)
    if band is None:
        return None
    for row in (*band.slices, *([band.unknown] if band.unknown else [])):
        if ticker in row.members:
            return row
    return None


def _in_top_holdings(exposure: performance_service.ExposureView, *, ticker: str) -> bool:
    band = next((band for band in exposure.bands if band.kind == "holding"), None)
    if band is None:
        return False
    largest = sorted(band.slices, key=lambda row: row.share.value, reverse=True)
    return any(ticker in row.members for row in largest[: performance_service.CONCENTRATION_COUNT])


async def security_of(session: AsyncSession, *, ticker: str, exchange: str) -> Security | None:
    found: Security | None = await session.scalar(
        select(Security).where(Security.ticker == ticker, Security.exchange == exchange)
    )
    return found
