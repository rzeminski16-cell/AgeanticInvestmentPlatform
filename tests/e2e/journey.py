"""The journey harness, browser half: every stopped state offers a way forward.

The instrument for ISSUE 1 (`docs/V1.0_Alpha/11-testing-strategy.md` §3.1). The inventory of
stopped states comes from `tests/journey_inventory.py`, generated from code; this module puts
a run into each state on the fake scene and, in a real browser, makes three assertions:

1. **A forward control exists** — visible, enabled, and labelled in the operator's vocabulary
   (*Continue this run*, *Raise the ceiling*, *Start again*), never *Retry* and never a bare
   identifier.
2. **The visible text is clean** — no UUID, no shell command, no code identifier. The
   vocabulary ratchet, asserted rather than hoped for.
3. **Pressing it moves** — the run's own record changes, or the control leads to the surface
   where the remedy lives. A button that re-renders the same dead end is the defect.

A state the harness cannot construct raises :class:`NoPathConstructedError` and never skips: an
unreachable row is either a defect in the harness or a dead branch in the engine, and the
harness says which by failing. Each assertion raises :class:`DeadEndError`, and only that,
so `tests/e2e/test_journey.py` can mark a row expected-red on that exception alone.

Everything here is a person's job or a worker's, never both: the browser does what an
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
from typing import Any, Final

from playwright.sync_api import Locator, Page
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer import errors
from aer.core.enums import Decision, GateKind, JobStatus
from aer.core.escalation import TriggerKind
from aer.db.models import Approval, Job, JobStep, WorkOrder
from aer.eval.metrics import Metric
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.services.approvals import payload_hash_for
from aer.workflow.engine import WorkflowDefinitionError
from aer.workflow.workflows.vertical_slice_v1 import build_steps, gate_payload, seal_step_for
from tests.db_fixtures import run_async
from tests.e2e.worker import Worker
from tests.journey_inventory import (
    UNCONSTRUCTED,
    Disposition,
    Family,
    Press,
    StoppedState,
    failed_step_codes,
)
from tests.request_fixtures import research_request
from tests.workflow_fixtures import (
    AS_OF_DATE,
    DEFAULT_PER_RUN_BUDGET_GBP,
    make_provider,
    owner_of,
    the_only_user,
)

__all__ = ["DeadEndError", "NoPathConstructedError", "Scene", "build", "check"]

CONSOLE_URL: Final = re.compile(
    r"/runs/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
STRANDED_FOR_SECONDS: Final = 120
SMALL_CAP: Final = Decimal("2.00")


class DeadEndError(AssertionError):
    """One of the three assertions failed: a dead end, or a page that speaks in code."""


class NoPathConstructedError(AssertionError):
    """The harness could not put a run into this state on the fake scene."""


@dataclass(slots=True)
class Scene:
    page: Page
    live_server: str
    database_url: str


# --- the vocabulary ------------------------------------------------------------------------

UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ROOT: Final = Path(__file__).resolve().parents[2]


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
SHOUTED_ENUM: Final = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")
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
    # NullPool for the reason every helper in this directory gives: each call runs on its own
    # loop and an asyncpg connection belongs to the loop that opened it.
    return create_async_engine(url, poolclass=NullPool)


async def _commission(url: str, *, max_cost_gbp: Decimal = DEFAULT_PER_RUN_BUDGET_GBP) -> uuid.UUID:
    engine = _engine(url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            user = await the_only_user(session)
            request = research_request(
                user_id=user.id,
                company_name="Microsoft Corporation",
                ticker="MSFT",
                exchange="NASDAQ",
                as_of_date=AS_OF_DATE,
                point_in_time=True,
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


async def _paused_message(url: str, job_id: uuid.UUID) -> str:
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
            return str(((row.error or {}) if row else {}).get("message", ""))
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
    job_id: uuid.UUID = run_async(_commission(url))
    worker = Worker(url, subscribed=gate is GateKind.PEER_SET)
    status = _stop_at(worker, job_id, gate)
    pending: GateKind | None = run_async(_pending_gate(url, job_id))
    if status is not JobStatus.AWAITING_APPROVAL or pending is not gate:
        message = f"the run stopped {status.value} at {pending}, not at the {gate.value} gate"
        raise NoPathConstructedError(message)
    if state.disposition is Disposition.PENDING:
        return job_id
    if state.disposition is Disposition.REJECTED:
        scene.page.goto(_gate_url(scene, job_id, gate))
        scene.page.click("#reject")
        scene.page.wait_for_url(CONSOLE_URL)
        return job_id
    run_async(_approve_by_service(url, job_id, gate))
    if state.disposition is Disposition.STALE_PAGE_MOVED:
        run_async(_move_the_page(url, job_id, gate))
        expected = "moved"
    else:
        run_async(_drift_the_seal(url, job_id, gate))
        expected = "drifted"
    status = worker.advance(job_id)
    paused: str = run_async(_paused_message(url, job_id))
    if status is not JobStatus.AWAITING_APPROVAL or expected not in paused.lower():
        detail = f"after the stale approval the run is {status.value}: {paused[:160]!r}"
        raise NoPathConstructedError(detail)
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
    """A stale hash posted to a live gate: the one problem page the fake scene reaches."""
    url = scene.database_url
    job_id: uuid.UUID = run_async(_commission(url))
    if Worker(url).advance(job_id) is not JobStatus.AWAITING_APPROVAL:
        raise NoPathConstructedError("the run did not reach the plan gate")
    scene.page.goto(_gate_url(scene, job_id, GateKind.PLAN))
    scene.page.evaluate("document.getElementById('payload-hash').value = 'a'.repeat(64)")
    scene.page.click("#approve")
    if scene.page.locator("#problem").count() == 0:
        raise NoPathConstructedError("the stale post did not land on the problem page")
    return job_id


_BUILDERS: Final[dict[Family, Any]] = {
    Family.QUEUED: _build_queued,
    Family.STRANDED: _build_stranded,
    Family.STEP_MODE: _build_step_mode,
    Family.GATE: _build_gate,
    Family.BUDGET: _build_budget,
    Family.FAILED_STEP: _build_failed,
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


def _main_text(page: Page) -> str:
    main = page.locator("main")
    return main.first.inner_text() if main.count() else page.locator("body").inner_text()


def _page_name(page: Page) -> str:
    """The page as a report names it: "the console", or the gate page's own word."""
    last = page.url.rstrip("/").rsplit("/", 1)[-1]
    return "the console" if UUID.fullmatch(last) else f"the {last} page"


_FINGERPRINT_FIELDS: Final = ("status", "newest step", "decisions", "ceiling", "runs")


def _what_changed(before: tuple[Any, ...], after: tuple[Any, ...]) -> str:
    changes = [
        f"{name} {was!r} → {now!r}"
        for name, was, now in zip(_FINGERPRINT_FIELDS, before, after, strict=True)
        if was != now
    ]
    return ", ".join(changes)


def assert_clean_vocabulary(page: Page, state: StoppedState) -> None:
    """Assertion 2. Nothing on the page is a UUID, a shell command or an identifier."""
    text = _main_text(page)
    offences: list[str] = []
    if UUID.search(text):
        offences.append("a UUID")
    shell = SHELL.search(text)
    if shell:
        offences.append(f"a shell command ({shell.group(0).strip()!r})")
    leaked = set(SNAKE_CASE.findall(text)) | set(SHOUTED_ENUM.findall(text))
    leaked |= set(XBRL_TAG.findall(text)) | set(MODULE_PATH.findall(text))
    leaked |= {name for name in CODE_IDENTIFIERS if re.search(rf"\b{re.escape(name)}\b", text)}
    if leaked:
        offences.append(f"code identifiers {sorted(leaked)[:8]}")
    if offences:
        message = f"{state.key}: {_page_name(page)} shows {', '.join(offences)}"
        raise DeadEndError(message)


def find_forward_control(page: Page, state: StoppedState) -> tuple[Locator, str]:
    """Assertion 1. A visible, enabled control whose label is in the operator's vocabulary."""
    scope = page.locator("main") if page.locator("main").count() else page.locator("body")
    candidates = [*scope.get_by_role("link").all(), *scope.get_by_role("button").all()]
    labelled: list[tuple[Locator, str]] = []
    for control in candidates:
        with contextlib.suppress(Exception):
            if control.is_visible() and control.is_enabled():
                labelled.append((control, " ".join(control.inner_text().split())))
    patterns = [re.compile(pattern, re.I) for pattern in state.expected_controls]
    for control, label in labelled:
        if label and any(pattern.search(label) for pattern in patterns):
            return control, label
    seen = [label for _, label in labelled if label]
    message = (
        f"{state.key}: no forward control — expected one of {list(state.expected_controls)}, "
        f"found {seen}"
    )
    raise DeadEndError(message)


def _assert_pressing_moves(
    state: StoppedState, scene: Scene, control: Locator, label: str, job_id: uuid.UUID
) -> str:
    """Assertion 3. The run's record changes, or the control leads where the remedy is."""
    page = scene.page
    if state.press is Press.NAVIGATE:
        control.click()
        with contextlib.suppress(Exception):
            page.wait_for_load_state("load")
        assert state.navigates_to is not None
        if not re.search(state.navigates_to, page.url):
            message = f"{state.key}: {label!r} led to {page.url}, not to {state.navigates_to}"
            raise DeadEndError(message)
        return f"pressing {label!r} led to {page.url}"

    before = run_async(_fingerprint(scene.database_url, job_id))
    try:
        if state.family is Family.GATE and state.disposition is Disposition.PENDING:
            control.click()
            page.wait_for_url(re.compile(r"/runs/[0-9a-f-]{36}/\w+$"))
            page.click("#approve")
            page.wait_for_url(CONSOLE_URL)
        elif state.family is Family.BUDGET:
            page.fill(
                "#raise-cap-form input[name='max_cost_gbp']", f"{DEFAULT_PER_RUN_BUDGET_GBP:.2f}"
            )
            control.click()
            page.wait_for_url(CONSOLE_URL)
            page.click("#resume-run")
            page.wait_for_url(CONSOLE_URL)
        else:
            control.click()
            with contextlib.suppress(Exception):
                page.wait_for_load_state("load")
    except Exception as stuck:
        message = f"{state.key}: pressing {label!r} did not lead on: {type(stuck).__name__}"
        raise DeadEndError(message) from stuck
    after = run_async(_fingerprint(scene.database_url, job_id))
    if before == after:
        message = f"{state.key}: pressing {label!r} changed nothing: the run is still {before}"
        raise DeadEndError(message)
    return f"pressing {label!r} moved the run: {_what_changed(before, after)}"


def check(state: StoppedState, scene: Scene, job_id: uuid.UUID) -> None:
    """The three assertions, on the pages the state is met on, every one of them reported.

    A row is green only when all three hold, so stopping at the first defect would be
    enough for the verdict; it would also hide which of the three a fix moved, and the
    harness exists to give every fix a number.
    """
    page = scene.page
    page.set_default_timeout(10_000)
    pages: list[str | None]
    if state.family is Family.PROBLEM_PAGE:
        pages = [None]  # the page the browser landed on
    elif state.family is Family.GATE and state.gate is not None:
        pages = [_console(scene, job_id), _gate_url(scene, job_id, state.gate)]
    else:
        pages = [_console(scene, job_id)]

    report: list[str] = []
    failed = False
    for url in pages:
        if url is not None:
            page.goto(url)
        try:
            assert_clean_vocabulary(page, state)
        except DeadEndError as found:
            failed = True
            report.append(str(found))
        else:
            report.append(f"clean text on {_page_name(page)}")

    if pages[0] is not None:
        page.goto(pages[0])
    try:
        control, label = find_forward_control(page, state)
    except DeadEndError as found:
        failed = True
        report.append(str(found))
    else:
        report.append(f"forward control {label!r}")
        try:
            report.append(_assert_pressing_moves(state, scene, control, label, job_id))
        except DeadEndError as found:
            failed = True
            report.append(str(found))
    if failed:
        raise DeadEndError(" | ".join(report))
