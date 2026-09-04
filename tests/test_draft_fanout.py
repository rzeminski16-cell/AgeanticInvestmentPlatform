"""Polish P10: section drafting fans out, bounded, on a session per section.

Drafting took 42 of the measured run's 63 minutes writing sixteen sections one after
another, and sections share the research wave's shape — each depends on the evidence pack
and on nothing another section produces. What these tests hold is the care P10 asked for,
not the speed-up itself: the fan-out is bounded at four rather than sixteen so the budget
window stays the size the research wave established; without a session factory the step
falls back to one section at a time in declared order, exactly as the engine does; the
stored output is in declared order however completion happened to schedule; and a failed
section abandons no sibling — every started draft runs to a committed outcome first,
because a paid draft discarded over someone else's error is the thing the length salvage
already exists to prevent.

The runs here are real FakeProvider runs through both gates, driven the way the worker
drives them; only the provider is replaced with one that gauges how many section calls
are in flight at once.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import GateKind, JobStatus, UserRole
from aer.db.models import (
    Cost,
    Job,
    JobStep,
    ReportSection,
    SectionDefinition,
    SectionStatus,
    User,
)
from aer.providers.fake import FakeProvider
from aer.services.resume import resume_run
from aer.workflow.workflows.vertical_slice_v1 import DRAFT_FAN_OUT
from tests.api_fixtures import build_app, client_for
from tests.request_fixtures import research_request
from tests.run_fixtures import Driver, start_run, to_final_gate
from tests.schema_guard import refuse_unanswerable_schema
from tests.workflow_fixtures import (
    AS_OF_DATE,
    CONDITIONAL_GATES,
    DEFAULT_PER_RUN_BUDGET_GBP,
    ScriptedSectionBrain,
    declared_schema_name,
)

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"


class MeteredSectionProvider(FakeProvider):
    """The scripted provider, with a concurrency gauge on the section-writer calls.

    Every section call yields for a moment before answering, so calls scheduled
    concurrently really are in flight together and ``peak`` measures the fan-out. The
    delay can differ per call — the order test slows the first section so completion
    order cannot equal declared order — and one call can be wired to fail, for the
    drain-never-abandon test. Only section schemas are gauged: the planner and the
    workers are not this step's fan-out.
    """

    def __init__(
        self,
        *args: Any,
        delay_for: Callable[[int], float] | None = None,
        fail_on_call: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.in_flight = 0
        self.peak = 0
        self.section_calls = 0
        self._delay_for = delay_for or (lambda _index: 0.05)
        self._fail_on_call = fail_on_call

    def stop_failing(self) -> None:
        """Disarm the wired outage, for a test that resumes past it."""
        self._fail_on_call = None

    async def complete_structured(self, schema: type[Any], **kwargs: Any) -> Any:
        if declared_schema_name(schema) in {"SectionDraft", "CustomSectionDraft"}:
            index = self.section_calls
            self.section_calls += 1
            if index == self._fail_on_call:
                message = "wired to fail: the metered provider's scripted outage"
                raise RuntimeError(message)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            try:
                await asyncio.sleep(self._delay_for(index))
            finally:
                self.in_flight -= 1
        return await super().complete_structured(schema, **kwargs)


def metered_driver(
    engine: Any,
    settings: Settings,
    *,
    parallel: bool,
    delay_for: Callable[[int], float] | None = None,
    fail_on_call: int | None = None,
) -> Driver:
    driver = Driver(engine, settings, parallel=parallel)
    brain = ScriptedSectionBrain()
    provider = MeteredSectionProvider(
        brain,
        inspect_schema=refuse_unanswerable_schema,
        delay_for=delay_for,
        fail_on_call=fail_on_call,
    )
    brain.provider = provider
    driver.provider = provider
    return driver


class EnqueueRecorder:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: Any, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


@pytest.fixture
async def clean_slate(db_engine: Any) -> Any:
    await _truncate(db_engine)
    yield
    await _truncate(db_engine)


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="fanout@example.invalid", display_name="P10", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
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
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        session.add(request)
        await session.commit()
        return {"user": user, "request": request}


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> EnqueueRecorder:
    recorder = EnqueueRecorder()
    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", recorder)
    monkeypatch.setattr("aer.web.pages.enqueue_run", recorder)
    return recorder


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    enqueued: EnqueueRecorder,
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


async def _draft_output(engine: Any, job_id: uuid.UUID) -> dict[str, Any]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        row = await session.scalar(
            select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == "draft")
        )
        assert row is not None, "the draft step has not run"
        return dict(row.output_ref or {})


async def _generated_count(engine: Any, job_id: uuid.UUID) -> int:
    """Model-written sections that have generated, on a fresh session."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        found = await session.scalar(
            select(func.count(ReportSection.id))
            .join(SectionDefinition, SectionDefinition.id == ReportSection.section_definition_id)
            .where(
                ReportSection.job_id == job_id,
                ReportSection.status == SectionStatus.GENERATED,
                SectionDefinition.origin == "builtin",
                SectionDefinition.token_budget > 0,
            )
        )
        return int(found or 0)


async def _model_written_keys(engine: Any, job_id: uuid.UUID) -> list[str]:
    """The run's own model-written built-ins, in the one order a report is assembled in."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        rows = await session.scalars(
            select(ReportSection.section_key)
            .join(SectionDefinition, SectionDefinition.id == ReportSection.section_definition_id)
            .where(
                ReportSection.job_id == job_id,
                SectionDefinition.origin == "builtin",
                SectionDefinition.token_budget > 0,
            )
            .order_by(ReportSection.position, ReportSection.section_key)
        )
        return list(rows)


class TestTheFanOut:
    async def test_sections_draft_four_at_a_time_and_never_more(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        """The bound is the whole point: parallel enough to matter, never sixteen at once."""
        driver = metered_driver(db_engine, api_settings, parallel=True)

        job_id = await to_final_gate(api, committed["request"].id, driver)

        assert driver.provider.peak == DRAFT_FAN_OUT
        output = await _draft_output(db_engine, job_id)
        drafted = [entry["section_key"] for entry in output["builtin_sections"]]
        assert drafted == await _model_written_keys(db_engine, job_id)
        assert output["sections_drafted"] >= len(drafted)

        # The step's recorded cost is the sum of what every fanned-out session metered —
        # a fan-out that lost its spend would report a free draft to the budget's ledger.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            step_row = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == "draft")
            )
            assert step_row is not None
            metered = await session.scalar(
                select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(
                    Cost.job_step_id == step_row.id
                )
            )
        assert step_row.cost_gbp > 0
        # The step column keeps four decimal places against the ledger's six, so the
        # comparison allows the storage rounding and nothing more.
        assert abs(step_row.cost_gbp - metered) < Decimal("0.001")

    async def test_without_a_factory_sections_draft_one_at_a_time(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        """The engine's own fallback, one level down: no factory, no concurrency."""
        driver = metered_driver(db_engine, api_settings, parallel=False)

        await to_final_gate(api, committed["request"].id, driver)

        assert driver.provider.peak == 1
        assert driver.provider.section_calls > 1

    async def test_the_stored_order_is_declared_order_not_completion_order(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        """The first section drafts slowest, so finishing order cannot match position."""
        driver = metered_driver(
            db_engine,
            api_settings,
            parallel=True,
            delay_for=lambda index: 0.3 if index == 0 else 0.01,
        )

        job_id = await to_final_gate(api, committed["request"].id, driver)

        output = await _draft_output(db_engine, job_id)
        drafted = [entry["section_key"] for entry in output["builtin_sections"]]
        assert drafted == await _model_written_keys(db_engine, job_id)


async def _drive_through_gates(driver: Driver, job_id: uuid.UUID) -> None:
    """Advance past every conditional gate until the run stops advancing — or raises."""
    status = await driver.advance(job_id)
    while status is JobStatus.AWAITING_APPROVAL:
        clearing = CONDITIONAL_GATES.get(await driver.waiting_at(job_id) or "")
        assert clearing is not None, "paused at a gate the drive does not know"
        gate, step = clearing
        await driver.approve(job_id, gate=gate, step=step)
        status = await driver.advance(job_id)


class TestDrainNeverAbandon:
    async def test_a_failed_section_abandons_no_paid_sibling(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        """One section's outage fails the step — after every sibling committed its draft.

        The engine records the failure and re-raises it, so the drive itself raises; what
        must survive is the siblings' work. The committed rows are read back on a fresh
        session, which is what proves each fanned-out section wrote and committed on a
        session of its own: the step's own session never flushed them, and the step
        itself failed.
        """
        driver = metered_driver(db_engine, api_settings, parallel=True, fail_on_call=2)

        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        await driver.approve(job_id, gate=GateKind.PLAN, step="critique_plan")

        with pytest.raises(RuntimeError, match="scripted outage"):
            await _drive_through_gates(driver, job_id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.status is JobStatus.FAILED

            # Model-written sections only: the deterministic fill rides the step's own
            # session and is committed alongside the failure record, so it would count
            # a section no fanned-out task ever touched.
            generated = await session.scalar(
                select(func.count(ReportSection.id))
                .join(
                    SectionDefinition,
                    SectionDefinition.id == ReportSection.section_definition_id,
                )
                .where(
                    ReportSection.job_id == job_id,
                    ReportSection.status == SectionStatus.GENERATED,
                    SectionDefinition.token_budget > 0,
                )
            )
        # Every section call except the wired failure ran to a committed outcome — the
        # ones already in flight and the ones still queued behind the semaphore alike —
        # and one budgeted section generated with no call at all: the valuation section
        # is filled from the record when no valuation exists (gap A51c).
        assert generated == (driver.provider.section_calls - 1) + 1
        assert (generated or 0) > 0


class TestAResumedDraftKeepsWhatWasPaidFor:
    """The engine skips a step that already succeeded; the draft step skips a section that
    already generated. Without it a run stopped part-way through drafting — by an outage,
    a crash, or its own cost ceiling — pays for every finished section a second time, and
    overwrites words a person may already have read."""

    async def test_a_section_an_earlier_attempt_wrote_is_not_written_again(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        driver = metered_driver(db_engine, api_settings, parallel=True, fail_on_call=2)

        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        await driver.approve(job_id, gate=GateKind.PLAN, step="critique_plan")
        with pytest.raises(RuntimeError, match="scripted outage"):
            await _drive_through_gates(driver, job_id)

        written_first = await _generated_count(db_engine, job_id)
        calls_first = driver.provider.section_calls
        assert written_first > 0, "the wave committed nothing to keep"

        # Resume the way an operator does: the decision is recorded, then the engine
        # re-enters and skips what succeeded.
        driver.provider.stop_failing()
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            await resume_run(session, job=job, actor=user, reason="the outage is over")
            await session.commit()
        await driver.advance(job_id)

        # Only the sections that were not already written were written on the re-entry.
        total = len(await _model_written_keys(db_engine, job_id))
        assert driver.provider.section_calls - calls_first == total - written_first
        assert await _generated_count(db_engine, job_id) == total

    async def test_the_kept_sections_stay_in_the_step_s_own_record(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        """The draft step's output is the only place the per-section record lives, and the
        escalation trigger and the review page both read it. A kept section that vanished
        from it would read as a section the run never had.

        What a kept row can say is bounded by what survives: a step writes its output only
        when it succeeds, so the attempt that wrote these sections — the one that failed —
        left no tally to carry forward. The row therefore says the section is generated and
        that this attempt did not write it, and no evidence count, which is honest about a
        record that was never written rather than a guess at one."""
        driver = metered_driver(db_engine, api_settings, parallel=True, fail_on_call=2)

        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        await driver.approve(job_id, gate=GateKind.PLAN, step="critique_plan")
        with pytest.raises(RuntimeError, match="scripted outage"):
            await _drive_through_gates(driver, job_id)

        driver.provider.stop_failing()
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            await resume_run(session, job=job, actor=user, reason="the outage is over")
            await session.commit()
        await driver.advance(job_id)

        output = await _draft_output(db_engine, job_id)
        recorded = [*output.get("builtin_sections", []), *output.get("custom_sections", [])]
        kept = [row for row in recorded if row.get("kept")]

        assert kept, "the re-entry kept nothing, so nothing was carried"
        assert output["sections_drafted"] == len(await _model_written_keys(db_engine, job_id))
        # Every section the run has is still in the record, kept or freshly written.
        assert {row["section_key"] for row in recorded} >= set(
            await _model_written_keys(db_engine, job_id)
        )
        # A kept row is marked as kept and claims no work of its own, so nothing downstream
        # reads it as an attempt that dealt no evidence.
        for row in kept:
            assert row["status"] == SectionStatus.GENERATED.value
            assert row["attempts"] == 0
            assert "evidence_dealt" not in row
