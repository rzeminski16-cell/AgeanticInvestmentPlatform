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

from aer.api.deps import CurrentUser, DbSession, RedisClient, SettingsDep
from aer.calc.units import CalculationError
from aer.core.enums import AttestationKind, Grade, TransactionKind
from aer.db.models import Attestation, Portfolio, PriceBar, Security, Transaction, User
from aer.errors import AerError
from aer.runtime import standalone_price_client
from aer.services import calculations as calculation_service
from aer.services import decisions as decision_service
from aer.services import performance as performance_service
from aer.services import portfolio as portfolio_service
from aer.services import splits as splits_service
from aer.services.listings import add_listing
from aer.storage.local import LocalArtefactStore
from aer.web import verdict as verdicts
from aer.web import vocabulary
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


def _percent(value: Decimal) -> str:
    """A rate, to one decimal place, with its sign always shown.

    **The sign is never dropped**, even at zero, because the reader's question is which way
    the book went and a bare "0.0%" answers it while "+0.0%" and "-0.0%" both say it moved
    and rounded away. Rounding stops at one place on purpose: a return quoted to four is a
    precision the price history does not have.
    """
    scaled = (value * 100).quantize(Decimal("0.1"))
    sign = "-" if scaled < 0 else "+"
    return f"{sign}{abs(scaled):,.1f}%"


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

    totals = _totals(view, book)
    # Both are computed in the *same* ledger as the book above, which is what lets the
    # exposure reuse it: a view from another context cites calculations this one does not
    # hold, and grading it would be a claim about the part that happened to be readable.
    returns = await performance_service.returns_as_at(session, context, portfolio=book, as_of=as_of)
    exposure = await performance_service.exposure_as_at(
        session, context, portfolio=book, as_of=as_of, view=view
    )
    response = render(
        request,
        "portfolio/index.html",
        {
            "book": book,
            "as_of": as_of,
            "view": view,
            "rows": [_holding_row(row, book) for row in view.holdings],
            "cash": [_cash_row(row, book) for row in view.cash],
            "totals": totals,
            "returns": _return_rows(returns),
            "returns_problem": returns.problem,
            "exposure": _exposure_bands(exposure, book),
            "concentration": _concentration(exposure),
            "exposure_problem": exposure.problem,
            "verdict": _book_verdict(view, totals=totals),
            "securities": await _dealable(session),
            # The decisions a trade could carry out (ADR 0104): held, and of a kind that
            # moves the book. Labelled by what was decided, so the operator picks the entry
            # they wrote rather than an id.
            "open_decisions": [
                {
                    "value": str(row.judgement_id),
                    "label": (
                        f"{decision_service.ACTION_WORDS[row.action].capitalize()} — "
                        f"{row.thesis.title} ({row.judgement.held_at:%d %b %Y})"
                    ),
                }
                for row in await decision_service.open_for_the_book(session, user_id=user.id)
            ],
            "no_listings": NO_LISTINGS,
            # Every kind except SPLIT: a split is derived from the corporate action,
            # never typed (ADR 0094), so the form does not offer it.
            "kinds": [kind for kind in TransactionKind if kind is not TransactionKind.SPLIT],
            "cash_kinds": sorted(kind.value for kind in CASH_KINDS),
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


def _book_verdict(
    view: portfolio_service.PortfolioView, *, totals: dict[str, object]
) -> verdicts.Verdict:
    """The sentence the book leads with, composed from what the walk actually resolved.

    The book-level grade is stated here, once (the redesign's §10.1): every row's chip
    stays for the row, and the sentence carries the weakest grade the whole book rests on.
    An incomplete valuation refuses the success tone by construction — a partial book
    presented as the all-clear is the exact failure the four coupled totals exist to stop.
    """
    if not view.holdings and not view.cash:
        return verdicts.sentence(
            ["nothing is recorded yet, so there is nothing to value"],
            when_none="Nothing is recorded yet",
            tone=vocabulary.Tone.MUTED,
        )
    if not totals["is_complete"]:
        return verdicts.sentence(
            [
                "the four figures are withheld while a position cannot be valued",
                "a partial sum shown as a total would overstate every weight on the page",
            ],
            when_none="The four figures are withheld",
            tone=vocabulary.Tone.WARNING,
            is_complete=False,
            gap="the rows below name what could not be priced",
        )
    grade_clause = (
        "some figures rest on typed, self-certified entries and are withheld from anything shared"
        if view.rests_on_anything_typed
        else "every figure rests on documented entries"
    )
    return verdicts.sentence(
        [f"fully valued, net assets {totals['net_assets']}", grade_clause],
        when_none="Fully valued",
        tone=vocabulary.Tone.SUCCESS,
    )


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
async def record_transaction(  # noqa: PLR0911 -- one refusal per thing a trade can get wrong
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    redis: RedisClient,
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
    if isinstance(resolved, _Unheld):
        # The third door (roadmap §3.1, ADR 0093): a ticker the platform has never seen is
        # verified with the market-data provider once, at first sight, and either becomes
        # dealable — the trade then records against it in the same submission — or the
        # operator gets the reason it cannot.
        resolved = await _verified_at_first_sight(
            session, settings=settings, redis=redis, book=book, unheld=resolved
        )
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
    recorded = Transaction(
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
    session.add(recorded)

    refused_link = await _carried_out(
        session, request, submitted.get("decision", ""), trade=recorded, user=user
    )
    if refused_link is not None:
        return refused_link

    if resolved is not None:
        # ADR 0094: the self-healing half of the derivation. A backfilled first-ever
        # trade in a security that has since split gets the derived rows the book had no
        # reason to carry before this submission.
        await session.flush()
        await splits_service.ensure_for(session, portfolio_id=book.id, security=resolved)

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


# What to say when the platform holds no listing at all. Not a shrug: it names both things
# that create a `Security` row, which is the honest and complete answer (roadmap §3.1).
NO_LISTINGS: Final = (
    "This platform holds no priced listing yet. Type a ticker with its exchange — "
    "MSFT NASDAQ, BARC LSE — and it is verified with the market-data provider at first "
    "sight; a research run that acquires prices creates a listing too. Both need a "
    "market-data subscription configured. Cash transactions — a deposit, a withdrawal, "
    "a dividend, a fee — work either way."
)


async def _verified_at_first_sight(
    session: DbSession,
    *,
    settings: SettingsDep,
    redis: RedisClient,
    book: Portfolio,
    unheld: _Unheld,
) -> Security | str:
    """The third door: verify a never-seen ticker once, or say exactly why not.

    Committed as it lands, whatever happens to the trade being recorded around it: a
    verification is an acquisition with an artefact and a work order behind it, and losing
    it to a typo in the quantity field would mean fetching the same series twice. The
    refused attempt is committed too — a `FAILED` order is the record that the question
    was asked.
    """
    if unheld.exchange is None:
        return (
            f"This platform holds no priced listing for {unheld.ticker!r}. Name the "
            f"exchange too — for example {unheld.ticker} NASDAQ — and it will "
            "be verified with the market-data provider at first sight. Commissioning a "
            "research report on it also creates the listing, with the company's full "
            "price history behind it."
        )

    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    added = await add_listing(
        session,
        store,
        portfolio=book,
        ticker=unheld.ticker,
        exchange=unheld.exchange,
        client=standalone_price_client(settings, store=store, redis=redis),
    )
    await session.commit()
    if added.security is None:
        return added.refusal
    return added.security


@dataclass(frozen=True, slots=True)
class _Unheld:
    """A ticker this platform holds no listing for, parsed for the third door.

    ``exchange`` is what the operator named, or ``None`` for a bare ticker — and the third
    door needs one: verifying against a guessed venue could resolve a different company's
    listing somewhere else, which is worse than a refusal.
    """

    ticker: str
    exchange: str | None


async def _resolve_security(session: DbSession, typed: str) -> Security | str | _Unheld | None:
    """One typed ticker to the listing it names, or what to do about it naming none.

    ``None`` for an empty box, which is a cash transaction and not a mistake. A string is a
    refusal an operator can act on; a :class:`Security` is the answer; an :class:`_Unheld`
    is the third door's case — nothing held matches, and the handler decides whether it can
    be verified at first sight (roadmap §3.1, ADR 0093).

    Three shapes are accepted because all three are what somebody types: ``MSFT``,
    ``MSFT.US`` — the vendor's own symbol, which is what a research run stored — and
    ``MSFT NASDAQ``. Matching is case-insensitive, and an ambiguous ticker is refused with
    the choices named rather than resolved by picking one.
    """
    wanted = typed.strip().upper()
    if not wanted:
        return None

    held = list(await session.scalars(select(Security).where(Security.is_active)))
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

    # Nothing held matches. `TICKER EXCHANGE` or `TICKER.EXCHANGE` names a venue the third
    # door can verify against; a bare ticker names none, and the handler says so.
    for separator in (" ", "."):
        ticker, found, venue = wanted.partition(separator)
        if found and ticker and venue:
            return _Unheld(ticker=ticker, exchange=venue)
    return _Unheld(ticker=wanted, exchange=None)


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


def _return_rows(view: performance_service.ReturnView) -> list[dict[str, object]]:
    """One row per measured interval, with both figures and whatever refused either.

    **Two columns rather than one**, and they are labelled for the questions they answer.
    A screen showing a single "return" is quietly asserting which one the reader meant,
    and the whole point of showing both is that a well-timed top-up moves one and not the
    other.
    """
    return [
        {
            "label": period.label,
            "span": f"{period.begin.isoformat()} to {period.end.isoformat()}",
            "time_weighted": (
                _percent(period.time_weighted.value) if period.time_weighted else NO_FIGURE
            ),
            "money_weighted": (
                _percent(period.money_weighted.value) if period.money_weighted else NO_FIGURE
            ),
            "is_down": bool(period.time_weighted and period.time_weighted.value < 0),
            "problem": period.problem,
            "grade": _grade_of(period.time_weighted or period.money_weighted),
            "grade_label": _grade_label(period.time_weighted or period.money_weighted),
        }
        for period in view.periods
    ]


def _exposure_bands(
    view: performance_service.ExposureView, book: Portfolio
) -> list[dict[str, object]]:
    """The four cuts, each with its unclassified group held separately.

    ``unknown`` is its own key rather than a slice with a flag, so a template cannot render
    "Sector not known" as though it were a sector the book is in. The roadmap's own words:
    it reports what it knows and names what it does not.
    """
    return [
        {
            "kind": band.kind,
            "title": band.title,
            "slices": [_exposure_row(row, book) for row in band.slices],
            "unknown": _exposure_row(band.unknown, book) if band.unknown else None,
        }
        for band in view.bands
    ]


def _exposure_row(row: performance_service.ExposureSlice, book: Portfolio) -> dict[str, object]:
    return {
        "label": row.label,
        "value": _pounds(row.value.value, book.base_currency),
        "share": _percent(row.share.value).lstrip("+"),
        # The bar's width, as a whole number of percent. Presentation only: the figure
        # beside it is the one a reader takes away.
        "width": int(max(Decimal(0), min(Decimal(1), row.share.value)) * 100),
        "members": ", ".join(row.members),
        "count": len(row.members),
        "grade": _grade_of(row.share),
        "grade_label": _grade_label(row.share),
    }


def _concentration(view: performance_service.ExposureView) -> dict[str, object]:
    """The top-five figure, and how many holdings it actually covers.

    The count matters: a book of three names has a top five of everything it holds, and a
    figure of 100% with no count beside it reads as dangerous concentration rather than as
    a small book.
    """
    holdings = next((band for band in view.bands if band.kind == "holding"), None)
    covered = len(holdings.slices) if holdings is not None else 0
    return {
        "share": _percent(view.top_holdings.value).lstrip("+") if view.top_holdings else NO_FIGURE,
        "count": min(covered, performance_service.CONCENTRATION_COUNT),
        "of": covered,
    }


# What the screen calls each grade, derived from `web/vocabulary.py` rather than written
# here.
#
# **"Typed", not "Attested", and the difference is not cosmetic.** The shell's provenance
# vocabulary already spends the word ``Attested`` on a *record class* — a figure whose origin
# is the operator's own book — and a `documented` attestation is every bit as attested in that
# sense. This chip is about a different axis: how strong the evidence behind it is. Two
# vocabularies sharing one word teach a reader that the word means neither.
#
# Kept as a name because a test and this module's own rendering both read it; kept *derived*
# because a second copy of a label is a second answer to what a holding is called.
GRADE_LABELS: Final[dict[Grade, str]] = {
    grade: state.label for grade, state in vocabulary.GRADES.items()
}


def _grade_of(figure: portfolio_service.Figure | None) -> str:
    """The stable grade value a chip and a test both key on."""
    return "" if figure is None else figure.grade.value


def _grade_label(figure: portfolio_service.Figure | None) -> str:
    return "" if figure is None else GRADE_LABELS[figure.grade]


# -- Form handling -------------------------------------------------------------------------


async def _carried_out(
    session: DbSession, request: Request, raw: str, *, trade: Transaction, user: User
) -> Response | None:
    """Name the decision this trade carried out, or say why it cannot (ADR 0104).

    Resolved against the operator's own journal and refused if the pairing cannot be what
    it claims — a sale carrying out a buy — so the journal never holds a trade under the
    wrong entry. ``None`` is success, including the ordinary case of no decision named.
    """
    decision_id = _uuid_or_none(raw)
    if decision_id is None:
        return None
    decision = await decision_service.decision_of(session, decision_id, user_id=user.id)
    if decision is None:
        await session.rollback()
        return _problem(request, "That decision is not one in your journal.")
    await session.flush()
    try:
        await decision_service.carry_out(session, transaction=trade, decision=decision, actor=user)
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    return None


def _uuid_or_none(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw.strip()) if raw.strip() else None
    except ValueError:
        return None


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
    if kind is TransactionKind.SPLIT:
        # Derived, never typed (ADR 0094). The form does not offer it, so a submission
        # carrying it is a tampered request — and even without this refusal, the
        # `transaction_split_derives_from_an_action` constraint would reject the row.
        message = "A split is derived from the corporate action, never entered by hand."
        raise ValueError(message)
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
