"""The book, as at a date, with every figure carrying its working and its grade.

One screen and two forms. The screen is *as at* an instant chosen by the reader, defaulting
to the last close the platform holds — which is not a nicety: reconciling against a broker
statement is the only external check this tool has, and a statement arrives dated.

**Everything on it is computed on the way to it.** There is no ``positions`` table (ADR
0083), so a page load walks the transactions, pools the cost, marks the holdings and
converts them. Nothing is written: a page load is not a run, it has no job to hang a ledger
off, and a GET that wrote a few hundred ``calculations`` rows would make a read a writer.

**Every figure states its grade.** A holding typed from memory and one parsed from a
contract note look identical on screen, and only one of them is evidence — so the row says
which. The containment that matters is not the chip: it is
:meth:`aer.services.portfolio.Figure.for_sharing`, which hands an attested figure back as a
type with no field for the figure at all.

**Nothing here is regulated investment advice**, and the shell says so on every page.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import func, select
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.calc.units import CalculationError
from aer.core.enums import AttestationKind, Grade, TransactionKind
from aer.db.models import Attestation, Portfolio, PriceBar, Security, Transaction
from aer.errors import AerError
from aer.services import calculations as calculation_service
from aer.services import portfolio as portfolio_service
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = ["GRADE_LABELS", "NO_FIGURE", "router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.portfolio")

# The name the first book gets. One row from the first day, so separating an ISA from a SIPP
# is a setting rather than a migration — and a name the operator can change beats a blank
# field they have to fill in before the tool does anything.
DEFAULT_BOOK: Final = "My portfolio"

# Which kinds move money rather than units, for the form's own branching. Read from the
# service so the two cannot disagree about what a dividend is.
CASH_KINDS: Final = portfolio_service.CASH_KINDS


def _pounds(value: Decimal, currency: str) -> str:
    """An amount, exact to the penny.

    **Not `aer.render.display.money`.** That is the research report's house style, which
    renders in millions and would show a £1.2m book as "£1m" — right for a company's revenue
    and wrong here, because this screen is reconciled line by line against a statement. A
    figure rounded to the nearest million cannot be reconciled against anything.
    """
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(currency.upper(), f"{currency.upper()} ")
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"


def _shares(value: Decimal) -> str:
    """A share count, with the trailing zeros a NUMERIC(38, 12) round-trip adds removed."""
    trimmed = value.normalize()
    # `normalize` turns 100 into 1E+2, which is correct and unreadable.
    return f"{trimmed:f}" if trimmed == trimmed.to_integral_value() else f"{trimmed:,f}"


@router.get("/portfolio", response_class=HTMLResponse, summary="Portfolio")
async def portfolio_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """The book as at a date.

    The date comes from the query string so a view is a link somebody can keep — "as it
    stood on the thirtieth" is a thing an operator wants to send themselves, and a date held
    only in a form is a view that cannot be returned to.
    """
    book = await _book_of(session, user_id=user.id)
    token = new_csrf_token(settings)

    if book is None:
        response: Response = render(
            request,
            "portfolio/empty.html",
            {"csrf_field": CSRF_FIELD_NAME, "csrf_token": token, "default_name": DEFAULT_BOOK},
        )
        set_csrf_cookie(response, token)
        return response

    as_of = _requested_date(request) or await _latest_close(session, portfolio=book)
    context = calculation_service.new_context()

    try:
        view = await portfolio_service.book_as_at(session, context, portfolio=book, as_of=as_of)
    except AerError as problem:
        # A book that will not compute at all is rare and worth showing plainly. The
        # alternative — an empty table — would read as "you hold nothing".
        _log.warning("portfolio.failed", portfolio=str(book.id), error=str(problem))
        broken: Response = render(
            request,
            "portfolio/broken.html",
            {"book": book, "as_of": as_of, "problem": str(problem)},
        )
        return broken

    response = render(
        request,
        "portfolio/index.html",
        {
            "book": book,
            "as_of": as_of,
            "view": view,
            "rows": [_holding_row(row, book) for row in view.holdings],
            "cash": [_cash_row(row, book) for row in view.cash],
            "totals": _totals(view, book),
            "securities": await _dealable(session),
            "no_listings": NO_LISTINGS,
            "kinds": list(TransactionKind),
            "cash_kinds": sorted(kind.value for kind in CASH_KINDS),
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/portfolio", summary="Create the book")
async def create_book(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Make the first portfolio. One row, and the table has held it since day one."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was created.")

    existing = await _book_of(session, user_id=user.id)
    if existing is not None:
        return RedirectResponse("/portfolio", status_code=HTTP_303_SEE_OTHER)

    session.add(
        Portfolio(
            user_id=user.id,
            name=(submitted.get("name") or DEFAULT_BOOK).strip() or DEFAULT_BOOK,
            base_currency=(submitted.get("base_currency") or "GBP").strip().upper(),
        )
    )
    await session.commit()
    return RedirectResponse("/portfolio", status_code=HTTP_303_SEE_OTHER)


@router.post("/portfolio/transactions", summary="Record a transaction")
async def record_transaction(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Write down one thing that happened to the book.

    **At the attested grade, always.** There is no argument to this handler that could make
    it otherwise: a *documented* attestation is one extracted from a hashed artefact with a
    citation behind it, and typing into a form produces no artefact. The importer that reads
    a contract note is the second door into this table and it is not this one — so a
    hand-entered trade is marked as what it is, and every figure above it inherits that.
    """
    book = await _book_of(session, user_id=user.id)
    if book is None:
        return _problem(request, "There is no book to record a transaction against.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was recorded.")

    resolved = await _resolve_security(session, submitted.get("security", ""))
    if isinstance(resolved, str):
        # A refusal that names what to do about it, not a validation error. The operator
        # typed a ticker; the useful answer is why this platform cannot deal it.
        return _problem(request, resolved)

    try:
        trade = _parsed(submitted, security=resolved)
    except (ValueError, KeyError, CalculationError) as problem:
        return _problem(request, f"That transaction could not be recorded: {problem}")

    attestation = Attestation(
        kind=AttestationKind.TRANSACTION,
        grade=Grade.ATTESTED,
        effective_at=datetime.combine(trade.trade_date, datetime.min.time(), tzinfo=UTC),
        recorded_by=user.email,
        note=submitted.get("note") or None,
    )
    session.add(attestation)
    await session.flush()
    session.add(
        Transaction(
            attestation_id=attestation.id,
            portfolio_id=book.id,
            kind=trade.kind,
            security_id=trade.security_id,
            trade_date=trade.trade_date,
            settlement_date=trade.settlement_date,
            quantity=trade.quantity,
            price=trade.price,
            fees=trade.fees,
            currency=trade.currency,
        )
    )

    try:
        await session.commit()
    except Exception as problem:
        await session.rollback()
        # The check constraints are the real control and their messages name the rule. A
        # sell entered as a positive number lands here rather than in a holding that grew.
        return _problem(request, f"The database refused that transaction: {problem}")

    _log.info(
        "portfolio.transaction_recorded",
        portfolio=str(book.id),
        kind=trade.kind.value,
        trade_date=trade.trade_date.isoformat(),
    )
    return RedirectResponse("/portfolio", status_code=HTTP_303_SEE_OTHER)


# -- Reading -------------------------------------------------------------------------------


async def _book_of(session: DbSession, *, user_id: uuid.UUID) -> Portfolio | None:
    found: Portfolio | None = await session.scalar(
        select(Portfolio)
        .where(Portfolio.user_id == user_id, Portfolio.archived_at.is_(None))
        .order_by(Portfolio.created_at)
        .limit(1)
    )
    return found


def _requested_date(request: Request) -> date | None:
    raw = request.query_params.get("as_of", "").strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        # A malformed date falls back to the default rather than erroring. The control is a
        # date input; anything else in the query string is a hand-typed URL, and the useful
        # answer to one is the page.
        return None


async def _latest_close(session: DbSession, *, portfolio: Portfolio) -> date:
    """The last day the platform has a price for anything in this book.

    The default, because a book shown at today's date is a book with no prices for today —
    markets close, and a screen that defaulted to now would show every holding unpriced
    every evening and all weekend.
    """
    latest = await session.scalar(
        select(func.max(PriceBar.bar_date))
        .join(Transaction, Transaction.security_id == PriceBar.security_id)
        .where(Transaction.portfolio_id == portfolio.id)
    )
    return latest or datetime.now(UTC).date()


async def _dealable(session: DbSession) -> list[Security]:
    """The listings a transaction may name.

    Only ones the platform already holds prices for, because a holding it cannot price is a
    row that refuses the whole net asset value — and refusing the total is the correct
    behaviour, so the way to avoid it is not to accept the holding.

    **The list is typed into rather than picked from** (gap R18). A `<select>` of every
    listing is unusable at any real size, and it was worse than unusable at size zero: on a
    machine whose research runs had no market-data subscription it held one option reading
    "cash, no security", and an operator could neither type a ticker nor find out why. The
    control is now an `<input list>` over a `<datalist>` — a native typeable combobox, no
    script — and what is typed is resolved by :func:`_resolve_security`.
    """
    rows = await session.scalars(
        select(Security).where(Security.is_active).order_by(Security.ticker)
    )
    return list(rows)


# What to say when the platform holds no listing at all. Not a shrug: it names the one thing
# that creates a `Security` row today, which is the honest and complete answer.
NO_LISTINGS: Final = (
    "This platform holds no priced listing yet. A listing is created when a research run "
    "acquires prices for a company, which needs a market-data subscription configured. "
    "Until there is one, cash transactions — a deposit, a withdrawal, a dividend, a fee — "
    "work exactly as they will later."
)


async def _resolve_security(session: DbSession, typed: str) -> Security | str | None:
    """One typed ticker to the listing it names, or the reason it names none.

    ``None`` for an empty box, which is a cash transaction and not a mistake. A string is a
    refusal an operator can act on; a :class:`Security` is the answer.

    Three shapes are accepted because all three are what somebody types: ``MSFT``,
    ``MSFT.US`` — the vendor's own symbol, which is what a research run stored — and
    ``MSFT NASDAQ``. Matching is case-insensitive, and an ambiguous ticker is refused with
    the choices named rather than resolved by picking one.
    """
    wanted = typed.strip().upper()
    if not wanted:
        return None

    held = list(await session.scalars(select(Security).where(Security.is_active)))
    if not held:
        return NO_LISTINGS

    exact = [
        row
        for row in held
        if wanted in {row.ticker.upper(), row.provider_symbol.upper()}
        or wanted == f"{row.ticker.upper()} {row.exchange.upper()}"
        or wanted == f"{row.ticker.upper()}.{row.exchange.upper()}"
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        choices = ", ".join(sorted(f"{row.ticker}.{row.exchange}" for row in exact))
        return (
            f"{typed.strip()!r} names more than one listing this platform holds ({choices}). "
            "Name the exchange too — a dual listing trades at two prices in two currencies, "
            "and picking one for you is the kind of guess a book cannot be reconciled against."
        )

    return (
        f"This platform holds no priced listing for {typed.strip()!r}. A listing is created "
        "when a research run acquires prices for a company; commissioning a report on this "
        "ticker is what makes it dealable. Holding a security the platform cannot price "
        "would refuse the whole net asset value rather than only that row."
    )


# -- Rendering -----------------------------------------------------------------------------


def _holding_row(row: portfolio_service.HoldingRow, book: Portfolio) -> dict[str, object]:
    """One line of the table, already formatted.

    Formatted here rather than in the template because a template that formatted a figure
    would be a second house style nobody configured (ADR 0077) — and because the grade has
    to travel with the number rather than beside it.
    """
    return {
        "key": row.security.provider_symbol,
        "ticker": row.security.ticker,
        "exchange": row.security.exchange,
        "name": row.security.name or row.security.ticker,
        "quantity": _shares(row.quantity.value) if row.quantity else "",
        "cost": _pounds(row.cost.value, book.base_currency) if row.cost else "",
        "value": _pounds(row.value.value, book.base_currency) if row.value else "",
        "unrealised": (_pounds(row.unrealised.value, book.base_currency) if row.unrealised else ""),
        "is_down": bool(row.unrealised and row.unrealised.value < 0),
        "weight": f"{row.weight.value * 100:.1f}%" if row.weight else "",
        "grade": _grade_of(row.quantity),
        "grade_label": _grade_label(row.quantity),
        "problem": row.problem,
        "is_closed": row.problem == portfolio_service.CLOSED,
    }


def _cash_row(row: portfolio_service.CashRow, book: Portfolio) -> dict[str, object]:
    return {
        "currency": row.currency,
        "balance": _pounds(row.balance.value, row.currency),
        "in_base": _pounds(row.in_base.value, book.base_currency) if row.in_base else "",
        "weight": f"{row.weight.value * 100:.1f}%" if row.weight else "",
        "grade": _grade_of(row.balance),
        "grade_label": _grade_label(row.balance),
        "problem": row.problem,
    }


# What a tile shows when the figure behind it cannot be stated.
NO_FIGURE: Final = "—"


def _totals(view: portfolio_service.PortfolioView, book: Portfolio) -> dict[str, object]:
    """The four tiles, and all four go blank together.

    **A subtotal is a total short a position, which is the failure the service refuses.**
    The first draft of this screen showed a refused net asset value beside a cash tile
    reading £50,000 — a book whose dollars could not be converted, with its sterling summed
    and stated as though that were the cash. Every one of these tiles is a sum over the rows
    that happened to resolve, so if any row did not, none of them may be shown.
    """
    if not view.is_complete:
        return {
            "net_assets": NO_FIGURE,
            "securities": NO_FIGURE,
            "cash": NO_FIGURE,
            "unrealised": NO_FIGURE,
            "is_down": False,
            "grade": "",
            "is_complete": False,
        }

    priced = [row for row in view.holdings if row.value is not None]
    securities = sum((row.value.value for row in priced if row.value), Decimal(0))
    cash = sum((row.in_base.value for row in view.cash if row.in_base), Decimal(0))
    profit = sum((row.unrealised.value for row in priced if row.unrealised), Decimal(0))
    return {
        "net_assets": (
            _pounds(view.net_assets.value, book.base_currency) if view.net_assets else NO_FIGURE
        ),
        "securities": _pounds(securities, book.base_currency),
        "cash": _pounds(cash, book.base_currency),
        "unrealised": _pounds(profit, book.base_currency),
        "is_down": profit < 0,
        "grade": _grade_of(view.net_assets),
        "grade_label": _grade_label(view.net_assets),
        "is_complete": True,
    }


# What the screen calls each grade.
#
# **"Typed", not "Attested", and the difference is not cosmetic.** The shell's provenance
# vocabulary already spends the word ``Attested`` on a *record class* — a figure whose
# origin is the operator's own book — and a `documented` attestation is every bit as
# attested in that sense. This chip is about a different axis: how strong the evidence
# behind it is. Two vocabularies sharing one word teach a reader that the word means
# neither, so this one says plainly what happened instead.
GRADE_LABELS: Final[dict[Grade, str]] = {
    Grade.DOCUMENTED: "Documented",
    Grade.ATTESTED: "Typed",
}


def _grade_of(figure: portfolio_service.Figure | None) -> str:
    """The stable grade value a chip and a test both key on."""
    return "" if figure is None else figure.grade.value


def _grade_label(figure: portfolio_service.Figure | None) -> str:
    return "" if figure is None else GRADE_LABELS[figure.grade]


# -- Form handling -------------------------------------------------------------------------


async def _submitted(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}


@dataclass(frozen=True, slots=True)
class _Parsed:
    """The form, as the columns a transaction has."""

    kind: TransactionKind
    security_id: uuid.UUID | None
    trade_date: date
    settlement_date: date | None
    quantity: Decimal
    price: Decimal | None
    fees: Decimal
    currency: str


def _parsed(submitted: dict[str, str], *, security: Security | None = None) -> _Parsed:
    """The form, as the columns a transaction has.

    Deliberately strict. Every value the operator types about their own money is checked
    here for shape and again by the database for meaning, and the second is the control: a
    sell entered as a positive number is refused by a check constraint, not by this.
    """
    kind = TransactionKind(submitted["kind"])
    quantity = Decimal(submitted["quantity"].replace(",", "").strip())

    # The sign is the form's job, not the operator's. Nobody types a minus in front of a
    # sale, and a book that required it would fill with additions that look like disposals.
    if kind in (TransactionKind.SELL, TransactionKind.FEE, TransactionKind.WITHDRAWAL):
        quantity = -abs(quantity)
    else:
        quantity = abs(quantity)

    dealt = kind in (TransactionKind.BUY, TransactionKind.SELL)
    raw_price = submitted.get("price", "").strip()

    return _Parsed(
        kind=kind,
        # Resolved by the handler against the listings this platform holds, never taken
        # from the form: an id in a hidden field is an id somebody can substitute, and the
        # security decides which price series values the whole holding.
        security_id=security.id if security is not None else None,
        trade_date=date.fromisoformat(submitted["trade_date"]),
        settlement_date=(
            date.fromisoformat(submitted["settlement_date"])
            if submitted.get("settlement_date", "").strip()
            else None
        ),
        quantity=quantity,
        price=Decimal(raw_price) if dealt and raw_price else None,
        fees=Decimal(submitted.get("fees", "").strip() or 0),
        currency=submitted.get("currency", "GBP").strip().upper(),
    )


def _problem(request: Request, message: str, *, status: int = HTTP_404_NOT_FOUND) -> Response:
    rendered: Response = render(
        request, "runs/problem.html", {"message": message}, status_code=status
    )
    return rendered


def _refused(request: Request, consequence: str) -> Response:
    return _problem(
        request,
        f"This form's security token was missing or had expired. {consequence}",
        status=HTTP_403_FORBIDDEN,
    )
