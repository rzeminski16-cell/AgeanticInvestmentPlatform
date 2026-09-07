"""What could go wrong with a book: the figures, the scenarios the operator stated, and the
analyst's reading of both.

Roadmap §3.9, under ADRs 0080 and 0106. Three things, in the order a reading happens.

**The figures are code's.** :func:`risk_as_at` computes the book as at a date, its exposure
bands, each holding's daily returns over the year to that date, the book's return series
with today's weights held fixed, and from those the volatility, the drawdown, the tail,
each measured holding's beta to the book and its contribution — every one a recorded
calculation through :mod:`aer.calc.risk` and :mod:`aer.calc.prices`, and every one
*ex-ante*: how the book as it stands would have moved, in each listing's own currency.

**A scenario is a statement.** :func:`state_scenario` writes what the operator said a
shock reaches and by how much; the profit and loss is computed here against the book's
values, never stored, and a scenario that reaches nothing says so.

**The analyst reads and cannot write.** :func:`run_reading` renders the figures as
strings, hands them to the ``risk_analyst`` role, and refuses a commentary that names a
numeral the block does not hold or that prescribes anything, once with the problems
carried back and then for good. The pass keeps what was said and why it was not shown.

**Nothing here sizes, limits, ranks or scores.** ADR 0080's list, enforced by there being
no function for any of it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.agents.base import AgentContext
from aer.agents.risk_analyst import (
    FigureLine,
    HoldingLine,
    RiskAnalystAgent,
    RiskCommentary,
    RiskInput,
    ScenarioLine,
    commentary_problems,
)
from aer.calc import performance as performance_calc
from aer.calc import prices as price_calc
from aer.calc import risk as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import DIMENSIONLESS, CalculationError, Quantity, SourceRef
from aer.config import Settings
from aer.core.enums import JobStatus, RequestStatus, ShockKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Attestation,
    AuditEvent,
    Job,
    JobStep,
    Portfolio,
    RiskScenario,
    RiskScenarioShock,
    Security,
    Transaction,
    User,
    WorkOrder,
)
from aer.errors import AerError, ConflictError, ValidationError
from aer.providers.protocol import LLMProvider
from aer.providers.router import Router
from aer.services.calculations import new_context, persist_context
from aer.services.performance import (
    ExposureView,
    country_of,
    currency_of,
    exposure_as_at,
    major_currency,
    sector_of,
)
from aer.services.portfolio import CLOSED, Figure, PortfolioView, book_as_at, graded_figure
from aer.services.prices import adjusted_series_for
from aer.storage.protocol import ArtefactStore
from aer.version import git_sha

__all__ = [
    "FREQUENCY",
    "STEP_KEY",
    "SUBJECT_BOOK",
    "TOOL",
    "WINDOW_DAYS",
    "WORKFLOW_VERSION",
    "HoldingRisk",
    "Reading",
    "RiskView",
    "ScenarioOutcome",
    "Shock",
    "ShockedPosition",
    "block_of",
    "last_trade_recorded_at",
    "latest_reading",
    "money",
    "percent",
    "reading_of",
    "risk_as_at",
    "run_reading",
    "scenarios_for",
    "state_scenario",
    "withdraw_scenario",
]

_log = structlog.get_logger("aer.services.risk")

TOOL: Final = "risk"
SUBJECT_BOOK: Final = "portfolio"
WORKFLOW_VERSION: Final = "risk_reading_v1"
STEP_KEY: Final = "risk"

# The window every figure is measured over, and how often a return is taken inside it. A
# year of daily returns is the ex-ante convention (ADR 0106 §1); both are recorded on the
# figures, because a volatility quoted without its window is not reproducible.
WINDOW_DAYS: Final = 365
FREQUENCY: Final = price_calc.Frequency.DAILY

# One retry after a refused commentary, with the problems carried back. A second refusal
# is recorded rather than retried: a role that cannot stop naming numbers in two attempts
# is not going to in three, and each attempt spends.
_RETRIES: Final = 1

_NO_DENOMINATOR: Final = (
    "The book nets to nothing or less, so there is no whole for a risk to be a share of."
)


# -- The figures -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HoldingRisk:
    """One priced holding: how it moved on its own, and how much of the book's risk is it."""

    security: Security
    weight: Figure | None
    volatility: Figure | None
    beta_to_book: Figure | None
    contribution: Figure | None
    observations: int
    problem: str = ""

    @property
    def is_measured(self) -> bool:
        return self.volatility is not None


@dataclass(frozen=True, slots=True)
class ShockedPosition:
    """One position a scenario reaches: what it is worth, what it takes, what that costs.

    The scenario as a diff of the book, one row at a time. ``shock`` is the combined
    fraction after every shock that reaches the position; ``pnl`` is its own recorded
    calculation, and the rows sum to the scenario's total.
    """

    label: str
    value: Figure
    shock: Decimal
    pnl: Figure


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """What one stated scenario does to the book as at the date."""

    scenario: RiskScenario
    pnl: Figure | None
    impact: Figure | None
    reached: tuple[str, ...]
    problem: str = ""
    positions: tuple[ShockedPosition, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskView:
    """Everything the risk page shows, as at one date. Every figure is ex-ante (ADR 0106)."""

    portfolio: Portfolio
    as_of: date
    window_from: date
    book: PortfolioView
    exposure: ExposureView
    volatility: Figure | None
    drawdown: Figure | None
    expected_shortfall: Figure | None
    observations: int
    coverage: Figure | None
    """The share of net assets the measured holdings account for."""
    holdings: tuple[HoldingRisk, ...] = ()
    scenarios: tuple[ScenarioOutcome, ...] = ()
    problem: str = ""

    @property
    def is_measured(self) -> bool:
        return self.volatility is not None

    @property
    def measured(self) -> tuple[HoldingRisk, ...]:
        return tuple(row for row in self.holdings if row.is_measured)


async def risk_as_at(
    session: AsyncSession,
    context: CalculationContext,
    *,
    portfolio: Portfolio,
    as_of: date,
    scenarios: list[RiskScenario] | None = None,
) -> RiskView:
    """The book's risk as at a date, every figure a recorded calculation in ``context``.

    The book and its exposure are computed in the same ledger, so the risk figures cite
    the same valuation the page shows. A holding whose price history does not reach far
    enough is *unmeasured*, named with the reason, and left out of the book's series rather
    than filled in; the coverage figure says how much of the book that leaves.
    """
    book = await book_as_at(session, context, portfolio=portfolio, as_of=as_of)
    exposure = await exposure_as_at(session, context, portfolio=portfolio, as_of=as_of, view=book)
    window_from = as_of - timedelta(days=WINDOW_DAYS)
    stated = (
        scenarios if scenarios is not None else await scenarios_for(session, portfolio=portfolio)
    )

    if book.net_assets is None or book.net_assets.value <= 0:
        problem = f"No risk figure: {book.problem}" if book.problem else _NO_DENOMINATOR
        return RiskView(
            portfolio=portfolio,
            as_of=as_of,
            window_from=window_from,
            book=book,
            exposure=exposure,
            volatility=None,
            drawdown=None,
            expected_shortfall=None,
            observations=0,
            coverage=None,
            scenarios=tuple(
                ScenarioOutcome(scenario=row, pnl=None, impact=None, reached=(), problem=problem)
                for row in stated
            ),
            problem=problem,
        )

    priced = [
        row
        for row in book.holdings
        if row.problem != CLOSED and row.value is not None and row.weight is not None
    ]
    returns: dict[str, tuple[tuple[date, Quantity], ...]] = {}
    problems: dict[str, str] = {}
    # Keyed by the security's id rather than its ticker: securities are unique on
    # (ticker, exchange), and two listings of one issuer would otherwise overwrite each
    # other's series and weight.
    for row in priced:
        series, problem = await _daily_returns(
            session, row.security, as_of=as_of, since=window_from
        )
        if problem:
            problems[str(row.security.id)] = problem
        else:
            returns[str(row.security.id)] = series

    holdings: list[HoldingRisk] = []
    book_series: tuple[tuple[date, Quantity], ...] = ()
    volatility = drawdown = shortfall = coverage = None
    observations = 0
    problem = ""

    if returns:
        assert book.net_assets is not None
        weights = {
            str(row.security.id): row.weight.quantity
            for row in priced
            if str(row.security.id) in returns and row.weight is not None
        }
        series_source = SourceRef.calculation(
            book.net_assets.record.id,
            label="the book's return series, today's weights held fixed",
        )
        try:
            book_series = calc.static_weight_returns(weights, returns, source=series_source)
            book_values = [value for _, value in book_series]
            observations = len(book_values)
            volatility = graded_figure(
                context,
                calc.annualised_volatility(
                    context,
                    variance=price_calc.variance(context, observations=book_values),
                    periods_per_year=calc.PERIODS_PER_YEAR[FREQUENCY],
                ),
            )
            drawdown = graded_figure(
                context,
                calc.max_drawdown(
                    context, levels=calc.cumulative_index(book_series, source=series_source)
                ),
            )
            measured_values = [
                row.value.quantity
                for row in priced
                if str(row.security.id) in returns and row.value is not None
            ]
            coverage = graded_figure(
                context,
                performance_calc.exposure(
                    context,
                    value=performance_calc.grouped_value(context, values=measured_values),
                    net_assets=book.net_assets.quantity,
                ),
            )
        except CalculationError as refused:
            problem = str(refused)
            book_series = ()
        if book_series:
            try:
                shortfall = graded_figure(
                    context,
                    calc.expected_shortfall(
                        context,
                        observations=[value for _, value in book_series],
                        tail_per_cent=calc.DEFAULT_TAIL_PER_CENT,
                    ),
                )
            except CalculationError as refused:
                problem = str(refused)
    elif priced:
        problem = "No holding has enough price history in the window to measure."

    for row in priced:
        holdings.append(
            _holding_risk(
                context,
                row_weight=row.weight,
                security=row.security,
                series=returns.get(str(row.security.id)),
                book_series=book_series,
                problem=problems.get(str(row.security.id), ""),
            )
        )

    outcomes = tuple(_scenario_outcome(context, book, scenario) for scenario in stated)
    view = RiskView(
        portfolio=portfolio,
        as_of=as_of,
        window_from=window_from,
        book=book,
        exposure=exposure,
        volatility=volatility,
        drawdown=drawdown,
        expected_shortfall=shortfall,
        observations=observations,
        coverage=coverage,
        holdings=tuple(holdings),
        scenarios=outcomes,
        problem=problem,
    )
    _log.info(
        "risk.computed",
        portfolio=str(portfolio.id),
        as_of=as_of.isoformat(),
        measured=len(view.measured),
        holdings=len(view.holdings),
        scenarios=len(outcomes),
        calculations=len(context.records),
    )
    return view


async def _daily_returns(
    session: AsyncSession, security: Security, *, as_of: date, since: date
) -> tuple[tuple[tuple[date, Quantity], ...], str]:
    """One holding's daily total returns over the window, or the reason there are none."""
    try:
        series = await adjusted_series_for(session, security, as_of=as_of, since=since)
        source = SourceRef.security(
            security.id,
            label=f"{security.provider_symbol} daily total returns {since.isoformat()} to "
            f"{as_of.isoformat()}",
        )
        returns = price_calc.simple_returns(series.bars, source=source)
    except CalculationError as refused:
        return (), str(refused)
    if len(returns) < price_calc.MIN_RETURN_OBSERVATIONS:
        return (), (
            f"{len(returns)} daily return(s) in the window, and at least "
            f"{price_calc.MIN_RETURN_OBSERVATIONS} are needed to measure anything. Acquire "
            "more price history for this listing."
        )
    return returns, ""


def _holding_risk(
    context: CalculationContext,
    *,
    row_weight: Figure | None,
    security: Security,
    series: tuple[tuple[date, Quantity], ...] | None,
    book_series: tuple[tuple[date, Quantity], ...],
    problem: str,
) -> HoldingRisk:
    if series is None or row_weight is None:
        return HoldingRisk(
            security=security,
            weight=row_weight,
            volatility=None,
            beta_to_book=None,
            contribution=None,
            observations=0,
            problem=problem or "Unpriced, so unmeasured.",
        )
    own = [value for _, value in series]
    volatility = graded_figure(
        context,
        calc.annualised_volatility(
            context,
            variance=price_calc.variance(context, observations=own),
            periods_per_year=calc.PERIODS_PER_YEAR[FREQUENCY],
        ),
    )
    beta = contribution = None
    if book_series:
        try:
            subject, market = price_calc.aligned_returns(series, book_series)
            to_book = price_calc.beta(
                context,
                subject_market_covariance=price_calc.covariance(
                    context, subject=subject, market=market
                ),
                market_variance=price_calc.variance(context, observations=market),
                frequency=FREQUENCY,
                observations=len(subject),
            )
            beta = graded_figure(context, to_book)
            contribution = graded_figure(
                context,
                calc.risk_contribution(context, weight=row_weight.quantity, beta_to_book=to_book),
            )
        except CalculationError as refused:
            problem = str(refused)
    return HoldingRisk(
        security=security,
        weight=row_weight,
        volatility=volatility,
        beta_to_book=beta,
        contribution=contribution,
        observations=len(own),
        problem=problem,
    )


# -- Scenarios -------------------------------------------------------------------------------


_SHOCK_PLACES: Final = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Shock:
    """One shock as the form states it, before it is a row."""

    kind: ShockKind
    target: str
    shock: Decimal


@dataclass(frozen=True, slots=True)
class _Position:
    """One thing a shock can reach: a priced holding or a cash balance, valued in base."""

    label: str
    value: Quantity
    ticker: str = ""
    sector: str = ""
    currency: str = ""
    country: str = ""
    is_cash: bool = False


def _positions(book: PortfolioView) -> list[_Position]:
    out = [
        _Position(
            label=row.security.ticker,
            value=row.value.quantity,
            ticker=row.security.ticker.upper(),
            sector=(sector_of(row.security) or "").lower(),
            currency=(currency_of(row.security) or "").upper(),
            country=(country_of(row.security) or "").lower(),
        )
        for row in book.holdings
        if row.problem != CLOSED and row.value is not None
    ]
    out.extend(
        _Position(
            label=f"{row.currency} cash",
            value=row.in_base.quantity,
            currency=(major_currency(row.currency) or row.currency).upper(),
            is_cash=True,
        )
        for row in book.cash
        if row.in_base is not None
    )
    return out


def _reaches(shock: RiskScenarioShock, position: _Position, *, base_currency: str) -> bool:
    target = shock.target.strip()
    if shock.kind is ShockKind.BOOK:
        return not position.is_cash
    if shock.kind is ShockKind.HOLDING:
        return not position.is_cash and position.ticker == target.upper()
    if shock.kind is ShockKind.SECTOR:
        return not position.is_cash and position.sector == target.lower()
    if shock.kind is ShockKind.COUNTRY:
        return not position.is_cash and position.country == target.lower()
    # A currency shock is the currency moving against the book's own, so the book's
    # currency never moves against itself; cash in another currency is reached.
    wanted = (major_currency(target) or target).upper()
    return wanted != base_currency.upper() and position.currency == wanted


def _scenario_outcome(
    context: CalculationContext, book: PortfolioView, scenario: RiskScenario
) -> ScenarioOutcome:
    if book.net_assets is None:
        return ScenarioOutcome(
            scenario=scenario, pnl=None, impact=None, reached=(), problem=book.problem
        )
    values: list[Quantity] = []
    shocks: list[Quantity] = []
    reached: list[str] = []
    positions: list[_Position] = []
    for position in _positions(book):
        applying = [
            Quantity.of(
                row.shock,
                DIMENSIONLESS,
                source=SourceRef.scenario_shock(
                    row.id, label=f"{scenario.name}: {row.kind.value} {row.target} {row.shock}"
                ),
            )
            for row in scenario.shocks
            if _reaches(row, position, base_currency=book.portfolio.base_currency)
        ]
        if not applying:
            continue
        values.append(position.value)
        shocks.append(calc.combined_shock(context, shocks=applying))
        reached.append(position.label)
        positions.append(position)
    if not values:
        return ScenarioOutcome(
            scenario=scenario,
            pnl=None,
            impact=None,
            reached=(),
            problem="This scenario reaches nothing the book holds as at this date.",
        )
    try:
        pnl = calc.scenario_pnl(context, values=values, shocks=shocks)
        impact = calc.scenario_impact(context, pnl=pnl, net_assets=book.net_assets.quantity)
        shocked = tuple(
            ShockedPosition(
                label=position.label,
                value=graded_figure(context, position.value),
                shock=shock.value,
                pnl=graded_figure(
                    context, calc.position_pnl(context, value=position.value, shock=shock)
                ),
            )
            for position, shock in zip(positions, shocks, strict=True)
        )
    except CalculationError as refused:
        return ScenarioOutcome(
            scenario=scenario, pnl=None, impact=None, reached=tuple(reached), problem=str(refused)
        )
    return ScenarioOutcome(
        scenario=scenario,
        pnl=graded_figure(context, pnl),
        impact=graded_figure(context, impact),
        reached=tuple(reached),
        positions=shocked,
    )


async def scenarios_for(
    session: AsyncSession, *, portfolio: Portfolio, include_withdrawn: bool = False
) -> list[RiskScenario]:
    statement = (
        select(RiskScenario)
        .options(selectinload(RiskScenario.shocks))
        .where(RiskScenario.portfolio_id == portfolio.id)
        .order_by(RiskScenario.created_at)
    )
    if not include_withdrawn:
        statement = statement.where(RiskScenario.withdrawn_at.is_(None))
    return list(await session.scalars(statement))


async def state_scenario(
    session: AsyncSession,
    *,
    actor: User,
    portfolio: Portfolio,
    name: str,
    shocks: list[Shock],
) -> RiskScenario:
    """Write down what the operator said a scenario moves, and by how much.

    Raises:
        ValidationError: If the name is blank, there is no shock, a shock is nil or a
            total loss, or a targeted shock names no target.
        ConflictError: If the book is not this person's.
    """
    if portfolio.user_id != actor.id:
        message = "A scenario is stated by the person whose book it is about."
        raise ConflictError(message, context={"portfolio_id": str(portfolio.id)})
    if not name.strip():
        message = "A scenario needs a name: what it is a scenario of."
        raise ValidationError(message, context={"field": "name"})
    if not shocks:
        message = "A scenario is at least one shock; this one moves nothing."
        raise ValidationError(message, context={"field": "shocks"})
    for shock in shocks:
        if not shock.shock.is_finite():
            message = f"A shock of {shock.shock} is not a number. State a fraction."
            raise ValidationError(message, context={"field": "shocks"})
    # The column holds six places: a shock finer than that would round to nothing on the
    # way in and fail the row's own check as a database error, so it is settled here.
    shocks = [
        Shock(kind=shock.kind, target=shock.target, shock=shock.shock.quantize(_SHOCK_PLACES))
        for shock in shocks
    ]
    for shock in shocks:
        if shock.shock <= -1 or shock.shock == 0:
            message = (
                f"A shock of {shock.shock} moves nothing or takes a position to nil or below. "
                "State a fraction between -1 and 0, or above 0."
            )
            raise ValidationError(message, context={"field": "shock"})
        if shock.kind is not ShockKind.BOOK and not shock.target.strip():
            message = f"A shock to {shock.kind.value} needs a target to reach."
            raise ValidationError(message, context={"field": "target"})

    scenario = RiskScenario(portfolio_id=portfolio.id, name=name.strip(), stated_by=actor.email)
    session.add(scenario)
    await session.flush()
    for position, shock in enumerate(shocks, start=1):
        session.add(
            RiskScenarioShock(
                scenario_id=scenario.id,
                position=position,
                kind=shock.kind,
                target="" if shock.kind is ShockKind.BOOK else shock.target.strip(),
                shock=shock.shock,
            )
        )
    await session.flush()
    await session.refresh(scenario, attribute_names=["shocks"])
    await _record(
        session,
        actor=actor.email,
        event_type="risk.scenario_stated",
        scenario_id=scenario.id,
        payload={
            "scenario_id": str(scenario.id),
            "portfolio_id": str(portfolio.id),
            "name": scenario.name,
            "shocks": [
                {"kind": row.kind.value, "target": row.target, "shock": str(row.shock)}
                for row in scenario.shocks
            ],
        },
    )
    _log.info("risk.scenario_stated", scenario_id=str(scenario.id), shocks=len(shocks))
    return scenario


async def withdraw_scenario(
    session: AsyncSession, *, actor: User, scenario: RiskScenario
) -> RiskScenario:
    """Stop watching a scenario. The row stays; what was once watched is still a record."""
    if scenario.is_withdrawn:
        message = "This scenario was already withdrawn."
        raise ConflictError(message, context={"scenario_id": str(scenario.id)})
    scenario.withdrawn_at = datetime.now(UTC)
    await session.flush()
    await _record(
        session,
        actor=actor.email,
        event_type="risk.scenario_withdrawn",
        scenario_id=scenario.id,
        payload={"scenario_id": str(scenario.id), "name": scenario.name},
    )
    return scenario


# -- The reading -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reading:
    """One analyst pass as the page reads it back off its step."""

    job: Job
    output: dict[str, Any]

    @property
    def as_of(self) -> date | None:
        raw = self.output.get("as_of")
        return date.fromisoformat(str(raw)) if raw else None

    @property
    def commentary(self) -> RiskCommentary | None:
        raw = self.output.get("commentary")
        return RiskCommentary.model_validate(raw) if isinstance(raw, dict) else None

    @property
    def refusals(self) -> list[str]:
        raw = self.output.get("refused") or []
        return [str(item) for item in raw] if isinstance(raw, list) else []

    @property
    def nothing_to_read(self) -> str:
        return str(self.output.get("nothing_to_read") or "")

    @property
    def failed(self) -> bool:
        return self.job.status is JobStatus.FAILED

    @property
    def reason(self) -> str:
        return str((self.job.error or {}).get("message") or "")


async def latest_reading(session: AsyncSession, *, portfolio: Portfolio) -> Reading | None:
    job = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .options(selectinload(Job.steps))
        .where(
            WorkOrder.tool == TOOL,
            WorkOrder.subject_kind == SUBJECT_BOOK,
            WorkOrder.subject_id == portfolio.id,
            WorkOrder.user_id == portfolio.user_id,
        )
        .order_by(Job.started_at.desc().nullslast())
        .limit(1)
    )
    return _reading(job) if job is not None else None


async def reading_of(
    session: AsyncSession, job_id: uuid.UUID, *, user_id: uuid.UUID
) -> Reading | None:
    job = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .options(selectinload(Job.steps))
        .where(Job.id == job_id, WorkOrder.tool == TOOL, WorkOrder.user_id == user_id)
    )
    return _reading(job) if job is not None else None


def _reading(job: Job) -> Reading:
    step = next((row for row in job.steps if row.step_key == STEP_KEY), None)
    output = step.output_ref if step is not None and isinstance(step.output_ref, dict) else {}
    return Reading(job=job, output=output)


async def last_trade_recorded_at(session: AsyncSession, *, portfolio: Portfolio) -> datetime | None:
    """When the book last changed, which is when a reading of it went stale."""
    found: datetime | None = await session.scalar(
        select(Attestation.recorded_at)
        .join(Transaction, Transaction.attestation_id == Attestation.id)
        .where(Transaction.portfolio_id == portfolio.id)
        .order_by(Attestation.recorded_at.desc())
        .limit(1)
    )
    return found


async def run_reading(
    session: AsyncSession,
    *,
    settings: Settings,
    provider: LLMProvider,
    router: Router,
    store: ArtefactStore,
    user: User,
    portfolio: Portfolio,
    as_of: date,
) -> Job:
    """One analyst pass over the book as at a date, leaving its reading on the step.

    The figures are computed and their ledger persisted against this job before the model
    is asked anything. A commentary that fails the deterministic edge is retried once with
    the problems, then recorded as refused. A cost refusal fails the job with the reason.

    Raises:
        ConflictError: If the book is not this person's.
    """
    if portfolio.user_id != user.id:
        message = "A book is read by the person whose book it is."
        raise ConflictError(message, context={"portfolio_id": str(portfolio.id)})

    order, job, step = await _open_pass(
        session, settings=settings, user=user, portfolio=portfolio, as_of=as_of
    )
    ledger = new_context()
    view = await risk_as_at(session, ledger, portfolio=portfolio, as_of=as_of)
    if ledger.records:
        await persist_context(session, ledger, job_id=job.id)
    payload = block_of(view)
    output: dict[str, Any] = {
        "portfolio_id": str(portfolio.id),
        "as_of": as_of.isoformat(),
        "block": payload.model_dump(mode="json"),
        "commentary": None,
        "refused": [],
        "attempts": 0,
        "nothing_to_read": _nothing_to_read(view),
    }
    if output["nothing_to_read"]:
        _close_pass(order, job, step, output=output, spend=Decimal(0))
        await session.flush()
        _log.info("risk.nothing_to_read", job_id=str(job.id))
        return job

    context = AgentContext(
        session=session,
        provider=provider,
        router=router,
        settings=settings,
        store=store,
        job_step=step,
    )
    problems: list[str] = []
    try:
        for attempt in range(1, _RETRIES + 2):
            output["attempts"] = attempt
            asked = payload.model_copy(update={"problems": problems})
            commentary = await RiskAnalystAgent().run(context, asked)
            problems = commentary_problems(commentary, payload)
            if not problems:
                output["commentary"] = commentary.model_dump(mode="json")
                break
            output["refused"] = problems
            output["last_draft"] = commentary.model_dump(mode="json")
            _log.warning(
                "risk.commentary_refused",
                job_id=str(job.id),
                attempt=attempt,
                problems=len(problems),
            )
    except AerError as refused:
        # The budget refusal, and everything else a call can fail on — a provider outage,
        # a reply billed and then refused. Failing the pass here, on the session the page
        # commits, is what keeps the cost row of the failed call; letting it propagate to
        # a rollback would lose it, and the caps would never see it.
        step.status = JobStatus.FAILED
        step.finished_at = datetime.now(UTC)
        step.error = {"code": refused.code, "message": refused.message, **refused.context}
        step.cost_gbp = context.spend_gbp
        step.output_ref = output
        job.status = JobStatus.FAILED
        job.finished_at = step.finished_at
        job.error = {"code": refused.code, "message": refused.message, "context": refused.context}
        order.status = RequestStatus.FAILED
        await session.flush()
        _log.warning("risk.stopped", job_id=str(job.id), scope=refused.context.get("scope"))
        return job

    _close_pass(order, job, step, output=output, spend=context.spend_gbp)
    await session.flush()
    _log.info(
        "risk.read",
        job_id=str(job.id),
        portfolio=str(portfolio.id),
        as_of=as_of.isoformat(),
        shown=output["commentary"] is not None,
        spend_gbp=str(context.spend_gbp),
    )
    return job


async def _open_pass(
    session: AsyncSession,
    *,
    settings: Settings,
    user: User,
    portfolio: Portfolio,
    as_of: date,
) -> tuple[WorkOrder, Job, JobStep]:
    started = datetime.now(UTC)
    order = WorkOrder(
        user_id=user.id,
        tool=TOOL,
        subject_kind=SUBJECT_BOOK,
        subject_id=portfolio.id,
        as_of_date=as_of,
        point_in_time=False,
        max_cost_gbp=settings.per_run_budget_gbp,
        status=RequestStatus.RUNNING,
    )
    session.add(order)
    await session.flush()
    job = Job(
        work_order_id=order.id,
        workflow_version=WORKFLOW_VERSION,
        code_version=git_sha() or "unknown",
        status=JobStatus.RUNNING,
        started_at=started,
    )
    session.add(job)
    await session.flush()
    step = JobStep(
        job_id=job.id,
        step_key=STEP_KEY,
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:{STEP_KEY}",
        input_hash=sha256_hex(
            canonical_json({"portfolio": str(portfolio.id), "as_of": as_of.isoformat()})
        ),
        started_at=started,
    )
    session.add(step)
    await session.flush()
    return order, job, step


def _close_pass(
    order: WorkOrder, job: Job, step: JobStep, *, output: dict[str, Any], spend: Decimal
) -> None:
    step.status = JobStatus.SUCCEEDED
    step.finished_at = datetime.now(UTC)
    step.cost_gbp = spend
    step.output_ref = output
    job.status = JobStatus.SUCCEEDED
    job.finished_at = step.finished_at
    job.total_cost_gbp = spend
    order.status = RequestStatus.COMPLETED


def _nothing_to_read(view: RiskView) -> str:
    """Why the block is the whole truthful content, or empty for the ordinary path.

    The reason ADR 0080 gives for gap A51c: a commentary's only job is to interpret
    recorded figures, and a block with none is not improved by a paid sentence saying so.
    """
    if not view.exposure.bands and not view.is_measured:
        return view.problem or "The book holds nothing priced, so there is nothing to read."
    return ""


# -- The block -------------------------------------------------------------------------------


def percent(value: Decimal) -> str:
    """A fraction as a signed percentage with one decimal place: 0.2 reads as +20.0%."""
    return f"{(value * 100).quantize(Decimal('0.1')):+.1f}%"


def money(value: Decimal, currency: str) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f} {currency}"


def block_of(view: RiskView) -> RiskInput:
    """The figures as strings — what the analyst reads and what the page shows, one text."""
    currency = view.portfolio.base_currency
    exposure: list[FigureLine] = []
    if view.exposure.top_holdings is not None:
        exposure.append(
            FigureLine(
                label="Largest five holdings",
                value=percent(view.exposure.top_holdings.value).lstrip("+"),
                note="of the book",
            )
        )
    for band in view.exposure.bands:
        for row in band.slices[:5]:
            exposure.append(
                FigureLine(
                    label=f"{band.title}: {row.label}",
                    value=percent(row.share.value).lstrip("+"),
                    note=", ".join(row.members) if band.kind != "holding" else "",
                )
            )
        if band.unknown is not None:
            exposure.append(
                FigureLine(
                    label=f"{band.title}: {band.unknown.label}",
                    value=percent(band.unknown.share.value).lstrip("+"),
                    note=", ".join(band.unknown.members),
                )
            )
    book: list[FigureLine] = []
    if view.volatility is not None:
        book.append(
            FigureLine(
                label="Annualised volatility",
                value=percent(view.volatility.value).lstrip("+"),
                note=f"over {view.observations} daily returns",
            )
        )
    if view.drawdown is not None:
        book.append(FigureLine(label="Maximum drawdown", value=percent(view.drawdown.value)))
    if view.expected_shortfall is not None:
        book.append(
            FigureLine(
                label=f"Expected shortfall, worst {calc.DEFAULT_TAIL_PER_CENT}% of days",
                value=percent(view.expected_shortfall.value),
            )
        )
    if view.coverage is not None:
        book.append(
            FigureLine(
                label="Coverage",
                value=percent(view.coverage.value).lstrip("+"),
                note="of net assets is in measured holdings; the rest is cash or unmeasured",
            )
        )
    return RiskInput(
        book_name=view.portfolio.name,
        currency=currency,
        as_of=view.as_of.isoformat(),
        window=f"{view.window_from.isoformat()} to {view.as_of.isoformat()}, daily",
        coverage=(
            f"{percent(view.coverage.value).lstrip('+')} of net assets measured"
            if view.coverage is not None
            else (view.problem or "nothing measured")
        ),
        exposure=exposure,
        book=book,
        holdings=[
            HoldingLine(
                ticker=row.security.ticker,
                weight=percent(row.weight.value).lstrip("+") if row.weight is not None else "",
                volatility=(
                    percent(row.volatility.value).lstrip("+") if row.volatility is not None else ""
                ),
                beta_to_book=(
                    f"{row.beta_to_book.value.quantize(Decimal('0.01'))}"
                    if row.beta_to_book is not None
                    else ""
                ),
                contribution=(
                    percent(row.contribution.value).lstrip("+")
                    if row.contribution is not None
                    else ""
                ),
                problem=row.problem,
            )
            for row in view.holdings
        ],
        scenarios=[
            ScenarioLine(
                name=row.scenario.name,
                shocks="; ".join(
                    f"{shock.kind.value} {shock.target} {percent(shock.shock)}".replace("  ", " ")
                    for shock in row.scenario.shocks
                ),
                pnl=money(row.pnl.value, currency) if row.pnl is not None else "",
                impact=percent(row.impact.value) if row.impact is not None else "",
                problem=row.problem,
            )
            for row in view.scenarios
        ],
    )


# -- The chain -------------------------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    scenario_id: uuid.UUID,
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            job_id=None,
            subject_kind="risk_scenario",
            subject_id=scenario_id,
        )
    )
    await session.flush()
