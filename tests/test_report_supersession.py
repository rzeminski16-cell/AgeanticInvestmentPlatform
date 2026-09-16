"""ADR 0116: a report is superseded, never replaced, and exactly one is current.

Held on three surfaces: the database (the partial unique index refuses two current reports
for one company), the service (supersede with a successor, withdraw without one, both with a
reason and an audit event), and the pages (the band on a superseded report, the state in the
library, the withdraw control) — plus the two readers that change behaviour: a withdrawn
report lets its request be run again, and a thesis points only at a current report.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.enums import GateKind, JobStatus, UserRole
from aer.db.models import AuditEvent, Company, Job, Report, ResearchRequest, User
from aer.errors import ConflictError, ValidationError
from aer.services import runs as run_service
from aer.services.reports import (
    SUPERSEDED_EVENT,
    WITHDRAWN_EVENT,
    current_report,
    report_state,
    supersede,
    withdraw,
)
from aer.web.csrf import CSRF_FIELD_NAME
from aer.workflow.workflows.vertical_slice_v1 import seal_step_for
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.request_fixtures import research_request
from tests.run_fixtures import Driver, to_final_gate
from tests.test_run_api import _hidden_value
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

APPROVED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def _request(session: AsyncSession, *, user_id: uuid.UUID, as_of: date) -> ResearchRequest:
    request = research_request(
        user_id=user_id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=as_of,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
    )
    session.add(request)
    await session.flush()
    return request


async def _approved_report(
    session: AsyncSession, *, request: ResearchRequest, company: Company, as_of: date
) -> Report:
    job = Job(
        work_order_id=request.id,
        workflow_version="supersession_scene_v1",
        code_version="scene",
        status=JobStatus.SUCCEEDED,
    )
    session.add(job)
    await session.flush()
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=as_of,
        content={"markdown": "prior"},
        content_hash="a" * 64,
        approved_at=APPROVED_AT,
        immutable=True,
    )
    session.add(report)
    await session.flush()
    return report


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="super@example.invalid", display_name="Super", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()
    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()
    first_request = await _request(db_session, user_id=user.id, as_of=date(2026, 3, 31))
    first = await _approved_report(
        db_session, request=first_request, company=company, as_of=date(2026, 3, 31)
    )
    return {"session": db_session, "user": user, "company": company, "first": first}


class TestTheDatabaseHoldsOneCurrentReportPerCompany:
    async def test_a_second_current_report_is_refused_at_flush(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id, as_of=date(2026, 6, 30))
        with pytest.raises(IntegrityError, match="reports_one_current_per_company"):
            await _approved_report(
                session, request=request, company=scene["company"], as_of=date(2026, 6, 30)
            )
        await session.rollback()

    async def test_the_states_are_three_readings_of_two_columns(
        self, scene: dict[str, Any]
    ) -> None:
        first: Report = scene["first"]
        assert report_state(first) == "current"
        assert first.is_current
        first.superseded_at = datetime.now(UTC)
        first.supersession_reason = "wrong"
        assert report_state(first) == "withdrawn"
        assert first.is_withdrawn
        assert not first.is_current
        first.superseded_by = uuid.uuid4()
        assert report_state(first) == "superseded"
        assert first.is_superseded
        assert not first.is_withdrawn


class TestSuperseding:
    async def test_it_records_the_successor_the_time_the_reason_and_an_event(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id, as_of=date(2026, 6, 30))
        # Frozen only after the first is superseded, as the render step orders it.
        job = Job(
            work_order_id=request.id,
            workflow_version="supersession_scene_v1",
            code_version="scene",
            status=JobStatus.SUCCEEDED,
        )
        session.add(job)
        await session.flush()
        successor = Report(
            job_id=job.id,
            request_id=request.id,
            company_id=scene["company"].id,
            as_of_date=date(2026, 6, 30),
            content={"markdown": "next"},
            content_hash="b" * 64,
        )
        session.add(successor)
        await session.flush()

        first: Report = scene["first"]
        await supersede(
            session,
            previous=first,
            successor=successor,
            reason="Two quarters filed since.",
            actor=scene["user"],
        )
        successor.approved_at = APPROVED_AT
        successor.immutable = True
        await session.flush()

        assert first.superseded_by == successor.id
        assert first.superseded_at is not None
        assert first.supersession_reason == "Two quarters filed since."
        assert first.immutable, "superseding rewrote the frozen report"
        assert report_state(first) == "superseded"
        assert await current_report(session, company_id=scene["company"].id) is successor
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == SUPERSEDED_EVENT)
        )
        assert event is not None
        assert event.payload["superseded_by"] == str(successor.id)

    async def test_it_refuses_a_blank_reason_and_a_report_that_is_not_current(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        first: Report = scene["first"]
        with pytest.raises(ValidationError, match="needs a reason"):
            await supersede(
                session, previous=first, successor=first, reason="  ", actor=scene["user"]
            )
        with pytest.raises(ConflictError, match="cannot supersede itself"):
            await supersede(
                session, previous=first, successor=first, reason="x y", actor=scene["user"]
            )
        await withdraw(
            session, report=first, reason="A defect after approval.", actor=scene["user"]
        )
        with pytest.raises(ConflictError, match="already withdrawn"):
            await withdraw(session, report=first, reason="again", actor=scene["user"])


class TestWithdrawing:
    async def test_it_stops_the_report_being_current_and_leaves_it_readable(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        first: Report = scene["first"]
        await withdraw(
            session, report=first, reason="The margin was impossible.", actor=scene["user"]
        )
        assert first.is_withdrawn
        assert first.immutable
        assert first.content_hash == "a" * 64
        assert await current_report(session, company_id=scene["company"].id) is None
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == WITHDRAWN_EVENT)
        )
        assert event is not None
        assert event.payload["reason"] == "The margin was impossible."

    async def test_a_draft_cannot_be_withdrawn(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id, as_of=date(2026, 6, 30))
        job = Job(
            work_order_id=request.id,
            workflow_version="supersession_scene_v1",
            code_version="scene",
            status=JobStatus.FAILED,
        )
        session.add(job)
        await session.flush()
        draft = Report(
            job_id=job.id,
            request_id=request.id,
            company_id=scene["company"].id,
            as_of_date=date(2026, 6, 30),
            content={},
            content_hash="c" * 64,
        )
        session.add(draft)
        await session.flush()
        with pytest.raises(ConflictError, match="never approved"):
            await withdraw(session, report=draft, reason="a draft", actor=scene["user"])

    async def test_a_withdrawn_reports_request_may_be_run_again(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        first: Report = scene["first"]
        request = await session.get(ResearchRequest, first.request_id)
        assert request is not None
        before = await run_service.start_run(session, request=request)
        assert before.id == first.job_id, "a current report's run is the run"
        await withdraw(session, report=first, reason="Wrong as-of date.", actor=scene["user"])
        after = await run_service.start_run(session, request=request)
        assert after.id != first.job_id
        assert after.status is JobStatus.QUEUED


# --- the pages, and the render step -----------------------------------------------------


@pytest.fixture
async def committed(db_engine: Any) -> dict[str, Any]:
    await delete_all(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="pages@example.invalid", display_name="Pages", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = await _request(session, user_id=user.id, as_of=AS_OF_DATE)
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


async def _approved_run(api: Any, request_id: uuid.UUID, driver: Driver) -> uuid.UUID:
    job_id = await to_final_gate(api, request_id, driver)
    await driver.approve(job_id, gate=GateKind.FINAL, step=seal_step_for(GateKind.FINAL.value))
    assert await driver.advance(job_id) is JobStatus.SUCCEEDED
    return job_id


class TestTheRenderStepSupersedesTheCurrentReport:
    async def test_approving_a_second_report_supersedes_the_first(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        first_job = await _approved_run(api, committed["request"].id, driver)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            second_request = await _request(session, user_id=committed["user"].id, as_of=AS_OF_DATE)
            await session.commit()
        second_job = await _approved_run(api, second_request.id, driver)

        async with factory() as session:
            first = await session.scalar(select(Report).where(Report.job_id == first_job))
            second = await session.scalar(select(Report).where(Report.job_id == second_job))
            assert first is not None
            assert second is not None
            assert first.company_id == second.company_id
            assert second.is_current
            assert first.superseded_by == second.id
            assert first.supersession_reason is not None
            assert "approved" in first.supersession_reason
            assert first.immutable, "the superseded report was unfrozen"
            assert first.content_hash, "the superseded report lost its hash"
            assert await current_report(session, company_id=second.company_id) is not None

        library = await api.get("/reports")
        assert 'data-report-state="superseded"' in library.text
        assert 'data-report-state="current"' in library.text
        superseded_page = await api.get(f"/reports/{first.id}")
        assert 'id="superseded-band"' in superseded_page.text
        assert f'href="/reports/{second.id}"' in superseded_page.text
        assert 'id="withdraw-form"' not in superseded_page.text

    async def test_withdrawing_on_the_page_needs_a_reason_and_records_it(
        self, api: Any, committed: dict[str, Any], driver: Driver, db_engine: Any
    ) -> None:
        job_id = await _approved_run(api, committed["request"].id, driver)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            report = await session.scalar(select(Report).where(Report.job_id == job_id))
            assert report is not None
        page = await api.get(f"/reports/{report.id}")
        assert 'id="withdraw-form"' in page.text
        token = _hidden_value(page.text, CSRF_FIELD_NAME)

        blank = await api.post(
            f"/reports/{report.id}/withdraw",
            data={CSRF_FIELD_NAME: token, "reason": "   "},
            follow_redirects=False,
        )
        assert blank.status_code == 422
        assert "needs a reason" in blank.text

        withdrawn = await api.post(
            f"/reports/{report.id}/withdraw",
            data={CSRF_FIELD_NAME: token, "reason": "The net margin was impossible."},
            follow_redirects=False,
        )
        assert withdrawn.status_code == 303, withdrawn.text
        after = await api.get(f"/reports/{report.id}")
        assert 'id="withdrawn-band"' in after.text
        assert "The net margin was impossible." in after.text
        assert 'id="withdraw-form"' not in after.text

        read = await api.get(f"/api/reports/{report.id}")
        assert read.status_code == 200
        assert read.json()["state"] == "withdrawn"
        assert read.json()["supersession_reason"] == "The net margin was impossible."
