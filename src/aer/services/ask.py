"""Ask: a question over a company's record, answered at the tier it needs (F6, ADR 0130).

Three tiers, and the tier is decided before anything runs by :func:`aer.core.ask.resolve`
over what this module read from the record. **Tier 1** strikes the current report's base
case again on the question's own ledger with one input changed, and calls no model.
**Tier 2** deals the ask reader what the record holds within a token budget, runs one
metered pass, and keeps the answer only if every check in :func:`_judge` passes. **Tier 3**
is priced from the routed models' published rates and recorded; its acquisition lands after
F4, and until then the page says researching it is not yet available here.

Every question is its own run root (ADR 0072): a work order on the company with the tool
``ask``, one job, one step. Its calculations, its model call and its cost rows are written
under that job, the calculation walk checks ownership through it, and a cost refusal fails
the job with the reason rather than pausing for a decision nobody is awake to make
(ADR 0078).

**No answer from the model's own knowledge**, enforced structurally rather than asked for:
tier 1 makes no call; tier 2's reader sees the pack and nothing else, a citation outside
the pack is dropped, an answer with none is discarded, a numeral no dealt figure or cited
excerpt reads as discards the answer, and a discarded answer is a refusal with the third
tier's price.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.agents.ask_reader import AskAnswer, AskInput, AskReaderAgent
from aer.agents.base import AgentContext
from aer.calc.dcf import DcfResult
from aer.calc.units import Quantity, SourceKind, SourceRef, Unit
from aer.core.ask import (
    DISCOUNT_RATE,
    Change,
    HeldRecord,
    Resolution,
    Tier,
    resolve,
    subject_tokens,
)
from aer.core.enums import JobStatus, RequestStatus
from aer.core.figures import numeral_tokens, reads_as
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.sectors import (
    ValuationModel,
    mandate_for,
    unclassified_mandate,
)
from aer.db.models import (
    Calculation,
    Company,
    Extraction,
    FinancialFact,
    Job,
    JobStep,
    Question,
    Report,
    ResearchRequest,
    SourceDocument,
    User,
    WorkOrder,
)
from aer.errors import AerError, BudgetExceededError, ValidationError
from aer.providers.costs import estimate_gbp, price_web_search
from aer.render import display
from aer.services import configuration, provenance
from aer.services.analysis import ANNUAL, analyse_company
from aer.services.assumptions import confirmed_values
from aer.services.calculations import indexed_calculations, new_context, persist_context
from aer.services.extractions import may_print_excerpt
from aer.services.mandate import mandate_of
from aer.services.reports import current_report
from aer.services.sectors import confirmed_classification
from aer.services.valuation import run_valuation
from aer.services.valuation_run import base_case_inputs, latest_period, prior_period
from aer.version import git_sha
from aer.workflow.engine import BudgetGuard

if TYPE_CHECKING:
    from aer.config import Settings
    from aer.providers.protocol import LLMProvider
    from aer.providers.router import Router
    from aer.storage.protocol import ArtefactStore

__all__ = [
    "STEP_KEY",
    "TOKEN_BUDGET",
    "TOOL",
    "WORKFLOW_VERSION",
    "Estimate",
    "Note",
    "ask",
    "companies_with_a_record",
    "held_record",
    "note_of",
    "question_of",
    "questions_for",
    "research_estimate",
]

_log = structlog.get_logger("aer.services.ask")

TOOL: Final = "ask"
SUBJECT_COMPANY: Final = "company"
WORKFLOW_VERSION: Final = "ask_v1"
STEP_KEY: Final = "answer"

# What a tier-2 pass may be dealt, in tokens. The specification's price for the tier is
# pennies, and this is what makes it so: forty thousand tokens of the workhorse model is a
# little over a tenth of a pound. The ranking below decides what fits.
TOKEN_BUDGET: Final = 40_000
_CHARS_PER_TOKEN: Final = 4
_EXCERPT_POOL: Final = 400
_FACT_POOL: Final = 300
_VOCABULARY_POOL: Final = 600
_ANSWER_TOKENS: Final = 4_000
# Too short to carry anything a question could rest on: a heading, a signature line.
_SUBSTANTIVE_CHARS: Final = 80

# How the third tier is priced before it exists to be run: searches for the question and
# for each thing it names the record lacks, a worker turn per search, and the reading pass.
_MAX_SEARCHES: Final = 4
_HITS_PER_SEARCH: Final = 5
_WORKER_INPUT_TOKENS: Final = 12_000
_WORKER_OUTPUT_TOKENS: Final = 2_000
_PENNY: Final = Decimal("0.01")

_DEALT_AS: Final[dict[str, str]] = {
    "source": "documents",
    "fact": "facts",
    "calculation": "calculations",
    "excerpt": "excerpts",
}
_SECTOR_GATE: Final = "gate:SECTOR_SPECIALIST"
_VALUE_STEP: Final = "value"
_PRICES_STEP: Final = "acquire_prices"
_DEFAULT_YEARS: Final = 5

_WORD: Final = re.compile(r"[a-z][a-z\-]{3,}")
_CAPITALISED_WORD: Final = re.compile(r"\b[A-Z][A-Za-z]{2,}\b")
_PER_YEAR: Final = re.compile(r"^(?P<name>[a-z][a-z0-9_]*?)_y(?P<year>[1-9][0-9]*)$")
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "what",
        "does",
        "about",
        "their",
        "there",
        "have",
        "with",
        "from",
        "this",
        "that",
        "they",
        "were",
        "been",
        "will",
        "would",
        "could",
        "should",
        "which",
        "when",
        "where",
        "much",
        "many",
        "into",
        "than",
        "then",
        "company",
        "companies",
        "year",
        "years",
        "say",
        "said",
        "says",
    }
)


# -- The record, as code reads it ------------------------------------------------------------


async def companies_with_a_record(session: AsyncSession, *, user: User) -> list[Company]:
    """The companies this account holds anything for: a run's subject, or a document about.

    Scoped by the account's work orders (ADR 0120): a company somebody else researched is
    not in this account's record.
    """
    subjects = select(WorkOrder.subject_id).where(
        WorkOrder.user_id == user.id,
        WorkOrder.subject_kind == SUBJECT_COMPANY,
        WorkOrder.subject_id.is_not(None),
    )
    # A research request names its company once the acquire step has resolved it, on its
    # own row rather than on the work order's subject.
    requested = (
        select(ResearchRequest.company_id)
        .join(WorkOrder, WorkOrder.id == ResearchRequest.id)
        .where(WorkOrder.user_id == user.id, ResearchRequest.company_id.is_not(None))
    )
    documented = (
        select(SourceDocument.company_id)
        .join(WorkOrder, WorkOrder.id == SourceDocument.work_order_id)
        .where(WorkOrder.user_id == user.id, SourceDocument.company_id.is_not(None))
    )
    rows = await session.scalars(
        select(Company)
        .where(or_(Company.id.in_(subjects), Company.id.in_(requested), Company.id.in_(documented)))
        .order_by(Company.name)
    )
    return list(rows)


def _about(user: User, company: Company) -> Any:
    """The condition on a work order that it was raised about this company."""
    requested = (
        select(ResearchRequest.id)
        .join(WorkOrder, WorkOrder.id == ResearchRequest.id)
        .where(WorkOrder.user_id == user.id, ResearchRequest.company_id == company.id)
    )
    return or_(
        (WorkOrder.subject_kind == SUBJECT_COMPANY) & (WorkOrder.subject_id == company.id),
        WorkOrder.id.in_(requested),
    )


async def held_record(session: AsyncSession, *, user: User, company: Company) -> HeldRecord:
    """What the record holds for this company, for the resolver to decide the tier over."""
    documents = await _documents(session, user=user, company=company)
    titles = tuple((row.title or row.url).lower() for row in documents)

    fact_rows = await session.execute(
        select(FinancialFact.fiscal_year, FinancialFact.fiscal_period)
        .where(FinancialFact.company_id == company.id)
        .distinct()
    )
    years: set[int] = set()
    has_quarterly = False
    for fiscal_year, fiscal_period in fact_rows:
        if fiscal_year is not None:
            years.add(int(fiscal_year))
        if fiscal_period is not None and fiscal_period != ANNUAL:
            has_quarterly = True
    for row in documents:
        published = row.publication_date_latest or row.publication_date
        if published is not None:
            years.add(published.year)

    others = await session.scalars(
        select(Company.name)
        .join(SourceDocument, SourceDocument.company_id == Company.id)
        .join(WorkOrder, WorkOrder.id == SourceDocument.work_order_id)
        .where(
            WorkOrder.user_id == user.id,
            _about(user, company),
            SourceDocument.company_id != company.id,
        )
        .distinct()
    )
    other_names = subject_tokens(*others)

    vocabulary: set[str] = set()
    for title in titles:
        vocabulary.update(word.lower() for word in _CAPITALISED_WORD.findall(title.title()))
    if documents:
        excerpts = await session.scalars(
            select(Extraction.excerpt)
            .where(Extraction.source_document_id.in_([row.id for row in documents]))
            .order_by(Extraction.created_at)
            .limit(_VOCABULARY_POOL)
        )
        for excerpt in excerpts:
            vocabulary.update(word.lower() for word in _CAPITALISED_WORD.findall(excerpt))

    report = await current_report(session, company_id=company.id)
    variable: set[str] = set()
    if report is not None and await _reached_a_valuation(session, report.job_id):
        names = await confirmed_values(session, report.request_id)
        for name in names:
            per_year = _PER_YEAR.match(name)
            variable.add(per_year["name"] if per_year is not None else name)
        variable.add(DISCOUNT_RATE)

    return HeldRecord(
        subject_names=subject_tokens(company.name, company.ticker),
        variable_inputs=frozenset(variable),
        document_titles=titles,
        years=frozenset(years),
        has_quarterly=has_quarterly,
        other_names=other_names,
        vocabulary=frozenset(vocabulary),
    )


async def _documents(
    session: AsyncSession, *, user: User, company: Company
) -> list[SourceDocument]:
    """The admissible documents this account holds about the company, across every run.

    A document is about the company when it says so, or when it carries no company and
    was fetched under a run whose subject is this one — `visible_sources`'s own reading of
    a null, applied across work orders rather than within one.
    """
    rows = await session.scalars(
        select(SourceDocument)
        .join(WorkOrder, WorkOrder.id == SourceDocument.work_order_id)
        .where(
            WorkOrder.user_id == user.id,
            or_(
                SourceDocument.company_id == company.id,
                SourceDocument.company_id.is_(None) & _about(user, company),
            ),
        )
        .order_by(SourceDocument.retrieved_at, SourceDocument.id)
    )
    return [row for row in rows if row.is_admissible]


async def _reached_a_valuation(session: AsyncSession, job_id: uuid.UUID) -> bool:
    count = await session.scalar(
        select(func.count())
        .select_from(Calculation)
        .where(Calculation.job_id == job_id, Calculation.name == "value_per_share")
    )
    return bool(count)


# -- Asking -----------------------------------------------------------------------------------


async def ask(
    session: AsyncSession,
    *,
    settings: Settings,
    provider: LLMProvider,
    router: Router,
    store: ArtefactStore,
    user: User,
    company: Company,
    text: str,
) -> Question:
    """Resolve a question's tier, record it, and answer it where the tier runs unasked.

    Raises:
        ValidationError: A blank question.
    """
    asked = re.sub(r"\s+", " ", text).strip()
    if not asked:
        message = "A question has to say something. Nothing was asked."
        raise ValidationError(message, context={"company_id": str(company.id)})

    record = await held_record(session, user=user, company=company)
    resolution = resolve(asked, record)
    report = await current_report(session, company_id=company.id)

    question = Question(
        user_id=user.id,
        company_id=company.id,
        report_id=report.id if report is not None else None,
        question=asked,
        tier=int(resolution.tier),
        tier_rationale=resolution.rationale,
    )
    session.add(question)
    await session.flush()

    if resolution.tier is Tier.RECOMPUTE and resolution.change is not None and report is not None:
        await _recompute(
            session,
            settings=settings,
            user=user,
            company=company,
            question=question,
            report=report,
            change=resolution.change,
        )
    elif resolution.tier is Tier.RE_READ:
        await _re_read(
            session,
            settings=settings,
            provider=provider,
            router=router,
            store=store,
            user=user,
            company=company,
            question=question,
            report=report,
            resolution=resolution,
        )
    else:
        _price(question, resolution, router=router, settings=settings, company=company)

    await session.flush()
    _log.info(
        "ask.asked",
        question_id=str(question.id),
        company=company.ticker,
        tier=question.tier,
        answered=question.is_answered,
    )
    return question


async def questions_for(session: AsyncSession, *, user: User, limit: int = 100) -> list[Question]:
    """This account's questions, newest first."""
    rows = await session.scalars(
        select(Question)
        .where(Question.user_id == user.id)
        .options(selectinload(Question.company))
        .order_by(Question.asked_at.desc())
        .limit(limit)
    )
    return list(rows)


async def question_of(
    session: AsyncSession, question_id: uuid.UUID, *, user_id: uuid.UUID
) -> Question | None:
    """One question, when it is this account's."""
    found: Question | None = await session.scalar(
        select(Question)
        .where(Question.id == question_id, Question.user_id == user_id)
        .options(selectinload(Question.company), selectinload(Question.job))
    )
    return found


# -- Tier 1: the run's base case, struck again with one input changed -------------------------


async def _recompute(
    session: AsyncSession,
    *,
    settings: Settings,
    user: User,
    company: Company,
    question: Question,
    report: Report,
    change: Change,
) -> None:
    """Strike the base case as held and as asked on the question's own ledger.

    A run the model cannot be re-struck for — a bank's residual income, a run whose
    classification nobody confirmed, a filing missing a line — is refused by name and the
    question says so; it does not fall through to a tier that would spend.
    """
    order, job, step = await _open_pass(session, settings=settings, user=user, company=company)
    question.job_id = job.id
    run = await session.get(Job, report.job_id)
    request = await mandate_of(session, run) if run is not None else None
    if run is None or request is None:  # pragma: no cover -- a report cannot exist without its run
        await _fail(session, order=order, job=job, step=step, reason="The report has no run.")
        question.content = {"kind": "refused", "reason": "The report has no run to re-strike."}
        return

    try:
        profile, _ = await confirmed_classification(session, run)
        analysis = await analyse_company(
            session, new_context(), company_id=company.id, profile=profile
        )
        latest = latest_period(analysis)
        if latest is None:
            message = (
                "No annual period could be assembled from this company's filings, so there "
                "is no base year to forecast from."
            )
            raise ValidationError(message, context={"company_id": str(company.id)})
        mandate = (
            unclassified_mandate(ValuationModel.DCF_FCFF, subject=request.ticker)
            if profile is None
            else mandate_for(
                ValuationModel.DCF_FCFF,
                subject=request.ticker,
                profile=profile,
                confirmed_by=_SECTOR_GATE,
            )
        )
        years = await _forecast_years(session, run.id)
        capitalisation = await _market_capitalisation(
            session, run.id, currency=request.base_currency
        )
        values = dict(await confirmed_values(session, request.id))

        ledger = new_context()
        _, held_inputs = base_case_inputs(
            ledger,
            values,
            latest=latest,
            prior=prior_period(analysis),
            years=years,
            market_capitalisation=capitalisation,
        )
        as_held = await run_valuation(
            session,
            job_id=job.id,
            inputs=held_inputs,
            mandate=mandate,
            context=ledger,
            case="as_held",
        )
        if change.parameter == DISCOUNT_RATE:
            moved_from = held_inputs.wacc
            changed_inputs = dataclass_replace(
                held_inputs, wacc=_moved(moved_from, change, question=question)
            )
        else:
            moved_from, changed_values = _apply(values, change, question=question)
            _, changed_inputs = base_case_inputs(
                ledger,
                changed_values,
                latest=latest,
                prior=prior_period(analysis),
                years=years,
                market_capitalisation=capitalisation,
            )
        as_asked = await run_valuation(
            session,
            job_id=job.id,
            inputs=changed_inputs,
            mandate=mandate,
            context=ledger,
            case="as_asked",
        )
        await persist_context(session, ledger, job_id=job.id)
    except AerError as refused:
        await _fail(session, order=order, job=job, step=step, reason=str(refused))
        question.content = {"kind": "refused", "reason": str(refused)}
        return

    moved_to = (
        changed_inputs.wacc
        if change.parameter == DISCOUNT_RATE
        else _first_changed(changed_values, change.parameter)
    )
    style = await configuration.effective_house_style(session)
    words = change.words
    from_text = display.figure(moved_from.value, unit="pure", label=words, style=style)
    to_text = display.figure(moved_to.value, unit="pure", label=words, style=style)
    figures = [
        *_figure_rows(as_asked, f"with the {words} at {to_text}", style=style),
        *_figure_rows(as_held, "with the inputs as the report holds them", style=style),
        _figure_row(f"The {words}, as the report holds it", moved_from, style=style),
        _figure_row(f"The {words}, as asked", moved_to, style=style),
    ]
    every_year = "" if change.parameter == DISCOUNT_RATE else " in every forecast year"
    answer = (
        f"With the {words} at {to_text} rather than {from_text}{every_year}, the discounted "
        f"cash flow gives a value per share of {figures[0]['rendered']} under Gordon growth "
        f"and {figures[1]['rendered']} under the exit multiple, against "
        f"{figures[2]['rendered']} and {figures[3]['rendered']} with the inputs as the "
        "report holds them. Every figure is a recorded calculation on this question's own "
        "ledger, and nothing else in the model was changed."
    )
    question.content = {
        "kind": "recompute",
        "change": {
            "parameter": change.parameter,
            "words": words,
            "kind": change.kind,
            "stated": change.stated,
            "from": _plain(moved_from.value),
            "to": _plain(moved_to.value),
        },
        "figures": figures,
        "report_job_id": str(run.id),
    }
    question.answer = answer
    question.answered_at = datetime.now(UTC)
    question.actual_cost_gbp = Decimal(0)
    await _succeed(
        session, order=order, job=job, step=step, cost=Decimal(0), output=question.content
    )


def _plain(value: Decimal) -> str:
    """A stored value without its trailing zeros: ``0.05``, not ``0.050000000000``."""
    return f"{value.normalize():f}"


def _moved(held: Quantity, change: Change, *, question: Question) -> Quantity:
    """The input as the question asked for it, sourced to the question."""
    value = change.value if change.kind == "absolute" else held.value + change.value
    return Quantity.of(
        value,
        held.unit,
        source=SourceRef.question(
            question.id, label=f"{change.words} {change.stated}, as the question stated it"
        ),
    )


def _apply(
    values: dict[str, Quantity], change: Change, *, question: Question
) -> tuple[Quantity, dict[str, Quantity]]:
    """The confirmed values with one input moved, in every year it is confirmed for."""
    names = [
        name
        for name in values
        if name == change.parameter
        or (
            (per_year := _PER_YEAR.match(name)) is not None and per_year["name"] == change.parameter
        )
    ]
    if not names:
        message = (
            f"The run holds no confirmed {change.words}, so there is nothing to move. The "
            "question named an input the report's model did not read."
        )
        raise ValidationError(message, context={"parameter": change.parameter})
    changed = dict(values)
    for name in names:
        changed[name] = _moved(values[name], change, question=question)
    return values[names[0]], changed


def _first_changed(values: dict[str, Quantity], parameter: str) -> Quantity:
    for name, quantity in values.items():
        per_year = _PER_YEAR.match(name)
        if name == parameter or (per_year is not None and per_year["name"] == parameter):
            return quantity
    message = f"No changed value for {parameter!r}."  # pragma: no cover -- `_apply` refused first
    raise ValidationError(message, context={"parameter": parameter})


def _figure_rows(result: DcfResult, qualifier: str, *, style: Any) -> list[dict[str, Any]]:
    return [
        _figure_row(
            f"Value per share, Gordon growth, {qualifier}",
            result.gordon.value_per_share,
            style=style,
        ),
        _figure_row(
            f"Value per share, exit multiple, {qualifier}",
            result.exit_multiple.value_per_share,
            style=style,
        ),
    ]


def _figure_row(label: str, figure: Quantity, *, style: Any) -> dict[str, Any]:
    source = figure.source
    return {
        "label": label,
        "value": str(figure.value),
        "unit": figure.unit.symbol,
        "rendered": display.figure(figure.value, unit=figure.unit.symbol, label=label, style=style),
        "calculation_id": (
            source.identifier
            if source is not None and source.kind is SourceKind.CALCULATION
            else None
        ),
    }


async def _forecast_years(session: AsyncSession, job_id: uuid.UUID) -> int:
    produced = await _step_output(session, job_id, _VALUE_STEP)
    years = produced.get("years")
    return int(years) if isinstance(years, int) and years > 0 else _DEFAULT_YEARS


async def _market_capitalisation(
    session: AsyncSession, job_id: uuid.UUID, *, currency: str
) -> Quantity | None:
    """The capitalisation the run's price step recorded, re-sourced to its calculation."""
    produced = await _step_output(session, job_id, _PRICES_STEP)
    recorded = produced.get("market_capitalisation")
    if isinstance(recorded, dict):
        value, identifier, kind = (
            recorded.get("value"),
            recorded.get("source_id"),
            recorded.get("source_kind"),
        )
    else:
        value, identifier, kind = recorded, None, None
    if not value:
        return None
    label = "market capitalisation"
    # The kind is read, not assumed, as the value step reads it: a capitalisation is a
    # calculation and a close is a stored fact, and re-sourcing the wrong way hands a
    # figure an id that resolves to no row.
    if not identifier:
        source = SourceRef.calculation(label, label=label)
    elif kind == SourceKind.FACT.value:
        source = SourceRef.security(str(identifier), label=label)
    else:
        source = SourceRef.calculation(str(identifier), label=label)
    return Quantity.of(Decimal(str(value)), Unit.currency(currency), source=source)


async def _step_output(session: AsyncSession, job_id: uuid.UUID, step_key: str) -> dict[str, Any]:
    step = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == step_key)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    produced = step.output_ref if step is not None else None
    return dict(produced) if isinstance(produced, dict) else {}


# -- Tier 2: one pass over what is held -------------------------------------------------------


@dataclass(slots=True)
class _Pack:
    """What the reader is dealt, and the index code keeps to check what it says."""

    internal: list[dict[str, Any]] = field(default_factory=list)
    untrusted: list[dict[str, str]] = field(default_factory=list)
    # Every id a paragraph may cite, with the kind and the label the note is composed from.
    notes: dict[str, dict[str, str]] = field(default_factory=dict)
    figures: dict[str, Decimal] = field(default_factory=dict)
    excerpt_text: dict[str, str] = field(default_factory=dict)
    tokens_known: set[str] = field(default_factory=set)
    cost: int = 0
    truncated: bool = False
    dropped: int = 0
    dealt: dict[str, int] = field(
        default_factory=lambda: {"documents": 0, "facts": 0, "calculations": 0, "excerpts": 0}
    )

    @property
    def is_empty(self) -> bool:
        return not self.notes


@dataclass(frozen=True, slots=True)
class _Unit:
    internal: dict[str, Any]
    note: tuple[str, str, str]  # id, kind, label
    kind: str
    untrusted: dict[str, str] | None = None
    figure: Decimal | None = None

    @property
    def cost(self) -> int:
        text = self.untrusted.get("text", "") if self.untrusted else ""
        return max(1, (len(str(self.internal)) + len(text)) // _CHARS_PER_TOKEN)


async def _re_read(
    session: AsyncSession,
    *,
    settings: Settings,
    provider: LLMProvider,
    router: Router,
    store: ArtefactStore,
    user: User,
    company: Company,
    question: Question,
    report: Report | None,
    resolution: Resolution,
) -> None:
    order, job, step = await _open_pass(session, settings=settings, user=user, company=company)
    question.job_id = job.id
    pack = await _pack(
        session,
        user=user,
        company=company,
        question=question.question,
        report_job_id=report.job_id if report is not None else None,
    )
    if pack.is_empty:
        await _succeed(session, order=order, job=job, step=step, cost=Decimal(0), output={})
        _refuse_to_research(
            question,
            resolution,
            reason="the record holds nothing the reader could be dealt",
            router=router,
            settings=settings,
            company=company,
        )
        return

    projected = estimate_gbp(
        model=router.resolve(AskReaderAgent.role).model,
        input_tokens=pack.cost,
        expected_output_tokens=_ANSWER_TOKENS,
        usd_to_gbp=settings.usd_to_gbp,
    )
    guard = BudgetGuard(
        per_run_cap_gbp=settings.per_run_budget_gbp, monthly_cap_gbp=settings.monthly_budget_gbp
    )
    context = AgentContext(
        session=session,
        provider=provider,
        router=router,
        settings=settings,
        store=store,
        job_step=step,
    )
    payload = AskInput(
        company_name=company.name,
        ticker=company.ticker,
        question=question.question,
        internal_evidence=pack.internal,
        untrusted_evidence=pack.untrusted,
        truncated=pack.truncated,
    )
    try:
        await guard.check(session, job=job, projected_gbp=projected)
        draft = await AskReaderAgent().run(context, payload)
    except BudgetExceededError as refused:
        await _fail(
            session, order=order, job=job, step=step, reason=str(refused), cost=context.spend_gbp
        )
        question.actual_cost_gbp = context.spend_gbp
        question.content = {"kind": "stopped", "reason": str(refused)}
        return

    kept, reason = _judge(draft, pack)
    question.actual_cost_gbp = context.spend_gbp
    if reason is not None or not kept:
        await _succeed(
            session,
            order=order,
            job=job,
            step=step,
            cost=context.spend_gbp,
            output={"discarded": reason or "no paragraph rested on the record"},
        )
        _refuse_to_research(
            question,
            resolution,
            reason=reason or "no paragraph rested on the record",
            router=router,
            settings=settings,
            company=company,
        )
        return

    numbered: dict[str, int] = {}
    notes: list[dict[str, str]] = []
    paragraphs: list[dict[str, Any]] = []
    for text, cites in kept:
        markers: list[int] = []
        for cite in cites:
            if cite not in numbered:
                numbered[cite] = len(numbered) + 1
                identifier, kind, label = (
                    pack.notes[cite]["id"],
                    pack.notes[cite]["kind"],
                    pack.notes[cite]["label"],
                )
                notes.append(
                    {"number": str(numbered[cite]), "id": identifier, "kind": kind, "label": label}
                )
            markers.append(numbered[cite])
        paragraphs.append({"text": text, "notes": markers})

    question.content = {
        "kind": "re_read",
        "paragraphs": paragraphs,
        "notes": notes,
        "dealt": dict(pack.dealt),
        "truncated": pack.truncated,
        "dropped": pack.dropped,
    }
    question.answer = "\n\n".join(text for text, _ in kept)
    question.answered_at = datetime.now(UTC)
    await _succeed(
        session, order=order, job=job, step=step, cost=context.spend_gbp, output=question.content
    )


async def _pack(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    question: str,
    report_job_id: uuid.UUID | None,
) -> _Pack:
    """Deal the record within the budget: sources, calculations, facts, then the excerpts
    ranked by the question's own words."""
    documents = await _documents(session, user=user, company=company)
    units: list[_Unit] = []
    tier_by_source = {str(row.id): row.evidence_tier.value for row in documents}
    for row in documents:
        identifier = str(row.id)
        published = row.publication_date_latest or row.publication_date
        units.append(
            _Unit(
                internal={
                    "source_document_id": identifier,
                    "title": row.title or row.url,
                    "tier": row.evidence_tier.value,
                    "publication_date": published.isoformat() if published else None,
                },
                note=(identifier, "source", row.title or row.url),
                kind="source",
            )
        )

    if report_job_id is not None:
        for calculation in await indexed_calculations(
            session, job_id=report_job_id, run_level_first=True
        ):
            identifier = str(calculation.id)
            units.append(
                _Unit(
                    internal={
                        "calculation_id": identifier,
                        "name": calculation.name,
                        "value": str(calculation.output_value),
                        "unit": calculation.output_unit,
                        "period": calculation.period_label,
                    },
                    note=(identifier, "calculation", calculation.name.replace("_", " ")),
                    kind="calculation",
                    figure=calculation.output_value,
                )
            )

    facts = await session.scalars(
        select(FinancialFact)
        .where(FinancialFact.company_id == company.id, FinancialFact.dimension_axis.is_(None))
        .order_by(FinancialFact.period_end.desc(), FinancialFact.concept)
        .limit(_FACT_POOL)
    )
    for fact in facts:
        identifier = str(fact.id)
        units.append(
            _Unit(
                internal={
                    "fact_id": identifier,
                    "concept": fact.concept,
                    "value": str(fact.value),
                    "unit": fact.unit,
                    "period_end": fact.period_end.isoformat(),
                    "fiscal_year": fact.fiscal_year,
                    "fiscal_period": fact.fiscal_period,
                    "source_document_id": str(fact.source_document_id),
                },
                note=(
                    identifier,
                    "fact",
                    f"{fact.concept.replace('_', ' ')}, {fact.period_end.isoformat()}",
                ),
                kind="fact",
                figure=fact.value,
            )
        )

    if documents:
        keywords = _keywords(question)
        extractions = list(
            await session.scalars(
                select(Extraction)
                .where(Extraction.source_document_id.in_([row.id for row in documents]))
                .order_by(Extraction.created_at)
                .limit(_EXCERPT_POOL)
            )
        )
        substantive = [
            (index, row)
            for index, row in enumerate(extractions)
            if len(row.excerpt.strip()) >= _SUBSTANTIVE_CHARS
        ]
        ranked = sorted(substantive, key=lambda item: (-_hits(item[1].excerpt, keywords), item[0]))
        for _, extraction in ranked:
            identifier = str(extraction.id)
            source_id = str(extraction.source_document_id)
            units.append(
                _Unit(
                    internal={"extraction_id": identifier, "source_document_id": source_id},
                    note=(
                        identifier,
                        "excerpt",
                        f"an excerpt of {tier_by_source.get(source_id, '')}".strip(),
                    ),
                    kind="excerpt",
                    untrusted={
                        "source_document_id": source_id,
                        "tier": tier_by_source.get(source_id, "T5_SECONDARY"),
                        "title": f"extraction {identifier}",
                        "text": extraction.excerpt,
                    },
                )
            )

    pack = _Pack()
    for unit in units:
        if pack.cost + unit.cost > TOKEN_BUDGET:
            pack.truncated = True
            pack.dropped += 1
            continue
        pack.cost += unit.cost
        pack.internal.append(unit.internal)
        identifier, kind, label = unit.note
        pack.notes[identifier] = {"id": identifier, "kind": kind, "label": label}
        if unit.untrusted is not None:
            pack.untrusted.append(unit.untrusted)
            pack.excerpt_text[identifier] = unit.untrusted["text"]
            pack.tokens_known.update(numeral_tokens(unit.untrusted["text"]))
        if unit.figure is not None:
            pack.figures[identifier] = unit.figure
        pack.dealt[_DEALT_AS[kind]] += 1
        pack.tokens_known.update(numeral_tokens(str(unit.internal)))
    return pack


def _keywords(question: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(word for word in _WORD.findall(question.lower()) if word not in _STOPWORDS)
    )


def _hits(excerpt: str, keywords: tuple[str, ...]) -> int:
    lowered = excerpt.lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def _judge(draft: AskAnswer, pack: _Pack) -> tuple[list[tuple[str, list[str]]], str | None]:
    """The checks that decide whether what the reader said reaches the page (ADR 0130 §4).

    Returns the paragraphs kept, each with the citations that survived, and the reason the
    answer is discarded, or ``None`` when it is not.
    """
    if not draft.answered:
        named = "; ".join(item.strip() for item in draft.not_in_record if item.strip())
        return [], (
            "the reader found the material does not answer it"
            + (f", needing {named}" if named else "")
        )
    if draft.not_in_record:
        named = "; ".join(item.strip() for item in draft.not_in_record if item.strip())
        return [], f"the reader needed {named or 'something'} the record does not hold"

    kept: list[tuple[str, list[str]]] = []
    for paragraph in draft.paragraphs:
        cites = list(dict.fromkeys(cite for cite in paragraph.cites if cite in pack.notes))
        if not cites:
            continue
        stray = _stray_numeral(paragraph.text, cites, pack)
        if stray is not None:
            return [], f"it states a figure the record does not hold ({stray})"
        kept.append((paragraph.text.strip(), cites))
    if not kept:
        return [], "no paragraph rested on anything in the record"
    return kept, None


def _stray_numeral(text: str, cites: list[str], pack: _Pack) -> str | None:
    """The first numeral in the text that nothing dealt reads as, or ``None``."""
    for token in numeral_tokens(text):
        if token in pack.tokens_known:
            continue
        quoted = Decimal(token)
        if any(reads_as(quoted, stored, sign_matters=False) for stored in pack.figures.values()):
            continue
        if any(token in numeral_tokens(pack.excerpt_text.get(cite, "")) for cite in cites):
            continue
        return token
    return None


# -- Tier 3: priced, and not yet run ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Estimate:
    """What researching a question would come to, before anything is spent."""

    searches: int
    documents_up_to: int
    cost_gbp: Decimal

    def sentence(self, company_name: str) -> str:
        return (
            f"This needs new material. About £{self.cost_gbp:.2f}, and up to "
            f"{self.documents_up_to} new documents will be added to {company_name}'s record."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "searches": self.searches,
            "documents_up_to": self.documents_up_to,
            "cost_gbp": str(self.cost_gbp),
        }


def research_estimate(resolution: Resolution, *, router: Router, settings: Settings) -> Estimate:
    """Price the third tier from the routed models' published rates (ADR 0130 §5).

    One search for the question and one for each thing it names that the record lacks, a
    worker turn to read each search's listing, and the reading pass over what arrives. An
    estimate, said as one — the acquisition that lands after F4 replaces it with the plan's
    own — and rounded up to the penny rather than down.
    """
    searches = min(_MAX_SEARCHES, 1 + len(resolution.missing))
    search_model = router.resolve("web_search").model
    reader_model = router.resolve(AskReaderAgent.role).model
    fee = price_web_search(
        searches, provider="anthropic", model=search_model, usd_to_gbp=settings.usd_to_gbp
    )
    cost = (fee.amount_gbp if fee is not None else Decimal(0)) + searches * estimate_gbp(
        model=search_model,
        input_tokens=_WORKER_INPUT_TOKENS,
        expected_output_tokens=_WORKER_OUTPUT_TOKENS,
        usd_to_gbp=settings.usd_to_gbp,
    )
    cost += estimate_gbp(
        model=reader_model,
        input_tokens=TOKEN_BUDGET,
        expected_output_tokens=_ANSWER_TOKENS,
        usd_to_gbp=settings.usd_to_gbp,
    )
    return Estimate(
        searches=searches,
        documents_up_to=searches * _HITS_PER_SEARCH,
        cost_gbp=cost.quantize(_PENNY, rounding=ROUND_CEILING),
    )


def _price(
    question: Question,
    resolution: Resolution,
    *,
    router: Router,
    settings: Settings,
    company: Company,
) -> None:
    estimate = research_estimate(resolution, router=router, settings=settings)
    question.tier = int(Tier.RESEARCH)
    question.estimated_cost_gbp = estimate.cost_gbp
    question.content = {
        "kind": "research",
        "estimate": estimate.as_dict(),
        "sentence": estimate.sentence(company.name),
        "missing": list(resolution.missing),
    }


def _refuse_to_research(
    question: Question,
    resolution: Resolution,
    *,
    reason: str,
    router: Router,
    settings: Settings,
    company: Company,
) -> None:
    """A tier-2 answer discarded: the mis-resolution caught, and the tier-3 price stated."""
    _log.info("ask.discarded", question_id=str(question.id), reason=reason)
    _price(question, resolution, router=router, settings=settings, company=company)
    question.tier_rationale = f"That is not in this record: {reason}. {question.tier_rationale}"
    question.content["discarded"] = reason


# -- The run root, and its two endings ----------------------------------------------------------


async def _open_pass(
    session: AsyncSession, *, settings: Settings, user: User, company: Company
) -> tuple[WorkOrder, Job, JobStep]:
    started = datetime.now(UTC)
    order = WorkOrder(
        user_id=user.id,
        tool=TOOL,
        subject_kind=SUBJECT_COMPANY,
        subject_id=company.id,
        as_of_date=date.today(),  # noqa: DTZ011 -- the day the question was asked, on the platform's clock
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
        input_hash=sha256_hex(canonical_json({"company": str(company.id), "job_id": str(job.id)})),
        started_at=started,
    )
    session.add(step)
    await session.flush()
    return order, job, step


async def _succeed(
    session: AsyncSession,
    *,
    order: WorkOrder,
    job: Job,
    step: JobStep,
    cost: Decimal,
    output: dict[str, Any],
) -> None:
    finished = datetime.now(UTC)
    step.status = JobStatus.SUCCEEDED
    step.finished_at = finished
    step.cost_gbp = cost
    step.output_ref = output
    job.status = JobStatus.SUCCEEDED
    job.finished_at = finished
    job.total_cost_gbp = cost
    order.status = RequestStatus.COMPLETED
    await session.flush()


async def _fail(
    session: AsyncSession,
    *,
    order: WorkOrder,
    job: Job,
    step: JobStep,
    reason: str,
    cost: Decimal = Decimal(0),
) -> None:
    finished = datetime.now(UTC)
    step.status = JobStatus.FAILED
    step.finished_at = finished
    step.cost_gbp = cost
    step.error = {"code": "ask_refused", "message": reason}
    job.status = JobStatus.FAILED
    job.finished_at = finished
    job.total_cost_gbp = cost
    job.error = {"code": "ask_refused", "message": reason, "context": {}}
    order.status = RequestStatus.FAILED
    await session.flush()


# -- The drawer -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Note:
    """One numbered note on an answer, resolved from the stored content."""

    number: int
    kind: str
    label: str
    identifier: str
    # An excerpt as stored, or the licence sentence where it may not be printed.
    excerpt: str = ""
    withheld: bool = False
    source: provenance.SourceView | None = None
    figure: str = ""
    calculation_id: str = ""


async def note_of(session: AsyncSession, question: Question, number: int) -> Note | None:
    """The note behind one marker, or ``None`` when the answer has no such note."""
    notes = question.content.get("notes") if isinstance(question.content, dict) else None
    if not isinstance(notes, list) or number < 1 or number > len(notes):
        return None
    stored = notes[number - 1]
    bare = Note(
        number=number,
        kind=str(stored.get("kind")),
        label=str(stored.get("label")),
        identifier=str(stored.get("id")),
    )
    parsed = _uuid_or_none(bare.identifier)
    if parsed is None:
        return bare
    if bare.kind == "calculation":
        return dataclass_replace(bare, calculation_id=bare.identifier)
    if bare.kind == "excerpt":
        return await _excerpt_note(session, bare, parsed)
    if bare.kind == "fact":
        return await _fact_note(session, bare, parsed)
    return dataclass_replace(bare, source=await provenance.source_detail(session, parsed))


async def _excerpt_note(session: AsyncSession, bare: Note, extraction_id: uuid.UUID) -> Note:
    """The excerpt as stored, subject to ADR 0119's licence rule, with its source."""
    extraction = await session.get(
        Extraction, extraction_id, options=[selectinload(Extraction.source_document)]
    )
    if extraction is None:
        return bare
    document = extraction.source_document
    printable = may_print_excerpt(document, extraction.excerpt)
    return dataclass_replace(
        bare,
        excerpt=extraction.excerpt if printable else "",
        withheld=not printable,
        source=await provenance.source_detail(session, document.id),
    )


async def _fact_note(session: AsyncSession, bare: Note, fact_id: uuid.UUID) -> Note:
    fact = await session.get(FinancialFact, fact_id)
    if fact is None:
        return bare
    style = await configuration.effective_house_style(session)
    return dataclass_replace(
        bare,
        figure=display.figure(fact.value, unit=fact.unit, label=fact.concept, style=style),
        source=await provenance.source_detail(session, fact.source_document_id),
    )


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
