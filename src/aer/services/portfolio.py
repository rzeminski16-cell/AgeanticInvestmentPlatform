"""The book as at a date: rows in, recorded calculations out.

The seam between ``transactions`` and :mod:`aer.calc.portfolio`. Everything
database-shaped lives here so the arithmetic stays testable without a session, and so a
figure's whole story — the trades, the mark, the rate — is assembled in one place a reader
can follow.

**Nothing is stored and nothing is cached.** There is no ``positions`` table (ADR 0083), so
every figure on the screen is computed on the way to it. That is genuinely slower than
reading seven columns and it is the correct trade for the one screen in the platform where
a wrong number has a cost measured in money.

**Nothing is persisted either, and that is a decision rather than an omission.** A page load
is not a run: it has no job to hang a ledger off, and writing a few hundred ``calculations``
rows on every GET would make a read a writer. The calculations are recorded in the sense
that matters — each carries its formula, its inputs and the code version — and they live in
the :class:`~aer.calc.engine.CalculationContext` the caller passes in, where a lineage view
and :func:`aer.calc.attestation.grade_of` can both reach them. A run that wants them on disk
persists that context the way every other run does.

**A correction is the current record.** An attestation superseded by another is not in the
book; the row that supersedes it is. That is a fact about the *record* rather than about the
world, so it applies at every as-of date — "I entered 1,000 and meant 100" was always 100,
and a screen that showed 1,000 for June because the correction came in July would be
reporting a keystroke rather than a holding.

**A holding nobody can price does not become zero.** It is carried with the reason, and the
net asset value is refused rather than stated short: a total missing a position is worse
than no total, because it looks like an answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aer.calc import portfolio as calc
from aer.calc.attestation import Attested, Graded, grade_of
from aer.calc.engine import CalculationContext, CalculationRecord
from aer.calc.prices import MINOR_UNITS, price_in_major_units
from aer.calc.units import CalculationError, Quantity, SourceRef, Unit
from aer.core.enums import Grade, TransactionKind
from aer.db.models import Attestation, PriceBar, Security, Transaction
from aer.services import fx as fx_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Portfolio

__all__ = [
    "CASH_KINDS",
    "CLOSED",
    "EMPTY",
    "CashRow",
    "Figure",
    "HoldingRow",
    "PortfolioView",
    "book_as_at",
    "graded_figure",
    "in_base",
    "transactions_in_force",
]

_log = structlog.get_logger("aer.services.portfolio")

# The kinds whose quantity is money rather than units of a security. Named here so "is this
# a dealt trade?" has one answer, and so a kind added to the enum without a decision about
# its cash treatment fails loudly in `_movement` rather than being silently pooled.
# What a row says instead of a value when the position is gone. A constant because the
# totalling pass reads it: a closed holding must not be counted as unpriced and refuse the
# whole net asset value.
CLOSED = "closed"

# What a book with nothing in it says. Not a net asset value of zero, which is a figure
# somebody could act on standing in for an answer nobody has.
EMPTY = "No holdings and no cash as at this date."

CASH_KINDS: frozenset[TransactionKind] = frozenset(
    {
        TransactionKind.DIVIDEND,
        TransactionKind.FEE,
        TransactionKind.DEPOSIT,
        TransactionKind.WITHDRAWAL,
    }
)


@dataclass(frozen=True, slots=True)
class Figure:
    """One number on the screen, with the grade of everything under it.

    **The figure is here and the grade is beside it, because this is the operator's own
    book.** Withholding a person's own holdings from that person would be absurd. What the
    grade is *for* is the moment the figure leaves: :meth:`for_sharing` is where an attested
    lineage stops being a number and becomes a sentence about why there is not one.

    ``shared`` is computed once, at construction, by walking the whole lineage rather than
    by reading this figure's own inputs — a net asset value is documented only if everything
    beneath it is, three levels down included.
    """

    quantity: Quantity
    shared: Graded | Attested
    record: CalculationRecord

    @property
    def value(self) -> Decimal:
        return self.quantity.value

    @property
    def unit(self) -> Unit:
        return self.quantity.unit

    @property
    def grade(self) -> Grade:
        return self.shared.grade

    @property
    def is_attested(self) -> bool:
        return self.grade is Grade.ATTESTED

    def for_sharing(self) -> Graded | Attested:
        """What a surface outside this machine may have.

        A :class:`~aer.calc.attestation.Graded` carries the figure; an
        :class:`~aer.calc.attestation.Attested` has no field for one. A caller cannot get the
        number out of the second, which is the whole mechanism — a flag would leave it
        sitting in the object with a boolean beside it saying not to look.
        """
        return self.shared


@dataclass(frozen=True, slots=True)
class HoldingRow:
    """One security the book holds, as at the date."""

    security: Security

    # ``None`` when the trades themselves will not walk — a disposal before its acquisition,
    # a pool in two currencies. Rare and worth surfacing rather than raising: the operator
    # needs to be told which holding is unreadable, not handed a stack trace for the page.
    quantity: Figure | None
    cost: Figure | None

    # ``None`` when nothing could be priced. The reason is what the screen shows instead of
    # a number, because a blank cell reads as nil.
    value: Figure | None
    unrealised: Figure | None
    weight: Figure | None
    problem: str = ""

    @property
    def is_priced(self) -> bool:
        return self.value is not None

    @property
    def is_readable(self) -> bool:
        return self.quantity is not None


@dataclass(frozen=True, slots=True)
class CashRow:
    """The balance in one currency, and what it is worth in the book's own."""

    currency: str
    balance: Figure
    in_base: Figure | None
    weight: Figure | None
    problem: str = ""


@dataclass(frozen=True, slots=True)
class PortfolioView:
    """Everything one screen shows, as at one date."""

    portfolio: Portfolio
    as_of: date
    holdings: tuple[HoldingRow, ...]
    cash: tuple[CashRow, ...]

    # ``None`` when any component could not be valued. A total missing a position is worse
    # than no total: it looks like an answer.
    net_assets: Figure | None
    problem: str = ""

    @property
    def is_complete(self) -> bool:
        return self.net_assets is not None

    @property
    def rests_on_anything_typed(self) -> bool:
        """Whether any figure on this screen stands on something nobody documented.

        Read from the figures rather than from the transactions, because a holding can be
        documented and its *mark* converted at a rate that was not.
        """
        every = [
            *(row.quantity for row in self.holdings),
            *(row.cost for row in self.holdings),
            *(row.balance for row in self.cash),
        ]
        return any(figure is not None and figure.is_attested for figure in every)


async def book_as_at(
    session: AsyncSession,
    context: CalculationContext,
    *,
    portfolio: Portfolio,
    as_of: date,
) -> PortfolioView:
    """Compute the whole book as at a date.

    Three passes, and the order is forced by the arithmetic. Every holding and every cash
    balance is computed in the currency it lives in; each is then converted into the book's
    reporting currency through a dated rate; and only then is there a denominator to take a
    weight against. A weight computed before the total would be a fraction of a subtotal.

    Args:
        context: Where every calculation is recorded. Passed in rather than made here, so a
            caller that *is* a run can persist the ledger and a page load simply does not.
        as_of: The instant the book is shown at. Positions are computed to it, prices are
            the last close on or before it, and rates likewise (ADR 0083).
    """
    base = Unit.currency(portfolio.base_currency)
    trades = await transactions_in_force(session, portfolio=portfolio, as_of=as_of)

    holdings = [
        await _holding(session, context, security=security, trades=dealt, as_of=as_of, base=base)
        for security, dealt in _by_security(trades).items()
    ]
    cash = [
        await _cash(session, context, currency=currency, effects=effects, as_of=as_of, base=base)
        for currency, effects in sorted(_cash_effects(context, trades).items())
    ]

    view = _totalled(context, portfolio=portfolio, as_of=as_of, holdings=holdings, cash=cash)
    _log.info(
        "portfolio.computed",
        portfolio=str(portfolio.id),
        as_of=as_of.isoformat(),
        holdings=len(view.holdings),
        currencies=len(view.cash),
        complete=view.is_complete,
        calculations=len(context.records),
    )
    return view


def _totalled(
    context: CalculationContext,
    *,
    portfolio: Portfolio,
    as_of: date,
    holdings: Sequence[HoldingRow],
    cash: Sequence[CashRow],
) -> PortfolioView:
    """Add everything up in the book's own currency, then weight against the total.

    **Refuses a total that is missing anything.** A holding nobody could price and a balance
    nobody could convert are both reasons to state no net asset value rather than a smaller
    one — a total short a position looks exactly like a total, and every weight taken
    against it would be too large.
    """
    open_rows = [row for row in holdings if row.problem != CLOSED]
    unpriced = [row for row in open_rows if row.value is None]
    unconverted = [row for row in cash if row.in_base is None]

    if unpriced or unconverted:
        missing = [row.security.ticker for row in unpriced] + [row.currency for row in unconverted]
        problem = (
            f"No total: {', '.join(missing)} could not be valued in "
            f"{portfolio.base_currency}. A net asset value short a position is not a "
            "smaller answer, it is a wrong one."
        )
        return PortfolioView(
            portfolio=portfolio,
            as_of=as_of,
            holdings=tuple(holdings),
            cash=tuple(cash),
            net_assets=None,
            problem=problem,
        )

    values = [row.value.quantity for row in open_rows if row.value is not None]
    balances = [row.in_base.quantity for row in cash if row.in_base is not None]
    if not values and not balances:
        return PortfolioView(
            portfolio=portfolio,
            as_of=as_of,
            holdings=tuple(holdings),
            cash=tuple(cash),
            net_assets=None,
            problem=EMPTY,
        )

    total = calc.net_assets(context, holdings=values, cash=balances)
    net = graded_figure(context, total)

    if total.value <= 0:
        # A book that nets to nothing or less has no denominator to take a fraction of, and
        # `calc.weight` says so by raising. Reported here rather than allowed to escape,
        # because the state is a real one — an account overdrawn against its holdings, or a
        # cash withdrawal entered without the sale that funded it — and the operator needs
        # to be shown the rows that produced it rather than a stack trace.
        return PortfolioView(
            portfolio=portfolio,
            as_of=as_of,
            holdings=tuple(holdings),
            cash=tuple(cash),
            net_assets=net,
            problem=(
                f"The book nets to {total.value} {portfolio.base_currency}, so no weight "
                "can be taken against it. That is usually a cash movement entered without "
                "the trade that funded it."
            ),
        )

    return PortfolioView(
        portfolio=portfolio,
        as_of=as_of,
        holdings=tuple(_weighted(context, row, total=total) for row in holdings),
        cash=tuple(_weighted_cash(context, row, total=total) for row in cash),
        net_assets=net,
    )


def _weighted(context: CalculationContext, row: HoldingRow, *, total: Quantity) -> HoldingRow:
    if row.value is None:
        return row
    share = calc.weight(context, value=row.value.quantity, net_assets=total)
    return replace(row, weight=graded_figure(context, share))


def _weighted_cash(context: CalculationContext, row: CashRow, *, total: Quantity) -> CashRow:
    if row.in_base is None:
        return row
    share = calc.weight(context, value=row.in_base.quantity, net_assets=total)
    return replace(row, weight=graded_figure(context, share))


# -- Reading the book ------------------------------------------------------------------------


async def transactions_in_force(
    session: AsyncSession, *, portfolio: Portfolio, as_of: date
) -> list[Transaction]:
    """Every transaction in force at the as-of date, oldest first.

    Public because "in force at a date" has to have one definition: the return series in
    :mod:`aer.services.performance` reads the same rows this does, and a second query with
    its own idea of supersession would let the two screens disagree about the book.

    Two filters and they answer different questions. ``trade_date <= as_of`` is about the
    world: a trade dealt after the date had not happened. The supersession filter is about
    the record: a row somebody corrected was never what the book said, whatever date it is
    read at.

    Ordered by trade date, then by ``effective_at`` — the instant the trade was true of the
    book — then by when it was recorded and finally by id. The pooled cost of ADR 0085
    depends on the order, so two trades on one day need a total ordering to walk, and
    ``effective_at`` is the field that carries the answer: a sale at four o'clock follows a
    purchase at ten.

    **A genuine tie is arbitrary and stable.** Two trades of one security at the same
    recorded instant sort by id, which is a stable arbitrary order rather than a meaningful
    one. For two purchases it changes nothing; for a purchase and a sale it is a same-day
    matched trade, which UK rules handle with a rule ADR 0085 explicitly does not implement.
    """
    superseded = select(Attestation.supersedes_id).where(Attestation.supersedes_id.is_not(None))

    rows = await session.scalars(
        select(Transaction)
        .join(Attestation, Attestation.id == Transaction.attestation_id)
        .where(
            Transaction.portfolio_id == portfolio.id,
            Transaction.trade_date <= as_of,
            Attestation.id.not_in(superseded),
        )
        .order_by(
            Transaction.trade_date,
            Attestation.effective_at,
            Attestation.recorded_at,
            Transaction.attestation_id,
        )
        .options(selectinload(Transaction.attestation), selectinload(Transaction.security))
    )
    return list(rows)


def _by_security(trades: Sequence[Transaction]) -> dict[Security, list[Transaction]]:
    """The dealt trades, grouped by listing and keeping their order.

    A dividend naming its security is *not* here: it is cash, and pooling it would add its
    quantity to a share count. What makes something a holding is that it was dealt.
    """
    grouped: dict[Security, list[Transaction]] = {}
    for trade in trades:
        if trade.kind in CASH_KINDS or trade.security is None:
            continue
        grouped.setdefault(trade.security, []).append(trade)
    return grouped


def _cash_effects(
    context: CalculationContext, trades: Sequence[Transaction]
) -> dict[str, list[Quantity]]:
    """What every trade did to cash, grouped by the currency it did it in.

    Each effect is its own recorded calculation, so a balance a reader disputes resolves to
    a list of movements rather than to a total.
    """
    effects: dict[str, list[Quantity]] = {}
    for trade in trades:
        if trade.kind is TransactionKind.SPLIT:
            # A split touches no money (ADR 0094). Skipped before the no-price branch
            # below, which would otherwise pour a share multiplier into a cash balance —
            # the exact silent double-count the currency-exchange refusal was written
            # against.
            continue
        money = Unit.currency(trade.currency)
        fees = Quantity.of(trade.fees, money, source=_source(trade, "fees"))
        if trade.kind in CASH_KINDS or trade.price is None:
            amount = Quantity.of(trade.quantity, money, source=_source(trade, "amount"))
            effect = calc.cash_movement(context, amount=amount, fees=fees)
        else:
            effect = calc.dealt_cash_effect(
                context,
                quantity=_movement(trade),
                price=Quantity.of(trade.price, money / calc.SHARES, source=_source(trade, "price")),
                fees=fees,
            )
        effects.setdefault(trade.currency, []).append(effect)
    return effects


async def _holding(
    session: AsyncSession,
    context: CalculationContext,
    *,
    security: Security,
    trades: Sequence[Transaction],
    as_of: date,
    base: Unit,
) -> HoldingRow:
    """One security: how much, what it cost, what it is worth, and in whose currency.

    The mark is in the listing's quote currency and the cost is in the dealing currency, and
    the two are only usually the same. Both are converted into the book's reporting currency
    before the profit is taken, so a reader is never shown a subtraction across two
    currencies and the profit column means one thing on every row.
    """
    try:
        movements = [_movement(trade) for trade in trades]
        costs = [_acquisition_cost(context, trade) for trade in trades]
        held = calc.quantity_held(context, movements=movements)
        native_cost = calc.pooled_cost(context, movements=movements, acquisition_costs=costs)
    except CalculationError as problem:
        return HoldingRow(
            security=security,
            quantity=None,
            cost=None,
            value=None,
            unrealised=None,
            weight=None,
            problem=str(problem),
        )

    quantity = graded_figure(context, held)

    if held.value == 0:
        # Sold out. Marked closed rather than shown as a nil row: a position that no longer
        # exists is not a holding of nothing, and it must not drag the total down with it.
        return HoldingRow(
            security=security,
            quantity=quantity,
            cost=graded_figure(context, native_cost),
            value=None,
            unrealised=None,
            weight=None,
            problem=CLOSED,
        )

    try:
        mark = await _mark(session, context, security=security, as_of=as_of)
        native_value = calc.holding_value(context, quantity=held, price=mark)
        value = await in_base(session, context, amount=native_value, base=base, as_of=as_of)
        cost = await in_base(session, context, amount=native_cost, base=base, as_of=as_of)
    except CalculationError as problem:
        return HoldingRow(
            security=security,
            quantity=quantity,
            cost=graded_figure(context, native_cost),
            value=None,
            unrealised=None,
            weight=None,
            problem=str(problem),
        )

    return HoldingRow(
        security=security,
        quantity=quantity,
        cost=graded_figure(context, cost),
        value=graded_figure(context, value),
        unrealised=graded_figure(context, calc.unrealised(context, value=value, cost=cost)),
        weight=None,
    )


async def _cash(
    session: AsyncSession,
    context: CalculationContext,
    *,
    currency: str,
    effects: Sequence[Quantity],
    as_of: date,
    base: Unit,
) -> CashRow:
    """One currency's balance, and what it is worth in the book's reporting currency."""
    balance = calc.cash_balance(context, effects=list(effects))
    try:
        converted = await in_base(session, context, amount=balance, base=base, as_of=as_of)
    except CalculationError as problem:
        return CashRow(
            currency=currency,
            balance=graded_figure(context, balance),
            in_base=None,
            weight=None,
            problem=str(problem),
        )
    return CashRow(
        currency=currency,
        balance=graded_figure(context, balance),
        in_base=graded_figure(context, converted),
        weight=None,
    )


async def in_base(
    session: AsyncSession,
    context: CalculationContext,
    *,
    amount: Quantity,
    base: Unit,
    as_of: date,
) -> Quantity:
    """The amount in the book's reporting currency, through a dated rate.

    ``as_of`` is the date the rate is taken at, which is the *flow's* date when a caller is
    converting a movement rather than a balance.

    A figure already in that currency is returned untouched rather than converted at one —
    a rate of exactly one is a number nobody published, and recording a conversion that did
    not happen would put a step in the ledger for every single-currency book.

    **A figure in pence becomes pounds by division, never by a rate.** A London contract note
    quotes 240p and an operator copying it should not have to divide by a hundred in their
    head — that is the error ADR 0032 exists to prevent them making. One pound has been one
    hundred pence since 1971, so the conversion is definitional: `aer.calc.fx` would refuse
    it for being stale or ask it for a source, both of which are the right questions to ask
    of an observed rate and neither of which means anything here.
    """
    if amount.unit.currencies and amount.unit.currencies[0] in MINOR_UNITS:
        amount = price_in_major_units(context, quoted=amount)
    if amount.unit == base:
        return amount
    return await fx_service.convert_as_at(
        session, context, amount=amount, into=base.symbol, as_of=as_of
    )


# -- Building sourced quantities ---------------------------------------------------------------


def _movement(trade: Transaction) -> Quantity:
    """A trade's signed quantity, in the unit its kind implies.

    Units of the security when something was dealt, units of the currency when cash moved.
    A kind this function has not been told about raises rather than defaulting, because
    both defaults are wrong: treating cash as shares pools a dividend into a holding, and
    treating shares as cash puts a share count into a balance.
    """
    if trade.kind in CASH_KINDS:
        unit = Unit.currency(trade.currency)
    elif trade.kind in (TransactionKind.BUY, TransactionKind.SELL):
        unit = calc.SHARES
    elif trade.kind is TransactionKind.SPLIT:
        # The third answer (ADR 0094): neither money nor units but the ratio the walk
        # multiplies the share count by.
        unit = calc.RATIO
    else:  # pragma: no cover -- unreachable until TransactionKind grows a value
        message = (
            f"{trade.kind.value!r} has no cash treatment. A transaction kind reaches the "
            "portfolio arithmetic only once somebody has decided whether its quantity is "
            "money or units."
        )
        raise CalculationError(message, context={"kind": trade.kind.value})
    return Quantity.of(trade.quantity, unit, source=_source(trade, "quantity"))


def _acquisition_cost(context: CalculationContext, trade: Transaction) -> Quantity:
    """What a purchase added to the pool, or a sourced nil for a disposal.

    A disposal removes cost at the pool's average rather than at what it fetched (ADR 0085),
    so the paired entry is zero — and it is a *sourced* zero, traced to the trade it stands
    for, because an unsourced one would be refused by the engine and rightly.
    """
    money = Unit.currency(trade.currency)
    if trade.kind is TransactionKind.SPLIT:
        # A split is not a purchase (ADR 0094): the pool's cost is untouched, and the
        # paired entry is a sourced nil exactly as a disposal's is.
        return Quantity.of(Decimal(0), money, source=_source(trade, "reorganisation"))
    if trade.quantity <= 0 or trade.price is None:
        return Quantity.of(Decimal(0), money, source=_source(trade, "disposal"))
    return calc.acquisition_cost(
        context,
        quantity=_movement(trade),
        price=Quantity.of(trade.price, money / calc.SHARES, source=_source(trade, "price")),
        fees=Quantity.of(trade.fees, money, source=_source(trade, "fees")),
    )


def _source(trade: Transaction, field: str) -> SourceRef:
    """Every number a trade contributes traces to the attestation, with its grade.

    The reference names the row and the label names the column, which is the same shape a
    price uses: the leaf is the listing rather than one bar, and which figure was taken is
    the calculation input's business.
    """
    return SourceRef.attestation(
        trade.attestation_id,
        grade=trade.attestation.grade,
        label=f"{trade.kind.value} {trade.trade_date.isoformat()} {field}",
    )


class _NoMarkError(CalculationError):
    """No usable close for this security on or before the as-of date."""

    code = "no_mark"


async def _mark(
    session: AsyncSession, context: CalculationContext, *, security: Security, as_of: date
) -> Quantity:
    """The last close on or before the as-of date, in major units.

    A London listing quotes in pence, so a raw close of 250 means £2.50 — the conversion is
    a traced calculation over a table with one deliberate entry (ADR 0032), never a division
    somebody remembers.
    """
    bar = await session.scalar(
        select(PriceBar)
        .where(PriceBar.security_id == security.id, PriceBar.bar_date <= as_of)
        .order_by(PriceBar.bar_date.desc())
        .limit(1)
    )
    if bar is None:
        message = (
            f"No close for {security.ticker} on or before {as_of.isoformat()}. The price "
            "history does not reach this date, so the holding is shown unpriced rather than "
            "marked at a number nobody has."
        )
        raise _NoMarkError(message, context={"security": security.provider_symbol})

    quoted = Quantity.of(
        bar.close,
        Unit.currency(security.quote_currency) / calc.SHARES,
        source=SourceRef.security(
            security.id, label=f"{security.provider_symbol} {bar.bar_date.isoformat()}"
        ),
    )
    if security.quote_currency in MINOR_UNITS:
        return price_in_major_units(context, quoted=quoted)
    return quoted


def graded_figure(context: CalculationContext, quantity: Quantity) -> Figure:
    """Wrap a computed quantity with the grade of everything beneath it.

    Public alongside :func:`in_base` and :func:`transactions_in_force` because
    :mod:`aer.services.performance` computes over the same book: a return that graded its
    figures differently from the holdings table beside it would be two answers to one
    question about the same rows.

    The quantity must be the output of a traced calculation in this context, which every
    caller here guarantees by having just computed it: the source reference *is* the
    calculation's id, so the walk starts where the figure came from.
    """
    record = context.find(quantity.source.identifier) if quantity.source is not None else None
    if record is None:  # pragma: no cover -- every caller passes a traced output
        message = (
            "This figure did not come from a calculation in this ledger, so there is no "
            "lineage to grade it against. A number reaching a portfolio screen ungraded "
            "would be one nobody could tell from a documented one."
        )
        raise CalculationError(message, context={"value": str(quantity.value)})
    return Figure(quantity=quantity, shared=grade_of(context, record), record=record)
