"""A closed position, its outcome, the reviewer's proposal, and the operator's review.

Roadmap §3.8, under ADRs 0081 and 0105. Four things, in the order a review happens.

**An episode is a closed position.** There is no ``positions`` table (ADR 0083), so a
position closes when the walk over a security's trades brings the held quantity back to
nil. The episode runs from the first trade after the holding was last nil to the trade
that closed it, and a security bought, sold out and bought again has two.

**The outcome is code's.** Cost, proceeds and the realised return over an episode are
recorded calculations through :mod:`aer.calc.outcomes`, every flow converted into the
book's currency at its own trade's date through the same helpers the portfolio page uses.
The holding period is a date difference beside the intended horizon the decisions stated.

**The reviewer proposes.** One pass per episode, rooted on its own work order, and the draft
— verdicts, a process quality with its basis, lessons — lands on the pass's job step as
output. Nothing is a judgement yet.

**The operator confirms.** The review is the third judgement subtype, held by the operator
on a basis that is theirs, with the proposal kept beside it so that whether they agreed with
the reviewer is a question the analytics page can answer.

**Nothing here reads a mark.** A closed position's proceeds are its sales, which are
attestations; no price bar is consulted, which is the condition ADR 0081 admitted the role
on and the one this module enforces by having no query for one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.agents.base import AgentContext
from aer.agents.post_trade_reviewer import (
    DecisionUnderReview,
    FindingWhileOpen,
    OutcomeFigures,
    PostTradeReviewerAgent,
    PremiseUnderReview,
    ReviewDraft,
    ReviewInput,
)
from aer.calc import portfolio as calc
from aer.calc.engine import CalculationContext
from aer.calc.outcomes import episode_cost, episode_proceeds, realised_return
from aer.calc.units import CalculationError, Quantity, Unit
from aer.config import Settings
from aer.core.enums import (
    FindingKind,
    JobStatus,
    JudgementKind,
    PremiseVerdict,
    ProcessQuality,
    RequestStatus,
    TransactionKind,
)
from aer.core.figures import plain_decimal
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    AuditEvent,
    Company,
    Decision,
    Finding,
    Job,
    JobStep,
    Judgement,
    Portfolio,
    Premise,
    Review,
    ReviewVerdict,
    Security,
    Thesis,
    Transaction,
    User,
    WorkOrder,
)
from aer.errors import BudgetExceededError, ConflictError, ValidationError
from aer.providers.protocol import LLMProvider
from aer.providers.router import Router
from aer.services import theses as thesis_service
from aer.services.calculations import new_context, persist_context
from aer.services.decisions import ACTION_WORDS
from aer.services.portfolio import (
    CASH_KINDS,
    acquisition_cost_of,
    in_base,
    movement_of,
    source_of,
    transactions_in_force,
)
from aer.services.thesis_monitor import predicate_sentence
from aer.storage.protocol import ArtefactStore
from aer.version import git_sha

__all__ = [
    "MINIMUM_SAMPLE",
    "STEP_KEY",
    "SUBJECT_POSITION",
    "TOOL",
    "WORKFLOW_VERSION",
    "Analytics",
    "Episode",
    "EpisodeState",
    "Outcome",
    "Part",
    "Proposal",
    "Statistic",
    "analytics_for",
    "closed_episodes",
    "confirm_review",
    "episode_of",
    "latest_pass_for",
    "outcome_for",
    "proposal_of",
    "review_for_episode",
    "review_of",
    "reviews_for",
    "run_review",
    "states_for",
]

_log = structlog.get_logger("aer.services.post_trade")

TOOL: Final = "review"
SUBJECT_POSITION: Final = "position"
WORKFLOW_VERSION: Final = "post_trade_review_v1"
STEP_KEY: Final = "review"

# Below this many reviewed positions a proportion is a tally rather than a percentage. The
# comps table's own floor (`services.overview.MINIMUM_SAMPLE`), for the same reason: three
# is where quoting a ratio stops being one anecdote wearing a plural.
MINIMUM_SAMPLE: Final = 3


# -- Episodes ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Episode:
    """One closed position: a security in a book, from first trade to the one that closed it."""

    portfolio: Portfolio
    security: Security
    opened_on: date
    closed_on: date
    trades: tuple[Transaction, ...]

    @property
    def key(self) -> str:
        return f"{self.security.id}:{self.closed_on.isoformat()}"


async def closed_episodes(
    session: AsyncSession, *, portfolio: Portfolio, as_of: date | None = None
) -> list[Episode]:
    """Every closed position in the book, newest close first.

    The same walk the pooled cost makes (ADR 0085), asking only one question of it: when did
    the held quantity return to nil? A holding still open at the as-of date has no closed
    episode and is not here.
    """
    trades = await transactions_in_force(
        session, portfolio=portfolio, as_of=as_of or datetime.now(UTC).date()
    )
    by_security: dict[uuid.UUID, list[Transaction]] = {}
    securities: dict[uuid.UUID, Security] = {}
    for trade in trades:
        if trade.security is None:
            continue
        by_security.setdefault(trade.security.id, []).append(trade)
        securities[trade.security.id] = trade.security

    episodes: list[Episode] = []
    for security_id, rows in by_security.items():
        held = Decimal(0)
        current: list[Transaction] = []
        for trade in rows:
            if trade.kind in CASH_KINDS:
                # A dividend belongs to the episode it fell in; it opens nothing.
                if current:
                    current.append(trade)
                continue
            current.append(trade)
            if trade.kind is TransactionKind.SPLIT:
                held = held * trade.quantity
            else:
                held = held + trade.quantity
            if held == 0 and any(row.kind is not TransactionKind.SPLIT for row in current):
                dealt = [row for row in current if row.kind not in CASH_KINDS]
                episodes.append(
                    Episode(
                        portfolio=portfolio,
                        security=securities[security_id],
                        opened_on=dealt[0].trade_date,
                        closed_on=trade.trade_date,
                        trades=tuple(current),
                    )
                )
                current = []
    episodes.sort(key=lambda row: (row.closed_on, row.security.ticker), reverse=True)
    return episodes


def episode_of(
    episodes: list[Episode], *, security_id: uuid.UUID, closed_on: date
) -> Episode | None:
    return next(
        (row for row in episodes if row.security.id == security_id and row.closed_on == closed_on),
        None,
    )


# -- The outcome -------------------------------------------------------------------------------


@dataclass(slots=True)
class Outcome:
    """What code computed about an episode before the reviewer was asked anything."""

    opened_on: date
    closed_on: date
    holding_days: int
    currency: str
    cost: Quantity | None = None
    proceeds: Quantity | None = None
    realised_return: Quantity | None = None
    problem: str = ""
    decisions: list[Decision] = field(default_factory=list)
    thesis: Thesis | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def intended_horizon_months(self) -> int | None:
        """The longest horizon any decision under review stated, or none."""
        stated = [row.horizon_months for row in self.decisions if row.horizon_months]
        return max(stated) if stated else None

    def as_json(self, context: CalculationContext) -> dict[str, Any]:
        """The figures as the review row and the reviewer see them: strings, and ids."""
        record_ids = {
            name: str(records[-1].id)
            for name in ("episode_cost", "episode_proceeds", "realised_return")
            if (records := context.named(name))
        }
        return {
            "opened_on": self.opened_on.isoformat(),
            "closed_on": self.closed_on.isoformat(),
            "holding_days": self.holding_days,
            "intended_horizon_months": self.intended_horizon_months,
            "currency": self.currency,
            "cost": _plain(self.cost),
            "proceeds": _plain(self.proceeds),
            "realised_return": _plain(self.realised_return),
            "calculation_ids": record_ids,
            "problem": self.problem,
        }


async def outcome_for(
    session: AsyncSession, context: CalculationContext, *, episode: Episode
) -> Outcome:
    """Cost, proceeds and the realised return, each converted at its own trade's date.

    A flow that cannot be converted — no rate on or before the day — leaves the outcome
    with a stated problem and no return, never a return over part of the trades: a figure
    missing a purchase is worse than no figure, because it looks like an answer.
    """
    base = Unit.currency(episode.portfolio.base_currency)
    outcome = Outcome(
        opened_on=episode.opened_on,
        closed_on=episode.closed_on,
        holding_days=(episode.closed_on - episode.opened_on).days,
        currency=episode.portfolio.base_currency,
    )
    outcome.decisions = await _decisions_of(session, episode)
    if outcome.decisions:
        # Through the theses service rather than the decision's own relationship, so the
        # premises and their judgements arrive loaded: this runs inside a web request as
        # well as a test session, and a lazy load there is not a query but an error.
        outcome.thesis = await thesis_service.thesis_of(
            session, outcome.decisions[0].thesis_id, user_id=episode.portfolio.user_id
        )
    outcome.findings = await _findings_while_open(session, episode, outcome.thesis)

    costs: list[Quantity] = []
    effects: list[Quantity] = []
    try:
        for trade in episode.trades:
            if trade.kind is TransactionKind.SPLIT:
                continue
            on = trade.trade_date
            money = Unit.currency(trade.currency)
            fees = Quantity.of(trade.fees, money, source=source_of(trade, "fees"))
            if trade.kind is TransactionKind.BUY:
                costs.append(
                    await in_base(
                        session,
                        context,
                        amount=acquisition_cost_of(context, trade),
                        base=base,
                        as_of=on,
                    )
                )
            elif trade.kind is TransactionKind.SELL and trade.price is not None:
                effect = calc.dealt_cash_effect(
                    context,
                    quantity=movement_of(trade),
                    price=Quantity.of(
                        trade.price, money / calc.SHARES, source=source_of(trade, "price")
                    ),
                    fees=fees,
                )
                effects.append(await in_base(session, context, amount=effect, base=base, as_of=on))
            elif trade.kind is TransactionKind.DIVIDEND:
                amount = Quantity.of(trade.quantity, money, source=source_of(trade, "amount"))
                effect = calc.cash_movement(context, amount=amount, fees=fees)
                effects.append(await in_base(session, context, amount=effect, base=base, as_of=on))
        outcome.cost = episode_cost(context, costs=costs)
        outcome.proceeds = episode_proceeds(context, effects=effects)
        outcome.realised_return = realised_return(
            context, proceeds=outcome.proceeds, cost=outcome.cost
        )
    except CalculationError as problem:
        outcome.problem = str(problem)
        outcome.cost = outcome.proceeds = outcome.realised_return = None
    return outcome


async def _decisions_of(session: AsyncSession, episode: Episode) -> list[Decision]:
    """The decisions the episode's trades carried out, then any hold or pass on the same
    thesis while the position was open. Oldest first."""
    ids = {trade.decision_id for trade in episode.trades if trade.decision_id is not None}
    carried: list[Decision] = []
    if ids:
        carried = list(
            await session.scalars(
                select(Decision)
                .join(Judgement, Judgement.id == Decision.judgement_id)
                .options(selectinload(Decision.thesis), selectinload(Decision.transactions))
                .where(Decision.judgement_id.in_(ids))
                .order_by(Judgement.held_at)
            )
        )
    if not carried:
        return []
    thesis_id = carried[0].thesis_id
    opened = datetime.combine(episode.opened_on, datetime.min.time(), tzinfo=UTC)
    closed = datetime.combine(episode.closed_on, datetime.max.time(), tzinfo=UTC)
    others = list(
        await session.scalars(
            select(Decision)
            .join(Judgement, Judgement.id == Decision.judgement_id)
            .options(selectinload(Decision.thesis), selectinload(Decision.transactions))
            .where(
                Decision.thesis_id == thesis_id,
                Decision.judgement_id.not_in(ids),
                Judgement.held_at >= opened,
                Judgement.held_at <= closed,
            )
            .order_by(Judgement.held_at)
        )
    )
    return sorted(carried + others, key=lambda row: row.judgement.held_at)


async def _findings_while_open(
    session: AsyncSession, episode: Episode, thesis: Thesis | None
) -> list[Finding]:
    if thesis is None:
        return []
    opened = datetime.combine(episode.opened_on, datetime.min.time(), tzinfo=UTC)
    closed = datetime.combine(episode.closed_on, datetime.max.time(), tzinfo=UTC)
    rows = await session.scalars(
        select(Finding)
        .options(selectinload(Finding.premise))
        .where(
            Finding.thesis_id == thesis.id,
            Finding.kind == FindingKind.READING,
            Finding.created_at >= opened,
            Finding.created_at <= closed,
        )
        .order_by(Finding.created_at)
    )
    return list(rows)


# -- The pass ----------------------------------------------------------------------------------


async def latest_pass_for(session: AsyncSession, *, episode: Episode) -> Job | None:
    """The most recent reviewer pass over this episode, if one ran."""
    found: Job | None = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .options(selectinload(Job.steps))
        .where(
            WorkOrder.tool == TOOL,
            WorkOrder.subject_kind == SUBJECT_POSITION,
            WorkOrder.subject_id == episode.security.id,
            WorkOrder.as_of_date == episode.closed_on,
            WorkOrder.user_id == episode.portfolio.user_id,
        )
        .order_by(Job.started_at.desc().nullslast())
        .limit(1)
    )
    return found


async def run_review(
    session: AsyncSession,
    *,
    settings: Settings,
    provider: LLMProvider,
    router: Router,
    store: ArtefactStore,
    user: User,
    episode: Episode,
) -> Job:
    """One reviewer pass over one closed position, leaving its draft on the step.

    The outcome is computed and its ledger persisted against this job before the model is
    asked anything; the draft is stored as the step's output, with everything the page
    needs to show beside it. A cost refusal fails the job with the reason and leaves the
    episode unreviewed rather than pausing for a decision.

    Raises:
        ConflictError: If the episode's book is not this person's, or it is already
            reviewed.
    """
    if episode.portfolio.user_id != user.id:
        message = "A position is reviewed by the person whose book it is in."
        raise ConflictError(message, context={"security_id": str(episode.security.id)})
    if await review_for_episode(session, episode=episode) is not None:
        message = "This position was already reviewed, and the review stands."
        raise ConflictError(message, context={"security_id": str(episode.security.id)})

    order, job, step = await _open_pass(session, settings=settings, user=user, episode=episode)

    ledger = new_context()
    outcome = await outcome_for(session, ledger, episode=episode)
    if ledger.records:
        await persist_context(session, ledger, job_id=job.id)
    company = (
        await session.get(Company, episode.security.company_id)
        if episode.security.company_id
        else None
    )
    premises = list(outcome.thesis.premises) if outcome.thesis is not None else []
    payload = _review_input(episode, outcome, company=company, premises=premises)
    output: dict[str, Any] = {
        "episode": {
            "security_id": str(episode.security.id),
            "ticker": episode.security.ticker,
            "exchange": episode.security.exchange,
            "opened_on": episode.opened_on.isoformat(),
            "closed_on": episode.closed_on.isoformat(),
            "portfolio_id": str(episode.portfolio.id),
        },
        "outcome": outcome.as_json(ledger),
        "thesis_id": str(outcome.thesis.id) if outcome.thesis is not None else None,
        "thesis_title": outcome.thesis.title if outcome.thesis is not None else "",
        "decisions": [row.model_dump(mode="json") for row in payload.decisions],
        "premises": [row.model_dump(mode="json") for row in payload.premises],
        "findings": [row.model_dump(mode="json") for row in payload.findings],
        "proposal": None,
    }

    context = AgentContext(
        session=session,
        provider=provider,
        router=router,
        settings=settings,
        store=store,
        job_step=step,
    )
    try:
        draft = await PostTradeReviewerAgent().run(context, payload)
    except BudgetExceededError as refused:
        return await _stop_pass(
            session, refused, order=order, job=job, step=step, output=output, context=context
        )

    known = {row.premise_id for row in payload.premises}
    kept = [row for row in draft.verdicts if row.premise_id in known]
    if len(kept) != len(draft.verdicts):
        _log.warning(
            "review.verdicts_dropped",
            job_id=str(job.id),
            dropped=len(draft.verdicts) - len(kept),
        )
    output["proposal"] = ReviewDraft(
        verdicts=kept,
        process_quality=draft.process_quality,
        basis=draft.basis,
        lessons=draft.lessons,
    ).model_dump(mode="json")
    step.status = JobStatus.SUCCEEDED
    step.finished_at = datetime.now(UTC)
    step.cost_gbp = context.spend_gbp
    step.output_ref = output
    job.status = JobStatus.SUCCEEDED
    job.finished_at = step.finished_at
    job.total_cost_gbp = context.spend_gbp
    order.status = RequestStatus.COMPLETED
    await session.flush()
    _log.info(
        "review.proposed",
        job_id=str(job.id),
        security=episode.security.provider_symbol,
        closed_on=episode.closed_on.isoformat(),
        quality=draft.process_quality.value,
        spend_gbp=str(context.spend_gbp),
    )
    return job


async def _open_pass(
    session: AsyncSession, *, settings: Settings, user: User, episode: Episode
) -> tuple[WorkOrder, Job, JobStep]:
    """The pass's own run root: a work order on the position, one job, one step."""
    started = datetime.now(UTC)
    order = WorkOrder(
        user_id=user.id,
        tool=TOOL,
        subject_kind=SUBJECT_POSITION,
        subject_id=episode.security.id,
        as_of_date=episode.closed_on,
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
        input_hash=sha256_hex(canonical_json({"episode": episode.key, "job_id": str(job.id)})),
        started_at=started,
    )
    session.add(step)
    await session.flush()
    return order, job, step


async def _stop_pass(
    session: AsyncSession,
    refused: BudgetExceededError,
    *,
    order: WorkOrder,
    job: Job,
    step: JobStep,
    output: dict[str, Any],
    context: AgentContext,
) -> Job:
    """A cost refusal fails the pass with its reason and keeps what code computed (ADR 0078).

    The outcome stays on the step so the page can show it; the episode stays unreviewed so
    it can be run again. Nothing pauses for a decision nobody is awake to make.
    """
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
    _log.warning("review.stopped", job_id=str(job.id), scope=refused.context.get("scope"))
    return job


def _review_input(
    episode: Episode,
    outcome: Outcome,
    *,
    company: Company | None,
    premises: list[Premise],
) -> ReviewInput:
    return ReviewInput(
        company_name=company.name
        if company is not None
        else episode.security.name or episode.security.ticker,
        ticker=episode.security.ticker,
        thesis_title=outcome.thesis.title if outcome.thesis is not None else "",
        decisions=[
            DecisionUnderReview(
                decision_id=str(row.judgement_id),
                action=ACTION_WORDS[row.action],
                statement=row.statement,
                basis=row.judgement.basis,
                decided_on=row.judgement.held_at.date().isoformat(),
                size_statement=row.size_statement or "",
                horizon_months=row.horizon_months,
                exit_plan=row.exit_plan or "",
                carried_out_by=sum(
                    1 for trade in episode.trades if trade.decision_id == row.judgement_id
                ),
            )
            for row in outcome.decisions
        ],
        premises=[
            PremiseUnderReview(
                premise_id=str(row.judgement_id),
                statement=row.statement,
                basis=row.judgement.basis,
                predicate=predicate_sentence(row),
                review_by=row.review_by.isoformat() if row.review_by else "",
                withdrawn=row.judgement.is_withdrawn,
                withdrawn_reason=row.judgement.withdrawn_reason or "",
            )
            for row in premises
        ],
        findings=[
            FindingWhileOpen(
                premise_id=str(row.judgement_id),
                status=row.status.value if row.status else "",
                justification=row.justification,
                raised_on=row.created_at.date().isoformat(),
            )
            for row in outcome.findings
            if row.judgement_id is not None
        ],
        outcome=OutcomeFigures(
            opened_on=outcome.opened_on.isoformat(),
            closed_on=outcome.closed_on.isoformat(),
            holding_days=outcome.holding_days,
            intended_horizon_months=outcome.intended_horizon_months,
            realised_return=_plain(outcome.realised_return) or outcome.problem or "not computed",
            currency=outcome.currency,
            cost=_plain(outcome.cost),
            proceeds=_plain(outcome.proceeds),
        ),
    )


@dataclass(frozen=True, slots=True)
class Proposal:
    """A reviewer pass as the page reads it back off its step."""

    job: Job
    output: dict[str, Any]

    @property
    def draft(self) -> ReviewDraft | None:
        raw = self.output.get("proposal")
        return ReviewDraft.model_validate(raw) if isinstance(raw, dict) else None

    @property
    def failed(self) -> bool:
        return self.job.status is JobStatus.FAILED

    @property
    def reason(self) -> str:
        return str((self.job.error or {}).get("message") or "")


async def proposal_of(
    session: AsyncSession, job_id: uuid.UUID, *, user_id: uuid.UUID
) -> Proposal | None:
    """One pass of this person's, with its step output, or ``None``."""
    job = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .options(selectinload(Job.steps))
        .where(Job.id == job_id, WorkOrder.tool == TOOL, WorkOrder.user_id == user_id)
    )
    if job is None:
        return None
    step = next((row for row in job.steps if row.step_key == STEP_KEY), None)
    output = step.output_ref if step is not None and isinstance(step.output_ref, dict) else {}
    return Proposal(job=job, output=output)


# -- Confirming ---------------------------------------------------------------------------------


async def confirm_review(
    session: AsyncSession,
    *,
    user: User,
    proposal: Proposal,
    process_quality: ProcessQuality,
    basis: str,
    lessons: str,
    verdicts: dict[uuid.UUID, tuple[PremiseVerdict, str]],
) -> Review:
    """The operator's judgement, with the reviewer's draft kept beside it.

    Raises:
        ValidationError: If the basis is blank, or the pass has no outcome to review.
        ConflictError: If the position was already reviewed.
    """
    if not basis.strip():
        message = (
            "Confirming a review needs a basis: what the quality rests on, in the reviewer's "
            "words if you agree with them or your own if you do not."
        )
        raise ValidationError(message, context={"field": "basis"})
    episode = proposal.output.get("episode") or {}
    outcome = proposal.output.get("outcome") or {}
    if not episode or not outcome:
        message = "This pass recorded no outcome to review."
        raise ValidationError(message, context={"job_id": str(proposal.job.id)})
    security_id = uuid.UUID(str(episode["security_id"]))
    portfolio_id = uuid.UUID(str(episode["portfolio_id"]))
    closed_on = date.fromisoformat(str(episode["closed_on"]))
    existing = await session.scalar(
        select(Review).where(
            Review.portfolio_id == portfolio_id,
            Review.security_id == security_id,
            Review.closed_on == closed_on,
        )
    )
    if existing is not None:
        message = "This position was already reviewed, and the review stands."
        raise ConflictError(message, context={"review_id": str(existing.judgement_id)})

    judgement = Judgement(
        kind=JudgementKind.REVIEW,
        held_by=user.email,
        held_at=datetime.now(UTC),
        basis=basis.strip(),
    )
    session.add(judgement)
    await session.flush()
    thesis_id = proposal.output.get("thesis_id")
    review = Review(
        judgement_id=judgement.id,
        portfolio_id=portfolio_id,
        security_id=security_id,
        opened_on=date.fromisoformat(str(episode["opened_on"])),
        closed_on=closed_on,
        thesis_id=uuid.UUID(str(thesis_id)) if thesis_id else None,
        job_id=proposal.job.id,
        process_quality=process_quality,
        lessons=lessons.strip(),
        outcome=outcome,
        proposal=proposal.output.get("proposal"),
    )
    session.add(review)
    await session.flush()
    for position, premise in enumerate(proposal.output.get("premises") or [], start=1):
        premise_id = uuid.UUID(str(premise["premise_id"]))
        verdict, note = verdicts.get(premise_id, (PremiseVerdict.UNTESTED, ""))
        session.add(
            ReviewVerdict(
                review_id=review.judgement_id,
                premise_id=premise_id,
                position=position,
                statement=str(premise["statement"]),
                verdict=verdict,
                note=note.strip(),
            )
        )
    await session.flush()
    await session.refresh(judgement)
    loaded = await review_of(session, review.judgement_id, user_id=user.id)
    assert loaded is not None

    await _record(
        session,
        actor=user.email,
        event_type="review.confirmed",
        review_id=review.judgement_id,
        job_id=proposal.job.id,
        payload={
            "review_id": str(review.judgement_id),
            "security_id": str(security_id),
            "closed_on": closed_on.isoformat(),
            "process_quality": process_quality.value,
            "basis": judgement.basis,
            "verdicts": {str(key): value[0].value for key, value in verdicts.items()},
            "proposed_quality": (proposal.draft.process_quality.value if proposal.draft else None),
            "outcome": outcome,
        },
    )
    _log.info(
        "review.confirmed",
        review_id=str(review.judgement_id),
        quality=process_quality.value,
        actor=user.email,
    )
    return loaded


# -- Reading -----------------------------------------------------------------------------------


def _loaded() -> Any:
    return (selectinload(Review.verdicts),)


async def reviews_for(session: AsyncSession, *, user_id: uuid.UUID) -> list[Review]:
    rows = await session.scalars(
        select(Review)
        .join(Portfolio, Portfolio.id == Review.portfolio_id)
        .options(*_loaded())
        .where(Portfolio.user_id == user_id)
        .order_by(Review.closed_on.desc())
    )
    return list(rows)


async def review_of(
    session: AsyncSession, review_id: uuid.UUID, *, user_id: uuid.UUID
) -> Review | None:
    found: Review | None = await session.scalar(
        select(Review)
        .join(Portfolio, Portfolio.id == Review.portfolio_id)
        .options(*_loaded())
        .where(Review.judgement_id == review_id, Portfolio.user_id == user_id)
    )
    return found


async def review_for_episode(session: AsyncSession, *, episode: Episode) -> Review | None:
    found: Review | None = await session.scalar(
        select(Review)
        .options(*_loaded())
        .where(
            Review.portfolio_id == episode.portfolio.id,
            Review.security_id == episode.security.id,
            Review.closed_on == episode.closed_on,
        )
    )
    return found


@dataclass(frozen=True, slots=True)
class EpisodeState:
    """An episode and where its review stands."""

    episode: Episode
    review: Review | None
    proposal: Job | None

    @property
    def state(self) -> str:
        if self.review is not None:
            return "reviewed"
        if self.proposal is None:
            return "unreviewed"
        if self.proposal.status is JobStatus.FAILED:
            return "stopped"
        return "proposed"


async def states_for(session: AsyncSession, *, portfolio: Portfolio) -> list[EpisodeState]:
    """Every closed position with its review, its latest pass, or neither."""
    states: list[EpisodeState] = []
    for episode in await closed_episodes(session, portfolio=portfolio):
        review = await review_for_episode(session, episode=episode)
        proposal = None if review is not None else await latest_pass_for(session, episode=episode)
        states.append(EpisodeState(episode=episode, review=review, proposal=proposal))
    return states


# -- Analytics ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Part:
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class Statistic:
    """A breakdown that cannot be built without its denominator (ADR 0081).

    ``count`` is required and there is no constructor without it; a template has no way to
    show a proportion without the number it is a proportion of. Below :data:`MINIMUM_SAMPLE`
    the parts are shown as a tally rather than as percentages.
    """

    label: str
    count: int
    parts: tuple[Part, ...]

    def __post_init__(self) -> None:
        if sum(part.count for part in self.parts) != self.count:
            message = f"The parts of {self.label!r} do not account for its total."
            raise ValueError(message)

    @property
    def is_a_finding(self) -> bool:
        return self.count >= MINIMUM_SAMPLE


@dataclass(frozen=True, slots=True)
class Analytics:
    reviewed: int
    cells: Statistic
    qualities: Statistic
    verdicts: Statistic
    horizons: Statistic
    written_down: Statistic
    agreement: Statistic


async def analytics_for(session: AsyncSession, *, user_id: uuid.UUID) -> Analytics:
    """What the reviewed positions have in common, every statistic with its ``n``."""
    reviews = await reviews_for(session, user_id=user_id)
    return Analytics(
        reviewed=len(reviews),
        cells=_cells(reviews),
        qualities=_counted(
            "Process quality",
            reviews,
            key=lambda row: row.process_quality.value,
            order=[member.value for member in ProcessQuality],
        ),
        verdicts=_counted(
            "Premise verdicts",
            [verdict for row in reviews for verdict in row.verdicts],
            key=lambda row: row.verdict.value,
            order=[member.value for member in PremiseVerdict],
        ),
        horizons=_counted(
            "Holding period against the intended horizon",
            reviews,
            key=_horizon_word,
            order=["no horizon stated", "closed early", "closed near the horizon", "held past it"],
        ),
        written_down=_counted(
            "A decision written before the trade",
            reviews,
            key=lambda row: "yes" if row.thesis_id is not None else "no",
            order=["yes", "no"],
        ),
        agreement=_counted(
            "Confirmed as the reviewer proposed",
            reviews,
            key=lambda row: (
                "no proposal"
                if not row.proposal
                else (
                    "agreed"
                    if row.proposal.get("process_quality") == row.process_quality.value
                    else "amended"
                )
            ),
            order=["agreed", "amended", "no proposal"],
        ),
    )


def _cells(reviews: list[Review]) -> Statistic:
    """The four cells: process quality against the sign of the realised return."""
    labels = {
        (True, True): "sound process, gain",
        (True, False): "sound process, loss",
        (False, True): "flawed or questionable process, gain",
        (False, False): "flawed or questionable process, loss",
    }
    counts = dict.fromkeys(labels.values(), 0)
    unknown = 0
    for row in reviews:
        raw = row.outcome.get("realised_return")
        if not raw:
            unknown += 1
            continue
        sound = row.process_quality is ProcessQuality.SOUND
        gained = Decimal(str(raw)) >= 0
        counts[labels[(sound, gained)]] += 1
    parts = [Part(label, count) for label, count in counts.items()]
    if unknown:
        parts.append(Part("outcome not computed", unknown))
    return Statistic("Process against outcome", len(reviews), tuple(parts))


def _counted(label: str, rows: list[Any], *, key: Any, order: list[str]) -> Statistic:
    counts = dict.fromkeys(order, 0)
    for row in rows:
        counts[key(row)] = counts.get(key(row), 0) + 1
    return Statistic(label, len(rows), tuple(Part(name, count) for name, count in counts.items()))


def _horizon_word(row: Review) -> str:
    intended = row.outcome.get("intended_horizon_months")
    if not intended:
        return "no horizon stated"
    held_days = int(row.outcome.get("holding_days") or 0)
    intended_days = int(intended) * 30
    if held_days < intended_days * Decimal("0.75"):
        return "closed early"
    if held_days > intended_days * Decimal("1.25"):
        return "held past it"
    return "closed near the horizon"


def _plain(quantity: Quantity | None) -> str:
    return plain_decimal(quantity.value if quantity is not None else None)


# -- The chain ----------------------------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    review_id: uuid.UUID,
    job_id: uuid.UUID | None,
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            job_id=job_id,
            subject_kind="review",
            subject_id=review_id,
        )
    )
    await session.flush()
