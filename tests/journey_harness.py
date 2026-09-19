"""The journey harness: every stopped state offers a way forward. The half both halves share.

The instrument for ISSUE 1 (`docs/V1.0_Alpha/11-testing-strategy.md` §3.1). The inventory of
stopped states comes from `tests/journey_inventory.py`, generated from code; this module puts
a run into each state on the fake scene and makes three assertions about the page it is met
on:

1. **A forward control exists** — visible, enabled, and labelled in the operator's vocabulary
   (*Continue this run*, *Raise the ceiling*, *Start again*), never *Retry* and never a bare
   identifier.
2. **The visible text is clean** — no UUID, no shell command, no code identifier. The
   vocabulary ratchet, asserted rather than hoped for.
3. **Pressing it moves** — the run's own record changes, or the control leads to the surface
   where the remedy lives. A button that re-renders the same dead end is the defect.

The page itself is behind :class:`Surface`, and two things supply one: a real browser
(`tests/e2e/journey.py`, the browser half) and an in-process HTTP client over the parsed HTML
(`audit/journey.py`, the shape half, run by `python -m audit.smoke --journey`). The builders
and the assertions are written once, here, so the two halves cannot drift into two ideas of
what a way forward is.

A state the harness cannot construct raises :class:`NoPathConstructedError` and never skips: an
unreachable row is either a defect in the harness or a dead branch in the engine, and the
harness says which by failing. Each assertion raises :class:`DeadEndError`, and only that,
so a row can be marked expected-red on that exception alone.

Everything here is a person's job or a worker's, never both: the surface does what an
operator does, and `tests/e2e/worker.py` does what the worker would have done in between.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer import errors
from aer.core.disagreement import Position
from aer.core.enums import Decision, GateKind, JobStatus, SourceTier, UserRole
from aer.core.escalation import TriggerKind
from aer.db.models import (
    Approval,
    Citation,
    Claim,
    Extraction,
    Job,
    JobStep,
    ReportSection,
    Skill,
    SourceDocument,
    User,
    WorkOrder,
)
from aer.eval.metrics import Metric
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.services.approvals import payload_hash_for
from aer.services.disagreements import resolve_and_record
from aer.services.skills import save_skill
from aer.workflow.engine import WorkflowDefinitionError
from aer.workflow.pauses import PauseReason
from aer.workflow.workflows.vertical_slice_v1 import build_steps, gate_payload, seal_step_for
from tests.db_cleanup import delete_all
from tests.db_fixtures import run_async
from tests.e2e.worker import Worker
from tests.journey_inventory import (
    STILL_RED,
    UNCONSTRUCTED,
    Disposition,
    Family,
    Press,
    StoppedState,
    failed_step_codes,
)
from tests.request_fixtures import research_request
from tests.sec_fixtures import fixture_bytes
from tests.workflow_fixtures import (
    AS_OF_DATE,
    BANK_FACTS_FIXTURE,
    BANK_SUBMISSIONS_FIXTURE,
    DEFAULT_PER_RUN_BUDGET_GBP,
    UNMAPPED_FACTS_FIXTURE,
    make_provider,
    make_provider_that_misquotes_its_figures,
    owner_of,
    seed_starved_section,
    the_only_user,
)

__all__ = [
    "ASSERTIONS",
    "CONSOLE_URL",
    "Control",
    "DeadEndError",
    "NoPathConstructedError",
    "NotAsRecordedError",
    "Scene",
    "Surface",
    "Verdict",
    "build",
    "check",
    "environment_for",
    "judge",
    "reset_scene",
]

CONSOLE_URL: Final = re.compile(
    r"/runs/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
GATE_PAGE_URL: Final = re.compile(r"/runs/[0-9a-f-]{36}/\w+$")
STRANDED_FOR_SECONDS: Final = 120
SMALL_CAP: Final = Decimal("2.00")


class DeadEndError(AssertionError):
    """One of the three assertions failed: a dead end, or a page that speaks in code."""


class NoPathConstructedError(AssertionError):
    """The harness could not put a run into this state on the fake scene."""


class NotAsRecordedError(AssertionError):
    """The row is red on different assertions from the ones `STILL_RED` records.

    Either direction: a fix that moved an assertion the record still calls red, or a
    regression on one it calls green. Both fail the build, so the record is always what was
    measured last, and a fix is done when it has moved the record as well as the page.
    """


# The three assertions, by the names the record uses.
ASSERTIONS: Final = ("text", "control", "press")


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the three assertions found on one row: the defects, by assertion, and the rest.

    ``press`` is measured only when ``control`` passed — a control nobody found cannot be
    pressed — so a row red on ``control`` says nothing about ``press`` until that is fixed,
    and the record lists only what was measured.
    """

    red: dict[str, str]
    notes: tuple[str, ...]

    def report(self) -> str:
        return " | ".join([*self.red.values(), *self.notes])


# --- the surface ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Control:
    """A visible, enabled link or button in the page's main content, as the operator reads it.

    ``handle`` is the surface's own reference to it — a Playwright locator, a parsed node —
    and means nothing outside the surface that produced it.
    """

    label: str
    kind: str
    handle: Any


class Surface(Protocol):
    """What the harness needs from a page. The browser half and the shape half each supply one.

    A surface is a person at a page: it can go somewhere, read the main content, see which
    controls are offered, press one, and type into a field. A ``press`` follows whatever the
    control does — a link's navigation, a form's submission and redirect — and lands on the
    resulting page; when ``expect_url`` is given the press must end at a URL matching it, and
    a press that ends anywhere else raises. That is how a control that does not lead on is
    caught by both halves alike.
    """

    @property
    def url(self) -> str: ...

    def goto(self, url: str) -> None: ...

    def text(self) -> str:
        """The main content's visible text, as a reader meets it."""
        ...

    def controls(self) -> list[Control]:
        """Every visible, enabled link and button in the main content, with its label."""
        ...

    def press(self, control: Control, *, expect_url: re.Pattern[str] | None = None) -> None: ...

    def press_by_id(self, element_id: str, *, expect_url: re.Pattern[str] | None = None) -> None:
        """Press the control with this id — the product's own ids, which the pages fix."""
        ...

    def fill(self, name: str, value: str) -> None:
        """Type into the field with this name, as the operator would."""
        ...

    def set_hidden(self, name: str, value: str) -> None:
        """Change a hidden field: what a page moving under the operator leaves behind."""
        ...

    def has(self, element_id: str) -> bool: ...


@dataclass(slots=True)
class Scene:
    surface: Surface
    live_server: str
    database_url: str

    # The citations a builder made unverifiable, so the assertion that presses the way
    # forward knows which forms on the review page an operator would fill in. Set by the
    # builder that needs it and read by nothing else; a row that drifts none leaves it empty.
    unverified_citations: tuple[str, ...] = ()


def environment_for(state: StoppedState) -> dict[str, str]:
    """The ceilings a budget row needs, set before the server and the worker read them.

    One place for both halves: the browser half sets these through its settings fixture
    before the live server starts; the shape half patches the process environment around
    the row.
    """
    if state.family is not Family.BUDGET:
        return {}
    detail = state.detail or ""
    if detail.endswith(":at_ceiling"):
        return {"AER_PER_RUN_BUDGET_GBP": "2.00"}
    if detail == "monthly":
        return {"AER_MONTHLY_BUDGET_GBP": "1.00"}
    return {}


# --- the vocabulary ------------------------------------------------------------------------

# What a drifted excerpt is rewritten to: a sentence in the platform's own register that
# no filing contains, so the verifier's failure is "does not match" rather than a near
# miss whose similarity score would vary with whatever the document happened to say.
_DRIFTED_EXCERPT: Final = (
    "This passage was never in the filing and is here so a citation has something to fail against."
)

UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ROOT: Final = Path(__file__).resolve().parents[1]


def _shell_commands() -> re.Pattern[str]:
    """Every command an operator could be told to type, read from where they are defined.

    The justfile's recipes and the CLI's registered commands, so that ``just worker`` and
    ``aer reseal`` are caught and the English word *just* is not.
    """
    from aer.cli import app  # noqa: PLC0415 -- the CLI module is heavy; imported once here

    recipes = re.findall(
        r"^([a-z][a-z-]*)(?:\s+[^:\n]*)?:\s*$", (ROOT / "justfile").read_text(), re.M
    )
    commands = [command.name or "" for command in app.registered_commands]
    names = "|".join(re.escape(name) for name in sorted({*recipes, *commands} - {""}))
    return re.compile(rf"\bjust (?:{names})\b|\buv run\b|\baer (?:{names})\b|(?:^|\n)\$ ")


SHELL: Final = _shell_commands()
SNAKE_CASE: Final = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
# A leading digit counts. `[A-Z]{2,}` missed `T4_LICENSED_MARKET` — the second character
# is a digit — and behind that hole sat a real one: the conflict ladder's own rationale,
# on the review page and in the report's appendix, read "both T4_LICENSED_MARKET, both
# as_reported". A regex with a shape a real identifier does not have is an assertion that
# passes for the wrong reason, which is worse than not having it.
SHOUTED_ENUM: Final = re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b")
XBRL_TAG: Final = re.compile(r"\b[a-z-]+:[A-Z][A-Za-z]+\b")
MODULE_PATH: Final = re.compile(r"\baer\.[a-z_][a-z_.]+\b")


def _code_identifiers() -> frozenset[str]:
    """The identifiers the platform defines, read from the code rather than listed by hand.

    Only the ones that could not be English: a step named ``plan`` is a word, and a page that
    says "Review the plan" is right to; ``propose_assumptions`` and ``citation_accuracy`` are
    not words and reach a page only by leaking.
    """
    names: set[str] = set()
    names.update(metric.value for metric in Metric)
    names.update(step.key for step in build_steps())
    names.update(kind.value for kind in TriggerKind)
    names.update(failed_step_codes())
    names.update(status.value for status in JobStatus)
    names.update(gate.value for gate in GateKind)
    return frozenset(name for name in names if "_" in name)


CODE_IDENTIFIERS: Final = _code_identifiers()


# --- the database, one throwaway engine per operation ------------------------------------


def _engine(url: str) -> Any:
    # NullPool for the reason every helper in `tests/e2e/` gives: each call runs on its own
    # loop and an asyncpg connection belongs to the loop that opened it.
    return create_async_engine(url, poolclass=NullPool)


async def reset_scene(url: str) -> None:
    """Empty the database and seed the one operator, the way the browser suite does per test.

    The shape half runs every row on one database, and a row that inherits the previous
    row's runs is a row whose monthly spend — and whose "the only user" — depends on
    ordering.

    """
    engine = _engine(url)
    try:
        await delete_all(engine)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                User(email="journey@example.invalid", display_name="Journey", role=UserRole.OWNER)
            )
            await session.commit()
    finally:
        await engine.dispose()


@dataclass(frozen=True, slots=True)
class Subject:
    """A company to commission, and the filer the stub answers as while it runs.

    Three of them, because two gates only fire for a filer the default scene is not: the
    unmapped-concepts gate needs a filing that extends the taxonomy, and the sector gate
    needs an index whose SIC reaches a profile that blocks a valuation model. Both are
    properties of the *subject*, so the harness changes the subject rather than writing the
    gate's own state in afterwards — a gate exercised against a state no acquisition
    produces is a gate nobody has tested.
    """

    company_name: str
    ticker: str
    exchange: str
    facts: bytes | None = None
    submissions: bytes | None = None


MICROSOFT: Final = Subject(company_name="Microsoft Corporation", ticker="MSFT", exchange="NASDAQ")


def _bank() -> Subject:
    return Subject(
        company_name="M&T Bank Corporation",
        ticker="MTB",
        exchange="NYSE",
        facts=fixture_bytes(BANK_FACTS_FIXTURE),
        submissions=fixture_bytes(BANK_SUBMISSIONS_FIXTURE),
    )


def _extension_filer() -> Subject:
    return Subject(
        company_name="Example Industries Inc",
        ticker="EXMPL",
        exchange="NYSE",
        facts=fixture_bytes(UNMAPPED_FACTS_FIXTURE),
    )


# Which subject each gate needs to be raised at all. Everything not named here fires for the
# ordinary one, which is most of them.
_SUBJECT_FOR: Final[dict[GateKind, Any]] = {
    GateKind.SECTOR_SPECIALIST: _bank,
    GateKind.UNMAPPED_CONCEPTS: _extension_filer,
}


def subject_for(gate: GateKind | None) -> Subject:
    build = _SUBJECT_FOR.get(gate) if gate is not None else None
    return build() if build is not None else MICROSOFT


async def _commission(
    url: str,
    *,
    max_cost_gbp: Decimal = DEFAULT_PER_RUN_BUDGET_GBP,
    subject: Subject = MICROSOFT,
) -> uuid.UUID:
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            user = await the_only_user(session)
            request = research_request(
                user_id=user.id,
                company_name=subject.company_name,
                ticker=subject.ticker,
                exchange=subject.exchange,
                as_of_date=AS_OF_DATE,
                base_currency="USD",
                reporting_currency="USD",
                investment_horizon_months=12,
                max_cost_gbp=max_cost_gbp,
            )
            session.add(request)
            await session.flush()
            job = await run_service.start_run(session, request=request)
            await session.commit()
            return job.id
    finally:
        await engine.dispose()


async def _status(url: str, job_id: uuid.UUID) -> JobStatus:
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            return job.status
    finally:
        await engine.dispose()


async def _pending_gate(url: str, job_id: uuid.UUID) -> GateKind | None:
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            return await approval_service.pending_gate(session, job)
    finally:
        await engine.dispose()


async def _budget_scope(url: str, job_id: uuid.UUID) -> str | None:
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            state = await run_service.run_state(session, job_id=job_id)
            return state.budget_scope
    finally:
        await engine.dispose()


async def _pause_reason(url: str, job_id: uuid.UUID) -> str | None:
    """Why the run is paused, read from the paused step's own record (`PauseReason`)."""
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(
                select(JobStep)
                .where(JobStep.job_id == job_id, JobStep.status == JobStatus.AWAITING_APPROVAL)
                .order_by(JobStep.sequence.desc(), JobStep.attempt.desc())
                .limit(1)
            )
            error = (row.error or {}) if row else {}
            reason = error.get("context", {}).get("reason")
            return str(reason) if reason else None
    finally:
        await engine.dispose()


async def _set_step_mode(url: str, job_id: uuid.UUID) -> None:
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            job.step_mode = True
            await session.commit()
    finally:
        await engine.dispose()


async def _strand(url: str, job_id: uuid.UUID) -> None:
    """Leave the run as a dead worker leaves it: RUNNING, with a RUNNING step, and nobody."""
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            began = datetime.now(UTC) - timedelta(seconds=STRANDED_FOR_SECONDS)
            job.status = JobStatus.RUNNING
            job.started_at = began
            session.add(
                JobStep(
                    job_id=job.id,
                    step_key="extract",
                    sequence=3,
                    status=JobStatus.RUNNING,
                    attempt=0,
                    idempotency_key=f"{job.id}:extract",
                    input_hash="0" * 64,
                    started_at=began,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _approve_by_service(url: str, job_id: uuid.UUID, gate: GateKind) -> None:
    """Approve the way the page does: with the hash of the payload as it renders now."""
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            await approval_service.record_decision(
                session,
                job=job,
                gate=gate,
                decision=Decision.APPROVED,
                actor=await owner_of(session, job),
                payload_hash=payload_hash_for(
                    await gate_payload(session, job=job, gate=gate.value)
                ),
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _move_the_page(url: str, job_id: uuid.UUID, gate: GateKind) -> None:
    """The approval now matches neither the seal nor the page: what a payload moving under an
    approval leaves behind, written straight to the row as the audit found it."""
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(
                select(Approval).where(Approval.job_id == job_id, Approval.gate == gate)
            )
            assert row is not None, "the approval was not recorded"
            row.payload_hash = "0" * 64
            await session.commit()
    finally:
        await engine.dispose()


async def _drift_the_seal(url: str, job_id: uuid.UUID, gate: GateKind) -> None:
    """The page still matches the approval; the run's own seal has moved."""
    step = seal_step_for(gate.value)
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(
                select(JobStep)
                .where(JobStep.job_id == job_id, JobStep.step_key == step)
                .order_by(JobStep.attempt.desc())
                .limit(1)
            )
            assert row is not None, f"the {step} step has not run"
            row.output_ref = {**(row.output_ref or {}), "payload_hash": "1" * 64}
            await session.commit()
    finally:
        await engine.dispose()


async def _fingerprint(url: str, job_id: uuid.UUID) -> tuple[Any, ...]:
    """What "moved" is measured against: status, newest step, decisions, cap, sibling runs."""
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            newest = await session.scalar(
                select(JobStep)
                .where(JobStep.job_id == job_id)
                .order_by(JobStep.sequence.desc(), JobStep.attempt.desc())
                .limit(1)
            )
            approvals = await session.scalar(
                select(func.count()).select_from(Approval).where(Approval.job_id == job_id)
            )
            order = await session.get(WorkOrder, job.work_order_id)
            assert order is not None
            siblings = await session.scalar(
                select(func.count()).select_from(Job).where(Job.work_order_id == job.work_order_id)
            )
            return (
                job.status.value,
                (newest.step_key, newest.attempt, newest.status.value) if newest else None,
                int(approvals or 0),
                str(order.max_cost_gbp),
                int(siblings or 0),
            )
    finally:
        await engine.dispose()


_FINGERPRINT_FIELDS: Final = ("status", "newest step", "decisions", "ceiling", "runs")


def _what_changed(before: tuple[Any, ...], after: tuple[Any, ...]) -> str:
    changes = [
        f"{name} {was!r} → {now!r}"
        for name, was, now in zip(_FINGERPRINT_FIELDS, before, after, strict=True)
        if was != now
    ]
    return ", ".join(changes)


# --- the builders --------------------------------------------------------------------------

GATE_PAGE: Final[dict[GateKind, str]] = {
    GateKind.PLAN: "plan",
    GateKind.UNMAPPED_CONCEPTS: "financials",
    GateKind.SECTOR_SPECIALIST: "sector",
    GateKind.PEER_SET: "peers",
    GateKind.THEME_SET: "themes",
    GateKind.ASSUMPTIONS: "assumptions",
    GateKind.FINAL: "review",
}


def _console(scene: Scene, job_id: uuid.UUID) -> str:
    return f"{scene.live_server}/runs/{job_id}"


def _gate_url(scene: Scene, job_id: uuid.UUID, gate: GateKind) -> str:
    return f"{_console(scene, job_id)}/{GATE_PAGE[gate]}"


def _stop_at(worker: Worker, job_id: uuid.UUID, gate: GateKind) -> JobStatus:
    if gate is GateKind.PLAN:
        return worker.advance(job_id)
    if gate is GateKind.FINAL:
        return worker.advance_to_the_final_gate(job_id)
    return worker.advance_until(job_id, gate)


def _build_queued(state: StoppedState, scene: Scene) -> uuid.UUID:
    return run_async(_commission(scene.database_url))  # type: ignore[no-any-return]


def _build_stranded(state: StoppedState, scene: Scene) -> uuid.UUID:
    job_id: uuid.UUID = run_async(_commission(scene.database_url))
    run_async(_strand(scene.database_url, job_id))
    return job_id


def _build_step_mode(state: StoppedState, scene: Scene) -> uuid.UUID:
    job_id: uuid.UUID = run_async(_commission(scene.database_url))
    run_async(_set_step_mode(scene.database_url, job_id))
    status = Worker(scene.database_url).advance(job_id)
    if status is not JobStatus.PAUSED:
        message = f"step mode left the run {status.value}, not PAUSED"
        raise NoPathConstructedError(message)
    return job_id


def _build_gate(state: StoppedState, scene: Scene) -> uuid.UUID:
    gate = state.gate
    assert gate is not None
    url = scene.database_url
    subject = subject_for(gate)
    job_id: uuid.UUID = run_async(_commission(url, subject=subject))
    worker = Worker(
        url,
        subscribed=gate is GateKind.PEER_SET,
        facts=subject.facts,
        submissions=subject.submissions,
    )
    status = _stop_at(worker, job_id, gate)
    pending: GateKind | None = run_async(_pending_gate(url, job_id))
    if status is not JobStatus.AWAITING_APPROVAL or pending is not gate:
        message = f"the run stopped {status.value} at {pending}, not at the {gate.value} gate"
        raise NoPathConstructedError(message)
    if state.disposition is Disposition.PENDING:
        return job_id
    if state.disposition is Disposition.REJECTED:
        scene.surface.goto(_gate_url(scene, job_id, gate))
        scene.surface.press_by_id("reject", expect_url=CONSOLE_URL)
        return job_id
    run_async(_approve_by_service(url, job_id, gate))
    if state.disposition is Disposition.STALE_PAGE_MOVED:
        run_async(_move_the_page(url, job_id, gate))
        expected = PauseReason.GATE_STALE_PAGE_MOVED
    else:
        run_async(_drift_the_seal(url, job_id, gate))
        expected = PauseReason.GATE_STALE_SEAL_DRIFT
    status = worker.advance(job_id)
    reason: str | None = run_async(_pause_reason(url, job_id))
    if status is not JobStatus.AWAITING_APPROVAL or reason != expected.value:
        detail = f"after the stale approval the run is {status.value}, paused for {reason!r}"
        raise NoPathConstructedError(detail)
    return job_id


async def _drift_a_cited_excerpt(url: str, job_id: uuid.UUID) -> None:
    """Move one cited excerpt away from what its document says.

    The state a citation verifier exists to catch: the excerpt recorded against a locator is
    no longer the text at that locator, so the figure in the draft is quoting something the
    artefact does not say. Reached by surgery on the extraction row rather than by a model
    that lies, because the verifier compares the *recorded* excerpt with the document and a
    fabricated citation never gets that far — a claim naming an id no run produced is refused
    where the claim is recorded, several steps earlier.
    """
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            citation = await session.scalar(
                select(Citation)
                .join(Claim, Claim.id == Citation.claim_id)
                .join(ReportSection, ReportSection.id == Claim.report_section_id)
                .where(ReportSection.job_id == job_id, Citation.extraction_id.isnot(None))
                .order_by(Citation.created_at, Citation.id)
                .limit(1)
            )
            assert citation is not None, "the draft recorded no citation naming an extraction"
            extraction = await session.get(Extraction, citation.extraction_id)
            assert extraction is not None, "the cited extraction row is gone"
            extraction.excerpt = _DRIFTED_EXCERPT
            citation.excerpt_verified = False
            await session.commit()
    finally:
        await engine.dispose()


async def _unverified_citations(url: str, job_id: uuid.UUID) -> tuple[str, ...]:
    """Which citations the verifier refused, read after it has run.

    Read rather than predicted: one extraction can be cited by many sections, so how many
    citations a single drifted excerpt takes down is a property of what the draft happened to
    quote. The forward path is the same for one as for twelve — each accepted individually —
    and a builder that assumed one would leave the run stuck behind the other eleven.
    """
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            rows = await session.scalars(
                select(Citation)
                .join(Claim, Claim.id == Citation.claim_id)
                .join(ReportSection, ReportSection.id == Claim.report_section_id)
                .where(
                    ReportSection.job_id == job_id,
                    Citation.excerpt_verified.is_(False),
                    Citation.override_reason.is_(None),
                )
                .order_by(Citation.created_at, Citation.id)
            )
            return tuple(str(row.id) for row in rows)
    finally:
        await engine.dispose()


# --- the eight §2.4 conditions, each held before the run seals its own gate ----------------
#
# **Every one of these is arranged before the `revise` step.** The fired triggers ride
# inside the gate-2 payload hash, so a condition arranged after the seal would leave the
# page and the run computing two different payloads and no approval could ever match — the
# run would stop on drift rather than on the banner, and the row would be measuring the
# wrong state. There are two windows: before the run is commissioned at all, and at the
# assumptions gate, which is the last stop before the draft.
#
# Three of them are one scene, and that is the platform's answer rather than a shortcut: a
# required section that cites nothing is thinly sourced, below its floor *and* unsure of
# itself, so §2.4's coverage, missing-section and uncertainty conditions all genuinely
# hold. Each row asserts its own kind is among what fired; what else fired with it is what
# the run found.

# A skill asking for more per-section tokens than the platform's ceiling (12,000). The
# additive-only composer grants the ceiling and records the difference as a clamp — which
# is the §2.4 condition, and is what an operator's own file asking for headroom does.
_SKILL_ASKING_ABOVE_THE_CEILING: Final = """\
---
aer_skill: 1
key: journey_clamped_probe
kind: custom_section
title: "Clamped Probe"
version: 1
required: false
scope: global
evidence_policy:
  min_sources: 1
  requires_primary: true
  max_tier: 4
output:
  summary: string
token_budget: 16000
allowed_tools: [search_facts]
---

## What I want from this section

Anything at all; this section exists to be pinned under a clamped policy.
"""

# A cap the plan's own estimate is above 80% of, and which the run still fits inside —
# the state an operator reaches by commissioning at roughly what the platform says the
# work will cost. The guard refuses on *spend*, which a fake run barely touches, so this
# stops nothing; what it does is put the banner up before the hard cap ever has to.
#
# A constant, and the builder's own assertion is what keeps it honest: when a step's
# estimate moves far enough that this no longer clears 80%, the row fails with the
# triggers it did fire rather than quietly measuring a clean run.
_A_CAP_THE_ESTIMATE_CROWDS: Final = Decimal("10.00")


async def _seed_the_starved_section(url: str) -> None:
    """A required section whose token budget admits no evidence at all.

    The fixture the workflow tests already use for a genuinely fired banner: one token
    buys no evidence unit, so the section generates, cites nothing and misses the floor
    it declared. Seeded before the run because a section definition is what the draft
    step reads, and the draft runs long before the seal.
    """
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            await seed_starved_section(session)
            await session.commit()
    finally:
        await engine.dispose()


async def _enable_a_clamped_skill(url: str) -> None:
    """Save an operator's skill file the composer has to tighten, and switch it on.

    Enabled here rather than through the settings page because the row is about the
    *gate*, not about the settings page: a skill nobody enabled is pinned to no run, and
    the pin is what carries the clamp.
    """
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            user = await the_only_user(session)
            version = await save_skill(session, source=_SKILL_ASKING_ABOVE_THE_CEILING, actor=user)
            skill = await session.get(Skill, version.skill_id)
            assert skill is not None
            skill.enabled = True
            await session.commit()
    finally:
        await engine.dispose()


async def _flag_a_source(url: str, job_id: uuid.UUID) -> None:
    """Mark a document this run really acquired as tripping the injection heuristics.

    Written to the row the scanner writes to, because the scanner cannot be provoked from
    here: it reads the fetched bytes, and every document the fake scene holds is a real
    filing served from a stored fixture. Planting a pattern in one of those would change
    what nine other tests read out of the same file to make one banner fire.
    """
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            document = await session.scalar(
                select(SourceDocument)
                .where(SourceDocument.job_id == job_id)
                .order_by(SourceDocument.retrieved_at, SourceDocument.id)
                .limit(1)
            )
            assert document is not None, "the run acquired no source document to flag"
            document.injection_flagged = True
            # The column's check constraint refuses a flag with no findings behind it.
            document.injection_findings = [{"signal": "hidden_text", "locator": "p:nth-child(9)"}]
            await session.commit()
    finally:
        await engine.dispose()


async def _record_a_source_conflict(url: str, job_id: uuid.UUID) -> None:
    """Two licensed feeds reporting different closes for one day, put on the ladder.

    Through the service the platform itself records conflicts with, so the row is the
    ladder's own verdict rather than a shape the harness invented. Same tier, same day,
    nothing to prefer by: the ladder escalates, which is the material, unsettled conflict
    §2.4's condition is about. A fake-scene run cannot reach this on its own — the only
    writer of a non-thesis conflict is the price step, and that needs a market-data
    subscription and a vendor that has restated a bar.
    """
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            recorded = await resolve_and_record(
                session,
                job_id=job_id,
                topic="Closing price on 30 June 2022",
                first=Position(
                    reference="journey:close:held",
                    label="Held by this platform",
                    value=Decimal("256.83"),
                    unit="USD",
                    tier=SourceTier.T4_LICENSED_MARKET,
                    filed_date=AS_OF_DATE,
                ),
                second=Position(
                    reference="journey:close:incoming",
                    label="Reported now by the vendor",
                    value=Decimal("271.87"),
                    unit="USD",
                    tier=SourceTier.T4_LICENSED_MARKET,
                    filed_date=AS_OF_DATE,
                ),
            )
            assert recorded is not None, "the ladder settled the conflict rather than escalating"
            assert recorded.material, "the ladder did not call the difference material"
            await session.commit()
    finally:
        await engine.dispose()


# Arranged before the run is commissioned: everything here is a row the run itself reads.
_BEFORE_THE_RUN: Final[dict[TriggerKind, Any]] = {
    TriggerKind.LOW_SOURCE_COVERAGE: _seed_the_starved_section,
    TriggerKind.HIGH_MODEL_UNCERTAINTY: _seed_the_starved_section,
    TriggerKind.MATERIAL_MISSING_SECTION: _seed_the_starved_section,
    TriggerKind.SKILL_POLICY_CLAMP: _enable_a_clamped_skill,
}

# Arranged at the assumptions gate: rows the run has to have produced first, and which
# nothing after the draft may touch.
_AT_THE_LAST_GATE_BEFORE_THE_DRAFT: Final[dict[TriggerKind, Any]] = {
    TriggerKind.SUSPICIOUS_SOURCE: _flag_a_source,
    TriggerKind.CREDIBLE_SOURCE_CONFLICT: _record_a_source_conflict,
}

_TRIGGER_KINDS: Final[dict[str, TriggerKind]] = {kind.value: kind for kind in TriggerKind}


async def _fired_triggers(url: str, job_id: uuid.UUID) -> tuple[str, ...]:
    """Which §2.4 conditions the gate stopped on, read from the paused step's own record."""
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(
                select(JobStep)
                .where(JobStep.job_id == job_id, JobStep.status == JobStatus.AWAITING_APPROVAL)
                .order_by(JobStep.sequence.desc(), JobStep.attempt.desc())
                .limit(1)
            )
            context = ((row.error or {}) if row else {}).get("context", {})
            return tuple(str(name) for name in context.get("triggers", []))
    finally:
        await engine.dispose()


def _build_final_trigger(state: StoppedState, scene: Scene) -> uuid.UUID:
    """A run stopped at the final gate with one of §2.4's conditions holding.

    The gate always waits for a person; what a fired trigger changes is what the wait
    *says*. So every row here reaches the same page by a different route, and what each
    one measures is the banner that route produces — which is where a trigger's own
    words, and its evidence lines, reach an operator for the first time.
    """
    kind = _TRIGGER_KINDS.get(state.detail or "")
    if kind is None:
        message = f"no path constructed: {state.detail!r} is not one of the §2.4 conditions"
        raise NoPathConstructedError(message)

    url = scene.database_url
    arrange = _BEFORE_THE_RUN.get(kind)
    if arrange is not None:
        run_async(arrange(url))

    cap = (
        _A_CAP_THE_ESTIMATE_CROWDS
        if kind is TriggerKind.COST_ABOVE_THRESHOLD
        else DEFAULT_PER_RUN_BUDGET_GBP
    )
    job_id: uuid.UUID = run_async(_commission(url, max_cost_gbp=cap))
    # A writer that states one figure and cites a calculation holding another: the only
    # one of the eight whose condition is a *verdict* the platform reaches, so the run
    # has to earn it rather than be handed it.
    provider = (
        make_provider_that_misquotes_its_figures()
        if kind is TriggerKind.VALIDATION_FAILURE
        else None
    )
    worker = Worker(url, provider=provider)

    plant = _AT_THE_LAST_GATE_BEFORE_THE_DRAFT.get(kind)
    if plant is not None:
        status = worker.advance_until(job_id, GateKind.ASSUMPTIONS)
        if status is not JobStatus.AWAITING_APPROVAL:
            message = f"the run stopped {status.value} before reaching the assumptions gate"
            raise NoPathConstructedError(message)
        run_async(plant(url, job_id))

    status = worker.advance_to_the_final_gate(job_id)
    reason: str | None = run_async(_pause_reason(url, job_id))
    expected = PauseReason.FINAL_TRIGGERS_FIRED.value
    if status is not JobStatus.AWAITING_APPROVAL or reason != expected:
        detail = f"the run is {status.value}, paused for {reason!r} rather than on a banner"
        raise NoPathConstructedError(detail)

    fired = run_async(_fired_triggers(url, job_id))
    if kind.value not in fired:
        detail = f"the banner names {list(fired)}, and {kind.value} is not among them"
        raise NoPathConstructedError(detail)
    return job_id


def _build_final(state: StoppedState, scene: Scene) -> uuid.UUID:
    """A run stopped at the final gate for a reason of its own.

    Not the pending final gate — that is a `gate.FINAL.pending` row and is already green.
    These are the states where the gate step itself refuses to hand over: a citation that
    did not verify, and each escalation trigger.
    """
    if state.detail != "unverified_citations":
        return _build_final_trigger(state, scene)

    url = scene.database_url
    job_id: uuid.UUID = run_async(_commission(url))
    worker = Worker(url)
    if worker.advance_to_the_final_gate(job_id) is not JobStatus.AWAITING_APPROVAL:
        raise NoPathConstructedError("the run did not reach the final gate")

    run_async(_drift_a_cited_excerpt(url, job_id))
    # Advanced with no approval on purpose. The gate step is incomplete, so advancing re-runs
    # it and it re-reads the evidence — and the verification is inside the step, *before* the
    # approval is consulted, so it stops there whether or not anybody has approved. Recording
    # an approval here to make it re-run would leave the row measuring a press against a
    # decision the builder had already taken, which is the state no operator is ever in.
    status = worker.advance(job_id)
    reason: str | None = run_async(_pause_reason(url, job_id))
    expected = PauseReason.FINAL_UNVERIFIED_CITATIONS.value
    if status is not JobStatus.AWAITING_APPROVAL or reason != expected:
        detail = f"after the drifted excerpt the run is {status.value}, paused for {reason!r}"
        raise NoPathConstructedError(detail)

    scene.unverified_citations = run_async(_unverified_citations(url, job_id))
    if not scene.unverified_citations:
        raise NoPathConstructedError("the run paused for unverified citations and named none")
    return job_id


def _build_budget(state: StoppedState, scene: Scene) -> uuid.UUID:
    scope, _, cap_state = (state.detail or "").partition(":")
    url = scene.database_url
    cap = SMALL_CAP if scope == "per_run" else DEFAULT_PER_RUN_BUDGET_GBP
    job_id: uuid.UUID = run_async(_commission(url, max_cost_gbp=cap))
    status = Worker(url).advance_to_the_final_gate(job_id)
    stopped_on: str | None = run_async(_budget_scope(url, job_id))
    if status is not JobStatus.BUDGET_EXCEEDED or stopped_on != scope:
        message = f"the run stopped {status.value} on {stopped_on!r}, not on the {scope} budget"
        raise NoPathConstructedError(message)
    del cap_state  # the ceiling itself is the environment's, set before the server started
    return job_id


def _failure_named(code: str, *, remedy: bool) -> Exception:
    classes: dict[str, type[errors.AerError]] = {
        cls.code: cls
        for cls in vars(errors).values()
        if isinstance(cls, type) and issubclass(cls, errors.AerError)
    }
    classes[WorkflowDefinitionError.code] = WorkflowDefinitionError
    if code == "unexpected_error":
        return RuntimeError("scripted outage")
    cls = classes[code]
    context = {"remedy": "Set the provider key in the platform's settings."} if remedy else {}
    if issubclass(cls, errors.ExternalServiceError):
        return errors.ExternalServiceError("scripted outage", provider="fake", context=context)
    return cls("scripted outage", context=context)


def _build_failed(state: StoppedState, scene: Scene) -> uuid.UUID:
    code, _, variant = (state.detail or "").partition(":")
    url = scene.database_url
    worker = Worker(
        url, provider=make_provider(fail_with=_failure_named(code, remedy=variant == "remedy"))
    )
    job_id: uuid.UUID = run_async(_commission(url))
    # The engine records the failure and re-raises it, as the worker sees it.
    with contextlib.suppress(Exception):
        worker.advance(job_id)
    status: JobStatus = run_async(_status(url, job_id))
    if status is not JobStatus.FAILED:
        message = f"the scripted {code} left the run {status.value}, not FAILED"
        raise NoPathConstructedError(message)
    return job_id


def _build_problem(state: StoppedState, scene: Scene) -> uuid.UUID:
    """The problem page, reached the way an operator reaches it.

    A *conflict* is a decision posted from a page that moved under it — the stale hash the
    route refuses before recording anything. A *validation error* is a rule the approval
    service refused: here, a decision posted from a page left open after the gate was
    decided from elsewhere, over content that has not changed, which is re-assertion. Both
    land on the same page; what it offers is what the row measures.
    """
    url = scene.database_url
    job_id: uuid.UUID = run_async(_commission(url))
    if Worker(url).advance(job_id) is not JobStatus.AWAITING_APPROVAL:
        raise NoPathConstructedError("the run did not reach the plan gate")
    scene.surface.goto(_gate_url(scene, job_id, GateKind.PLAN))
    if state.detail == errors.ConflictError.code:
        scene.surface.set_hidden("payload_hash", "a" * 64)
    elif state.detail == errors.ValidationError.code:
        run_async(_approve_by_service(url, job_id, GateKind.PLAN))
    else:
        message = f"no path constructed: no route reaches the problem page with {state.detail}"
        raise NoPathConstructedError(message)
    scene.surface.press_by_id("approve")
    if not scene.surface.has("problem"):
        raise NoPathConstructedError("the post did not land on the problem page")
    return job_id


_BUILDERS: Final[dict[Family, Any]] = {
    Family.QUEUED: _build_queued,
    Family.STRANDED: _build_stranded,
    Family.STEP_MODE: _build_step_mode,
    Family.GATE: _build_gate,
    Family.BUDGET: _build_budget,
    Family.FAILED_STEP: _build_failed,
    Family.FINAL_GATE: _build_final,
    Family.PROBLEM_PAGE: _build_problem,
}


def build(state: StoppedState, scene: Scene) -> uuid.UUID:
    """Put a run into the state, or say plainly that no path could be constructed."""
    if state.key in UNCONSTRUCTED:
        message = f"no path constructed: {UNCONSTRUCTED[state.key]}"
        raise NoPathConstructedError(message)
    builder = _BUILDERS.get(state.family)
    if builder is None:
        message = f"no path constructed: no builder for the {state.family.value} family"
        raise NoPathConstructedError(message)
    try:
        return builder(state, scene)  # type: ignore[no-any-return]
    except NoPathConstructedError:
        raise
    except AssertionError as failed:
        message = f"no path constructed: {failed}"
        raise NoPathConstructedError(message) from failed


# --- the three assertions ------------------------------------------------------------------


def _page_name(surface: Surface) -> str:
    """The page as a report names it: "the console", or the gate page's own word."""
    last = surface.url.rstrip("/").rsplit("/", 1)[-1]
    return "the console" if UUID.fullmatch(last) else f"the {last} page"


# The one gate whose subject *is* a taxonomy tag. The unmapped-concepts gate asks "does this
# gap matter?" about elements a filing used and this platform could not place, and the element
# name is what the operator is deciding about — it is in the filing, it is what they would
# search the taxonomy for, and a page that hid it would be asking the question without showing
# the thing. So the tag pattern is not applied there, and only the tag pattern: a UUID, a shell
# command, a module path or a step key on that page is the same defect it is anywhere else.
#
# An exemption rather than a change to the page, and narrow enough to state in one sentence.
# The alternative — inventing prose for an element nobody has mapped — would be the platform
# guessing at exactly the point it is asking somebody else not to.
_TAGS_ARE_THE_SUBJECT: Final = frozenset({GateKind.UNMAPPED_CONCEPTS})


def assert_clean_vocabulary(surface: Surface, state: StoppedState) -> None:
    """Assertion 2. Nothing on the page is a UUID, a shell command or an identifier."""
    text = surface.text()
    offences: list[str] = []
    if UUID.search(text):
        offences.append("a UUID")
    shell = SHELL.search(text)
    if shell:
        offences.append(f"a shell command ({shell.group(0).strip()!r})")
    leaked = set(SNAKE_CASE.findall(text)) | set(SHOUTED_ENUM.findall(text))
    leaked |= set(MODULE_PATH.findall(text))
    if state.gate not in _TAGS_ARE_THE_SUBJECT:
        leaked |= set(XBRL_TAG.findall(text))
    leaked |= {name for name in CODE_IDENTIFIERS if re.search(rf"\b{re.escape(name)}\b", text)}
    if leaked:
        offences.append(f"code identifiers {sorted(leaked)[:8]}")
    if offences:
        message = f"{state.key}: {_page_name(surface)} shows {', '.join(offences)}"
        raise DeadEndError(message)


def find_forward_control(surface: Surface, state: StoppedState) -> Control:
    """Assertion 1. A visible, enabled control whose label is in the operator's vocabulary."""
    offered = [control for control in surface.controls() if control.label]
    patterns = [re.compile(pattern, re.I) for pattern in state.expected_controls]
    for control in offered:
        if any(pattern.search(control.label) for pattern in patterns):
            return control
    seen = [control.label for control in offered]
    message = (
        f"{state.key}: no forward control — expected one of {list(state.expected_controls)}, "
        f"found {seen}"
    )
    raise DeadEndError(message)


def _assert_pressing_moves(
    state: StoppedState, scene: Scene, control: Control, job_id: uuid.UUID
) -> str:
    """Assertion 3. The run's record changes, or the control leads where the remedy is."""
    surface = scene.surface
    label = control.label
    if state.press is Press.NAVIGATE:
        try:
            surface.press(control)
        except Exception as stuck:
            message = (
                f"{state.key}: pressing {label!r} did not lead on: {type(stuck).__name__}: {stuck}"
            )
            raise DeadEndError(message) from stuck
        assert state.navigates_to is not None
        if not re.search(state.navigates_to, surface.url):
            message = f"{state.key}: {label!r} led to {surface.url}, not to {state.navigates_to}"
            raise DeadEndError(message)
        return f"pressing {label!r} led to {surface.url}"

    before = run_async(_fingerprint(scene.database_url, job_id))
    try:
        if state.family is Family.GATE and state.disposition in (
            Disposition.PENDING,
            Disposition.STALE_PAGE_MOVED,
        ):
            # The control leads to the gate's page; the decision there is what moves the
            # run — a first decision, or one that supersedes a stale one (ADR 0123).
            surface.press(control, expect_url=GATE_PAGE_URL)
            surface.press_by_id("approve", expect_url=CONSOLE_URL)
        elif state.family is Family.FINAL_GATE and scene.unverified_citations:
            # The whole path an operator walks, because half of it moves nothing: the control
            # leads to the draft, every citation that failed is accepted there with a written
            # reason, and only then does approving get the run past the gate. A press that
            # stopped at the review page would call the row green while the run was still
            # stuck — which is the dead end the harness exists to find.
            #
            # One press each, and that is the point rather than an inefficiency: the gate's
            # message promises there is no way to accept them all at once, and a harness that
            # found a bulk path would be reporting that the promise is false.
            surface.press(control, expect_url=GATE_PAGE_URL)
            for citation_id in scene.unverified_citations:
                surface.fill("reason", "Read the filing directly; the figure is stated there.")
                surface.press_by_id(f"override-{citation_id}", expect_url=GATE_PAGE_URL)
            surface.press_by_id("approve", expect_url=CONSOLE_URL)
        elif state.family is Family.FINAL_GATE:
            # A fired trigger changes what the gate *says*, not what it wants: the way past
            # it is still a decision taken with the banner in view. So the control leads to
            # the draft and the decision there is what moves the run — and a press that
            # stopped at the review page would call the row green for arriving somewhere.
            surface.press(control, expect_url=GATE_PAGE_URL)
            surface.press_by_id("approve", expect_url=CONSOLE_URL)
        elif state.family is Family.BUDGET:
            surface.fill("max_cost_gbp", f"{DEFAULT_PER_RUN_BUDGET_GBP:.2f}")
            surface.press(control, expect_url=CONSOLE_URL)
            surface.press_by_id("resume-run", expect_url=CONSOLE_URL)
        else:
            surface.press(control)
    except Exception as stuck:
        # The message, not only the class. A surface that says "no control #override-… on
        # /runs/…/review" names the defect; `SurfaceError` names nothing, and the record this
        # harness keeps is read by whoever comes next rather than by whoever wrote it.
        message = (
            f"{state.key}: pressing {label!r} did not lead on: {type(stuck).__name__}: {stuck}"
        )
        raise DeadEndError(message) from stuck
    after = run_async(_fingerprint(scene.database_url, job_id))
    if before == after:
        message = f"{state.key}: pressing {label!r} changed nothing: the run is still {before}"
        raise DeadEndError(message)
    return f"pressing {label!r} moved the run: {_what_changed(before, after)}"


def check(state: StoppedState, scene: Scene, job_id: uuid.UUID) -> Verdict:
    """The three assertions, on the pages the state is met on, every one of them measured.

    A row is green only when all three hold, so stopping at the first defect would be
    enough for a verdict; it would also hide which of the three a fix moved, and the
    harness exists to give every fix a number. So every assertion that can be measured is,
    and the verdict says which failed.
    """
    surface = scene.surface
    pages: list[str | None]
    if state.family is Family.PROBLEM_PAGE:
        pages = [None]  # the page the surface landed on
    elif state.gate is not None:
        # Both pages, for a row met at a gate. The console says why the run stopped; the
        # gate's own page carries the detail behind it — and for a fired trigger that
        # detail is the §2.4 evidence, which is where a metric's name and a section's key
        # reach an operator. A row that read only the console would call the banner clean
        # because the summary was.
        pages = [_console(scene, job_id), _gate_url(scene, job_id, state.gate)]
    else:
        pages = [_console(scene, job_id)]

    red: dict[str, str] = {}
    notes: list[str] = []
    unclean: list[str] = []
    for url in pages:
        if url is not None:
            surface.goto(url)
        try:
            assert_clean_vocabulary(surface, state)
        except DeadEndError as found:
            unclean.append(str(found))
        else:
            notes.append(f"clean text on {_page_name(surface)}")
    if unclean:
        red["text"] = " | ".join(unclean)

    if pages[0] is not None:
        surface.goto(pages[0])
    try:
        control = find_forward_control(surface, state)
    except DeadEndError as found:
        red["control"] = str(found)
    else:
        notes.append(f"forward control {control.label!r}")
        try:
            notes.append(_assert_pressing_moves(state, scene, control, job_id))
        except DeadEndError as found:
            red["press"] = str(found)
    return Verdict(red=red, notes=tuple(notes))


def judge(state: StoppedState, verdict: Verdict) -> None:
    """Hold the verdict to the record.

    Raises :class:`DeadEndError` when the row is red on exactly the assertions
    `STILL_RED` records — the expected failure — and :class:`NotAsRecordedError` when the
    measured set differs from the recorded one in either direction. A row the record calls
    green that measured green returns quietly.
    """
    recorded = STILL_RED.get(state.key, {})
    measured = verdict.red
    if set(measured) != set(recorded):
        parts: list[str] = []
        moved = sorted(set(recorded) - set(measured))
        if moved:
            parts.append(f"now green on {moved} — move the record")
        broke = sorted(set(measured) - set(recorded))
        if broke:
            parts.append(f"now red on {broke}: " + "; ".join(measured[name] for name in broke))
        message = f"{state.key}: not as recorded — " + "; ".join(parts)
        raise NotAsRecordedError(message)
    if measured:
        raise DeadEndError(verdict.report())
