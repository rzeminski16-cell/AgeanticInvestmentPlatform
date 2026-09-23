"""The refresh: a second run on the same request that pays only for what moved (F4).

ADR 0131. What this module owns is everything about a refresh that is not a workflow step:
whether a report can be refreshed and at what price; starting the run with the prior run's
decisions carried onto it; the diff between the two runs' figures, written as
``report_changes`` rows; and the rows the change summary is composed from. The steps
themselves are :mod:`aer.workflow.workflows.refresh_v1`.

**The diff is pure and the rows are code's.** :mod:`aer.calc.changes` judges every figure
by the mechanism's table; this module only reads the two ledgers and the fact store into
figures, asks the theses which premise a move crosses, and writes what came back. The
model writes prose *from* the rows in the change summary, never instead of them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.changes import Change, Figure, Movement, diff_figures
from aer.calc.dcf import SENSITIVITY_CASE
from aer.calc.units import Quantity, Unit, UnitMismatchError
from aer.core.enums import Decision, GateKind, JobStatus, PremiseComparator
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Calculation,
    Claim,
    FinancialFact,
    Job,
    JobStep,
    Judgement,
    Premise,
    Report,
    ReportChange,
    ReportSection,
    ResearchRequest,
    Thesis,
    User,
)
from aer.errors import ConflictError
from aer.render import display
from aer.services import approvals as approval_service
from aer.services import configuration
from aer.services.thesis_monitor import predicate_holds, resolve_metric, threshold_quantity
from aer.version import git_sha

if TYPE_CHECKING:
    from aer.config import Settings

__all__ = [
    "EXPECTED_SECTIONS",
    "FULL",
    "MAX_SECTIONS_FOR_A_REFRESH",
    "REFRESH",
    "WORKFLOW_VERSION",
    "DiffOutcome",
    "Estimate",
    "calculation_remap",
    "changes_for_job",
    "diff_runs",
    "estimate_refresh",
    "figures_named_by_sections",
    "mark_changes_read",
    "refusal_to_refresh",
    "start_refresh",
    "summary_block",
]

_log = structlog.get_logger("aer.services.refresh")

WORKFLOW_VERSION: Final = "refresh_v1"
REFRESH: Final = "refresh"
FULL: Final = "full"

# The mechanism's own expectation for an ordinary quarter, and its ceiling: past twelve of
# eighteen a refresh is a full run wearing the wrong price (08-mechanisms §1.4).
EXPECTED_SECTIONS: Final = 6
MAX_SECTIONS_FOR_A_REFRESH: Final = 12
SPINE_SECTIONS: Final = 18

# What the value of a section's draft is projected at: the vertical slice's own draft
# estimate over its spine, so the refresh and the run price a section the same way.
DRAFT_ESTIMATE_GBP: Final = Decimal("5.00")
VALIDATE_ESTIMATE_GBP: Final = Decimal("0.02")
_PENNY: Final = Decimal("0.01")

_ACQUIRE_STEP: Final = "acquire"
_DRAFT_STEP: Final = "draft"


@dataclass(frozen=True, slots=True)
class Estimate:
    """What a refresh is expected to cost, before it starts."""

    sections_expected: int
    per_section_gbp: Decimal
    cost_gbp: Decimal

    @property
    def label(self) -> str:
        return f"Refresh — about £{self.cost_gbp:.2f}"


def estimate_refresh(settings: Settings) -> Estimate:
    """The control's price: six sections at the draft step's per-section estimate, plus
    the validate step, rounded up to the penny. An estimate said as one; the ceiling
    (``settings.refresh_budget_gbp``) bounds the spend, not this."""
    per_section = DRAFT_ESTIMATE_GBP / SPINE_SECTIONS
    cost = min(per_section * EXPECTED_SECTIONS + VALIDATE_ESTIMATE_GBP, settings.refresh_budget_gbp)
    return Estimate(
        sections_expected=EXPECTED_SECTIONS,
        per_section_gbp=per_section.quantize(_PENNY, rounding=ROUND_CEILING),
        cost_gbp=cost.quantize(_PENNY, rounding=ROUND_CEILING),
    )


# -- Starting one ------------------------------------------------------------------------------


async def refusal_to_refresh(session: AsyncSession, *, report: Report, user: User) -> str | None:
    """Why this report cannot be refreshed now, in a sentence, or ``None``."""
    request = await session.get(ResearchRequest, report.request_id)
    if request is None or request.work_order.user_id != user.id:
        return "This report is not in your account's record."
    if not report.is_current:
        return (
            "Only the current report on a company can be refreshed; this one has been "
            "superseded or withdrawn."
        )
    live = await session.scalar(
        select(Job).where(Job.work_order_id == request.id, Job.status.in_(_LIVE_STATUSES))
    )
    if live is not None:
        return "This company's request already has a run going; a refresh waits for it."
    return None


_LIVE_STATUSES: Final = tuple(status for status in JobStatus if not status.is_terminal)


async def start_refresh(
    session: AsyncSession,
    *,
    report: Report,
    actor: User,
    settings: Settings,
    now: datetime | None = None,
) -> Job:
    """Commission a refresh of ``report``: the job, its plan approval, the carried decisions.

    The priced go-ahead is the plan gate (ADR 0131 §2). The classification, peer set and
    theme set the prior run passed are re-asserted on the new job with their prior payload
    hashes, so every reader that asks whether this job confirmed them finds the answer on
    this job. The request is re-dated to today (ADR 0110); the prior report keeps its date.

    Raises:
        ConflictError: When :func:`refusal_to_refresh` has a reason.
    """
    refusal = await refusal_to_refresh(session, report=report, user=actor)
    if refusal is not None:
        raise ConflictError(refusal, context={"report_id": str(report.id)})
    request = await session.get(ResearchRequest, report.request_id)
    prior = await session.get(Job, report.job_id)
    if request is None or prior is None:  # pragma: no cover -- refused above without them
        message = "The report has no run to refresh."
        raise ConflictError(message, context={"report_id": str(report.id)})

    started = now or datetime.now(UTC)
    request.work_order.as_of_date = started.date()
    job = Job(
        work_order_id=request.id,
        plan_id=prior.plan_id,
        workflow_version=WORKFLOW_VERSION,
        code_version=git_sha() or "unknown",
        status=JobStatus.QUEUED,
        started_at=started,
        refresh_kind=REFRESH,
        refreshes_report_id=report.id,
    )
    session.add(job)
    await session.flush()

    estimate = estimate_refresh(settings)
    await approval_service.record_decision(
        session,
        job=job,
        gate=GateKind.PLAN,
        decision=Decision.APPROVED,
        actor=actor,
        payload_hash=sha256_hex(
            canonical_json(
                {
                    "refreshes_report_id": str(report.id),
                    "carried_plan_id": str(prior.plan_id) if prior.plan_id else None,
                    "estimated_cost_gbp": str(estimate.cost_gbp),
                }
            )
        ),
        notes=f"A refresh of the report approved on {_day(report.approved_at)}, priced at "
        f"£{estimate.cost_gbp:.2f}. The plan is the prior run's, carried.",
    )
    for gate in (GateKind.PEER_SET, GateKind.SECTOR_SPECIALIST, GateKind.THEME_SET):
        decided = await approval_service.current_decision(session, prior.id, gate)
        if decided is None or decided.decision not in approval_service.PASSING_DECISIONS:
            continue
        await approval_service.record_decision(
            session,
            job=job,
            gate=gate,
            decision=Decision.APPROVED,
            actor=actor,
            payload_hash=decided.payload_hash,
            notes=f"Carried from the run of {_day(prior.started_at)} by the refresh.",
        )

    _log.info(
        "refresh.started",
        job_id=str(job.id),
        refreshes=str(report.id),
        request_id=str(request.id),
        estimate_gbp=str(estimate.cost_gbp),
    )
    return job


def _day(moment: datetime | None) -> str:
    return moment.strftime("%d %B %Y") if moment is not None else "an unrecorded day"


# -- The diff --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiffOutcome:
    """What the diff found, for the draft step and the summary."""

    changes: tuple[Change, ...]
    new_documents: tuple[dict[str, Any], ...]
    rows_written: int

    @property
    def material(self) -> tuple[Change, ...]:
        return tuple(change for change in self.changes if change.material)

    @property
    def unchanged(self) -> int:
        return sum(1 for change in self.changes if not change.material)

    @property
    def moved_keys(self) -> frozenset[tuple[str, str]]:
        """``(kind, name)`` of every material figure — what a section's claims are matched on."""
        return frozenset((change.kind, change.name) for change in self.material)

    @property
    def broke(self) -> tuple[Change, ...]:
        return tuple(change for change in self.material if change.movement is Movement.PREMISE)

    def as_dict(self) -> dict[str, Any]:
        return {
            "material": len(self.material),
            "unchanged": self.unchanged,
            "broke": len(self.broke),
            "new_documents": len(self.new_documents),
            "rows_written": self.rows_written,
            "moved": sorted(f"{kind}:{name}" for kind, name in self.moved_keys),
        }


async def diff_runs(
    session: AsyncSession, *, job: Job, prior: Job, request: ResearchRequest, report: Report
) -> DiffOutcome:
    """Compare the refresh's ledger and facts with the prior run's, and write the rows."""
    company_id = request.company_id
    prior_figures = [
        *await _calculation_figures(session, prior.id),
        *await _fact_figures(session, company_id, until=prior.finished_at),
    ]
    new_figures = [
        *await _calculation_figures(session, job.id),
        *await _fact_figures(session, company_id, until=None),
    ]
    crossings = await _crossings(session, request)
    changes = diff_figures(prior_figures, new_figures, crossed=crossings)
    documents = await _documents_read_first(session, job.id)

    written = 0
    for change in changes:
        if not change.material:
            continue
        session.add(_row(job, report, change))
        written += 1
    for document in documents:
        session.add(
            ReportChange(
                job_id=job.id,
                prior_report_id=report.id,
                kind="document",
                name=str(document.get("title") or document.get("accession") or "a document"),
                period=str(document.get("publication_date") or "") or None,
                material=True,
                movement=Movement.APPEARED.value,
                narrative=(
                    f"{document.get('form') or 'A filing'} {document.get('accession') or ''} was "
                    "read for the first time."
                ).replace("  ", " "),
                new_reference=str(document.get("source_document_id") or "") or None,
            )
        )
        written += 1
    await session.flush()

    outcome = DiffOutcome(changes=changes, new_documents=tuple(documents), rows_written=written)
    _log.info("refresh.diffed", job_id=str(job.id), **outcome.as_dict())
    return outcome


def _row(job: Job, report: Report, change: Change) -> ReportChange:
    return ReportChange(
        job_id=job.id,
        prior_report_id=report.id,
        kind=change.kind,
        name=change.name,
        period=change.period or None,
        case=change.case or None,
        prior_value=change.prior.value if change.prior is not None else None,
        prior_unit=change.prior.unit if change.prior is not None else None,
        new_value=change.new.value if change.new is not None else None,
        new_unit=change.new.unit if change.new is not None else None,
        change_pct=change.change_pct,
        material=change.material,
        movement=change.movement.value,
        narrative=change.narrative,
        prior_reference=change.prior.reference if change.prior is not None else None,
        new_reference=change.new.reference if change.new is not None else None,
    )


async def _calculation_figures(session: AsyncSession, job_id: uuid.UUID) -> list[Figure]:
    """Every calculation of a run as a figure, every period, never a sensitivity cell."""
    rows = await session.scalars(
        select(Calculation)
        .where(Calculation.job_id == job_id)
        .order_by(Calculation.created_at, Calculation.sequence)
    )
    figures: list[Figure] = []
    for row in rows:
        parameters = dict(row.parameters or {})
        case = str(parameters.pop("case", "") or "")
        if case == SENSITIVITY_CASE:
            continue
        figures.append(
            Figure(
                kind="calculation",
                name=row.name,
                value=row.output_value,
                unit=row.output_unit,
                period=row.period_label or (row.period_end.isoformat() if row.period_end else ""),
                case=case,
                distinguisher=json.dumps(parameters, sort_keys=True, default=str),
                reference=str(row.id),
            )
        )
    return figures


async def _fact_figures(
    session: AsyncSession, company_id: uuid.UUID | None, *, until: datetime | None
) -> list[Figure]:
    """The company's consolidated facts as a run saw them: the newest filing's word on
    each figure among the rows that existed by ``until``."""
    if company_id is None:
        return []
    query = select(FinancialFact).where(
        FinancialFact.company_id == company_id, FinancialFact.dimension_axis.is_(None)
    )
    if until is not None:
        query = query.where(FinancialFact.created_at <= until)
    rows = await session.scalars(query.order_by(FinancialFact.filed_date, FinancialFact.created_at))
    latest: dict[tuple[str, str, str], FinancialFact] = {}
    for row in rows:
        key = (row.concept, row.period_end.isoformat(), row.fiscal_period or "")
        latest[key] = row
    return [
        Figure(
            kind="fact",
            name=row.concept,
            value=row.value,
            unit=row.unit,
            period=f"{row.fiscal_period or 'period'} {row.period_end.isoformat()}",
            reference=str(row.id),
        )
        for row in latest.values()
    ]


async def _documents_read_first(session: AsyncSession, job_id: uuid.UUID) -> list[dict[str, Any]]:
    """What the refresh's acquire step fetched for the first time, from its own record."""
    step = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == _ACQUIRE_STEP)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    produced = step.output_ref if step is not None and isinstance(step.output_ref, dict) else {}
    filings = produced.get("filings", [])
    return [dict(item) for item in filings if isinstance(item, dict)]


async def _crossings(session: AsyncSession, request: ResearchRequest) -> Any:
    """A judge of premise crossings over this company's theses, for the pure diff.

    Reads the premises once, resolves each metric to the figure name the ledger or the
    fact store records it under, and answers the diff's question with the monitor's own
    ``predicate_holds`` — a premise crossed is one whose verdict differs between the prior
    figure and the new. A unit the threshold cannot meet is not a crossing.
    """
    if request.company_id is None:
        return None
    premises = list(
        await session.scalars(
            select(Premise)
            .join(Thesis, Thesis.id == Premise.thesis_id)
            .join(Judgement, Judgement.id == Premise.judgement_id)
            .where(
                Thesis.subject_kind == "company",
                Thesis.subject_id == request.company_id,
                Thesis.user_id == request.work_order.user_id,
                Judgement.withdrawn_at.is_(None),
            )
        )
    )
    watched: dict[tuple[str, str], list[Premise]] = {}
    for premise in premises:
        if not premise.has_predicate or premise.metric is None:
            continue
        resolved = resolve_metric(premise.metric)
        if resolved is None:
            continue
        key = ("fact", resolved.key) if resolved.kind == "level" else ("calculation", resolved.key)
        watched.setdefault(key, []).append(premise)
    if not watched:
        return None

    def crossed(prior: Figure, new: Figure) -> str | None:
        for premise in watched.get((new.kind, new.name), []):
            if premise.threshold is None or premise.comparator is None:
                continue
            try:
                unit = Unit.parse(new.unit) if new.unit and new.unit != "pure" else None
                before = Quantity.of(prior.value, unit) if unit else Quantity.of(prior.value)
                after = Quantity.of(new.value, unit) if unit else Quantity.of(new.value)
                threshold = threshold_quantity(Decimal(premise.threshold), premise.unit or "")
                comparator = PremiseComparator(premise.comparator)
                held = predicate_holds(before, comparator, threshold)
                holds = predicate_holds(after, comparator, threshold)
            except (UnitMismatchError, ValueError):
                continue
            if held != holds:
                return f"the premise {premise.statement!r}"
        return None

    return crossed


# -- Reading a refresh back --------------------------------------------------------------------


async def changes_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[ReportChange]:
    rows = await session.scalars(
        select(ReportChange)
        .where(ReportChange.job_id == job_id)
        .order_by(ReportChange.created_at, ReportChange.name)
    )
    return list(rows)


async def mark_changes_read(session: AsyncSession, *, job: Job, now: datetime | None = None) -> Job:
    """The operator has read the change summary; the work list's row lets go."""
    if job.changes_read_at is None:
        job.changes_read_at = now or datetime.now(UTC)
        await session.flush()
    return job


def sections_moved(changes: Sequence[Change], claims: dict[str, set[tuple[str, str]]]) -> set[str]:
    """Which sections a set of changes reaches, given what each section's claims name."""
    moved = {(change.kind, change.name) for change in changes if change.material}
    return {key for key, named in claims.items() if named & moved}


# -- What the draft step needs: the remap, the sections' figures, and the summary block ---------


async def calculation_remap(
    session: AsyncSession, *, prior_job_id: uuid.UUID, job_id: uuid.UUID
) -> dict[str, str]:
    """The prior run's calculation ids to the refresh's, by figure key.

    A carried section's claims point at the prior ledger; where the refresh struck the same
    figure — same name, inputs' shape, period and case — the claim is re-pointed at the new
    row, so the carried section's footnotes walk the ledger the document was rendered from.
    Where nothing matches, the prior id stays: a row that still resolves beats a dangling one.
    """
    prior = {
        figure.key: figure.reference for figure in await _calculation_figures(session, prior_job_id)
    }
    new = {figure.key: figure.reference for figure in await _calculation_figures(session, job_id)}
    return {prior_id: new[key] for key, prior_id in prior.items() if key in new}


async def figures_named_by_sections(
    session: AsyncSession, *, job_id: uuid.UUID
) -> dict[str, set[tuple[str, str]]]:
    """For each of a run's sections, the ``(kind, name)`` of every figure its claims name."""
    rows = await session.execute(
        select(
            ReportSection.section_key,
            Claim.calculation_id,
            Claim.financial_fact_id,
        )
        .join(Claim, Claim.report_section_id == ReportSection.id)
        .where(ReportSection.job_id == job_id)
    )
    calculation_ids: set[uuid.UUID] = set()
    fact_ids: set[uuid.UUID] = set()
    pairs: list[tuple[str, uuid.UUID | None, uuid.UUID | None]] = []
    for section_key, calculation_id, fact_id in rows:
        pairs.append((section_key, calculation_id, fact_id))
        if calculation_id is not None:
            calculation_ids.add(calculation_id)
        if fact_id is not None:
            fact_ids.add(fact_id)
    names_of_calculations: dict[uuid.UUID, str] = {}
    if calculation_ids:
        found = await session.execute(
            select(Calculation.id, Calculation.name).where(Calculation.id.in_(calculation_ids))
        )
        names_of_calculations = dict(found.tuples().all())
    names_of_facts: dict[uuid.UUID, str] = {}
    if fact_ids:
        found = await session.execute(
            select(FinancialFact.id, FinancialFact.concept).where(FinancialFact.id.in_(fact_ids))
        )
        names_of_facts = dict(found.tuples().all())
    named: dict[str, set[tuple[str, str]]] = {}
    for section_key, calculation_id, fact_id in pairs:
        bucket = named.setdefault(section_key, set())
        if calculation_id is not None and calculation_id in names_of_calculations:
            bucket.add(("calculation", str(names_of_calculations[calculation_id])))
        if fact_id is not None and fact_id in names_of_facts:
            bucket.add(("fact", str(names_of_facts[fact_id])))
    return named


async def summary_block(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> dict[str, Any]:
    """The change summary's platform-filled fields, composed from the refresh's rows."""
    style = await configuration.effective_house_style(session)
    rows = await changes_for_job(session, job.id)
    prior = await session.get(Report, job.refreshes_report_id) if job.refreshes_report_id else None
    diff = await _step_record(session, job.id, DIFF_STEP)
    drafted = await _step_record(session, job.id, _DRAFT_STEP)

    broke = [_row_block(row, style) for row in rows if row.movement == Movement.PREMISE.value]
    moved = sorted(
        (
            _row_block(row, style)
            for row in rows
            if row.kind in {"fact", "calculation"} and row.movement != Movement.PREMISE.value
        ),
        key=lambda item: item["_magnitude"],
        reverse=True,
    )
    for item in moved:
        item.pop("_magnitude", None)
    for item in broke:
        item.pop("_magnitude", None)
    documents = [
        {
            "label": row.name,
            "form": (row.narrative.split(" ", 1)[0] if row.narrative else ""),
            "published": row.period or "",
            "source_document_id": row.new_reference,
        }
        for row in rows
        if row.kind == "document"
    ]
    unchanged = int(diff.get("unchanged", 0) or 0)
    material = len(broke) + len(moved)
    dated = prior.as_of_date.isoformat() if prior is not None else "an earlier date"
    today = request.work_order.as_of_date.isoformat()
    if not rows:
        basis = (
            f"Nothing new was read since the report of {dated}, as at {today}: no filing the "
            "record did not already hold, and no figure that moved. The prior report stands."
        )
    else:
        read = len(documents)
        basis = (
            f"Refreshed against the report of {dated}, as at {today}: {material} figure"
            f"{'' if material == 1 else 's'} moved materially and {read} "
            f"document{'' if read == 1 else 's'} {'was' if read == 1 else 'were'} read for the "
            "first time. Every figure below is a recorded calculation or a stored fact; the "
            "prior report stays readable at its own address."
        )
    return {
        "basis": basis,
        "broke": broke,
        "moved": moved,
        "new_documents": documents,
        "unchanged": (
            f"{unchanged} other figure{'' if unchanged == 1 else 's'} "
            f"{'is' if unchanged == 1 else 'are'} within two per cent of the prior report."
            if unchanged
            else ""
        ),
        "sections": {
            "redrafted": list(drafted.get("redrafted", []) or []),
            "carried": list(drafted.get("carried", []) or []),
            "stale": list(drafted.get("stale", []) or []),
        },
    }


def _row_block(row: ReportChange, style: Any) -> dict[str, Any]:
    label = row.name.replace("_", " ")
    if row.period:
        label = f"{label}, {row.period}"
    if row.case and row.case != "base":
        label = f"{label} ({row.case} case)"
    unit = row.new_unit or row.prior_unit or ""
    rendered_prior = (
        display.figure(row.prior_value, unit=unit, label=row.name, style=style)
        if row.prior_value is not None
        else "not held"
    )
    rendered_new = (
        display.figure(row.new_value, unit=unit, label=row.name, style=style)
        if row.new_value is not None
        else "no longer computed"
    )
    pct = (
        f"{(row.change_pct * 100).quantize(Decimal('0.1')):+}%"
        if row.change_pct is not None
        else ""
    )
    reference = row.new_reference or row.prior_reference
    return {
        "label": label[:1].upper() + label[1:],
        "prior": rendered_prior,
        "new": rendered_new,
        "unit": unit,
        "change_pct": pct,
        "movement": row.movement,
        "narrative": row.narrative,
        "calculation_id": reference if row.kind == "calculation" else None,
        "financial_fact_id": reference if row.kind == "fact" else None,
        "_magnitude": abs(row.change_pct) if row.change_pct is not None else Decimal("Infinity"),
    }


async def _step_record(session: AsyncSession, job_id: uuid.UUID, step_key: str) -> dict[str, Any]:
    step = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == step_key)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    produced = step.output_ref if step is not None else None
    return dict(produced) if isinstance(produced, dict) else {}


DIFF_STEP: Final = "diff"
