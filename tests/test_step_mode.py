"""Step mode and resumption: the same job, continued deliberately (ADR 0090).

Three layers, matching the three decisions the ADR records. The **engine** pauses a
step-mode run after every step that actually executes — `PAUSED`, on the job alone, with
the steps saying nothing — and does not pause over skipped work or after the final step.
The **resume service** is the supported way to continue the same job, appending the
decision to the audit chain rather than rewriting anything the run said about itself. And
the **diagnostic** is assembled entirely from what the steps already recorded, because the
readout must cost nothing.

The engine tests run against Postgres with a real factory and real commits, in the
`test_workflow_dag` harness's image: step mode's whole point is what another process sees
between steps, and a savepoint-joined session cannot show that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import JobStatus, RequestStatus, UserRole
from aer.db.models import Artefact, AuditEvent, Job, JobStep, ResearchRequest, User
from aer.db.models.agent_run import AgentRun
from aer.errors import ConflictError
from aer.services.resume import resume_run, set_step_mode
from aer.services.runs import RunOutcome, start_run
from aer.services.step_diagnostic import run_diagnostic
from aer.workflow.engine import StepContext, StepPaused, StepResult, WorkflowEngine, WorkflowStep
from aer.workflow.workflows.vertical_slice_v1 import WORKFLOW_VERSION
from tests.workflow_fixtures import seed_job, seed_request, seed_user


def _step(key: str, calls: dict[str, int], *, needs: frozenset[str] | None = None) -> WorkflowStep:
    async def run(context: StepContext) -> StepResult:
        calls[key] = calls.get(key, 0) + 1
        return StepResult(output={"ran": key})

    return WorkflowStep(key=key, run=run, needs=needs)


# ==========================================================================================
# The engine, against Postgres: what another process sees between steps
# ==========================================================================================


@pytest.fixture
async def clean_slate(db_engine: Any) -> None:
    """Empty everything these tests write, before each one — the dag harness's rule."""
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text(
                "TRUNCATE audit_events, costs, job_steps, job_cancellations, jobs, "
                "research_requests, users RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def scene(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    """A committed step-mode job, and the factory its executions share."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="stepper@example.invalid", display_name="Step", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

        request = ResearchRequest(
            user_id=user.id,
            company_name="Contoso Corporation",
            ticker="CTSO",
            exchange="NASDAQ",
            as_of_date=date(2023, 1, 1),
            base_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.00",
            portfolio_context={},
            status=RequestStatus.APPROVED,
        )
        session.add(request)
        await session.flush()

        job = Job(
            work_order_id=request.id,
            request_id=request.id,
            workflow_version="step-test-1",
            code_version="a1b2c3d4",
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
            step_mode=True,
        )
        session.add(job)
        await session.commit()
        return {"factory": factory, "job_id": job.id}


async def _run(scene: dict[str, Any], engine: WorkflowEngine, **services: Any) -> Any:
    async with scene["factory"]() as control:
        job = await control.get(Job, scene["job_id"])
        assert job is not None
        # What `runs.execute` does before handing over to the engine: a continued run
        # re-enters as RUNNING, whatever state it paused in.
        job.status = JobStatus.RUNNING
        await control.commit()
        return await engine.run(control, job=job, services=services)


async def _rows(scene: dict[str, Any]) -> dict[str, JobStep]:
    async with scene["factory"]() as session:
        found = await session.scalars(select(JobStep).where(JobStep.job_id == scene["job_id"]))
        return {row.step_key: row for row in found}


async def _job_status(scene: dict[str, Any]) -> JobStatus:
    async with scene["factory"]() as session:
        job = await session.get(Job, scene["job_id"])
        assert job is not None
        return job.status


@pytest.mark.integration
class TestStepModeWalksTheRun:
    async def test_one_executed_step_per_call_and_no_pause_after_the_last(
        self, scene: dict[str, Any]
    ) -> None:
        calls: dict[str, int] = {}
        engine = WorkflowEngine([_step("a", calls), _step("b", calls), _step("c", calls)])

        await _run(scene, engine)
        assert calls == {"a": 1}
        assert await _job_status(scene) is JobStatus.PAUSED
        rows = await _rows(scene)
        # The pause is the job's state and never a step's: the executed step finished
        # exactly as it would have without step mode, and recorded no error-shaped thing.
        assert rows["a"].status is JobStatus.SUCCEEDED
        assert rows["a"].error is None

        await _run(scene, engine)
        assert calls == {"a": 1, "b": 1}
        assert await _job_status(scene) is JobStatus.PAUSED

        await _run(scene, engine)
        assert calls == {"a": 1, "b": 1, "c": 1}
        # The final step must finish the run, not flip it back to a pause over nothing.
        # The engine leaves the job as the caller had it; what matters is that it did not
        # write PAUSED over a run with nothing left to execute.
        assert await _job_status(scene) is JobStatus.RUNNING

    async def test_already_completed_steps_are_skipped_without_pausing(
        self, scene: dict[str, Any]
    ) -> None:
        calls: dict[str, int] = {}
        engine = WorkflowEngine(
            [_step("a", calls), _step("b", calls), _step("c", calls), _step("d", calls)]
        )

        # Two steps banked while step mode was off — the crash-recovery shape.
        async with scene["factory"]() as session:
            job = await session.get(Job, scene["job_id"])
            assert job is not None
            job.step_mode = False
            await session.commit()
        await _run(scene, WorkflowEngine([_step("a", calls), _step("b", calls)]))
        assert calls == {"a": 1, "b": 1}

        async with scene["factory"]() as session:
            job = await session.get(Job, scene["job_id"])
            assert job is not None
            job.step_mode = True
            await session.commit()

        await _run(scene, engine)

        # The banked steps were passed for free — one new execution, then the pause.
        assert calls == {"a": 1, "b": 1, "c": 1}
        assert await _job_status(scene) is JobStatus.PAUSED

    async def test_a_gate_still_pauses_as_a_gate_and_its_step_pauses_after_approval(
        self, scene: dict[str, Any]
    ) -> None:
        calls: dict[str, int] = {}
        approvals = {"granted": False}

        async def gate(context: StepContext) -> StepResult:
            if not approvals["granted"]:
                message = "A person has to look at this."
                raise StepPaused(message, gate="the-gate")
            return StepResult(output={"ran": "gate_b"})

        engine = WorkflowEngine(
            [_step("a", calls), WorkflowStep(key="gate_b", run=gate), _step("c", calls)]
        )

        await _run(scene, engine)
        assert await _job_status(scene) is JobStatus.PAUSED

        await _run(scene, engine)
        # The gate's pause outranks the deliberate one: waiting for an approval is a
        # different state from waiting for the operator's next step, and the console
        # must be able to tell them apart.
        assert await _job_status(scene) is JobStatus.AWAITING_APPROVAL

        approvals["granted"] = True
        await _run(scene, engine)
        # The gate's own step executed this time — and with a step still pending, the
        # run pauses deliberately again rather than running on.
        rows = await _rows(scene)
        assert rows["gate_b"].status is JobStatus.SUCCEEDED
        assert await _job_status(scene) is JobStatus.PAUSED
        assert calls.get("c") is None

    async def test_independent_nodes_do_not_form_a_wave_under_step_mode(
        self, scene: dict[str, Any]
    ) -> None:
        calls: dict[str, int] = {}
        engine = WorkflowEngine(
            [
                _step("a", calls),
                _step("b", calls, needs=frozenset({"a"})),
                _step("c", calls, needs=frozenset({"a"})),
            ]
        )

        await _run(scene, engine, session_factory=scene["factory"])
        assert calls == {"a": 1}

        await _run(scene, engine, session_factory=scene["factory"])
        # A factory is present and two nodes are ready; a step-through still executes
        # exactly one, because seven nodes at once would have nothing coherent to confirm.
        assert sum(calls.values()) == 2
        assert await _job_status(scene) is JobStatus.PAUSED


# ==========================================================================================
# The resume service: a recorded decision, never a rewrite
# ==========================================================================================


async def _latest_event(session: AsyncSession) -> AuditEvent | None:
    return await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))


class TestResumeIsARecordedDecision:
    async def test_a_failed_run_resumes_as_itself_and_the_chain_says_so(
        self, db_session: AsyncSession
    ) -> None:
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)
        job.status = JobStatus.FAILED

        await resume_run(db_session, job=job, actor=user, reason="the red team step died")

        assert job.status is JobStatus.QUEUED
        event = await _latest_event(db_session)
        assert event is not None
        assert event.event_type == "run.resumed"
        assert event.payload["resumed_from"] == "FAILED"
        assert event.payload["reason"] == "the red team step died"
        assert event.job_id == job.id

    @pytest.mark.parametrize(
        "status", [JobStatus.SUCCEEDED, JobStatus.CANCELLED, JobStatus.RUNNING]
    )
    async def test_the_unresumable_states_are_refused_with_the_remedy(
        self, db_session: AsyncSession, status: JobStatus
    ) -> None:
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)
        job.status = status

        with pytest.raises(ConflictError, match=status.value):
            await resume_run(db_session, job=job, actor=user)

        # Refused means untouched: no status change, no event describing a resume that
        # did not happen.
        assert job.status is status
        event = await _latest_event(db_session)
        assert event is None or event.event_type != "run.resumed"

    async def test_a_paused_run_is_not_superseded_by_starting_again(
        self, db_session: AsyncSession
    ) -> None:
        # PAUSED is non-terminal, so the run *is* the run: asking to start again returns
        # it rather than replacing it, exactly as for a run waiting at a gate.
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)
        job.status = JobStatus.PAUSED

        again = await start_run(db_session, request=request)

        assert again.id == job.id

    async def test_paused_counts_as_waiting_not_finished(self) -> None:
        outcome = RunOutcome(
            job=None,  # type: ignore[arg-type] -- the property reads only the status
            outputs={},
            status=JobStatus.PAUSED,
            spend_gbp=Decimal(0),
        )
        assert outcome.is_waiting

    async def test_step_mode_changes_are_recorded_and_idempotent_changes_are_not(
        self, db_session: AsyncSession
    ) -> None:
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)

        await set_step_mode(db_session, job=job, actor=user, enabled=True)
        assert job.step_mode is True
        event = await _latest_event(db_session)
        assert event is not None
        assert event.event_type == "run.step_mode_changed"
        assert event.payload["enabled"] is True

        marker = event.id
        await set_step_mode(db_session, job=job, actor=user, enabled=True)
        event = await _latest_event(db_session)
        assert event is not None
        assert event.id == marker, "asking for the state the run is in wrote a record"

    async def test_step_mode_cannot_be_set_on_a_finished_run(
        self, db_session: AsyncSession
    ) -> None:
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)
        job.status = JobStatus.SUCCEEDED

        with pytest.raises(ConflictError, match="no further steps"):
            await set_step_mode(db_session, job=job, actor=user, enabled=True)


# ==========================================================================================
# The diagnostic: assembled from the record, costing nothing
# ==========================================================================================


class TestTheDiagnosticIsAssembledFromTheRecord:
    async def test_the_readout_carries_what_each_step_recorded(
        self, db_session: AsyncSession
    ) -> None:
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)
        job.status = JobStatus.FAILED
        started = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)

        done = JobStep(
            job_id=job.id,
            step_key="plan",
            sequence=0,
            status=JobStatus.SUCCEEDED,
            attempt=0,
            idempotency_key=f"{job.id}:plan",
            input_hash="a" * 64,
            output_ref={"plan_id": "abc", "planned_sources": 1},
            cost_gbp=Decimal("0.1700"),
            started_at=started,
            finished_at=started + timedelta(seconds=12),
        )
        failed = JobStep(
            job_id=job.id,
            step_key="acquire",
            sequence=1,
            status=JobStatus.FAILED,
            attempt=2,
            idempotency_key=f"{job.id}:acquire",
            input_hash="b" * 64,
            error={"code": "external_service", "message": "EDGAR said 503."},
            cost_gbp=Decimal("0.0000"),
            started_at=started + timedelta(seconds=20),
            finished_at=started + timedelta(seconds=21),
        )
        db_session.add_all([done, failed])
        await db_session.flush()

        request_artefact = Artefact(
            sha256="c" * 64, media_type="application/json", size_bytes=10, storage_key="c"
        )
        response_artefact = Artefact(
            sha256="d" * 64, media_type="application/json", size_bytes=10, storage_key="d"
        )
        db_session.add_all([request_artefact, response_artefact])
        await db_session.flush()
        db_session.add(
            AgentRun(
                job_step_id=done.id,
                agent_role="planner",
                provider="anthropic",
                model="claude-opus-5",
                effort="high",
                request_payload_ref=request_artefact.id,
                response_payload_ref=response_artefact.id,
                input_tokens=1200,
                output_tokens=300,
                stop_reason="end_turn",
                latency_ms=900,
            )
        )
        await db_session.flush()

        readout = await run_diagnostic(db_session, job_id=job.id)

        assert readout.status is JobStatus.FAILED
        assert [step.key for step in readout.steps] == ["plan", "acquire"]

        plan = readout.step("plan")
        assert plan is not None
        assert plan.attempts == 1
        assert plan.elapsed_seconds == 12.0
        assert plan.output == {"plan_id": "abc", "planned_sources": 1}
        exchange = plan.exchanges[0]
        assert exchange.model == "claude-opus-5"
        assert exchange.request_sha256 == "c" * 64
        assert exchange.response_sha256 == "d" * 64

        acquire = readout.step("acquire")
        assert acquire is not None
        assert acquire.attempts == 3
        assert (acquire.error or {})["code"] == "external_service"

        # The failed step is what the engine executes next; the rest of the declared
        # workflow follows in order, so the readout says what is still to come.
        assert readout.next_step == "acquire"
        assert job.workflow_version == WORKFLOW_VERSION
        # The first declared-but-unrecorded step; the critique step (ADR 0091)
        # follows the plan in the shipped workflow.
        assert readout.not_reached[0] == "critique_plan"

    async def test_an_unknown_run_is_refused_by_name(self, db_session: AsyncSession) -> None:
        from aer.errors import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError, match="No run"):
            await run_diagnostic(db_session, job_id=uuid.uuid4())
