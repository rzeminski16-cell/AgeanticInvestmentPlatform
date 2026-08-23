"""Schema behaviour against a real PostgreSQL.

These run against the actual database rather than a mock or SQLite, because almost
everything worth testing here is a PostgreSQL behaviour: native enum rejection, cascade
semantics, CHECK constraints, and composite unique constraints. A SQLite stand-in would
pass while the production database rejected the same data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from aer.core.enums import (
    AnalysisMode,
    Decision,
    GateKind,
    JobStatus,
    RequestStatus,
    UserRole,
)
from aer.core.hashing import chain_hash, find_chain_break, verify_chain
from aer.db.models import (
    Approval,
    AuditEvent,
    Job,
    JobStep,
    ResearchPlan,
    ResearchRequest,
    User,
)

pytestmark = pytest.mark.integration


async def make_user(session, email: str = "owner@example.invalid") -> User:
    user = User(email=email, display_name="Test Owner", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    return user


async def make_request(session, *, user_id, **overrides) -> ResearchRequest:
    defaults = {
        "user_id": user_id,
        "company_name": "Microsoft Corporation",
        "ticker": "MSFT",
        "exchange": "NASDAQ",
        "as_of_date": date(2026, 7, 27),
        "base_currency": "USD",
        "investment_horizon_months": 60,
        "analysis_mode": AnalysisMode.FULL,
        "point_in_time": True,
        "max_cost_gbp": Decimal("2.50"),
    }
    request = ResearchRequest(**{**defaults, **overrides})
    session.add(request)
    await session.flush()
    return request


async def make_job(session, request: ResearchRequest, **overrides) -> Job:
    defaults = {
        "work_order_id": request.id,
        "request_id": request.id,
        "workflow_version": "equity-research@1.0.0",
        "code_version": "abc1234",
    }
    job = Job(**{**defaults, **overrides})
    session.add(job)
    await session.flush()
    return job


class TestSchemaShape:
    async def test_all_core_tables_exist(self, db_session):
        result = await db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' ORDER BY table_name"
            )
        )
        tables = {row[0] for row in result}
        expected = {
            "alembic_version",
            "approvals",
            "audit_events",
            "job_steps",
            "jobs",
            "research_plans",
            "research_requests",
            "users",
        }
        assert expected <= tables

    async def test_timestamps_are_timezone_aware(self, db_session):
        # A naive timestamp column would silently lose the offset, making "when did this
        # run" unanswerable across daylight-saving boundaries.
        result = await db_session.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'public'"
                "   AND data_type LIKE 'timestamp%'"
            )
        )
        naive = [f"{row[0]}.{row[1]}" for row in result if row[2] != "timestamp with time zone"]
        assert not naive, f"naive timestamp columns: {naive}"

    async def test_native_enum_types_exist(self, db_session):
        result = await db_session.execute(
            text(
                "SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace"
                " WHERE n.nspname = 'public' AND t.typtype = 'e'"
            )
        )
        assert {
            "analysis_mode",
            "decision",
            "gate_kind",
            "job_status",
            "request_status",
            "user_role",
        } <= {row[0] for row in result}

    async def test_email_is_case_insensitive(self, db_session):
        # CITEXT, so one person cannot become two accounts through capitalisation.
        await make_user(db_session, email="Jane@Example.invalid")
        duplicate = User(email="jane@example.invalid", display_name="Impostor")
        db_session.add(duplicate)
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestPersistenceAndRelationships:
    async def test_full_object_graph_round_trips(self, db_session):
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)

        plan = ResearchPlan(
            request_id=request.id,
            workflow_version="equity-research@1.0.0",
            plan={"sections": ["executive_summary"]},
            planned_sources=[{"provider": "sec_edgar", "tier": "T1_REGULATORY"}],
            estimated_cost_gbp=Decimal("1.2345"),
            estimated_runtime_seconds=900,
        )
        db_session.add(plan)
        await db_session.flush()

        approval = Approval(
            work_order_id=request.id,
            request_id=request.id,
            gate=GateKind.PLAN,
            decision=Decision.APPROVED,
            actor_user_id=user.id,
            payload_hash="a" * 64,
        )
        db_session.add(approval)

        job = await make_job(db_session, request, plan_id=plan.id)
        step = JobStep(
            job_id=job.id,
            step_key="acquire.sec.10k",
            sequence=0,
            idempotency_key="acquire.sec.10k:0",
            input_hash="b" * 64,
        )
        db_session.add(step)
        await db_session.commit()

        loaded = await db_session.get(Job, job.id)
        assert loaded is not None
        assert loaded.status is JobStatus.QUEUED
        assert loaded.total_cost_gbp == Decimal("0")
        assert loaded.plan_id == plan.id

        reloaded_request = await db_session.get(ResearchRequest, request.id)
        assert reloaded_request is not None
        assert reloaded_request.status is RequestStatus.DRAFT
        assert reloaded_request.analysis_mode is AnalysisMode.FULL
        assert reloaded_request.point_in_time is True
        assert reloaded_request.portfolio_context == {}

    async def test_deleting_a_request_cascades_to_jobs_and_steps(self, db_session):
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)
        job = await make_job(db_session, request)
        db_session.add(
            JobStep(
                job_id=job.id,
                step_key="plan",
                sequence=0,
                idempotency_key="plan:0",
                input_hash="c" * 64,
            )
        )
        await db_session.commit()

        await db_session.execute(
            text("DELETE FROM research_requests WHERE id = :rid"), {"rid": request.id}
        )
        await db_session.commit()

        remaining_jobs = await db_session.execute(select(Job).where(Job.id == job.id))
        assert remaining_jobs.first() is None
        remaining_steps = await db_session.execute(select(JobStep).where(JobStep.job_id == job.id))
        assert remaining_steps.first() is None

    async def test_a_plan_cannot_be_deleted_while_a_job_references_it(self, db_session):
        # RESTRICT, not CASCADE: losing the plan would erase the record of what the job
        # actually executed, which is the thing reproducibility depends on.
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)
        plan = ResearchPlan(
            request_id=request.id,
            workflow_version="equity-research@1.0.0",
            plan={},
            planned_sources=[],
            estimated_cost_gbp=Decimal("1.0"),
            estimated_runtime_seconds=60,
        )
        db_session.add(plan)
        await db_session.flush()
        await make_job(db_session, request, plan_id=plan.id)
        await db_session.commit()

        delete_plan = text("DELETE FROM research_plans WHERE id = :pid")
        with pytest.raises(IntegrityError):
            await db_session.execute(delete_plan, {"pid": plan.id})


class TestConstraints:
    async def test_unknown_enum_value_is_rejected_by_the_database(self, db_session):
        # The application is not the only writer. A native enum means a bad status cannot
        # be inserted by a script, a migration or a future service either.
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)
        await db_session.commit()

        with pytest.raises(DBAPIError):
            await db_session.execute(
                text("UPDATE research_requests SET status = 'NOT_A_STATUS' WHERE id = :rid"),
                {"rid": request.id},
            )

    @pytest.mark.parametrize("months", [0, -1, 241, 1000])
    async def test_horizon_outside_one_to_240_months_is_rejected(self, db_session, months):
        user = await make_user(db_session)
        with pytest.raises(IntegrityError):
            await make_request(db_session, user_id=user.id, investment_horizon_months=months)

    @pytest.mark.parametrize("months", [1, 60, 240])
    async def test_horizon_within_range_is_accepted(self, db_session, months):
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id, investment_horizon_months=months)
        assert request.investment_horizon_months == months

    async def test_non_positive_budget_is_rejected(self, db_session):
        user = await make_user(db_session)
        with pytest.raises(IntegrityError):
            await make_request(db_session, user_id=user.id, max_cost_gbp=Decimal("0"))

    async def test_malformed_currency_code_is_rejected(self, db_session):
        user = await make_user(db_session)
        with pytest.raises(IntegrityError):
            await make_request(db_session, user_id=user.id, base_currency="US")

    @pytest.mark.parametrize("weight", ["1.5", "-0.1", "8"])
    async def test_portfolio_weight_outside_zero_to_one_is_rejected(self, db_session, weight):
        # A weight of 800% would silently poison every portfolio-impact calculation
        # downstream. The database is the last place that can refuse it.
        user = await make_user(db_session)
        with pytest.raises(IntegrityError):
            await make_request(
                db_session,
                user_id=user.id,
                portfolio_context={"current_weight": float(weight), "benchmark": "S&P 500"},
            )

    async def test_valid_portfolio_weights_are_accepted(self, db_session):
        user = await make_user(db_session)
        request = await make_request(
            db_session,
            user_id=user.id,
            portfolio_context={
                "current_weight": 0.0,
                "maximum_weight": 0.08,
                "benchmark": "S&P 500",
            },
        )
        assert request.portfolio_context["maximum_weight"] == 0.08

    async def test_a_step_retry_must_increment_attempt(self, db_session):
        # The resumability contract: history is appended to, never overwritten.
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)
        job = await make_job(db_session, request)

        first = JobStep(
            job_id=job.id,
            step_key="acquire",
            sequence=0,
            attempt=0,
            idempotency_key="acquire:0",
            input_hash="d" * 64,
        )
        db_session.add(first)
        await db_session.flush()

        clash = JobStep(
            job_id=job.id,
            step_key="acquire",
            sequence=0,
            attempt=0,
            idempotency_key="acquire:0",
            input_hash="d" * 64,
        )
        db_session.add(clash)
        with pytest.raises(IntegrityError):
            await db_session.flush()

        await db_session.rollback()

    async def test_finished_without_started_is_rejected(self, db_session):
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)
        with pytest.raises(IntegrityError):
            await make_job(db_session, request, finished_at=datetime.now(UTC), started_at=None)

    async def test_finished_before_started_is_rejected(self, db_session):
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)
        started = datetime.now(UTC)
        with pytest.raises(IntegrityError):
            await make_job(
                db_session,
                request,
                started_at=started,
                finished_at=started - timedelta(minutes=5),
            )

    async def test_orphan_foreign_key_is_rejected(self, db_session):
        # Proves the FK columns carry no server default. If they inherited
        # gen_random_uuid() a missing FK would become a random dangling id instead of an
        # error -- the bug the UuidPk/UuidFk split exists to prevent.
        job = Job(
            work_order_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            workflow_version="equity-research@1.0.0",
            code_version="abc1234",
        )
        db_session.add(job)
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestAuditChain:
    async def test_three_events_form_a_verifiable_chain(self, db_session):
        events: list[AuditEvent] = []
        previous: AuditEvent | None = None
        for index, event_type in enumerate(["request.created", "plan.approved", "job.started"]):
            event = AuditEvent.create_linked(
                actor="test",
                event_type=event_type,
                payload={"sequence": index, "detail": f"event {index}"},
                previous=previous,
            )
            db_session.add(event)
            events.append(event)
            previous = event
        await db_session.commit()

        stored = (
            (await db_session.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars().all()
        )

        assert len(stored) == 3
        assert stored[0].prev_hash is None
        assert stored[1].prev_hash == stored[0].this_hash
        assert stored[2].prev_hash == stored[1].this_hash
        assert verify_chain(stored)

    async def test_editing_a_middle_payload_breaks_verification(self, db_session):
        # The point of the chain: a single edited record cannot be made to look
        # consistent without rewriting everything after it.
        previous: AuditEvent | None = None
        for index in range(3):
            event = AuditEvent.create_linked(
                actor="test",
                event_type="test.event",
                payload={"sequence": index},
                previous=previous,
            )
            db_session.add(event)
            previous = event
        await db_session.commit()

        stored = (
            (await db_session.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars().all()
        )
        assert verify_chain(stored)

        await db_session.execute(
            text("UPDATE audit_events SET payload = :p WHERE id = :i"),
            {"p": '{"sequence": 999}', "i": stored[1].id},
        )
        await db_session.commit()
        db_session.expire_all()

        tampered = (
            (await db_session.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars().all()
        )

        assert not verify_chain(tampered)
        assert find_chain_break(tampered) == 1

    async def test_deleting_a_record_breaks_verification(self, db_session):
        previous: AuditEvent | None = None
        for index in range(4):
            event = AuditEvent.create_linked(
                actor="test",
                event_type="test.event",
                payload={"sequence": index},
                previous=previous,
            )
            db_session.add(event)
            previous = event
        await db_session.commit()

        stored = (
            (await db_session.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars().all()
        )
        await db_session.execute(
            text("DELETE FROM audit_events WHERE id = :i"), {"i": stored[1].id}
        )
        await db_session.commit()

        remaining = (
            (await db_session.execute(select(AuditEvent).order_by(AuditEvent.id))).scalars().all()
        )
        assert len(remaining) == 3
        # Index 1 is now the record whose prev_hash points at the deleted one.
        assert find_chain_break(remaining) == 1

    async def test_hash_matches_an_independent_recomputation(self, db_session):
        event = AuditEvent.create_linked(
            actor="system",
            event_type="request.created",
            payload={"ticker": "MSFT", "as_of_date": "2026-07-27"},
            previous=None,
        )
        db_session.add(event)
        await db_session.commit()

        assert event.this_hash == chain_hash(None, event.payload)
        assert len(event.this_hash) == 64

    async def test_an_audit_record_survives_the_request_it_describes(self, db_session):
        # job_id and request_id are deliberately not foreign keys: the log must outlive
        # the thing it records, or it loses exactly the entries most worth keeping.
        user = await make_user(db_session)
        request = await make_request(db_session, user_id=user.id)
        await db_session.commit()

        event = AuditEvent.create_linked(
            actor=str(user.id),
            event_type="request.created",
            payload={"request_id": str(request.id)},
            previous=None,
            request_id=request.id,
        )
        db_session.add(event)
        await db_session.commit()

        await db_session.execute(
            text("DELETE FROM research_requests WHERE id = :rid"), {"rid": request.id}
        )
        await db_session.commit()

        surviving = (
            (
                await db_session.execute(
                    select(AuditEvent).where(AuditEvent.request_id == request.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(surviving) == 1
