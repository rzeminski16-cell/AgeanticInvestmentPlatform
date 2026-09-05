"""The thesis monitor: what has happened since a thesis was written that bears on it.

Roadmap §3.6, under ADRs 0078, 0079 and 0103. One pass reads one thesis: for every premise
that carries a predicate, code resolves the metric it names, measures it from the fiscal
years filed since the premise was last read, decides whether the predicate holds, and only
then asks the model what the new facts do to the premise — bounded by the crossing code
already made. A premise nothing new bears on is not read; a premise with no predicate is not
read either, because a person reviews it by a date instead.

**Findings, not decisions.** A reading lands in ``findings``, never in ``approvals``. The one
status with a consequence — `contradicted` — opens the thesis gate, and even that is written
here rather than through :func:`aer.services.approvals.record_decision`, because that
function enforces a research run's gate order and once-per-gate-per-job, and neither applies
to a gate a pass may open several times.

**A finding is closed by an act with a reason, never by the condition lifting.** The acts
are appended rows; every one of them, and every finding, goes on the audit chain with the
thesis as its subject.

**One transaction per premise, committed as each one finishes**, for the reason
:mod:`aer.worker` gives: the base agent meters spend into the same session it was called
with, and a pass that died after three model calls with nothing committed would have spent
money the cap cannot see. A commit is where the recorded state is whole.

**Nothing here reads a price** (ADR 0079). The evidence is ``financial_facts`` and only
``financial_facts``; the observation has no field a mark could occupy.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.agents.base import AgentContext
from aer.agents.thesis_monitor import (
    MAX_FACTS,
    Observation,
    PremiseInput,
    PremiseReading,
    ThesisMonitorAgent,
    WindowFact,
)
from aer.calc.basic import growth_rate
from aer.calc.engine import CalculationContext
from aer.calc.ratios import RATIO_DEFINITIONS
from aer.calc.statements import (
    BALANCE_SHEET_LINES,
    CASH_FLOW_LINES,
    INCOME_STATEMENT_LINES,
    SUPPLEMENTARY_LINES,
)
from aer.calc.units import CalculationError, Quantity, Unit, UnitMismatchError
from aer.config import Settings
from aer.core.enums import (
    Decision,
    FindingAction,
    FindingKind,
    GateKind,
    JobStatus,
    PremiseComparator,
    PremiseStatus,
    RequestStatus,
)
from aer.core.figures import plain_decimal
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Approval,
    AuditEvent,
    Company,
    Finding,
    FindingResolution,
    Job,
    JobStep,
    Premise,
    Thesis,
    User,
    WorkOrder,
)
from aer.errors import BudgetExceededError, ConflictError, ValidationError
from aer.providers.protocol import LLMProvider
from aer.providers.router import Router
from aer.services import theses as thesis_service
from aer.services.analysis import AnalysisOutcome, PeriodAnalysis, analyse_company, annual_facts
from aer.services.approvals import payload_hash_for
from aer.services.calculations import new_context, persist_context
from aer.services.theses import COMPARATOR_WORDS, SUBJECT_COMPANY
from aer.storage.protocol import ArtefactStore
from aer.version import git_sha

__all__ = [
    "GROWTH_SUFFIX",
    "SUBJECT_THESIS",
    "TOOL",
    "WORKFLOW_VERSION",
    "Measurement",
    "MonitorOutcome",
    "PassRow",
    "ResolvedMetric",
    "decide_finding",
    "finding_of",
    "finding_payload",
    "findings_for",
    "measurable_metrics",
    "predicate_holds",
    "predicate_sentence",
    "recent_passes",
    "resolve_finding",
    "resolve_metric",
    "reviews_due",
    "run_monitor",
    "theses_to_monitor",
    "threshold_quantity",
]

_log = structlog.get_logger("aer.services.thesis_monitor")

TOOL: Final = "monitor"
SUBJECT_THESIS: Final = "thesis"
WORKFLOW_VERSION: Final = "thesis_monitor_v1"

# A metric name ending in this is the year-on-year growth of the concept before it.
GROWTH_SUFFIX: Final = "_growth"

# The threshold units that mean "a fraction written a hundred times larger" (ADR 0027) and
# the ones that mean "dimensionless as written". Anything else is parsed as a unit.
_PERCENT_UNITS: Final[frozenset[str]] = frozenset({"percent", "per cent", "pct", "%", "percentage"})
_DIMENSIONLESS_UNITS: Final[frozenset[str]] = frozenset({"ratio", "pure", "x", "times", ""})
_HUNDRED: Final = Decimal(100)

# Two fiscal years: the one being read, and the one a growth rate needs behind it.
_PERIODS_TO_READ: Final = 2

# Every canonical concept a statement carries, which is every level and every growth the
# resolver can name.
_CONCEPTS: Final[tuple[str, ...]] = (
    *INCOME_STATEMENT_LINES,
    *BALANCE_SHEET_LINES,
    *CASH_FLOW_LINES,
    *SUPPLEMENTARY_LINES,
)
_RATIO_KEYS: Final[dict[str, str]] = {row.key: row.label for row in RATIO_DEFINITIONS}


# -- Resolving what a premise names --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedMetric:
    """What a metric name turned out to mean: a growth, a ratio or a level."""

    kind: str
    """``growth``, ``ratio`` or ``level``."""
    key: str
    """The concept (growth, level) or the ratio key."""
    label: str


def slug_of(metric: str) -> str:
    """``"Revenue growth"`` → ``revenue_growth``. The operator's words, in the code's shape."""
    return re.sub(r"[^a-z0-9]+", "_", metric.strip().lower()).strip("_")


def resolve_metric(metric: str) -> ResolvedMetric | None:
    """The measurement a metric name asks for, or ``None`` when the platform has none.

    In order: the growth of a concept, a ratio the suite computes, a concept's own level.
    The order matters only for a name that could be two things, and none is today.
    """
    slug = slug_of(metric)
    if slug.endswith(GROWTH_SUFFIX):
        concept = slug[: -len(GROWTH_SUFFIX)]
        if concept in _CONCEPTS:
            return ResolvedMetric("growth", concept, f"{concept.replace('_', ' ')} growth")
    if slug in _RATIO_KEYS:
        return ResolvedMetric("ratio", slug, _RATIO_KEYS[slug])
    if slug in _CONCEPTS:
        return ResolvedMetric("level", slug, slug.replace("_", " "))
    return None


def measurable_metrics() -> tuple[str, ...]:
    """Every name the resolver understands, for the form's help and the unobservable finding."""
    ratios = tuple(_RATIO_KEYS)
    growths = tuple(f"{concept}{GROWTH_SUFFIX}" for concept in _CONCEPTS)
    return (*ratios, *growths, *_CONCEPTS)


def threshold_quantity(threshold: Decimal, unit: str) -> Quantity:
    """The threshold as a quantity the observed figure can be compared with.

    Per cent is a convention, not a unit (ADR 0027): a threshold "25 percent" is the
    fraction 0.25, divided once here, so that it meets a dimensionless ratio on equal
    terms. Everything else is parsed, and a unit the platform does not know raises rather
    than being guessed at.

    Raises:
        UnitMismatchError: If the unit is not one the platform can parse.
    """
    cleaned = unit.strip().lower()
    if cleaned in _PERCENT_UNITS:
        return Quantity.of(threshold / _HUNDRED)
    if cleaned in _DIMENSIONLESS_UNITS:
        return Quantity.of(threshold)
    return Quantity.of(threshold, Unit.parse(unit.strip()))


def predicate_holds(observed: Quantity, comparator: PremiseComparator, threshold: Quantity) -> bool:
    """Whether the premise's predicate holds on this observation.

    Through :class:`Quantity`'s own comparisons, which refuse a unit mismatch rather than
    coercing one — the whole reason a threshold carries a unit.

    Raises:
        UnitMismatchError: If the two are not in the same unit.
    """
    if comparator is PremiseComparator.AT_LEAST:
        return observed >= threshold
    if comparator is PremiseComparator.AT_MOST:
        return observed <= threshold
    if comparator is PremiseComparator.ABOVE:
        return observed > threshold
    return observed < threshold


def predicate_sentence(premise: Premise) -> str:
    """The predicate as the operator wrote it — "revenue growth at least 25 percent"."""
    if premise.comparator is None:
        return ""
    threshold = premise.threshold.normalize() if premise.threshold is not None else ""
    return f"{premise.metric} {COMPARATOR_WORDS[premise.comparator]} {threshold:f} {premise.unit}"


# -- Measuring ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Measurement:
    """What code measured for one premise, verdict included."""

    resolved: ResolvedMetric
    quantity: Quantity
    period_end: date
    threshold: Quantity
    holds: bool
    prior: Quantity | None = None
    prior_period_end: date | None = None
    calculation_id: uuid.UUID | None = None
    fact_id: uuid.UUID | None = None

    def as_observation(self, premise: Premise) -> Observation:
        assert premise.comparator is not None
        return Observation(
            metric=self.resolved.label,
            value=plain_decimal(self.quantity.value),
            unit=_unit_word(self.quantity),
            period_end=self.period_end.isoformat(),
            prior_value=plain_decimal(self.prior.value) if self.prior is not None else "",
            prior_period_end=self.prior_period_end.isoformat() if self.prior_period_end else "",
            threshold=plain_decimal(self.threshold.value),
            comparator=COMPARATOR_WORDS[premise.comparator],
            holds=self.holds,
        )

    def as_json(self, premise: Premise) -> dict[str, Any]:
        observation = self.as_observation(premise).model_dump(mode="json")
        observation["calculation_id"] = str(self.calculation_id) if self.calculation_id else None
        observation["fact_id"] = str(self.fact_id) if self.fact_id else None
        observation["threshold_unit"] = _unit_word(self.threshold)
        return observation


def _unit_word(quantity: Quantity) -> str:
    """The unit as a reader would name it. A dimensionless figure is a ratio, not "pure"."""
    return "ratio" if quantity.unit.is_dimensionless else quantity.unit.symbol


class _UnobservableError(Exception):
    """Code could not measure what the premise names, and this is why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _measure(
    context: CalculationContext,
    analysis: AnalysisOutcome,
    *,
    premise: Premise,
) -> Measurement:
    """Measure the premise's metric on the latest period, or say why it cannot be.

    Raises:
        _UnobservableError: With the reason in words, for the finding to carry.
    """
    assert premise.metric is not None
    assert premise.comparator is not None
    assert premise.threshold is not None
    assert premise.unit is not None

    resolved = resolve_metric(premise.metric)
    if resolved is None:
        known = ", ".join(_RATIO_KEYS)
        message = (
            f"No filing this platform reads measures {premise.metric!r}. It can measure a "
            f"ratio ({known}), the growth of a statement line (revenue_growth, "
            "net_income_growth, …) or a line's level (revenue, operating_income, …); a "
            "premise about anything else is one a person reviews."
        )
        raise _UnobservableError(message)

    latest = analysis.latest
    if latest is None:
        message = "No annual facts are stored for this company, so nothing could be measured."
        raise _UnobservableError(message)
    prior = analysis.periods[1] if len(analysis.periods) > 1 else None

    try:
        threshold = threshold_quantity(premise.threshold, premise.unit)
    except (UnitMismatchError, CalculationError) as refused:
        message = (
            f"The threshold's unit {premise.unit!r} is not one the platform can compare "
            f"with a stored fact: {refused.message}"
        )
        raise _UnobservableError(message) from refused

    quantity, prior_quantity, calculation_id, fact_id = _observe(
        context, resolved, latest=latest, prior=prior
    )
    try:
        holds = predicate_holds(quantity, premise.comparator, threshold)
    except UnitMismatchError as mismatch:
        message = (
            f"{resolved.label} is measured in {quantity.unit.symbol or 'ratio'} and the "
            f"threshold is in {threshold.unit.symbol or 'ratio'}; the two cannot be "
            f"compared, and the platform refuses to guess: {mismatch.message}"
        )
        raise _UnobservableError(message) from mismatch

    return Measurement(
        resolved=resolved,
        quantity=quantity,
        period_end=latest.period_end,
        threshold=threshold,
        holds=holds,
        prior=prior_quantity,
        prior_period_end=prior.period_end if prior is not None else None,
        calculation_id=calculation_id,
        fact_id=fact_id,
    )


def _observe(
    context: CalculationContext,
    resolved: ResolvedMetric,
    *,
    latest: PeriodAnalysis,
    prior: PeriodAnalysis | None,
) -> tuple[Quantity, Quantity | None, uuid.UUID | None, uuid.UUID | None]:
    """The metric on the latest period, its prior where held, and the row it came from."""
    if resolved.kind == "ratio":
        found = next((row for row in latest.ratios if row.key == resolved.key), None)
        if found is None or found.quantity is None:
            reason = found.absent_because if found is not None else "the ratio is not computed"
            message = f"{resolved.label} could not be computed for {latest.period_end}: {reason}"
            raise _UnobservableError(message)
        prior_quantity = None
        if prior is not None:
            prior_row = next((row for row in prior.ratios if row.key == resolved.key), None)
            prior_quantity = prior_row.quantity if prior_row is not None else None
        return found.quantity, prior_quantity, _record_id(context, resolved.key), None

    end = latest.statements.get(resolved.key)
    if end is None:
        message = (
            f"The {latest.period_end} filing carries no {resolved.key.replace('_', ' ')} line, "
            f"so {resolved.label} could not be measured."
        )
        raise _UnobservableError(message)
    start = prior.statements.get(resolved.key) if prior is not None else None

    if resolved.kind == "level":
        fact_id = _fact_id_of(end)
        return end, start, None, fact_id

    if start is None:
        message = (
            f"{resolved.label} needs two fiscal years and the store holds one for this line, "
            "so it could not be measured."
        )
        raise _UnobservableError(message)
    with context.period(
        f"FY{latest.fiscal_year}" if latest.fiscal_year else latest.period_end.isoformat(),
        end=latest.period_end,
    ):
        try:
            grown = growth_rate(context, start=start, end=end)
        except (UnitMismatchError, CalculationError) as refused:
            message = f"{resolved.label} could not be computed: {refused.message}"
            raise _UnobservableError(message) from refused
    prior_growth = None
    return grown, prior_growth, _record_id(context, "growth_rate"), None


def _record_id(context: CalculationContext, name: str) -> uuid.UUID | None:
    records = context.named(name)
    return records[-1].id if records else None


def _fact_id_of(quantity: Quantity) -> uuid.UUID | None:
    source = quantity.source
    if source is None:
        return None
    try:
        return uuid.UUID(str(source.identifier))
    except (AttributeError, ValueError):
        return None


# -- The pass ----------------------------------------------------------------------------------


@dataclass(slots=True)
class MonitorOutcome:
    """What one pass over one thesis did."""

    job: Job
    findings: list[Finding] = field(default_factory=list)
    read: int = 0
    nothing_new: int = 0
    unobservable: int = 0
    reviewed_by_a_person: int = 0
    stopped: bool = False
    note: str = ""

    @property
    def spend_gbp(self) -> Decimal:
        return Decimal(str(self.job.total_cost_gbp))


async def theses_to_monitor(session: AsyncSession, *, user_id: uuid.UUID) -> list[Thesis]:
    """Every open thesis of this person with a premise the monitor can read.

    The retired are records, and a thesis whose premises are all withdrawn or all without a
    predicate would get a work order and an empty pass for nothing; "run the monitor over
    N theses" counts the passes that can read something.
    """
    theses = await thesis_service.theses_for(session, user_id=user_id, retired=False)
    return [
        thesis
        for thesis in theses
        if any(
            premise.has_predicate and not premise.judgement.is_withdrawn
            for premise in thesis.premises
        )
    ]


async def run_monitor(
    session: AsyncSession,
    *,
    settings: Settings,
    provider: LLMProvider,
    router: Router,
    store: ArtefactStore,
    user: User,
    thesis: Thesis,
    now: datetime | None = None,
) -> MonitorOutcome:
    """Read every predicated premise of one thesis against what has been filed since.

    A work order with the thesis as its subject, no mandate, today's clock and the store
    read whole; one job; one step per premise read. Commits after each premise (see the
    module docstring). A cost refusal stops the pass with a `stopped` finding and a FAILED
    job — never a pause (ADR 0078).

    Raises:
        ConflictError: If the thesis is retired, or is not this person's.
    """
    if thesis.is_retired:
        message = f"The thesis {thesis.title!r} is retired; a retired thesis is not monitored."
        raise ConflictError(message, context={"thesis_id": str(thesis.id)})
    if thesis.user_id != user.id:
        message = "A thesis is monitored by the person who holds it."
        raise ConflictError(message, context={"thesis_id": str(thesis.id)})

    started = now or datetime.now(UTC)
    order = WorkOrder(
        user_id=user.id,
        tool=TOOL,
        subject_kind=SUBJECT_THESIS,
        subject_id=thesis.id,
        as_of_date=started.date(),
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
    outcome = MonitorOutcome(job=job)

    company = await _company_of(session, thesis)
    if company is None:
        outcome.note = "The company this thesis is about is no longer on record."
        await _finish(session, job=job, order=order, status=JobStatus.SUCCEEDED, at=started)
        await session.commit()
        return outcome

    for sequence, premise in enumerate(thesis.premises):
        if premise.judgement.is_withdrawn:
            continue
        if not premise.has_predicate:
            outcome.reviewed_by_a_person += 1
            continue
        context = AgentContext(
            session=session,
            provider=provider,
            router=router,
            settings=settings,
            store=store,
            job_step=await _step(session, job=job, premise=premise, sequence=sequence),
        )
        try:
            stopped = await _read_one(
                context,
                outcome=outcome,
                order=order,
                thesis=thesis,
                company=company,
                premise=premise,
            )
        except Exception as failure:
            # Anything but the budget refusal `_read_one` handles: a provider outage, a
            # reply the schema refused after it was paid for, a broken record. The pass
            # commits after every premise, so leaving here without a verdict would strand
            # a RUNNING job nothing will finish and lose the metered spend of the call
            # that failed. Fail the step and the pass on the record, then let it propagate.
            await _fail_pass(session, job=job, order=order, step=context.job_step, failure=failure)
            context.job_step.cost_gbp = context.spend_gbp
            await session.commit()
            raise
        await session.commit()
        if stopped:
            return outcome

    await _finish(session, job=job, order=order, status=JobStatus.SUCCEEDED, at=datetime.now(UTC))
    await session.commit()
    _log.info(
        "monitor.pass_finished",
        thesis_id=str(thesis.id),
        job_id=str(job.id),
        read=outcome.read,
        nothing_new=outcome.nothing_new,
        unobservable=outcome.unobservable,
        spend_gbp=str(outcome.spend_gbp),
    )
    return outcome


async def _read_one(
    context: AgentContext,
    *,
    outcome: MonitorOutcome,
    order: WorkOrder,
    thesis: Thesis,
    company: Company,
    premise: Premise,
) -> bool:
    """Read one premise and book-keep its step. True if the pass must stop.

    The step is marked as the reading left it — succeeded with what it produced, or failed
    with the refusal — before the caller commits, so a pass that dies between premises
    leaves each step's record whole.
    """
    session = context.session
    step = context.job_step
    job = outcome.job
    try:
        finding = await _read_premise(
            context, order=order, thesis=thesis, company=company, premise=premise
        )
    except BudgetExceededError as refused:
        stopped = await _stop(
            session, job=job, order=order, thesis=thesis, premise=premise, refused=refused
        )
        step.status = JobStatus.FAILED
        step.finished_at = datetime.now(UTC)
        step.error = {"code": refused.code, "message": refused.message, **refused.context}
        step.cost_gbp = context.spend_gbp
        outcome.findings.append(stopped)
        outcome.stopped = True
        return True

    step.status = JobStatus.SUCCEEDED
    step.finished_at = datetime.now(UTC)
    step.cost_gbp = context.spend_gbp
    job.total_cost_gbp = Decimal(str(job.total_cost_gbp)) + context.spend_gbp
    if finding is None:
        outcome.nothing_new += 1
        step.output_ref = {"nothing_new": True}
        return False
    outcome.findings.append(finding)
    step.output_ref = {"finding_id": str(finding.id), "status": finding.status}
    if finding.status is PremiseStatus.UNOBSERVABLE:
        outcome.unobservable += 1
    else:
        outcome.read += 1
    return False


async def _company_of(session: AsyncSession, thesis: Thesis) -> Company | None:
    if thesis.subject_kind != SUBJECT_COMPANY:  # pragma: no cover -- one kind exists today
        return None
    found: Company | None = await session.get(Company, thesis.subject_id)
    return found


async def _step(session: AsyncSession, *, job: Job, premise: Premise, sequence: int) -> JobStep:
    step = JobStep(
        job_id=job.id,
        step_key=f"premise.{premise.position}",
        sequence=sequence,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:premise.{premise.judgement_id}",
        input_hash=sha256_hex(
            canonical_json({"judgement_id": str(premise.judgement_id), "job_id": str(job.id)})
        ),
        started_at=datetime.now(UTC),
    )
    session.add(step)
    await session.flush()
    return step


async def _fail_pass(
    session: AsyncSession, *, job: Job, order: WorkOrder, step: JobStep, failure: Exception
) -> None:
    """The pass failed on something that was not the budget: say so where a reader looks."""
    at = datetime.now(UTC)
    error = {
        "code": getattr(failure, "code", "monitor_failed"),
        "message": str(failure) or type(failure).__name__,
    }
    step.status = JobStatus.FAILED
    step.finished_at = at
    step.error = error
    job.error = error
    await _finish(session, job=job, order=order, status=JobStatus.FAILED, at=at)
    _log.warning("monitor.pass_failed", job_id=str(job.id), error=error["message"])


async def _finish(
    session: AsyncSession, *, job: Job, order: WorkOrder, status: JobStatus, at: datetime
) -> None:
    job.status = status
    job.finished_at = at
    order.status = (
        RequestStatus.COMPLETED if status is JobStatus.SUCCEEDED else RequestStatus.FAILED
    )
    await session.flush()


async def _read_premise(
    context: AgentContext,
    *,
    order: WorkOrder,
    thesis: Thesis,
    company: Company,
    premise: Premise,
) -> Finding | None:
    """One premise against what arrived since it was last read, or ``None`` for nothing new.

    Raises:
        BudgetExceededError: From the base agent's refusal, for the caller to stop on.
    """
    session = context.session
    last = await _last_reading(session, premise)
    since = _read_since(premise, last)
    facts = await annual_facts(session, company_id=company.id, as_of=None, point_in_time=False)
    window = {
        period_end: rows
        for period_end, rows in facts.items()
        if max(row.filed_date for row in rows) > since
    }
    if not window or _nothing_newer(last, window):
        _log.info(
            "monitor.nothing_new",
            thesis_id=str(thesis.id),
            judgement_id=str(premise.judgement_id),
            since=since.isoformat(),
        )
        return None
    filed = [row.filed_date for rows in window.values() for row in rows]
    window_from, window_to = min(filed), max(filed)

    ledger = new_context()
    analysis = await analyse_company(
        session,
        ledger,
        company_id=company.id,
        work_order=order,
        max_periods=_PERIODS_TO_READ,
    )
    try:
        measurement = _measure(ledger, analysis, premise=premise)
    except _UnobservableError as why:
        return await _write_finding(
            session,
            thesis=thesis,
            premise=premise,
            job_id=context.job_step.job_id,
            status=PremiseStatus.UNOBSERVABLE,
            justification=why.reason,
            source_ids=(),
            observed=None,
            window=(window_from, window_to),
        )
    if ledger.records:
        await persist_context(session, ledger, job_id=context.job_step.job_id)

    window_facts = _window_facts(window)
    known_sources = {fact.source_document_id for fact in window_facts}
    reply = await ThesisMonitorAgent().run(
        context,
        PremiseInput(
            company_name=company.name,
            ticker=company.ticker,
            premise_id=str(premise.judgement_id),
            statement=premise.statement,
            basis=premise.judgement.basis,
            held_on=premise.judgement.held_at.date().isoformat(),
            predicate=predicate_sentence(premise),
            observation=measurement.as_observation(premise),
            facts=window_facts,
        ),
    )
    status = _bounded_status(reply, holds=measurement.holds, judgement_id=premise.judgement_id)
    cited = [ref for ref in reply.source_document_ids if ref in known_sources]
    if len(cited) != len(reply.source_document_ids):
        _log.warning(
            "monitor.sources_dropped",
            judgement_id=str(premise.judgement_id),
            dropped=len(reply.source_document_ids) - len(cited),
        )
    return await _write_finding(
        session,
        thesis=thesis,
        premise=premise,
        job_id=context.job_step.job_id,
        status=status,
        justification=reply.justification,
        source_ids=tuple(dict.fromkeys(cited)),
        observed=measurement.as_json(premise),
        window=(window_from, window_to),
    )


async def _last_reading(session: AsyncSession, premise: Premise) -> Finding | None:
    """The newest reading that measured something, or none."""
    last: Finding | None = await session.scalar(
        select(Finding)
        .where(
            Finding.judgement_id == premise.judgement_id,
            Finding.kind == FindingKind.READING,
            Finding.window_to.is_not(None),
            # An unobservable reading measured nothing, so it settles nothing: a premise
            # read while one year was stored is read again when the prior year arrives,
            # rather than waiting for the filing after that.
            Finding.status != PremiseStatus.UNOBSERVABLE,
        )
        .order_by(Finding.created_at.desc())
        .limit(1)
    )
    return last


def _read_since(premise: Premise, last: Finding | None) -> date:
    """The date after which a filing is news to this premise."""
    held = premise.judgement.held_at.date()
    if last is None or last.window_to is None:
        return held
    return max(held, last.window_to)


def _nothing_newer(last: Finding | None, window: dict[date, list[Any]]) -> bool:
    """Whether the window's newest period is one the last reading already measured.

    Facts filed since the last reading can all belong to an older period — an amended or
    late-filed prior year — and the measurement reads the newest period stored, so a
    "new" reading would re-issue the old verdict about the old period, and a contradicted
    one would open the gate a second time.
    """
    if last is None or not last.observed:
        return False
    observed_end = last.observed.get("period_end")
    if not observed_end:
        return False
    return date.fromisoformat(str(observed_end)) >= max(window)


def _window_facts(window: dict[date, list[Any]]) -> list[WindowFact]:
    rows = sorted(
        (row for rows in window.values() for row in rows),
        key=lambda row: (row.period_end, row.concept),
        reverse=True,
    )
    return [
        WindowFact(
            concept=row.concept,
            value=str(row.value),
            unit=row.unit,
            period_end=row.period_end.isoformat(),
            filed_date=row.filed_date.isoformat(),
            source_document_id=str(row.source_document_id),
        )
        for row in rows[:MAX_FACTS]
    ]


def _bounded_status(
    reply: PremiseReading, *, holds: bool, judgement_id: uuid.UUID
) -> PremiseStatus:
    """The model's status, held within the crossing code made (ADR 0103).

    A defeated predicate is `contradicted` whatever the reply says; a confirmed one is never
    `contradicted` and never `unobservable`. A reply outside the bounds is corrected and the
    correction logged — the tier is code's, and always was (ADR 0078).
    """
    if not holds:
        bounded = PremiseStatus.CONTRADICTED
    elif reply.status in {PremiseStatus.CONTRADICTED, PremiseStatus.UNOBSERVABLE}:
        bounded = PremiseStatus.UNCHANGED
    else:
        bounded = reply.status
    if bounded is not reply.status:
        _log.warning(
            "monitor.status_bounded",
            judgement_id=str(judgement_id),
            replied=reply.status.value,
            written=bounded.value,
            holds=holds,
        )
    return bounded


async def _write_finding(
    session: AsyncSession,
    *,
    thesis: Thesis,
    premise: Premise,
    job_id: uuid.UUID,
    status: PremiseStatus,
    justification: str,
    source_ids: tuple[str, ...],
    observed: dict[str, Any] | None,
    window: tuple[date, date],
) -> Finding:
    finding = Finding(
        thesis_id=thesis.id,
        judgement_id=premise.judgement_id,
        job_id=job_id,
        kind=FindingKind.READING,
        status=status,
        justification=justification,
        source_document_ids=list(source_ids),
        observed=observed,
        window_from=window[0],
        window_to=window[1],
        opens_gate=status.opens_a_gate,
    )
    session.add(finding)
    await session.flush()
    await _record(
        session,
        actor="monitor",
        event_type="monitor.finding",
        thesis_id=thesis.id,
        job_id=job_id,
        payload={
            "finding_id": str(finding.id),
            "thesis_id": str(thesis.id),
            "judgement_id": str(premise.judgement_id),
            "status": status.value,
            "opens_gate": finding.opens_gate,
            "observed": observed,
            "source_document_ids": list(source_ids),
        },
    )
    _log.info(
        "monitor.finding",
        finding_id=str(finding.id),
        thesis_id=str(thesis.id),
        status=status.value,
        opens_gate=finding.opens_gate,
    )
    return finding


async def _stop(
    session: AsyncSession,
    *,
    job: Job,
    order: WorkOrder,
    thesis: Thesis,
    premise: Premise,
    refused: BudgetExceededError,
) -> Finding:
    """Stop with a finding rather than pause for nobody (ADR 0078)."""
    at = datetime.now(UTC)
    job.error = {"code": refused.code, "message": refused.message, "context": refused.context}
    await _finish(session, job=job, order=order, status=JobStatus.FAILED, at=at)
    finding = Finding(
        thesis_id=thesis.id,
        judgement_id=None,
        job_id=job.id,
        kind=FindingKind.STOPPED,
        status=None,
        justification=(
            f"The pass stopped while reading premise {premise.position} "
            f"({premise.statement[:80]!r}) because a call would have breached a cost ceiling: "
            f"{refused.message} It did not pause for a decision; the premises after it were "
            "not read, and the next pass reads them."
        ),
        source_document_ids=[],
        observed=None,
        opens_gate=False,
    )
    session.add(finding)
    await session.flush()
    await _record(
        session,
        actor="monitor",
        event_type="monitor.stopped",
        thesis_id=thesis.id,
        job_id=job.id,
        payload={
            "finding_id": str(finding.id),
            "thesis_id": str(thesis.id),
            "judgement_id": str(premise.judgement_id),
            "scope": refused.context.get("scope"),
            "cap_gbp": refused.context.get("cap_gbp"),
        },
    )
    _log.warning(
        "monitor.stopped",
        thesis_id=str(thesis.id),
        job_id=str(job.id),
        scope=refused.context.get("scope"),
    )
    return finding


# -- What a person does about a finding ------------------------------------------------------


def finding_payload(finding: Finding) -> dict[str, Any]:
    """Exactly what the gate displays, for the hash the decision binds."""
    return {
        "finding_id": str(finding.id),
        "status": finding.status.value if finding.status else None,
        "justification": finding.justification,
        "observed": finding.observed,
        "source_document_ids": list(finding.source_document_ids),
        "judgement_id": str(finding.judgement_id) if finding.judgement_id else None,
        "premise": finding.premise.statement if finding.premise is not None else None,
    }


async def decide_finding(
    session: AsyncSession,
    *,
    finding: Finding,
    actor: User,
    decision: Decision,
    reason: str,
    payload_hash: str,
) -> FindingResolution:
    """Decide the thesis gate a contradicted finding opened (ADR 0103 §3).

    ``APPROVED`` accepts the finding and withdraws the premise with the reason; ``REJECTED``
    keeps the premise and records that the contradiction was seen. Both write an
    ``approvals`` row keyed to the pass's own run root, a chained audit event, and a
    resolution pointing at the approval.

    Raises:
        ValidationError: If the finding opens no gate, the hash is not of what is shown,
            the reason is blank, the decision is not one of the two, or the pass that
            raised the finding is gone.
        ConflictError: If the finding is already resolved.
    """
    if not finding.opens_gate:
        message = "This finding opened no gate; it is dismissed or acted on, not decided."
        raise ValidationError(message, context={"finding_id": str(finding.id)})
    if not finding.is_open:
        message = "This finding was already decided, and the decision stands."
        raise ConflictError(message, context={"finding_id": str(finding.id)})
    if decision not in {Decision.APPROVED, Decision.REJECTED}:
        message = "A contradicted premise is withdrawn or kept; there is no third answer."
        raise ValidationError(message, context={"decision": decision.value})
    if payload_hash != payload_hash_for(finding_payload(finding)):
        message = (
            "The finding changed between the page being served and the decision being made. "
            "Reload it and decide on what it shows now."
        )
        raise ValidationError(message, context={"finding_id": str(finding.id)})
    _require_reason(reason, doing="Deciding what to do about a contradicted premise")
    job = await session.get(Job, finding.job_id) if finding.job_id is not None else None
    if job is None:
        message = (
            "The pass that raised this finding is no longer on record, so no run root can "
            "carry the decision. Close the finding with the reason instead; the page offers "
            "that form in the gate's place."
        )
        raise ValidationError(message, context={"finding_id": str(finding.id)})

    approval = Approval(
        work_order_id=job.work_order_id,
        job_id=job.id,
        gate=GateKind.THESIS,
        decision=decision,
        actor_user_id=actor.id,
        notes=reason.strip(),
        payload_hash=payload_hash,
    )
    session.add(approval)
    await session.flush()

    action = FindingAction.WITHDRAWN if decision is Decision.APPROVED else FindingAction.DISMISSED
    if action is FindingAction.WITHDRAWN:
        assert finding.premise is not None
        # Withdrawn from the thesis page since the pass ran: the finding still closes as
        # a withdrawal — that is what happened — but the first reason stands.
        if not finding.premise.judgement.is_withdrawn:
            await thesis_service.withdraw_premise(
                session, premise=finding.premise, actor=actor, reason=reason
            )
    resolution = await _append_resolution(
        session, finding=finding, actor=actor, action=action, reason=reason, approval=approval
    )
    await _record(
        session,
        actor=actor.email,
        event_type=f"approval.{decision.value.lower()}",
        thesis_id=finding.thesis_id,
        job_id=job.id,
        payload={
            "gate": GateKind.THESIS.value,
            "decision": decision.value,
            "payload_hash": payload_hash,
            "approval_id": str(approval.id),
            "finding_id": str(finding.id),
            "action": action.value,
        },
    )
    _log.info(
        "monitor.gate_decided",
        finding_id=str(finding.id),
        decision=decision.value,
        actor=actor.email,
    )
    return resolution


async def resolve_finding(
    session: AsyncSession,
    *,
    finding: Finding,
    actor: User,
    action: FindingAction,
    reason: str,
) -> FindingResolution:
    """Dismiss, act on, or reopen a finding that opened no gate. Appended, never flipped.

    A contradicted finding whose pass is still on record is decided at its gate and refused
    here. One whose pass has gone — `aer reset-research` removes run roots and leaves
    findings — has no row for an approval to hang on, and is closed here like any other,
    with the reason (see `Finding.gate_is_decidable`).

    Raises:
        ValidationError: If the reason is blank, or the act does not fit the finding — a
            contradicted finding with a pass is decided at its gate, a stopped pass has no
            premise to withdraw.
        ConflictError: If the finding is already in the state the act would put it in.
    """
    _require_reason(reason, doing=f"Marking a finding {action.value}")
    if finding.gate_is_decidable:
        message = (
            "This finding opened a gate: decide it there — withdraw the premise or keep it — "
            "rather than dismissing it."
        )
        raise ValidationError(message, context={"finding_id": str(finding.id)})
    if action is FindingAction.REOPENED:
        if finding.is_open:
            message = "This finding is open already."
            raise ConflictError(message, context={"finding_id": str(finding.id)})
    elif not finding.is_open:
        message = "This finding was already resolved, and the reason given then stands."
        raise ConflictError(message, context={"finding_id": str(finding.id)})
    if action is FindingAction.WITHDRAWN:
        if finding.premise is None:
            message = "This finding names no premise, so there is none to withdraw."
            raise ValidationError(message, context={"finding_id": str(finding.id)})
        if not finding.premise.judgement.is_withdrawn:
            await thesis_service.withdraw_premise(
                session, premise=finding.premise, actor=actor, reason=reason
            )
    return await _append_resolution(
        session, finding=finding, actor=actor, action=action, reason=reason, approval=None
    )


def _require_reason(reason: str, *, doing: str) -> None:
    if reason.strip():
        return
    message = (
        f'{doing} needs a reason. "I saw this and chose to do nothing" is decision data, '
        "and a resolution without a reason is the least reviewable row this table could hold."
    )
    raise ValidationError(message, context={"field": "reason"})


async def _append_resolution(
    session: AsyncSession,
    *,
    finding: Finding,
    actor: User,
    action: FindingAction,
    reason: str,
    approval: Approval | None,
) -> FindingResolution:
    resolution = FindingResolution(
        finding_id=finding.id,
        action=action,
        reason=reason.strip(),
        actor=actor.email,
        approval_id=approval.id if approval is not None else None,
    )
    session.add(resolution)
    await session.flush()
    await session.refresh(finding, attribute_names=["resolutions"])
    await _record(
        session,
        actor=actor.email,
        event_type=f"monitor.finding_{action.value}",
        thesis_id=finding.thesis_id,
        job_id=finding.job_id,
        payload={
            "finding_id": str(finding.id),
            "resolution_id": str(resolution.id),
            "action": action.value,
            "reason": resolution.reason,
            "approval_id": str(approval.id) if approval is not None else None,
        },
    )
    return resolution


# -- Reading ------------------------------------------------------------------------------------


async def findings_for(
    session: AsyncSession, *, user_id: uuid.UUID, open_only: bool = True
) -> list[Finding]:
    """This person's findings, newest first, with their theses, premises and resolutions.

    ``open_only`` filters in Python rather than SQL: "open" is a fact about the last of a
    finding's resolutions, and the rows are few.
    """
    rows = await _all_findings(session, user_id=user_id)
    if open_only:
        return [row for row in rows if row.is_open]
    return [row for row in rows if not row.is_open]


async def _all_findings(session: AsyncSession, *, user_id: uuid.UUID) -> list[Finding]:
    return list(
        await session.scalars(
            select(Finding)
            .join(Thesis, Thesis.id == Finding.thesis_id)
            .options(
                selectinload(Finding.thesis),
                selectinload(Finding.premise),
                selectinload(Finding.resolutions),
            )
            .where(Thesis.user_id == user_id)
            .order_by(Finding.created_at.desc())
        )
    )


async def findings_partitioned(
    session: AsyncSession, *, user_id: uuid.UUID
) -> tuple[list[Finding], list[Finding]]:
    """The open findings and the resolved ones, from one query rather than two."""
    rows = await _all_findings(session, user_id=user_id)
    return [row for row in rows if row.is_open], [row for row in rows if not row.is_open]


async def finding_of(
    session: AsyncSession, finding_id: uuid.UUID, *, user_id: uuid.UUID
) -> Finding | None:
    """One finding of this person's, or ``None`` for both "no such" and "not yours"."""
    found: Finding | None = await session.scalar(
        select(Finding)
        .join(Thesis, Thesis.id == Finding.thesis_id)
        .options(
            selectinload(Finding.thesis),
            selectinload(Finding.premise),
            selectinload(Finding.resolutions),
        )
        .where(Finding.id == finding_id, Thesis.user_id == user_id)
    )
    return found


async def reviews_due(
    session: AsyncSession, *, user_id: uuid.UUID, today: date
) -> list[tuple[Thesis, Premise]]:
    """Held premises a person said they would look at again by a date that has passed."""
    rows = await session.execute(
        select(Thesis, Premise)
        .join(Premise, Premise.thesis_id == Thesis.id)
        .where(
            Thesis.user_id == user_id,
            Thesis.retired_at.is_(None),
            Premise.review_by.is_not(None),
            Premise.review_by <= today,
        )
        .order_by(Premise.review_by)
    )
    return [(thesis, premise) for thesis, premise in rows if not premise.judgement.is_withdrawn]


@dataclass(frozen=True, slots=True)
class PassRow:
    """One monitor pass as the page lists it."""

    job: Job
    thesis_id: uuid.UUID
    findings: int


async def recent_passes(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = 8
) -> list[PassRow]:
    """The latest passes, newest first, with how many findings each left."""
    rows = await session.execute(
        select(Job, WorkOrder)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(WorkOrder.user_id == user_id, WorkOrder.tool == TOOL)
        .order_by(Job.started_at.desc().nullslast())
        .limit(limit)
    )
    listed: list[PassRow] = []
    for job, order in rows:
        counted = await session.scalar(
            select(func.count()).select_from(Finding).where(Finding.job_id == job.id)
        )
        assert order.subject_id is not None
        listed.append(PassRow(job=job, thesis_id=order.subject_id, findings=int(counted or 0)))
    return listed


# -- The chain ----------------------------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    thesis_id: uuid.UUID,
    job_id: uuid.UUID | None,
) -> None:
    """One link on the chain, correlated to the thesis and, where there is one, the pass."""
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            job_id=job_id,
            subject_kind=SUBJECT_THESIS,
            subject_id=thesis_id,
        )
    )
    await session.flush()
