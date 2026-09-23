"""A price move past the operator's threshold, delivered beside what the record says about it.

F11's second alert kind, under ADRs 0078, 0079 and 0120. It is deliberately not the monitor:
ADR 0079 keeps every price out of the monitor's evidence, because a price is the sum of every
view every holder has about everything and none of its moves is evidence about a premise.
So a move is written as a **finding of its own kind** — the row the monitor already stores
well, with a measurement in ``observed``, a sentence, and an appended act with a reason to
close it — and never touches a premise's status or opens the thesis gate.

**A move is never shipped alone.** The sentence it arrives with is the product in miniature:

    Down 12.4% over 7 days, from 410.00 USD to 359.16 USD. Nothing has been filed since your
    last check; all 3 premises still hold; the market moved -6.1% over the same period.

Every clause is a stored fact: the move is a recorded calculation, the filings are counted
from ``source_documents``, the premises' states are the monitor's own latest readings, and the
market's move is the same calculation on the exchange's proxy index. **Sector, as the
specification asked, is not available** — the platform holds a market index per exchange and
no sector series — so the sentence says *the market* and says so honestly, rather than calling
an index a sector.

**The threshold is the operator's, never a default nobody chose.** Per listing on the
watchlist entry, or the account's own default from the settings page when the entry set none.
Too many dismissals mean the threshold is wrong, not the market, and the page says so.

**It costs nothing.** Arithmetic on stored bars; no provider, no router, no model. The daily
pass (F15) calls it after the closes are in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.calc import prices as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import CalculationError, Quantity, SourceRef, Unit, UnitMismatchError
from aer.config import Settings
from aer.core.enums import FindingAction, FindingKind, PremiseStatus
from aer.db.models import (
    AuditEvent,
    Finding,
    Security,
    SourceDocument,
    Thesis,
    User,
    WatchlistEntry,
)
from aer.errors import AerError
from aer.services import watchlist as watchlist_service
from aer.services.calculations import new_context, persist_context
from aer.services.prices import adjusted_series_for
from aer.services.risk import money, percent
from aer.services.theses import SUBJECT_COMPANY, theses_for
from aer.sources.eodhd.proxies import market_proxy_for

__all__ = [
    "ACTOR",
    "CHART_SESSIONS",
    "DEFAULT_WINDOW_DAYS",
    "EVENT_TYPE",
    "SUBJECT_LISTING",
    "PremiseState",
    "PriceMove",
    "check_watched_listings",
    "figures_of",
    "headline_of",
    "moves_in_last_six_months",
    "observed_of",
    "sentence_of",
]

_log = structlog.get_logger("aer.services.price_alerts")

ACTOR: Final = "daily"
EVENT_TYPE: Final = "monitor.price_move"
SUBJECT_LISTING: Final = "security"

# The page specification's chart: a 14-session end-of-day column, the only chart sanctioned
# outside Risk, carried in `observed` so the page draws what the pass saw.
CHART_SESSIONS: Final = 14

# The window a listing followed without one gets, and what "this week" means on the page.
# The watchlist's own constant, so the form, the schema default (migration 0083) and the
# page agree on one seven.
DEFAULT_WINDOW_DAYS: Final = watchlist_service.DEFAULT_PRICE_WINDOW_DAYS

# Per cent is a convention, not a unit (ADR 0027): the operator's "10" is the fraction 0.10,
# divided exactly once, here.
_HUNDRED: Final = Decimal(100)
_SIX_MONTHS: Final = timedelta(days=182)


@dataclass(frozen=True, slots=True)
class PremiseState:
    """One premise of a thesis about the listing, as the monitor last left it."""

    thesis_title: str
    statement: str
    state: str
    """``holds``, ``contradicted``, ``weakened``, ``unobservable``, ``not yet read`` or
    ``reviewed by a person`` — the monitor's own words, never a verdict of this module's."""

    value: str = ""
    threshold: str = ""

    @property
    def is_contradicted(self) -> bool:
        return self.state == PremiseStatus.CONTRADICTED.value


@dataclass(frozen=True, slots=True)
class PriceMove:
    """One move past the threshold, with everything the record says beside it."""

    security: Security
    entry: WatchlistEntry
    window_from: date
    window_to: date
    start_close: Decimal
    end_close: Decimal
    currency: str
    move: Quantity
    """A fraction: the recorded `total_return` between the two adjusted closes."""

    threshold_pct: Decimal
    calculation_id: uuid.UUID | None
    market_move: Quantity | None
    market_label: str
    filed_since: int
    premises: tuple[PremiseState, ...]
    series: tuple[tuple[date, Decimal], ...]

    @property
    def is_down(self) -> bool:
        return self.move.value < 0

    @property
    def any_premise_contradicted(self) -> bool:
        return any(row.is_contradicted for row in self.premises)


# -- The check ---------------------------------------------------------------------------------


async def check_watched_listings(
    session: AsyncSession,
    *,
    user: User,
    settings: Settings,
    as_of: date,
    job_id: uuid.UUID,
    now: datetime | None = None,
) -> list[Finding]:
    """Compare every watched listing's move against its threshold, and write what crossed it.

    One finding per crossing, and at most one finding per listing per window, open or
    closed: a move that is still past the threshold tomorrow is the same move, not a second
    alert, and a second alert on a move already dismissed is how a threshold teaches its
    reader to dismiss it.

    A listing with no threshold of its own uses the account's default. A listing the
    platform holds no closes for is skipped and logged; the daily pass is what fetches
    closes, and a first-night follow has none yet.
    """
    checked_at = now or datetime.now(UTC)
    written: list[Finding] = []
    for entry in await watchlist_service.entries_for(session, user_id=user.id):
        security = await _security_for(session, entry)
        if security is None:
            continue
        threshold = (
            entry.price_move_threshold_pct
            if entry.price_move_threshold_pct is not None
            else settings.price_move_threshold_pct
        )
        window = timedelta(days=entry.price_move_window_days)
        window_from = as_of - window
        # "Since your last check" is the check before this one; the stamp goes on after
        # the count is taken, or every pass would report nothing filed since a moment ago.
        previous_check = entry.last_checked_at or datetime.combine(
            window_from, datetime.min.time(), tzinfo=UTC
        )

        context = new_context()
        measured = await _move_over_window(
            session, context, security=security, as_of=as_of, window_from=window_from
        )
        entry.last_checked_at = checked_at
        if measured is None:
            continue
        start, end, move = measured
        listing_record = context.records[-1] if context.records else None

        if abs(move.value) < threshold / _HUNDRED:
            continue
        if await _already_raised(session, user_id=user.id, security=security, since=window_from):
            continue

        market_move, market_label = await _market_move(
            session, context, security=security, as_of=as_of, window_from=window_from
        )
        if context.records:
            await persist_context(session, context, job_id=job_id)

        found = PriceMove(
            security=security,
            entry=entry,
            window_from=start.on,
            window_to=end.on,
            start_close=start.total_return_close,
            end_close=end.total_return_close,
            currency=security.quote_currency,
            move=move,
            threshold_pct=Decimal(threshold),
            calculation_id=listing_record.id if listing_record is not None else None,
            market_move=market_move,
            market_label=market_label,
            filed_since=await _filed_since(session, security=security, since=previous_check),
            premises=await _premise_states(session, user_id=user.id, security=security),
            series=await _chart_series(session, security=security, as_of=as_of),
        )
        finding = Finding(
            user_id=user.id,
            thesis_id=None,
            security_id=security.id,
            job_id=job_id,
            kind=FindingKind.PRICE_MOVE,
            status=None,
            justification=sentence_of(found),
            source_document_ids=[],
            observed=observed_of(found),
            window_from=found.window_from,
            window_to=found.window_to,
            opens_gate=False,
        )
        session.add(finding)
        await session.flush()
        await _record(session, finding=finding, job_id=job_id, observed=finding.observed)
        _log.info(
            "price_alerts.moved",
            user_id=str(user.id),
            listing=security.listing,
            move=str(move.value),
            threshold_pct=str(threshold),
            finding_id=str(finding.id),
        )
        written.append(finding)
    return written


async def _security_for(session: AsyncSession, entry: WatchlistEntry) -> Security | None:
    found: Security | None = await session.scalar(
        select(Security)
        .options(selectinload(Security.company))
        .where(
            Security.ticker == entry.ticker,
            Security.exchange == entry.exchange,
            Security.is_active.is_(True),
        )
        .limit(1)
    )
    return found


async def _move_over_window(
    session: AsyncSession,
    context: CalculationContext,
    *,
    security: Security,
    as_of: date,
    window_from: date,
) -> tuple[calc.AdjustedBar, calc.AdjustedBar, Quantity] | None:
    """The recorded return from the last close on or before the window's start to the latest.

    On the *total-return* series, so a dividend's ex-date is not read as a fall (see
    :func:`aer.calc.prices.total_return`). ``None`` when the platform holds too little
    history to measure — a listing followed last night has no window yet.
    """
    try:
        series = await adjusted_series_for(
            session, security, as_of=as_of, since=window_from - timedelta(days=31)
        )
    except (AerError, CalculationError) as why:
        _log.info("price_alerts.unmeasured", listing=security.listing, reason=str(why))
        return None
    if not series.bars:
        return None
    start = next((bar for bar in reversed(series.bars) if bar.on <= window_from), None)
    end = series.latest
    if start is None or end.on <= start.on:
        return None
    unit = _unit_of(series.currency)
    # A close is a fact somebody published, and the listing is its leaf (SourceTable
    # docstring): the same reference the risk service gives a return series, so the
    # recorded calculation traces to the row rather than arriving as a bare number.
    source = SourceRef.security(
        security.id,
        label=(
            f"{security.provider_symbol} total-return close {start.on.isoformat()} to "
            f"{end.on.isoformat()}"
        ),
    )
    try:
        move = calc.total_return(
            context,
            start=Quantity.of(start.total_return_close, unit, source=source),
            end=Quantity.of(end.total_return_close, unit, source=source),
        )
    except (CalculationError, UnitMismatchError) as why:
        _log.info("price_alerts.unmeasured", listing=security.listing, reason=str(why))
        return None
    return start, end, move


def _unit_of(currency: str) -> Unit:
    """The quote currency as a unit, or a base unit named for it when it is not a currency
    the parser knows — pence quotes are ``GBX``. A return is the same fraction either way,
    and the check is that both ends share the unit, which they do."""
    try:
        return Unit.parse(currency)
    except (CalculationError, UnitMismatchError, ValueError):
        return Unit.base(currency)


async def _already_raised(
    session: AsyncSession, *, user_id: uuid.UUID, security: Security, since: date
) -> bool:
    """Whether a finding on this listing already covers the window — resolved or not.

    Resolved counts: a move dismissed on Monday is not news again on Tuesday, and counting
    it twice would also double the dismissals the threshold band reads as its own verdict.
    """
    found = await session.scalar(
        select(Finding.id)
        .where(
            Finding.user_id == user_id,
            Finding.security_id == security.id,
            Finding.kind == FindingKind.PRICE_MOVE,
            Finding.window_to >= since,
        )
        .limit(1)
    )
    return found is not None


async def _market_move(
    session: AsyncSession,
    context: CalculationContext,
    *,
    security: Security,
    as_of: date,
    window_from: date,
) -> tuple[Quantity | None, str]:
    """The exchange's proxy index over the same window, or nothing and its name.

    The name is bare — *S&P 500*, *FTSE All-Share*, or *market* when the exchange has no
    proxy — and the sentence and the page put their own *the* in front of it.
    """
    try:
        proxy = market_proxy_for(security.exchange)
    except (AerError, KeyError, ValueError):
        return None, "market"
    index = await session.scalar(
        select(Security).where(Security.provider_symbol == proxy.symbol).limit(1)
    )
    if index is None:
        return None, proxy.label
    measured = await _move_over_window(
        session, context, security=index, as_of=as_of, window_from=window_from
    )
    if measured is None:
        return None, proxy.label
    return measured[2], proxy.label


async def _filed_since(session: AsyncSession, *, security: Security, since: datetime) -> int:
    if security.company_id is None:
        return 0
    count = await session.scalar(
        select(func.count())
        .select_from(SourceDocument)
        .where(
            SourceDocument.company_id == security.company_id, SourceDocument.retrieved_at > since
        )
    )
    return int(count or 0)


async def _premise_states(
    session: AsyncSession, *, user_id: uuid.UUID, security: Security
) -> tuple[PremiseState, ...]:
    """Every premise of every open thesis about this company, as the monitor last read it.

    Read, never re-measured: the state beside a price move is what the record already says,
    and re-reading a premise here would be the monitor's job done in the wrong place with
    the wrong evidence (ADR 0079).
    """
    if security.company_id is None:
        return ()
    states: list[PremiseState] = []
    for thesis in await theses_for(session, user_id=user_id, retired=False):
        if thesis.subject_kind != SUBJECT_COMPANY or thesis.subject_id != security.company_id:
            continue
        for premise in thesis.premises:
            if premise.judgement.is_withdrawn:
                continue
            if not premise.has_predicate:
                states.append(PremiseState(thesis.title, premise.statement, "reviewed by a person"))
                continue
            last = await _last_reading(session, thesis=thesis, judgement_id=premise.judgement_id)
            if last is None or last.status is None:
                states.append(PremiseState(thesis.title, premise.statement, "not yet read"))
                continue
            observed = last.observed or {}
            state = "holds" if last.status is PremiseStatus.UNCHANGED else last.status.value
            states.append(
                PremiseState(
                    thesis.title,
                    premise.statement,
                    state,
                    value=f"{observed.get('value', '')} {observed.get('unit', '')}".strip(),
                    threshold=(
                        f"{observed.get('comparator', '')} {observed.get('threshold', '')} "
                        f"{observed.get('threshold_unit') or observed.get('unit') or ''}"
                    ).strip(),
                )
            )
    return tuple(states)


async def _last_reading(
    session: AsyncSession, *, thesis: Thesis, judgement_id: uuid.UUID
) -> Finding | None:
    found: Finding | None = await session.scalar(
        select(Finding)
        .where(
            Finding.thesis_id == thesis.id,
            Finding.judgement_id == judgement_id,
            Finding.kind == FindingKind.READING,
        )
        .order_by(Finding.created_at.desc())
        .limit(1)
    )
    return found


async def _chart_series(
    session: AsyncSession, *, security: Security, as_of: date
) -> tuple[tuple[date, Decimal], ...]:
    """The last fourteen closes, for the one chart the specification sanctions here."""
    try:
        series = await adjusted_series_for(
            session, security, as_of=as_of, since=as_of - timedelta(days=CHART_SESSIONS * 2)
        )
    except (AerError, CalculationError):
        return ()
    return tuple((bar.on, bar.close) for bar in series.bars[-CHART_SESSIONS:])


# -- What the record says, in words ------------------------------------------------------------


def sentence_of(move: PriceMove) -> str:
    """The move beside what the record says about it. Every clause is a stored fact."""
    direction = "Down" if move.is_down else "Up"
    days = move.entry.price_move_window_days
    head = (
        f"{direction} {_unsigned(move.move.value)} over {days} day{'s' if days != 1 else ''}, "
        f"from {money(move.start_close, move.currency)} to {money(move.end_close, move.currency)}."
    )
    if move.filed_since == 0:
        filed = "Nothing has been filed since your last check"
    else:
        filed = (
            f"{move.filed_since} document{'s have' if move.filed_since != 1 else ' has'} "
            "been filed since your last check"
        )
    if not move.premises:
        premises = "there is no thesis behind this listing"
    else:
        broken = [row for row in move.premises if row.is_contradicted]
        if broken:
            premises = (
                f"{len(broken)} of {len(move.premises)} premise"
                f"{'s' if len(move.premises) != 1 else ''} "
                f"{'are' if len(broken) != 1 else 'is'} contradicted"
            )
        else:
            premises = (
                f"all {len(move.premises)} premises still hold"
                if len(move.premises) != 1
                else "the one premise still holds"
            )
    if move.market_move is None:
        market = f"the {move.market_label} could not be measured over the same period"
    else:
        market = (
            f"the {move.market_label} moved {percent(move.market_move.value)} over the same period"
        )
    return f"{head} {filed}; {premises}; {market}."


def headline_of(finding: Finding) -> str:
    """The page specification's row: *"{company} moved {pct} this week"*."""
    observed = finding.observed or {}
    name = observed.get("company") or observed.get("listing") or "A listing"
    days = int(observed.get("window_days", DEFAULT_WINDOW_DAYS))
    when = "this week" if days == DEFAULT_WINDOW_DAYS else f"over {days} days"
    return f"{name} moved {observed.get('move_pct', '')} {when}"


def observed_of(move: PriceMove) -> dict[str, Any]:
    """What the pass saw, for the record and for the page — strings, so JSON keeps them."""
    return {
        "listing": move.security.listing,
        "company": move.security.name or move.security.ticker,
        "move": str(move.move.value),
        "move_pct": percent(move.move.value),
        "direction": "down" if move.is_down else "up",
        "start_close": str(move.start_close),
        "end_close": str(move.end_close),
        "currency": move.currency,
        "window_days": move.entry.price_move_window_days,
        "window_from": move.window_from.isoformat(),
        "window_to": move.window_to.isoformat(),
        "threshold_pct": _plain(move.threshold_pct),
        "calculation_id": str(move.calculation_id) if move.calculation_id else None,
        "market_move_pct": percent(move.market_move.value) if move.market_move else None,
        "market_label": move.market_label,
        "filed_since": move.filed_since,
        "premises": [
            {
                "thesis": row.thesis_title,
                "statement": row.statement,
                "state": row.state,
                "value": row.value,
                "threshold": row.threshold,
            }
            for row in move.premises
        ],
        "series": [[on.isoformat(), str(close)] for on, close in move.series],
    }


def figures_of(finding: Finding) -> dict[str, Any] | None:
    """The finding page's price-move block, rendered from `observed`; ``None`` for a reading.

    Nothing here is computed: the page shows what the pass recorded, and the chart's bar
    heights are the one exception — a proportion of the tallest close, which is layout.
    """
    if finding.kind is not FindingKind.PRICE_MOVE or not finding.observed:
        return None
    observed = finding.observed
    premises = list(observed.get("premises") or [])
    contradicted = any(row.get("state") == PremiseStatus.CONTRADICTED.value for row in premises)
    if not premises:
        headline = "The price moved. There is no thesis behind this listing."
    elif contradicted:
        headline = "The price moved, and so did a premise."
    else:
        headline = "The price moved. Your thesis did not."
    series = [(on, Decimal(close)) for on, close in observed.get("series") or []]
    tallest = max((close for _, close in series), default=Decimal(0))
    return {
        "headline": headline,
        "company": observed.get("company", ""),
        "listing": observed.get("listing", ""),
        "direction": observed.get("direction", ""),
        "move_pct": observed.get("move_pct", ""),
        "start_close": money(Decimal(observed["start_close"]), observed.get("currency", "")),
        "end_close": money(Decimal(observed["end_close"]), observed.get("currency", "")),
        "window_days": observed.get("window_days", 7),
        "window_from": observed.get("window_from", ""),
        "window_to": observed.get("window_to", ""),
        "threshold_pct": observed.get("threshold_pct", ""),
        "calculation_href": (
            f"/calculations/{observed['calculation_id']}" if observed.get("calculation_id") else ""
        ),
        "filed_since": int(observed.get("filed_since") or 0),
        "market_label": observed.get("market_label", "market"),
        "market_move_pct": observed.get("market_move_pct"),
        "premises": premises,
        "any_contradicted": contradicted,
        "series": [
            {
                "on": on,
                "close": str(close),
                "height": int((close / tallest) * 100) if tallest > 0 else 0,
            }
            for on, close in series
        ],
    }


async def moves_in_last_six_months(
    session: AsyncSession, *, user_id: uuid.UUID, security_id: uuid.UUID, now: datetime
) -> tuple[int, int]:
    """How many alerts this listing's threshold has produced in six months, and how many
    were dismissed — the two numbers the threshold band puts beside the control to change
    it, because too many dismissals mean the threshold is wrong, not the market."""
    rows = list(
        await session.scalars(
            select(Finding)
            .options(selectinload(Finding.resolutions))
            .where(
                Finding.user_id == user_id,
                Finding.security_id == security_id,
                Finding.kind == FindingKind.PRICE_MOVE,
                Finding.created_at >= now - _SIX_MONTHS,
            )
        )
    )
    dismissed = sum(
        1
        for row in rows
        if row.resolutions and row.resolutions[-1].action is FindingAction.DISMISSED
    )
    return len(rows), dismissed


def _unsigned(fraction: Decimal) -> str:
    return percent(fraction).lstrip("+-")


def _plain(value: Decimal) -> str:
    """A threshold as a person typed it: the column's ``2.00`` reads back as ``2``, and the
    fixed-point format keeps ``10`` from becoming ``1E+1`` on the way."""
    return f"{value.normalize():f}"


# -- The chain ----------------------------------------------------------------------------------


async def _record(
    session: AsyncSession, *, finding: Finding, job_id: uuid.UUID, observed: dict[str, Any] | None
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=ACTOR,
            event_type=EVENT_TYPE,
            payload={
                "finding_id": str(finding.id),
                "security_id": str(finding.security_id),
                "observed": observed,
            },
            previous=previous,
            job_id=job_id,
            subject_kind=SUBJECT_LISTING,
            subject_id=finding.security_id,
        )
    )
    await session.flush()
