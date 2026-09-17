"""ADR 0123: a decision at a gate is superseded, a rejection ends the run, and the remedies
for a stale gate are controls on the page.

Four things the readiness audit found no way out of, each held here end to end on the fake
scene: a decision the page moved under is decided again and the new decision supersedes it;
a rejection ends the run and the request can be started again from the console; re-sealing
is a control that continues the run; and a refused check is re-measured for the cost of the
steps from `validate` onward rather than the whole run.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.db.models import Approval, AuditEvent, Evaluation, Job, JobStep, User
from aer.errors import ConflictError, ValidationError
from aer.services import approvals as approval_service
from aer.services.cancellation import cancellation_for
from aer.services.gates import (
    MEASURE_STEP,
    REMEASURE_EVENT,
    RESEALED_EVENT,
    remeasure_checks,
    reseal_gate,
)
from aer.services.runs import latest_run
from aer.services.themes import add_operator_theme
from aer.web.csrf import CSRF_FIELD_NAME
from aer.workflow.pauses import PauseReason
from aer.workflow.workflows.vertical_slice_v1 import build_steps, seal_step_for
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.request_fixtures import research_request
from tests.run_fixtures import Driver, start_run, to_final_gate
from tests.test_run_api import _hidden_value
from tests.workflow_fixtures import (
    AS_OF_DATE,
    CONDITIONAL_GATES,
    DEFAULT_PER_RUN_BUDGET_GBP,
    owner_of,
)

THEME_GATE_STEP = "gate_theme_set"


@pytest.fixture
async def committed(db_engine: Any) -> dict[str, Any]:
    await delete_all(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="remedies@example.invalid", display_name="Remedies", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = research_request(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        session.add(request)
        await session.commit()
        return {"user": user, "request": request}


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []

    async def record(redis: Any, job_id: uuid.UUID) -> str:
        recorded.append(str(job_id))
        return f"task-{job_id}"

    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", record)
    monkeypatch.setattr("aer.web.pages.enqueue_run", record)
    return recorded


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    enqueued: list[str],
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


@pytest.fixture
def driver(db_engine: Any, api_settings: Settings) -> Driver:
    return Driver(db_engine, api_settings)


async def _until(api: Any, request_id: uuid.UUID, driver: Driver, step: str) -> uuid.UUID:
    """Start a run and drive it until it waits at ``step``, clearing the gates before it."""
    body = await start_run(api, request_id)
    job_id = uuid.UUID(body["job_id"])
    await driver.advance(job_id)
    await driver.approve(job_id, gate=GateKind.PLAN, step="critique_plan")
    status = await driver.advance(job_id)
    for _ in range(6):
        paused = await driver.waiting_at(job_id)
        if status is not JobStatus.AWAITING_APPROVAL or paused == step:
            break
        clearing = CONDITIONAL_GATES.get(paused or "")
        assert clearing is not None, f"the run paused at {paused!r} on the way to {step}"
        gate, seal = clearing
        await driver.approve(job_id, gate=gate, step=seal)
        status = await driver.advance(job_id)
    assert await driver.waiting_at(job_id) == step, f"the run never waited at {step}"
    return job_id


async def _pause_reason(db_engine: Any, job_id: uuid.UUID) -> str | None:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        row = await session.scalar(
            select(JobStep)
            .where(JobStep.job_id == job_id, JobStep.status == JobStatus.AWAITING_APPROVAL)
            .order_by(JobStep.sequence.desc())
            .limit(1)
        )
        assert row is not None, "the run is not waiting"
        reason = (row.error or {}).get("context", {}).get("reason")
        return str(reason) if reason else None


async def _decide(
    api: Any, job_id: uuid.UUID, gate: GateKind, page: str, decision: Decision
) -> Any:
    opened = await api.get(f"/runs/{job_id}/{page}")
    assert opened.status_code == 200, opened.text
    return await api.post(
        f"/runs/{job_id}/gates/{gate.value}",
        data={
            CSRF_FIELD_NAME: _hidden_value(opened.text, CSRF_FIELD_NAME),
            "payload_hash": _hidden_value(opened.text, "payload_hash"),
            "decision": decision.value,
        },
        follow_redirects=False,
    )


class TestASupersedingDecision:
    async def _approved_then_moved(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> tuple[uuid.UUID, Approval]:
        """A theme gate approved, then a theme added: the page has moved under the approval."""
        job_id = await _until(api, committed["request"].id, driver, THEME_GATE_STEP)
        gate, seal = CONDITIONAL_GATES[THEME_GATE_STEP]
        assert gate is GateKind.THEME_SET
        await driver.approve(job_id, gate=gate, step=seal)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            first = await approval_service.current_decision(session, job_id, GateKind.THEME_SET)
            assert first is not None
            await add_operator_theme(
                session,
                job=job,
                label="Sovereign cloud",
                rationale="Public-sector demand for in-country hosting is a theme the run missed.",
                actor=await owner_of(session, job),
            )
            await session.commit()

        assert await driver.advance(job_id) is JobStatus.AWAITING_APPROVAL
        assert await _pause_reason(db_engine, job_id) == PauseReason.GATE_STALE_PAGE_MOVED.value
        return job_id, first

    async def test_the_page_names_the_stale_decision_and_still_offers_the_form(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id, _ = await self._approved_then_moved(api, committed, driver, db_engine)
        page = await api.get(f"/runs/{job_id}/themes")
        assert page.status_code == 200
        assert 'id="stale-decision"' in page.text
        assert "over an earlier version of this page" in page.text
        assert 'id="gate-form"' in page.text
        assert 'id="already-decided"' not in page.text

    async def test_deciding_again_supersedes_and_the_run_continues(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id, first = await self._approved_then_moved(api, committed, driver, db_engine)

        decided = await _decide(api, job_id, GateKind.THEME_SET, "themes", Decision.APPROVED)
        assert decided.status_code == 303, decided.text

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            current = await approval_service.current_decision(session, job_id, GateKind.THEME_SET)
            assert current is not None
            assert current.decision is Decision.AMENDED
            assert current.supersedes_id == first.id
            assert current.payload_hash != first.payload_hash
            untouched = await session.get(Approval, first.id)
            assert untouched is not None
            assert untouched.decision is Decision.APPROVED, "the superseded row was rewritten"
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.job_id == job_id, AuditEvent.event_type == "approval.amended"
                )
            )
            assert event is not None
            assert event.payload["supersedes"] == str(first.id)
            rows = await approval_service.approvals_for_job(session, job_id)
            assert [r.decision for r in rows if r.gate is GateKind.THEME_SET] == [
                Decision.APPROVED,
                Decision.AMENDED,
            ]

        status = await driver.advance(job_id)
        assert await driver.waiting_at(job_id) != THEME_GATE_STEP, status

    async def test_a_second_decision_over_unchanged_content_is_still_refused(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id, _ = await self._approved_then_moved(api, committed, driver, db_engine)
        first = await _decide(api, job_id, GateKind.THEME_SET, "themes", Decision.APPROVED)
        assert first.status_code == 303
        # The page now shows what was just amended: the form does not render, and a post
        # carrying the same hash is re-assertion.
        page = await api.get(f"/runs/{job_id}/themes")
        assert 'id="already-decided"' in page.text
        assert 'id="gate-form"' not in page.text

    async def test_a_superseding_rejection_ends_the_run(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id, first = await self._approved_then_moved(api, committed, driver, db_engine)
        decided = await _decide(api, job_id, GateKind.THEME_SET, "themes", Decision.REJECTED)
        assert decided.status_code == 303, decided.text

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.status is JobStatus.CANCELLED
            current = await approval_service.current_decision(session, job_id, GateKind.THEME_SET)
            assert current is not None
            assert current.decision is Decision.REJECTED
            assert current.supersedes_id == first.id

    async def test_the_service_refuses_superseding_anything_but_the_current_decision(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await _until(api, committed["request"].id, driver, THEME_GATE_STEP)
        gate, seal = CONDITIONAL_GATES[THEME_GATE_STEP]
        await driver.approve(job_id, gate=gate, step=seal)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            user = await owner_of(session, job)
            current = await approval_service.current_decision(session, job_id, GateKind.THEME_SET)
            assert current is not None
            plan = await approval_service.current_decision(session, job_id, GateKind.PLAN)
            assert plan is not None
            with pytest.raises(ConflictError, match="not the themes gate's current one"):
                await approval_service.record_decision(
                    session,
                    job=job,
                    gate=GateKind.THEME_SET,
                    decision=Decision.APPROVED,
                    actor=user,
                    payload_hash="c" * 64,
                    supersedes=plan,
                )
            with pytest.raises(ValidationError, match="exactly this content"):
                await approval_service.record_decision(
                    session,
                    job=job,
                    gate=GateKind.THEME_SET,
                    decision=Decision.APPROVED,
                    actor=user,
                    payload_hash=current.payload_hash,
                    supersedes=current,
                )
            with pytest.raises(ValidationError, match="supersedes another"):
                await approval_service.record_decision(
                    session,
                    job=job,
                    gate=GateKind.THEME_SET,
                    decision=Decision.AMENDED,
                    actor=user,
                    payload_hash="d" * 64,
                )


class TestARejectionEndsTheRun:
    async def test_rejecting_on_the_page_cancels_the_run_with_the_gate_as_the_reason(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        decided = await _decide(api, job_id, GateKind.PLAN, "plan", Decision.REJECTED)
        assert decided.status_code == 303, decided.text

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.status is JobStatus.CANCELLED
            cancellation = await cancellation_for(session, job_id=job_id)
            assert cancellation is not None
            assert cancellation.reason == "Rejected at the plan gate."

    async def test_the_console_offers_a_new_run_and_it_starts_one(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        await _decide(api, job_id, GateKind.PLAN, "plan", Decision.REJECTED)

        console = await api.get(f"/runs/{job_id}")
        assert console.status_code == 200
        assert 'id="start-again"' in console.text
        assert 'id="awaiting-approval"' not in console.text
        assert "Rejected at the plan gate." in console.text

        started = await api.post(
            "/runs",
            data={
                CSRF_FIELD_NAME: _hidden_value(console.text, CSRF_FIELD_NAME),
                "request_id": _hidden_value(console.text, "request_id"),
            },
            follow_redirects=False,
        )
        assert started.status_code == 303, started.text
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            newest = await latest_run(session, request_id=committed["request"].id)
            assert newest is not None
            assert newest.id != job_id
            assert newest.status is JobStatus.QUEUED
        assert started.headers["location"] == f"/runs/{newest.id}"


class TestResealIsAControl:
    async def _stuck_on_the_drift(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> uuid.UUID:
        job_id = await to_final_gate(api, committed["request"].id, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step=seal_step_for(GateKind.FINAL.value))
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(
                select(JobStep).where(
                    JobStep.job_id == job_id,
                    JobStep.step_key == seal_step_for(GateKind.FINAL.value),
                )
            )
            assert row is not None
            row.output_ref = {**(row.output_ref or {}), "payload_hash": "1" * 64}
            await session.commit()
        assert await driver.advance(job_id) is JobStatus.AWAITING_APPROVAL
        assert await _pause_reason(db_engine, job_id) == PauseReason.GATE_STALE_SEAL_DRIFT.value
        return job_id

    async def test_the_console_offers_it_and_pressing_it_continues_the_run(
        self,
        api: Any,
        committed: dict[str, Any],
        driver: Driver,
        db_engine: Any,
        enqueued: list[str],
    ) -> None:
        job_id = await self._stuck_on_the_drift(api, committed, driver, db_engine)
        console = await api.get(f"/runs/{job_id}")
        assert 'id="reseal-run"' in console.text
        assert "aer reseal" not in console.text

        pressed = await api.post(
            f"/runs/{job_id}/reseal",
            data={CSRF_FIELD_NAME: _hidden_value(console.text, CSRF_FIELD_NAME)},
            follow_redirects=False,
        )
        assert pressed.status_code == 303, pressed.text
        assert str(job_id) in enqueued

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.status is JobStatus.QUEUED
            kinds = {
                row.event_type
                for row in await session.scalars(
                    select(AuditEvent).where(AuditEvent.job_id == job_id)
                )
            }
            assert RESEALED_EVENT in kinds
            assert "run.resumed" in kinds

        assert await driver.advance(job_id) is JobStatus.SUCCEEDED

    async def test_a_run_with_nothing_sealed_is_refused_on_the_page(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        console = await api.get(f"/runs/{job_id}")
        pressed = await api.post(
            f"/runs/{job_id}/reseal",
            data={CSRF_FIELD_NAME: _hidden_value(console.text, CSRF_FIELD_NAME)},
            follow_redirects=False,
        )
        assert pressed.status_code == 422
        assert "nothing to re-seal" in pressed.text

    async def test_a_gate_decided_on_the_live_page_has_no_seal_to_move(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await _until(api, committed["request"].id, driver, THEME_GATE_STEP)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            user = await owner_of(session, job)
            with pytest.raises(ValidationError, match="decide again"):
                await reseal_gate(
                    session, job=job, actor=user, reason="a look", gate=GateKind.THEME_SET
                )


class TestRemeasureIsAControl:
    def test_the_measuring_step_is_where_the_workflow_measures(self) -> None:
        keys = [step.key for step in build_steps()]
        assert MEASURE_STEP in keys
        assert keys.index(MEASURE_STEP) < keys.index(seal_step_for(GateKind.FINAL.value))

    async def test_the_console_offers_it_only_over_a_refused_check(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await to_final_gate(api, committed["request"].id, driver)
        clean = await api.get(f"/runs/{job_id}")
        assert 'id="remeasure-run"' not in clean.text

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(select(Evaluation).where(Evaluation.job_id == job_id))
            assert row is not None
            row.passed = False
            await session.commit()
        refused = await api.get(f"/runs/{job_id}")
        assert 'id="remeasure-run"' in refused.text
        assert "1 check refused this draft" in refused.text

    async def test_pressing_it_runs_the_checks_again_as_their_next_attempt(
        self,
        api: Any,
        committed: dict[str, Any],
        driver: Driver,
        db_engine: Any,
        enqueued: list[str],
    ) -> None:
        job_id = await to_final_gate(api, committed["request"].id, driver)
        console = await api.get(f"/runs/{job_id}")
        pressed = await api.post(
            f"/runs/{job_id}/remeasure",
            data={
                CSRF_FIELD_NAME: _hidden_value(console.text, CSRF_FIELD_NAME),
                "reason": "The margin figure was corrected.",
            },
            follow_redirects=False,
        )
        assert pressed.status_code == 303, pressed.text
        assert str(job_id) in enqueued

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            assert job.status is JobStatus.QUEUED
            rows = {
                row.step_key: row
                for row in await session.scalars(select(JobStep).where(JobStep.job_id == job_id))
            }
            assert rows[MEASURE_STEP].status is JobStatus.QUEUED
            assert rows[seal_step_for(GateKind.FINAL.value)].status is JobStatus.QUEUED
            assert rows["draft"].status is JobStatus.SUCCEEDED, "a step before the checks moved"
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.job_id == job_id, AuditEvent.event_type == REMEASURE_EVENT
                )
            )
            assert event is not None
            assert event.payload["reason"] == "The margin figure was corrected."
            assert MEASURE_STEP in event.payload["invalidated"]

        assert await driver.advance(job_id) is JobStatus.AWAITING_APPROVAL
        assert await driver.waiting_at(job_id) == "gate_final"
        async with factory() as session:
            measured = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == MEASURE_STEP)
            )
            assert measured is not None
            assert measured.status is JobStatus.SUCCEEDED
            assert measured.attempt == 1, "the record lost the first attempt"
            evaluations = list(
                await session.scalars(select(Evaluation).where(Evaluation.job_id == job_id))
            )
            assert evaluations, "the checks were not written again"

    async def test_it_is_refused_before_the_checks_exist_and_after_the_run_has_finished(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            user = await owner_of(session, job)
            with pytest.raises(ValidationError, match="not measured its checks"):
                await remeasure_checks(session, job=job, actor=user, reason="too early")

        # The same run, driven to its report.
        await driver.approve(job_id, gate=GateKind.PLAN, step="critique_plan")
        status = await driver.advance(job_id)
        while status is JobStatus.AWAITING_APPROVAL and not await driver.has_run(job_id, "revise"):
            clearing = CONDITIONAL_GATES.get(await driver.waiting_at(job_id) or "")
            assert clearing is not None, "the run paused at a gate this test cannot clear"
            gate, seal = clearing
            await driver.approve(job_id, gate=gate, step=seal)
            status = await driver.advance(job_id)
        await driver.approve(job_id, gate=GateKind.FINAL, step=seal_step_for(GateKind.FINAL.value))
        assert await driver.advance(job_id) is JobStatus.SUCCEEDED

        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            user = await owner_of(session, job)
            with pytest.raises(ConflictError, match="already succeeded"):
                await remeasure_checks(session, job=job, actor=user, reason="too late")
