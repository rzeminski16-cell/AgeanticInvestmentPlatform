"""The book as a validity dashboard (page specification §2).

The portfolio page answers *"are the reasons I bought still standing?"* before it answers
*"what is it worth?"*. Everything here is read from the record on the way to the page —
the thesis state from the company record, the last check from the daily pass, the risk
flags from the exposure the risk page shows — and nothing is stored. The one figure that
is arithmetic, the day's move, is a traced calculation over two valuations of the book
(:func:`aer.calc.changes.relative_change`), never a subtraction in a template.

**The default sort is conviction risk, descending**: no thesis, then a premise broke, then
under review, then not checked within cadence, then holds — ties by weight. A position
whose thesis broke sits above one that is down eight per cent with every premise intact,
because the first needs a decision and the second needs nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.changes import relative_change
from aer.calc.units import CalculationError
from aer.core.dates import format_date
from aer.db.models import Portfolio, User
from aer.errors import AerError
from aer.services import calculations as calculation_service
from aer.services import company_record as record_service
from aer.services import daily_pass
from aer.services import limits as limit_service
from aer.services import performance as performance_service
from aer.services import portfolio as portfolio_service
from aer.services import risk as risk_service
from aer.services.company_record import CompanyRecord
from aer.web import vocabulary

__all__ = [
    "FILTERS",
    "SORTS",
    "Validity",
    "days_move",
    "filter_rows",
    "risk_summary",
    "sort_rows",
    "validity_for",
]

_log = structlog.get_logger("aer.web.portfolio.dashboard")

# The sort orders the control offers (§2.2). Conviction is the default on a fresh session.
SORTS: Final[tuple[str, ...]] = ("conviction", "weight", "value", "unrealised", "name")
# The filter row (§2.2). *Over ceiling* matches a holding past a ceiling the operator stated
# on Platform (ADR 0136); with none stated it matches nothing, and the page says why.
FILTERS: Final[tuple[str, ...]] = ("all", "in_doubt", "no_thesis", "overdue", "over_ceiling")

# The flag a holding past one of the operator's own ceilings carries, first among its flags.
OVER_CEILING: Final = "over ceiling"

# Conviction risk, descending (§2.2): the lower the number, the higher up the page.
CONVICTION: Final[dict[str, int]] = {
    "none": 0,
    "broke": 1,
    "under_review": 2,
    "unchecked": 3,
    "holds": 4,
}

# What each state is called, and the tone it wears. *No thesis* is the strongest wording
# on the page: it is money committed for reasons nobody wrote down.
THESIS_WORDS: Final[dict[str, tuple[str, str]]] = {
    "holds": ("holds", vocabulary.Tone.SUCCESS.value),
    "broke": ("one premise broke", vocabulary.Tone.FAILURE.value),
    "under_review": ("under review", vocabulary.Tone.WARNING.value),
    "none": ("no thesis", vocabulary.Tone.WARNING.value),
}


@dataclass(frozen=True, slots=True)
class Validity:
    """What the record says about one holding, beside its value."""

    company_id: uuid.UUID | None
    company_name: str
    thesis_state: str
    broken_premises: int
    checked: str
    checked_overdue: bool
    risk_flags: tuple[str, ...]
    over_ceiling: bool = False
    next_check: str = ""

    @property
    def thesis_words(self) -> str:
        return THESIS_WORDS[self.thesis_state][0]

    @property
    def thesis_tone(self) -> str:
        return THESIS_WORDS[self.thesis_state][1]

    @property
    def conviction(self) -> int:
        if self.thesis_state == "holds" and self.checked_overdue:
            return CONVICTION["unchecked"]
        return CONVICTION[self.thesis_state]

    @property
    def in_doubt(self) -> bool:
        return self.thesis_state in {"broke", "under_review"}


async def validity_for(
    session: AsyncSession,
    *,
    user: User,
    view: portfolio_service.PortfolioView,
    exposure: performance_service.ExposureView,
    limits: limit_service.Limits | None = None,
    now: datetime | None = None,
) -> dict[uuid.UUID, Validity]:
    """One reading per held listing, keyed by security id.

    A holding is *over ceiling* when its own weight is past the operator's single-position
    ceiling, or its sector's share past that sector's (ADR 0136) — the comparison made in
    code over the weights and shares the risk page prints.
    """
    moment = now or datetime.now(UTC)
    stated = limits or limit_service.Limits()
    sector_share = {
        member: (row.label, row.share.value)
        for band in exposure.bands
        if band.kind == "sector"
        for row in band.slices
        for member in row.members
    }
    records = {
        record.company.id: record
        for record in await record_service.records_for(session, user=user, now=moment)
        if record.company is not None
    }
    last = await daily_pass.last_pass(session, user_id=user.id)
    finished = last.finished_at if last is not None else None
    pass_state = daily_pass.pass_state(finished, now=moment)
    checked, next_check, overdue = _checked_words(finished, missed=pass_state.is_missed)
    concentrated, sector_of = _risk_cuts(exposure)

    readings: dict[uuid.UUID, Validity] = {}
    for row in view.holdings:
        if row.problem == portfolio_service.CLOSED:
            continue
        record = records.get(row.security.company_id) if row.security.company_id else None
        flags: list[str] = []
        sector, share = sector_share.get(row.security.ticker, ("", None))
        over = limit_service.is_over(
            row.weight.value if row.weight is not None else None, stated.single_position
        ) or limit_service.is_over(share, stated.for_sector(sector) if sector else None)
        if over:
            flags.append(OVER_CEILING)
        if row.security.ticker in concentrated:
            flags.append("concentration")
        if row.security.ticker in sector_of:
            flags.append(f"sector {sector_of[row.security.ticker]}")
        readings[row.security.id] = Validity(
            company_id=record.company.id if record is not None and record.company else None,
            company_name=record.name if record is not None else "",
            thesis_state=_thesis_state(record),
            broken_premises=record.broken_premises if record is not None else 0,
            checked=checked,
            checked_overdue=overdue,
            risk_flags=tuple(flags),
            over_ceiling=over,
            next_check=next_check,
        )
    return readings


def _thesis_state(record: CompanyRecord | None) -> str:
    if record is None or record.thesis is None:
        return "none"
    if record.broken_premises:
        return "broke"
    if record.thesis_state == "under_review":
        return "under_review"
    return "holds"


def _checked_words(finished: datetime | None, *, missed: bool) -> tuple[str, str, bool]:
    """The last monitor pass and the next scheduled one, or *Never* — the column's two
    lines, as drawn: the day it was read, and when it is next."""
    if finished is None:
        return "Never", "", True
    due = finished + daily_pass.CADENCE
    upcoming = f"Next {format_date(due, '%-d %b')}"
    return (
        format_date(finished, "%-d %b"),
        f"{upcoming} — overdue" if missed else upcoming,
        missed,
    )


def _risk_cuts(
    exposure: performance_service.ExposureView,
) -> tuple[frozenset[str], dict[str, str]]:
    """Which tickers are among the largest holdings, and which sit in the largest sector.

    *Concentration* means among the five largest holdings the top-five figure counts;
    *sector {name}* names the largest sector cut for each member of it. Neither is a
    breach — a breach is *over ceiling*, against a limit the operator stated — so both are
    flags that something applies, with the working on the risk page.
    """
    concentrated: frozenset[str] = frozenset()
    sector_of: dict[str, str] = {}
    for band in exposure.bands:
        if band.kind == "holding":
            largest = sorted(band.slices, key=lambda row: row.share.value, reverse=True)
            concentrated = frozenset(
                ticker
                for row in largest[: performance_service.CONCENTRATION_COUNT]
                for ticker in row.members
            )
        if band.kind == "sector" and band.slices:
            biggest = max(band.slices, key=lambda row: row.share.value)
            sector_of = dict.fromkeys(biggest.members, biggest.label)
    return concentrated, sector_of


# -- Ordering the table ------------------------------------------------------------------------


def sort_rows(
    rows: list[dict[str, Any]], readings: dict[uuid.UUID, Validity], sort: str
) -> list[dict[str, Any]]:
    """The table in the chosen order; anything unknown is conviction."""
    chosen = sort if sort in SORTS else "conviction"

    def weight_of(row: dict[str, Any]) -> Decimal:
        figure = row.get("weight_value")
        return figure if isinstance(figure, Decimal) else Decimal(-1)

    def money_of(row: dict[str, Any], key: str) -> Decimal:
        figure = row.get(key)
        return figure if isinstance(figure, Decimal) else Decimal("-Infinity")

    if chosen == "conviction":
        return sorted(
            rows,
            key=lambda row: (
                _conviction_of(row, readings),
                -weight_of(row),
                str(row["ticker"]),
            ),
        )
    if chosen == "weight":
        return sorted(rows, key=lambda row: (-weight_of(row), str(row["ticker"])))
    if chosen == "value":
        return sorted(rows, key=lambda row: (-money_of(row, "value_amount"), str(row["ticker"])))
    if chosen == "unrealised":
        return sorted(
            rows, key=lambda row: (-money_of(row, "unrealised_amount"), str(row["ticker"]))
        )
    return sorted(rows, key=lambda row: str(row["name"]).lower())


def _conviction_of(row: dict[str, Any], readings: dict[uuid.UUID, Validity]) -> int:
    reading = readings.get(row["security_id"])
    # A closed row has no conviction to rank; it sorts last.
    return reading.conviction if reading is not None else len(CONVICTION)


def filter_rows(
    rows: list[dict[str, Any]], readings: dict[uuid.UUID, Validity], show: str
) -> list[dict[str, Any]]:
    """The filter row's reading of the table. Unknown shows everything."""

    def reading_of(row: dict[str, Any]) -> Validity | None:
        return readings.get(row["security_id"])

    if show == "in_doubt":
        return [row for row in rows if (r := reading_of(row)) is not None and r.in_doubt]
    if show == "no_thesis":
        return [
            row for row in rows if (r := reading_of(row)) is not None and r.thesis_state == "none"
        ]
    if show == "overdue":
        return [row for row in rows if (r := reading_of(row)) is not None and r.checked_overdue]
    if show == "over_ceiling":
        return [row for row in rows if (r := reading_of(row)) is not None and r.over_ceiling]
    return list(rows)


# -- The header's move and the risk summary ---------------------------------------------------


async def days_move(
    session: AsyncSession, *, book: Portfolio, as_of: date, latest: Decimal
) -> Decimal | None:
    """The book's move from the close before ``as_of``, as a ratio, or ``None``.

    Two valuations and one traced change. ``None`` where the earlier close cannot be
    valued in full: a move against a partial book would be a figure about nothing.
    """
    try:
        prior = await portfolio_service.book_as_at(
            session,
            calculation_service.new_context(),
            portfolio=book,
            as_of=as_of - timedelta(days=1),
        )
    except (AerError, CalculationError) as problem:
        _log.info("dashboard.prior_close_unreadable", reason=str(problem))
        return None
    if not prior.is_complete or prior.net_assets is None:
        return None
    return relative_change(prior.net_assets.value, latest)


@dataclass(frozen=True, slots=True)
class SummaryRow:
    """One row of the risk summary (§2.3): a label, a figure, a note, the working's page."""

    key: str
    label: str
    value: str
    note: str
    href: str
    # The ink the figure is drawn in: warning past one of the operator's own ceilings,
    # failure for the loss a stated shock would take. Empty is the ordinary ink.
    tone: str = ""


async def risk_summary(
    session: AsyncSession,
    *,
    book: Portfolio,
    view: portfolio_service.PortfolioView,
    exposure: performance_service.ExposureView,
    as_of: date,
    money: Any,
    share: Any,
    readings: dict[uuid.UUID, Validity] | None = None,
    limits: limit_service.Limits | None = None,
) -> list[SummaryRow]:
    """The five rows, each read from what the risk page computes and linking to it.

    ``money`` and ``share`` are the page's own formatters, passed in so the summary renders
    figures exactly as the table beside it does. The top-five row and the count of positions
    over their ceiling read the operator's own limits (ADR 0136), and say so where none is set.
    """
    stated = limits or limit_service.Limits()
    rows: list[SummaryRow] = []
    holdings = next((band for band in exposure.bands if band.kind == "holding"), None)
    covered = len(holdings.slices) if holdings is not None else 0
    top = exposure.top_holdings
    ceiling = stated.five_largest
    top_over = False
    if ceiling is None:
        top_note = "Of the book. No top-five ceiling is set, so this is the figure alone."
    else:
        top_over = limit_service.is_over(top.value if top is not None else None, ceiling)
        top_note = f"Of the book, against {limit_service.ceiling_words(ceiling)}" + (
            " — over it." if top_over else "."
        )
    rows.append(
        SummaryRow(
            key="concentration",
            label=f"Largest {min(covered, performance_service.CONCENTRATION_COUNT)} holdings",
            value=(
                share(top.value)
                + (f" / {limit_service.limit_percent(ceiling)}" if ceiling is not None else "")
                if top is not None
                else "—"
            ),
            note=top_note,
            href="/risk#exposure",
            tone=vocabulary.Tone.WARNING.value if top_over else "",
        )
    )
    sector = next((band for band in exposure.bands if band.kind == "sector"), None)
    biggest = (
        max(sector.slices, key=lambda row: row.share.value)
        if sector is not None and sector.slices
        else None
    )
    rows.append(
        SummaryRow(
            key="sector",
            label="Largest sector",
            value=share(biggest.share.value) if biggest is not None else "—",
            note=biggest.label if biggest is not None else "No sector cut could be named.",
            href="/risk#exposure",
        )
    )
    past = sum(1 for reading in (readings or {}).values() if reading.over_ceiling)
    has_position_ceiling = stated.single_position is not None or bool(stated.sectors)
    rows.append(
        SummaryRow(
            key="over_ceiling",
            label="Over their ceiling",
            value=(
                (f"{past} position{'s' if past != 1 else ''}" if past else "none")
                if has_position_ceiling
                else "none set"
            ),
            note=(
                "Against the ceilings you set on Platform."
                if has_position_ceiling
                else "You have set no position or sector ceiling; nothing is proposed."
            ),
            href="/platform/book" if not has_position_ceiling else "/risk#exposure",
            tone=vocabulary.Tone.WARNING.value if past else "",
        )
    )
    rows.append(await _shock_row(session, book=book, as_of=as_of, money=money, share=share))
    cash = [row for row in view.cash if row.weight is not None]
    rows.append(
        SummaryRow(
            key="cash",
            label="Cash as a share",
            value=(
                "; ".join(f"{row.currency} {share(row.weight.value)}" for row in cash if row.weight)
                if cash
                else "—"
            ),
            note="Each currency's weight in the book." if len(cash) > 1 else "Of the book.",
            href="/risk",
        )
    )
    return rows


async def _shock_row(
    session: AsyncSession, *, book: Portfolio, as_of: date, money: Any, share: Any
) -> SummaryRow:
    """The latest stated scenario and what it does to the book, from the risk page's own
    computation (F12: one implementation, surfaced twice) — the same one the risk page's
    shock panel shows, because the latest stated is the one the operator last meant."""
    scenarios = (await risk_service.scenarios_for(session, portfolio=book))[-1:]
    if not scenarios:
        return SummaryRow(
            key="shock",
            label="Stated shock",
            value="none stated",
            note="State one on the risk page; the platform proposes nothing.",
            href="/risk#scenarios",
        )
    try:
        reading = await risk_service.risk_as_at(
            session,
            calculation_service.new_context(),
            portfolio=book,
            as_of=as_of,
            scenarios=scenarios,
        )
    except (AerError, CalculationError) as problem:
        _log.info("dashboard.scenario_unreadable", reason=str(problem))
        return SummaryRow(
            key="shock",
            label="Stated shock",
            value="—",
            note=f"{scenarios[0].name}: could not be read as at this date.",
            href="/risk#scenarios",
        )
    outcome = next((row for row in reading.scenarios if row.pnl is not None), None)
    if outcome is None:
        return SummaryRow(
            key="shock",
            label="Stated shock",
            value="—",
            note=f"{scenarios[0].name}: reaches nothing the book holds.",
            href="/risk#scenarios",
        )
    # The shock's own name above what it does, as drawn: the loss, then its share of the book.
    impact = f" · {share(outcome.impact.value)} of the book" if outcome.impact is not None else ""
    loss = outcome.pnl is not None and outcome.pnl.value < 0
    return SummaryRow(
        key="shock",
        label="Stated shock",
        value=f"{money(outcome.pnl.value, book.base_currency)}{impact}" if outcome.pnl else "—",
        note=outcome.scenario.name,
        href="/risk#scenarios",
        tone=vocabulary.Tone.FAILURE.value if loss else "",
    )
