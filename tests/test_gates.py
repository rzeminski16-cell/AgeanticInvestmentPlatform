"""The final gate's seal follows the record, and a decision is taken over a settled record.

The first live run of the confirmation runbook settled a red-team challenge on the review
page, approved what the page showed, and stopped: "what this run sealed and what the review
page shows have drifted apart". The payload lists only the disagreements still open, so the
settle moved it; the seal, written by the revise step, stayed where it was; the approval
carried the page's hash; and the engine compared it against the seal. Every hash was
honest and the run could not be released.

These tests hold the two rules and the recovery: settling before the decision re-seals, so
the operator's next approval matches; settling after the decision is refused, so a recorded
decision is never taken over a record that then changes; and `reseal_final_gate` moves a
stuck run's seal to its own record and says whether the approval now matches.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.disagreement import ResolutionOutcome
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.db.models import AuditEvent, Disagreement, Job, User
from aer.errors import ConflictError, ValidationError
from aer.services import approvals as approval_service
from aer.services.approvals import payload_hash_for
from aer.services.disagreements import settle_by_hand
from aer.services.gates import (
    RESEALED_EVENT,
    refuse_settling_after_decision,
    reseal_final_gate,
)
from aer.services.resume import resume_run
from aer.web.csrf import CSRF_FIELD_NAME
from aer.workflow.workflows.vertical_slice_v1 import gate_payload
from tests.api_fixtures import build_app, client_for
from tests.request_fixtures import research_request
from tests.run_fixtures import Driver, start_run, to_final_gate
from tests.test_run_api import _hidden_value, _planted_challenge
from tests.workflow_fixtures import AS_OF_DATE, CONDITIONAL_GATES, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"


@pytest.fixture
async def clean_slate(db_engine: Any) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="gates@example.invalid", display_name="Gates", role=UserRole.OWNER)
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


async def _at_the_final_gate_with_a_challenge(
    api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
) -> uuid.UUID:
    """A run at the final gate whose seal *includes* an open challenge.

    Planted after the plan gate and before the run reaches the revise step, because that
    is where the red team's challenges are when the seal is written. A challenge planted
    after the seal would put the page and the seal at odds before anything was settled —
    a different defect from the one these tests hold — and settling it would bring the
    page back to the seal rather than move it away.
    """
    body = await start_run(api, committed["request"].id)
    job_id = uuid.UUID(body["job_id"])
    await driver.advance(job_id)
    await driver.approve(job_id, gate=GateKind.PLAN, step="critique_plan")

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(_planted_challenge(job_id))
        await session.commit()

    status = await driver.advance(job_id)
    while status is JobStatus.AWAITING_APPROVAL and not await driver.has_run(job_id, "revise"):
        paused_at = await driver.waiting_at(job_id)
        clearing = CONDITIONAL_GATES.get(paused_at or "")
        if clearing is None:
            break
        gate, step = clearing
        await driver.approve(job_id, gate=gate, step=step)
        status = await driver.advance(job_id)
    assert await driver.has_run(job_id, "revise"), "the run did not reach the final gate"
    return job_id


async def _settle_on_the_page(api: Any, job_id: uuid.UUID) -> Any:
    page = await api.get(f"/runs/{job_id}/review")
    token = _hidden_value(page.text, CSRF_FIELD_NAME)
    challenge = re.search(r'data-challenge="([^"]+)"', page.text)
    assert challenge, "the challenge did not render"
    return await api.post(
        f"/runs/{job_id}/disagreements/{challenge.group(1)}/settle",
        data={
            CSRF_FIELD_NAME: token,
            "outcome": "chose_a",
            "rationale": "The fade sits inside the peer range once the outlier is dropped.",
        },
        follow_redirects=False,
    )


async def _approve_on_the_page(api: Any, job_id: uuid.UUID) -> Any:
    page = await api.get(f"/runs/{job_id}/review")
    return await api.post(
        f"/runs/{job_id}/gates/{GateKind.FINAL.value}",
        data={
            CSRF_FIELD_NAME: _hidden_value(page.text, CSRF_FIELD_NAME),
            "payload_hash": _hidden_value(page.text, "payload_hash"),
            "decision": Decision.APPROVED.value,
        },
        follow_redirects=False,
    )


class TestSettlingBeforeTheDecision:
    async def test_the_seal_follows_the_settle_and_the_approval_then_releases_the_run(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        """The operator's own path on the first live run, now ending in a rendered report
        rather than a run nothing can release."""
        job_id = await _at_the_final_gate_with_a_challenge(api, committed, driver, db_engine)
        sealed_before = await driver.payload_hash_of(job_id, "revise")

        assert (await _settle_on_the_page(api, job_id)).status_code == 303

        sealed_after = await driver.payload_hash_of(job_id, "revise")
        assert sealed_after != sealed_before, "the settle moved the payload and not the seal"

        assert (await _approve_on_the_page(api, job_id)).status_code == 303
        assert await driver.advance(job_id) is JobStatus.SUCCEEDED
        assert await driver.has_run(job_id, "render")

    async def test_the_move_is_on_the_audit_chain(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await _at_the_final_gate_with_a_challenge(api, committed, driver, db_engine)
        await _settle_on_the_page(api, job_id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.job_id == job_id, AuditEvent.event_type == RESEALED_EVENT
                )
            )
        assert event is not None
        assert event.payload["from"] != event.payload["to"]
        assert "settled by hand" in event.payload["reason"]


class TestSettlingAfterTheDecision:
    async def test_it_is_refused_and_the_record_stands(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await _at_the_final_gate_with_a_challenge(api, committed, driver, db_engine)
        assert (await _approve_on_the_page(api, job_id)).status_code == 303
        sealed = await driver.payload_hash_of(job_id, "revise")

        refused = await _settle_on_the_page(api, job_id)

        assert refused.status_code == 400
        assert "already approved" in refused.text
        assert await driver.payload_hash_of(job_id, "revise") == sealed
        # And the approval still releases the run, because nothing under it moved.
        assert await driver.advance(job_id) is JobStatus.SUCCEEDED

    async def test_the_service_rule_names_the_decision(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await _at_the_final_gate_with_a_challenge(api, committed, driver, db_engine)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            with pytest.raises(ValidationError, match="already approved"):
                await refuse_settling_after_decision(session, job=job)


class TestResealingAStuckRun:
    async def _stuck_on_the_drift(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> uuid.UUID:
        """The first live run's state, reproduced: the seal predates a settle the
        approval was taken after. Written straight to the rows, the way the run before
        this module left them."""
        job_id = await _at_the_final_gate_with_a_challenge(api, committed, driver, db_engine)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            found = await session.scalar(select(Disagreement).where(Disagreement.job_id == job_id))
            assert job is not None
            assert user is not None
            assert found is not None
            await settle_by_hand(
                session,
                disagreement=found,
                outcome=ResolutionOutcome.CHOSE_A,
                actor=user,
                rationale="Settled without re-sealing, as the old page did.",
            )
            await approval_service.record_decision(
                session,
                job=job,
                gate=GateKind.FINAL,
                decision=Decision.APPROVED,
                actor=user,
                payload_hash=payload_hash_for(
                    await gate_payload(session, job=job, gate=GateKind.FINAL.value)
                ),
            )
            await session.commit()
        # The engine refuses: the approval matches the page, the seal matches neither.
        assert await driver.advance(job_id) is JobStatus.AWAITING_APPROVAL
        return job_id

    async def test_it_moves_the_seal_and_the_recorded_approval_then_releases_the_run(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await self._stuck_on_the_drift(api, committed, driver, db_engine)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            outcome = await reseal_final_gate(
                session, job=job, actor=user, reason="the seal predates the settle"
            )
            assert outcome.changed
            assert outcome.approval_matches is True
            await resume_run(session, job=job, actor=user, reason="re-sealed")
            await session.commit()

        assert await driver.advance(job_id) is JobStatus.SUCCEEDED
        assert await driver.has_run(job_id, "render")

    async def test_a_seal_that_already_matches_is_left_alone(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await to_final_gate(api, committed["request"].id, driver)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            outcome = await reseal_final_gate(session, job=job, actor=user, reason="a look")
            assert not outcome.changed
            assert outcome.approval_matches is None
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.job_id == job_id, AuditEvent.event_type == RESEALED_EVENT
                )
            )
            assert event is None, "an unmoved seal wrote an event"

    async def test_a_run_that_has_not_sealed_is_refused(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)  # stops at the plan gate; revise has not run

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            with pytest.raises(ValidationError, match="not sealed"):
                await reseal_final_gate(session, job=job, actor=user, reason="too early")

    async def test_a_finished_run_is_refused(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await to_final_gate(api, committed["request"].id, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        assert await driver.advance(job_id) is JobStatus.SUCCEEDED

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            with pytest.raises(ConflictError, match="already succeeded"):
                await reseal_final_gate(session, job=job, actor=user, reason="too late")
