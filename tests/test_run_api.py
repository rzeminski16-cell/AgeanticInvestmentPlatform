"""The run surface: JSON API, server-sent events and the server-rendered pages.

The workflow itself is covered in ``test_workflow.py``. What is tested here is the layer a
human touches — that a gate page shows the hash it will submit, that approving through the
form goes through the same service the API does, that the console renders without
JavaScript, and that one operator cannot read another's run.

**Runs are driven directly, not through the queue.** ``enqueue_run`` is replaced by a
recorder, so the tests assert that work was *queued* without needing an arq worker or a
real Redis. Executing a run inside the request would be a different system from the one
that ships.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.api.sse import event_stream
from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.db.models import Approval, Job, JobStep, Report, ResearchRequest, User
from aer.providers.fake import FakeProvider
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.storage.local import LocalArtefactStore
from aer.web.csrf import CSRF_FIELD_NAME
from tests.api_fixtures import build_app, client_for
from tests.workflow_fixtures import AS_OF_DATE, StubSecClient, make_provider

pytestmark = pytest.mark.integration

# Truncating these clears everything a run produces. `section_definitions` is deliberately
# absent: those rows come from the migration and are the thing under test elsewhere.
_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"


class EnqueueRecorder:
    """Stands in for the arq enqueue, so the tests need no worker and no real queue."""

    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: Any, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


@pytest.fixture
async def clean_slate(db_engine: Any) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    """A user, a research request and a job, committed so the app's session sees them."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="runs@example.invalid", display_name="Runs", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

        request = ResearchRequest(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            point_in_time=True,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
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


class Driver:
    """Advances a run to its next stopping point, committing as the worker would."""

    def __init__(self, engine: Any, settings: Settings) -> None:
        self._factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        self._settings = settings
        self._store = LocalArtefactStore(
            settings.artefact_root, max_bytes=settings.max_artefact_bytes
        )
        self.provider: FakeProvider = make_provider()
        self.sec_client = StubSecClient(self._store)

    async def advance(self, job_id: uuid.UUID) -> JobStatus:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            outcome = await run_service.execute(
                session,
                job=job,
                settings=self._settings,
                provider=self.provider,
                store=self._store,
                sec_client=self.sec_client,
            )
            await session.commit()
            return outcome.status

    async def payload_hash_of(self, job_id: uuid.UUID, step: str) -> str:
        async with self._factory() as session:
            row = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == step)
            )
            assert row is not None, f"the {step} step has not run"
            return str((row.output_ref or {})["payload_hash"])

    async def approve(self, job_id: uuid.UUID, *, gate: GateKind, step: str) -> None:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            await approval_service.record_decision(
                session,
                job=job,
                gate=gate,
                decision=Decision.APPROVED,
                actor=user,
                payload_hash=await self.payload_hash_of(job_id, step),
            )
            await session.commit()


@pytest.fixture
def driver(db_engine: Any, api_settings: Settings) -> Driver:
    return Driver(db_engine, api_settings)


async def start(api: Any, request_id: uuid.UUID) -> dict[str, Any]:
    response = await api.post("/api/runs", json={"request_id": str(request_id)})
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


class TestStartingARun:
    async def test_it_returns_the_run_and_queues_it(
        self, api: Any, committed: dict, enqueued: EnqueueRecorder
    ) -> None:
        body = await start(api, committed["request"].id)

        assert body["status"] == JobStatus.QUEUED.value
        assert enqueued.job_ids == [body["job_id"]]

    async def test_nothing_runs_inside_the_request(
        self, api: Any, committed: dict, db_session: Any
    ) -> None:
        """A run takes tens of minutes. Doing it here would hold a browser open for it."""
        body = await start(api, committed["request"].id)

        steps = list(
            await db_session.scalars(
                select(JobStep).where(JobStep.job_id == uuid.UUID(body["job_id"]))
            )
        )
        assert steps == []

    async def test_starting_twice_returns_the_same_run(self, api: Any, committed: dict) -> None:
        first = await start(api, committed["request"].id)
        second = await start(api, committed["request"].id)
        assert first["job_id"] == second["job_id"]

    async def test_an_unknown_request_is_a_404(self, api: Any) -> None:
        response = await api.post("/api/runs", json={"request_id": str(uuid.uuid4())})
        assert response.status_code == 404
        assert response.json()["code"] == "run_not_found"


class TestReadingARun:
    async def test_the_state_shows_each_step(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        state = (await api.get(f"/api/runs/{body['job_id']}")).json()
        assert state["status"] == JobStatus.AWAITING_APPROVAL.value
        assert [step["key"] for step in state["steps"]] == ["plan", "gate_plan"]

    async def test_spend_is_a_string_not_a_float(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """A cost that changes in the sixth decimal place because it passed through JSON
        is a cost nobody can reconcile against the database."""
        body = await start(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        state = (await api.get(f"/api/runs/{body['job_id']}")).json()
        assert isinstance(state["spend_gbp"], str)

    async def test_an_unknown_run_is_a_404(self, api: Any) -> None:
        assert (await api.get(f"/api/runs/{uuid.uuid4()}")).status_code == 404


class TestTheGateApi:
    async def test_the_plan_endpoint_returns_the_hash_an_approval_must_carry(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        plan = (await api.get(f"/api/plans/for-run/{job_id}")).json()
        assert plan["payload_hash"] == await driver.payload_hash_of(job_id, "plan")
        assert plan["planned_sources"]

    async def test_approving_records_the_decision_and_queues_a_continuation(
        self,
        api: Any,
        committed: dict,
        driver: Driver,
        enqueued: EnqueueRecorder,
        db_session: Any,
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        response = await api.post(
            f"/api/runs/{job_id}/gates/{GateKind.PLAN.value}/decide",
            json={
                "decision": Decision.APPROVED.value,
                "payload_hash": await driver.payload_hash_of(job_id, "plan"),
            },
        )

        assert response.status_code == 202
        approval = await db_session.scalar(select(Approval).where(Approval.job_id == job_id))
        assert approval is not None
        assert approval.decision is Decision.APPROVED
        # Queued, not executed inline.
        assert enqueued.job_ids.count(str(job_id)) == 2

    async def test_approving_twice_is_refused(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        digest = await driver.payload_hash_of(job_id, "plan")

        payload = {"decision": Decision.APPROVED.value, "payload_hash": digest}
        assert (
            await api.post(f"/api/runs/{job_id}/gates/{GateKind.PLAN.value}/decide", json=payload)
        ).status_code == 202

        second = await api.post(
            f"/api/runs/{job_id}/gates/{GateKind.PLAN.value}/decide", json=payload
        )
        assert second.status_code == 422
        assert second.json()["code"] == "validation_error"

    async def test_the_final_gate_cannot_be_approved_first(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        response = await api.post(
            f"/api/runs/{job_id}/gates/{GateKind.FINAL.value}/decide",
            json={"decision": Decision.APPROVED.value, "payload_hash": "a" * 64},
        )
        assert response.status_code == 422
        assert "cannot be decided" in response.json()["detail"]

    async def test_an_approval_without_a_hash_is_rejected_by_the_schema(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        response = await api.post(
            f"/api/runs/{job_id}/gates/{GateKind.PLAN.value}/decide",
            json={"decision": Decision.APPROVED.value, "payload_hash": ""},
        )
        assert response.status_code == 422

    async def test_the_draft_endpoint_hashes_what_the_run_hashed(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)

        draft = (await api.get(f"/api/runs/{job_id}/draft")).json()
        assert draft["payload_hash"] == await driver.payload_hash_of(job_id, "draft")
        assert [section["key"] for section in draft["sections"]] == [
            "executive_summary",
            "historical_financial_analysis",
        ]


class TestTheReportApi:
    @pytest.fixture
    async def finished(self, api: Any, committed: dict, driver: Driver) -> uuid.UUID:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="draft")
        await driver.advance(job_id)
        return job_id

    async def test_the_report_is_readable_for_the_run(self, api: Any, finished: uuid.UUID) -> None:
        report = (await api.get(f"/api/reports/for-run/{finished}")).json()
        assert report["immutable"] is True
        assert report["sections"]
        assert report["markdown"].startswith("# ")

    async def test_the_download_serves_the_archived_bytes(
        self, api: Any, finished: uuid.UUID, db_session: Any
    ) -> None:
        """Served from the store, not re-rendered, and the digest says which bytes."""
        report_row = await db_session.scalar(select(Report).where(Report.job_id == finished))
        assert report_row is not None

        response = await api.get(f"/api/reports/{report_row.id}/download")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert len(response.headers["x-artefact-sha256"]) == 64
        assert response.text == report_row.content["markdown"]

    async def test_an_unknown_report_is_a_404(self, api: Any) -> None:
        assert (await api.get(f"/api/reports/{uuid.uuid4()}")).status_code == 404


class TestOwnership:
    """One operator's run must not be readable by another."""

    @pytest.fixture
    async def someone_elses(self, committed: dict, db_engine: Any) -> uuid.UUID:
        """A second user with their own request and run.

        Created *after* the fixture user, so ``get_current_user`` — which returns the
        oldest — still resolves to the first. That is what makes the API calls below act
        as the wrong operator.
        """
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            other = User(email="other@example.invalid", display_name="Other", role=UserRole.OWNER)
            session.add(other)
            await session.flush()

            request = ResearchRequest(
                user_id=other.id,
                company_name="Rio Tinto plc",
                ticker="RIO",
                exchange="LSE",
                as_of_date=AS_OF_DATE,
                base_currency="GBP",
                reporting_currency="GBP",
                investment_horizon_months=12,
                max_cost_gbp="1.00",
            )
            session.add(request)
            await session.flush()

            job = await run_service.start_run(session, request=request)
            await session.commit()
            return job.id

    async def test_reading_another_users_run_is_a_404(
        self, api: Any, someone_elses: uuid.UUID
    ) -> None:
        assert (await api.get(f"/api/runs/{someone_elses}")).status_code == 404

    async def test_deciding_on_another_users_gate_is_a_404(
        self, api: Any, someone_elses: uuid.UUID
    ) -> None:
        response = await api.post(
            f"/api/runs/{someone_elses}/gates/{GateKind.PLAN.value}/decide",
            json={"decision": Decision.APPROVED.value, "payload_hash": "a" * 64},
        )
        assert response.status_code == 404

    async def test_the_console_page_does_not_reveal_it_exists(
        self, api: Any, someone_elses: uuid.UUID
    ) -> None:
        response = await api.get(f"/runs/{someone_elses}")
        assert response.status_code == 404
        assert str(someone_elses) in response.text  # the id they asked for, not the run


class TestTheEventStream:
    async def test_a_disconnected_reader_ends_the_stream(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """A closed tab must stop the polling, not leave it running for an hour.

        Driven against the generator rather than through HTTP, because "the client went
        away" is delivered as a cancellation of the task consuming it, and an ASGI test
        client will not produce one on demand. Suppressing that cancellation would leave a
        database connection held for a reader that has gone.
        """
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        stream = event_stream(factory, job_id=job_id, poll_seconds=0.05)

        # One frame, so the generator is parked at the sleep between polls.
        assert "event: state" in await anext(stream)

        consumer = asyncio.create_task(anext(stream))  # type: ignore[arg-type]
        await asyncio.sleep(0.01)
        consumer.cancel()

        with pytest.raises(asyncio.CancelledError):
            await consumer

    async def test_a_terminal_run_emits_state_then_done(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="draft")
        await driver.advance(job_id)

        async with api.stream("GET", f"/api/runs/{job_id}/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join([chunk async for chunk in response.aiter_text()])

        assert "event: state" in body
        assert "event: done" in body
        assert JobStatus.SUCCEEDED.value in body

    async def test_the_stream_is_not_buffered_by_a_proxy(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """Buffering defeats it entirely: an hour of progress delivered at the end."""
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="draft")
        await driver.advance(job_id)

        async with api.stream("GET", f"/api/runs/{job_id}/events") as response:
            assert response.headers["x-accel-buffering"] == "no"
            assert response.headers["cache-control"] == "no-cache"
            async for _ in response.aiter_text():
                pass


class TestTheWebPages:
    async def test_the_console_renders_the_run_without_javascript(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        page = await api.get(f"/runs/{body['job_id']}")
        assert page.status_code == 200
        # The step list is server-rendered, so a browser with no script still sees it.
        assert 'data-step="plan"' in page.text
        assert 'id="awaiting-approval"' in page.text

    async def test_the_console_falls_back_to_polling(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """The meta refresh is in the markup before any script runs; console.js removes it."""
        body = await start(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        page = await api.get(f"/runs/{body['job_id']}")
        assert 'id="poll-fallback"' in page.text
        assert 'http-equiv="refresh"' in page.text

    async def test_a_finished_run_does_not_keep_refreshing(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="draft")
        await driver.advance(job_id)

        page = await api.get(f"/runs/{job_id}")
        assert 'id="poll-fallback"' not in page.text
        assert 'id="view-report"' in page.text

    async def test_the_plan_page_shows_the_hash_it_will_submit(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        page = await api.get(f"/runs/{job_id}/plan")
        assert page.status_code == 200

        shown = _hidden_value(page.text, "payload_hash")
        assert shown == await driver.payload_hash_of(job_id, "plan")

    async def test_approving_through_the_form_advances_the_gate(
        self, api: Any, committed: dict, driver: Driver, enqueued: EnqueueRecorder
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        page = await api.get(f"/runs/{job_id}/plan")
        response = await api.post(
            f"/runs/{job_id}/gates/{GateKind.PLAN.value}",
            data={
                CSRF_FIELD_NAME: _hidden_value(page.text, CSRF_FIELD_NAME),
                "payload_hash": _hidden_value(page.text, "payload_hash"),
                "decision": Decision.APPROVED.value,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == f"/runs/{job_id}"
        assert enqueued.job_ids.count(str(job_id)) == 2

    async def test_a_form_post_without_a_csrf_token_decides_nothing(
        self, api: Any, committed: dict, driver: Driver, db_session: Any
    ) -> None:
        """Loopback with no authentication is exactly where this matters most."""
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        response = await api.post(
            f"/runs/{job_id}/gates/{GateKind.PLAN.value}",
            data={
                "payload_hash": await driver.payload_hash_of(job_id, "plan"),
                "decision": Decision.APPROVED.value,
            },
        )

        assert response.status_code == 403
        assert await db_session.scalar(select(Approval).where(Approval.job_id == job_id)) is None

    async def test_a_decided_gate_shows_the_decision_rather_than_a_button(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        await driver.approve(job_id, gate=GateKind.PLAN, step="plan")

        page = await api.get(f"/runs/{job_id}/plan")
        assert 'id="already-decided"' in page.text
        assert 'id="approve"' not in page.text

    async def test_the_review_page_shows_the_document_as_text(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """Rendered as text, not markup: the draft comes from model output."""
        job_id = await _to_second_gate(api, committed, driver)

        page = await api.get(f"/runs/{job_id}/review")
        assert page.status_code == 200
        assert 'id="draft-markdown"' in page.text
        assert _hidden_value(page.text, "payload_hash") == await driver.payload_hash_of(
            job_id, "draft"
        )

    async def test_the_report_page_links_to_the_archived_download(
        self, api: Any, committed: dict, driver: Driver, db_session: Any
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="draft")
        await driver.advance(job_id)

        report = await db_session.scalar(select(Report).where(Report.job_id == job_id))
        assert report is not None

        page = await api.get(f"/reports/{report.id}")
        assert page.status_code == 200
        assert f"/api/reports/{report.id}/download" in page.text
        assert 'id="immutable-badge"' in page.text

    async def test_every_page_carries_the_disclaimer(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        for path in (f"/runs/{job_id}", f"/runs/{job_id}/plan"):
            page = await api.get(path)
            assert "not regulated investment advice" in page.text

    async def test_the_request_page_offers_to_start_a_run(self, api: Any, committed: dict) -> None:
        page = await api.get(f"/requests/{committed['request'].id}")
        assert 'id="start-run"' in page.text

    async def test_it_links_to_the_run_once_one_exists(self, api: Any, committed: dict) -> None:
        body = await start(api, committed["request"].id)

        page = await api.get(f"/requests/{committed['request'].id}")
        assert 'id="open-run"' in page.text
        assert f"/runs/{body['job_id']}" in page.text


async def _to_second_gate(api: Any, committed: dict, driver: Driver) -> uuid.UUID:
    """Start a run and drive it to the final gate, approving the plan on the way."""
    body = await start(api, committed["request"].id)
    job_id = uuid.UUID(body["job_id"])

    await driver.advance(job_id)
    await driver.approve(job_id, gate=GateKind.PLAN, step="plan")
    await driver.advance(job_id)
    return job_id


def _hidden_value(html: str, name: str) -> str:
    """The value of a hidden input, read out of the rendered page.

    Read from the page rather than recomputed, because the point of these tests is that
    what the operator was shown is what gets submitted.
    """
    match = re.search(
        rf'<input type="hidden"[^>]*name="{re.escape(name)}"[^>]*value="([^"]*)"', html
    )
    assert match is not None, f"no hidden input named {name!r} in the page"
    return match.group(1)
