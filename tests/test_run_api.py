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
import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any
from unittest import mock

import pikepdf
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.api import sse as sse_module
from aer.api.sse import event_stream
from aer.config import Settings
from aer.core.disagreement import (
    DisagreementKind,
    ResolutionOutcome,
    ResolutionRule,
    ResolvedBy,
)
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.db.models import (
    Approval,
    AuditEvent,
    Calculation,
    Company,
    Cost,
    Disagreement,
    Job,
    JobCancellation,
    JobStep,
    Report,
    ReportSection,
    ResearchRequest,
    Scenario,
    SectionDefinition,
    User,
)
from aer.db.models.report_section import SectionStatus
from aer.services import runs as run_service
from aer.web.csrf import CSRF_FIELD_NAME
from aer.workflow.workflows.vertical_slice_v1 import WORKFLOW_VERSION
from tests.api_fixtures import build_app, client_for
from tests.run_fixtures import Driver, start_run, to_final_gate
from tests.workflow_fixtures import (
    AS_OF_DATE,
    DEFAULT_PER_RUN_BUDGET_GBP,
    SPINE_KEYS,
    seed_starved_section,
)

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
async def clean_slate(db_engine: Any) -> AsyncIterator[None]:
    """Empty the tables a run writes to, before **and** after.

    Before, so a test's result never depends on which tests ran first. After, because this
    file commits for real and the rows outlive it — and the last test to run has nobody
    left to clean up for it. Without the teardown the suite relies on "the final test in
    this file happens not to create artefacts", which is an invariant nobody can see and
    which a new test at the bottom of the file silently breaks. It did.

    The statement timeout turns a lock conflict into a fast, readable failure instead of a
    suite that hangs.
    """
    await _truncate(db_engine)
    yield
    await _truncate(db_engine)


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
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
            # The platform default, read rather than restated -- a fixture budgeting a
            # different ceiling than production drifts the moment either moves (A33).
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        session.add(request)
        await session.commit()
        return {"user": user, "request": request}


@pytest.fixture
async def starved_section(db_engine: Any) -> AsyncIterator[None]:
    """A committed starved-probe definition, and its removal.

    The spine's own sections all carry citation fields since task 44, so a test that
    needs the §2.4 banner genuinely firing seeds this required, prose-only section. The
    teardown matters: `section_definitions` is deliberately outside `_TABLES` — its rows
    come from migrations — so a committed probe would otherwise outlive this file and
    fire the banner on every later run in the suite.
    """
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        await seed_starved_section(session)
        await session.commit()
    yield
    async with db_engine.begin() as connection:
        # The run's section rows first: they hold a RESTRICT foreign key to the probe.
        await connection.execute(
            text("DELETE FROM report_sections WHERE section_key = 'starved_probe'")
        )
        await connection.execute(
            text("DELETE FROM section_definitions WHERE key = 'starved_probe'")
        )


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


@pytest.fixture
def driver(db_engine: Any, api_settings: Settings) -> Driver:
    return Driver(db_engine, api_settings)


class TestStartingARun:
    async def test_it_returns_the_run_and_queues_it(
        self, api: Any, committed: dict, enqueued: EnqueueRecorder
    ) -> None:
        body = await start_run(api, committed["request"].id)

        assert body["status"] == JobStatus.QUEUED.value
        assert enqueued.job_ids == [body["job_id"]]

    async def test_nothing_runs_inside_the_request(
        self, api: Any, committed: dict, db_session: Any
    ) -> None:
        """A run takes tens of minutes. Doing it here would hold a browser open for it."""
        body = await start_run(api, committed["request"].id)

        steps = list(
            await db_session.scalars(
                select(JobStep).where(JobStep.job_id == uuid.UUID(body["job_id"]))
            )
        )
        assert steps == []

    async def test_starting_twice_returns_the_same_run(self, api: Any, committed: dict) -> None:
        first = await start_run(api, committed["request"].id)
        second = await start_run(api, committed["request"].id)
        assert first["job_id"] == second["job_id"]

    async def test_an_unknown_request_is_a_404(self, api: Any) -> None:
        response = await api.post("/api/runs", json={"request_id": str(uuid.uuid4())})
        assert response.status_code == 404
        assert response.json()["code"] == "run_not_found"


class TestReadingARun:
    async def test_the_state_shows_the_steps_that_have_run(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        state = (await api.get(f"/api/runs/{body['job_id']}")).json()
        assert state["status"] == JobStatus.AWAITING_APPROVAL.value

        reached = {step["key"]: step["status"] for step in state["steps"]}
        assert reached["plan"] == JobStatus.SUCCEEDED.value
        assert reached["gate_plan"] == JobStatus.AWAITING_APPROVAL.value

    async def test_the_state_also_shows_the_steps_still_to_come(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """Otherwise the first minute of a run is one line and no sense of scale.

        A step that has not started has no ``job_steps`` row -- the engine writes one when
        it begins -- so these come from the workflow definition, at ``QUEUED``.
        """
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        state = (await api.get(f"/api/runs/{body['job_id']}")).json()
        keys = [step["key"] for step in state["steps"]]

        assert keys == list(run_service.declared_steps(WORKFLOW_VERSION))
        assert state["steps_total"] == len(keys)
        # Two, since ADR 0091: the plan and its critique both complete before gate 1.
        assert state["steps_done"] == 2
        # The declared order, not the order things happened to start in: the point is to
        # show what is left. Three entries precede it: the plan, its critique, and the
        # gate the run is waiting at.
        unreached = {step["status"] for step in state["steps"][3:]}
        assert unreached == {JobStatus.QUEUED.value}

    async def test_a_running_step_says_when_it_started(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """The console's elapsed clock counts from this. Without it there is nothing to
        distinguish a model call four minutes in from a worker that died."""
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        state = (await api.get(f"/api/runs/{body['job_id']}")).json()
        started = {step["key"]: step["started_at"] for step in state["steps"]}

        assert datetime.fromisoformat(started["plan"]).tzinfo is not None
        assert started["render"] is None

    async def test_nothing_in_the_state_changes_on_its_own(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """The event stream hashes this frame to decide whether to send it.

        A wall-clock field would make every poll look like news, and a one-second poll
        would become a one-second event for the life of the run. Liveness is a heartbeat of
        its own; this must be a pure function of stored state.
        """
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        first = (await api.get(f"/api/runs/{body['job_id']}")).json()
        second = (await api.get(f"/api/runs/{body['job_id']}")).json()

        assert first == second

    async def test_spend_is_a_string_not_a_float(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """A cost that changes in the sixth decimal place because it passed through JSON
        is a cost nobody can reconcile against the database."""
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        state = (await api.get(f"/api/runs/{body['job_id']}")).json()
        assert isinstance(state["spend_gbp"], str)

    async def test_an_unknown_run_is_a_404(self, api: Any) -> None:
        assert (await api.get(f"/api/runs/{uuid.uuid4()}")).status_code == 404


class TestTheTimelineWhenTheWorkflowIsUnknown:
    """A job recorded under a workflow version this build no longer has.

    Built in memory rather than through the API, because a job cannot be *made* to carry an
    unknown workflow version through any supported path -- which is exactly why the
    fallback needs a test of its own.
    """

    @staticmethod
    def _state(version: str) -> run_service.RunState:
        job = Job(
            work_order_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            workflow_version=version,
            code_version="test",
            status=JobStatus.RUNNING,
        )
        step = JobStep(
            job_id=uuid.uuid4(),
            step_key="a_step_that_no_longer_exists",
            sequence=0,
            status=JobStatus.SUCCEEDED,
            attempt=0,
            idempotency_key="x",
            input_hash="y",
        )
        return run_service.RunState(
            job=job,
            steps=[step],
            spend_gbp=Decimal(0),
            declared_steps=run_service.declared_steps(version),
        )

    def test_an_unknown_version_declares_nothing(self) -> None:
        assert run_service.declared_steps("some_workflow_from_2019") == ()

    def test_the_timeline_falls_back_to_what_was_recorded(self) -> None:
        """Showing the current workflow's steps against an old run would be a fiction, and
        showing nothing would hide work that really happened."""
        timeline = self._state("some_workflow_from_2019").timeline

        assert [entry.key for entry in timeline] == ["a_step_that_no_longer_exists"]

    def test_a_recorded_step_the_workflow_no_longer_declares_is_still_shown(self) -> None:
        declared = list(run_service.declared_steps(WORKFLOW_VERSION))
        keys = [entry.key for entry in self._state(WORKFLOW_VERSION).timeline]

        assert keys[: len(declared)] == declared
        assert keys[-1] == "a_step_that_no_longer_exists"


class TestTheGateApi:
    async def test_the_plan_endpoint_returns_the_hash_an_approval_must_carry(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        plan = (await api.get(f"/api/plans/for-run/{job_id}")).json()
        assert plan["payload_hash"] == await driver.payload_hash_of(job_id, "critique_plan")
        assert plan["planned_sources"]

    async def test_approving_records_the_decision_and_queues_a_continuation(
        self,
        api: Any,
        committed: dict,
        driver: Driver,
        enqueued: EnqueueRecorder,
        db_session: Any,
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        response = await api.post(
            f"/api/runs/{job_id}/gates/{GateKind.PLAN.value}/decide",
            json={
                "decision": Decision.APPROVED.value,
                "payload_hash": await driver.payload_hash_of(job_id, "critique_plan"),
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
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        digest = await driver.payload_hash_of(job_id, "critique_plan")

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
        body = await start_run(api, committed["request"].id)
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
        body = await start_run(api, committed["request"].id)
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
        # The authoritative hash lives on the red_team step since task 40 — the last
        # step that can change the gate-2 payload.
        assert draft["payload_hash"] == await driver.payload_hash_of(job_id, "revise")
        assert [section["key"] for section in draft["sections"]] == list(SPINE_KEYS)

    async def test_the_draft_endpoint_reports_unsettled_disagreements(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        # A run with clean evidence has none, and the key is present rather than absent —
        # a client that has to distinguish "no conflicts" from "this build does not report
        # conflicts" would have to guess.
        job_id = await _to_second_gate(api, committed, driver)

        draft = (await api.get(f"/api/runs/{job_id}/draft")).json()
        assert draft["escalations"] == []

    async def test_a_real_run_fills_its_sources_table(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """The surface against an actual run rather than against constructed evidence.

        Attribution is the load-bearing part: ``source_documents.job_id`` is nullable, so a
        run that omitted it would leave this endpoint returning nothing while every other
        test still passed.
        """
        job_id = await _to_second_gate(api, committed, driver)

        body = (await api.get(f"/api/runs/{job_id}/sources")).json()

        assert body["sources"], "the run acquired a filing and the table must show it"
        assert body["sources"][0]["provider"] == "sec_edgar"
        assert len(body["sources"][0]["sha256"]) == 64

    async def test_the_slices_api_aggregate_is_shown_quarantined(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """And that is correct, not a defect, which is why it is pinned.

        **The reason changed, and the change is the point.** The SEC's ``companyfacts``
        response is a view assembled on request, so it used to be quarantined
        ``no_publication_date`` — which meant no claim could cite the only source the run
        held. ADR 0044 dates it by its newest component: the day it could first have
        existed. This run's as-of date is earlier than that day, so the aggregate is now
        quarantined for a reason that is *true* — it did not exist yet — and a run in that
        position should be reading the filings themselves, which this one now does.

        Surfacing it rather than suppressing it is the whole argument for the table.
        """
        job_id = await _to_second_gate(api, committed, driver)

        body = (await api.get(f"/api/runs/{job_id}/sources")).json()

        aggregate = next(row for row in body["sources"] if "companyfacts" in row["url"])
        assert body["quarantined"] == 1
        assert aggregate["quarantine_reason"] == "published_after_as_of_date"
        assert aggregate["publication_date_confidence"] == pytest.approx(0.9), (
            "derived from the contents rather than stated, and the row must say so"
        )

    async def test_the_filings_the_aggregate_could_not_supply_are_admissible(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """The other half of ADR 0044's argument, and the reason the quarantine above is
        tolerable: the run holds dated, citable primary documents of its own."""
        job_id = await _to_second_gate(api, committed, driver)

        body = (await api.get(f"/api/runs/{job_id}/sources")).json()

        filings = [row for row in body["sources"] if "/Archives/" in row["url"]]
        assert len(filings) > 1, "a run reading one document is the failure A4 closed"
        assert all(row["admissible"] for row in filings)
        assert all(row["excerpt_count"] > 0 for row in filings), (
            "a source with no excerpts cannot be cited, so acquiring it bought nothing"
        )


class TestTheReportApi:
    @pytest.fixture
    async def finished(self, api: Any, committed: dict, driver: Driver) -> uuid.UUID:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
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


@pytest.fixture
async def someone_elses_run(committed: dict, db_engine: Any) -> uuid.UUID:
    """A second user with their own request and run.

    Created *after* the fixture user, so ``get_current_user`` — which returns the oldest —
    still resolves to the first. That is what makes the calls below act as the wrong
    operator.
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


class TestReproducingARun:
    """B8. The work is A12's service; this is the surface that makes it reachable."""

    async def test_the_console_offers_it(self, api: Any, committed: dict) -> None:
        body = await start_run(api, committed["request"].id)
        page = await api.get(f"/runs/{body['job_id']}")

        assert 'id="replay-form"' in page.text

    async def test_it_reports_a_run_that_still_holds(self, api: Any, committed: dict) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        page = await api.get(f"/runs/{job_id}")

        replayed = await api.post(
            f"/runs/{job_id}/replay",
            data={CSRF_FIELD_NAME: _hidden_value(page.text, CSRF_FIELD_NAME)},
        )

        assert replayed.status_code == 200
        assert 'id="reproduces"' in replayed.text

    async def test_a_post_without_a_token_replays_nothing(self, api: Any, committed: dict) -> None:
        """A POST because re-verifying a citation writes its verdict back onto the row.

        It reads like a report, so the temptation is a plain link; that would let any page
        in any tab rewrite verification state on loopback.
        """
        body = await start_run(api, committed["request"].id)

        refused = await api.post(f"/runs/{body['job_id']}/replay", data={})

        assert refused.status_code == 403

    async def test_an_unknown_run_is_refused_rather_than_reported_as_sound(
        self, api: Any, committed: dict
    ) -> None:
        """A replay of a run that does not exist checks nothing, and nothing reproduces."""
        body = await start_run(api, committed["request"].id)
        page = await api.get(f"/runs/{body['job_id']}")

        missing = await api.post(
            f"/runs/{uuid.uuid4()}/replay",
            data={CSRF_FIELD_NAME: _hidden_value(page.text, CSRF_FIELD_NAME)},
        )

        assert missing.status_code == 404


def _planted_challenge(job_id: uuid.UUID) -> Disagreement:
    """One red-team challenge, in the shape `services.red_team` records them.

    Written by hand rather than by running the adversary: what is under test is how the
    page treats such a row, and a fake provider's challenge would vary with the fixture.
    """
    return Disagreement(
        job_id=job_id,
        topic="valuation: the terminal assumptions overstate the base case",
        kind=DisagreementKind.THESIS_CONFLICT,
        position_a={
            "reference": f"draft:{job_id}",
            "label": "Base thesis (the draft's recorded claims)",
            "value": "0",
            "unit": "thesis",
            "tier": "T1_REGULATORY",
        },
        position_b={
            "reference": "red_team:valuation:abc123",
            "label": "Red team challenge (valuation, severity 4/5)",
            "value": "0",
            "unit": "thesis",
            "tier": "T2_COMPANY",
        },
        resolution=ResolutionOutcome.ESCALATED,
        rule=ResolutionRule.THESIS_CONFLICT,
        resolved_by=ResolvedBy.RULE,
        resolution_rationale=(
            "A thesis-level disagreement is never resolved automatically; both are published."
        ),
        escalated_to_gate=GateKind.FINAL,
        material=True,
        fingerprint="f" * 64,
        detail={
            "challenge": "The terminal growth outruns the sector.",
            "basis": "The recorded fade against the peer medians.",
            "severity": 4,
            "dimension": "valuation",
            "evidence": {"facts": ["a", "b", "c"], "calculations": [], "sources": ["s"]},
        },
    )


class TestCancellingARun:
    """The surface of the cancel feature. The behaviour is in ``test_cancellation.py``."""

    async def test_cancelling_returns_202_and_records_the_request(
        self, api: Any, committed: dict, db_session: Any
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])

        # 202, not 200: the run has been *asked* to stop and will do so at the next step
        # boundary. Anything else would claim the run had already stopped.
        response = await api.post(f"/api/runs/{job_id}/cancel", json={"reason": "wrong date"})
        assert response.status_code == 202

        found = await db_session.scalar(
            select(JobCancellation).where(JobCancellation.job_id == job_id)
        )
        assert found is not None
        assert found.reason == "wrong date"

    async def test_a_reason_is_optional(self, api: Any, committed: dict) -> None:
        body = await start_run(api, committed["request"].id)
        assert (await api.post(f"/api/runs/{body['job_id']}/cancel")).status_code == 202

    async def test_cancelling_a_finished_run_is_a_409(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            job.status = JobStatus.SUCCEEDED
            await session.commit()

        response = await api.post(f"/api/runs/{job_id}/cancel")

        assert response.status_code == 409
        assert response.json()["code"] == "conflict"

    async def test_cancelling_another_users_run_is_a_404(
        self, api: Any, someone_elses_run: uuid.UUID
    ) -> None:
        assert (await api.post(f"/api/runs/{someone_elses_run}/cancel")).status_code == 404

    async def test_the_console_offers_a_cancel_button_while_the_run_is_live(
        self, api: Any, committed: dict
    ) -> None:
        body = await start_run(api, committed["request"].id)

        page = await api.get(f"/runs/{body['job_id']}")
        assert 'id="cancel-run"' in page.text

    async def test_the_console_stops_offering_it_once_asked(
        self, api: Any, committed: dict
    ) -> None:
        body = await start_run(api, committed["request"].id)
        await api.post(f"/api/runs/{body['job_id']}/cancel", json={"reason": "wrong date"})

        page = await api.get(f"/runs/{body['job_id']}")
        assert 'id="cancel-run"' not in page.text
        assert 'id="cancellation-requested"' in page.text
        # The reason is shown back, so "why did this stop?" is answerable from the page the
        # operator is already looking at.
        assert "wrong date" in page.text

    async def test_the_form_post_cancels_and_redirects_to_the_console(
        self, api: Any, committed: dict, db_session: Any
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        page = await api.get(f"/runs/{job_id}")

        response = await api.post(
            f"/runs/{job_id}/cancel",
            data={CSRF_FIELD_NAME: _hidden_value(page.text, CSRF_FIELD_NAME), "reason": "typo"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == f"/runs/{job_id}"
        found = await db_session.scalar(
            select(JobCancellation).where(JobCancellation.job_id == job_id)
        )
        assert found is not None

    async def test_a_form_post_without_a_csrf_token_cancels_nothing(
        self, api: Any, committed: dict, db_session: Any
    ) -> None:
        # This application runs on loopback with no authentication, so any page in any tab
        # can POST to it. An unprotected cancel means a page merely visited can stop a run.
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])

        response = await api.post(f"/runs/{job_id}/cancel", data={"reason": "not mine to give"})

        assert response.status_code == 403
        found = await db_session.scalar(
            select(JobCancellation).where(JobCancellation.job_id == job_id)
        )
        assert found is None


def _slow_run_state(*, delay_seconds: float) -> Any:
    """A ``run_state`` that holds its connection long enough to be cancelled mid-query.

    The sleep runs **on the database**, so the session really is mid-statement with its
    connection checked out — which is the state the leak needed. A plain
    ``asyncio.sleep`` would park the coroutine with the connection already returned.
    """

    # Bound before the patch is installed, or the wrapper would resolve the patched
    # attribute and call itself once per poll.
    original = run_service.run_state

    async def run_state(session: Any, *, job_id: uuid.UUID) -> Any:
        await session.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": delay_seconds})
        return await original(session, job_id=job_id)

    return run_state


class TestOwnership:
    """One operator's run must not be readable by another."""

    async def test_reading_another_users_run_is_a_404(
        self, api: Any, someone_elses_run: uuid.UUID
    ) -> None:
        assert (await api.get(f"/api/runs/{someone_elses_run}")).status_code == 404

    async def test_deciding_on_another_users_gate_is_a_404(
        self, api: Any, someone_elses_run: uuid.UUID
    ) -> None:
        response = await api.post(
            f"/api/runs/{someone_elses_run}/gates/{GateKind.PLAN.value}/decide",
            json={"decision": Decision.APPROVED.value, "payload_hash": "a" * 64},
        )
        assert response.status_code == 404

    async def test_the_console_page_does_not_reveal_it_exists(
        self, api: Any, someone_elses_run: uuid.UUID
    ) -> None:
        response = await api.get(f"/runs/{someone_elses_run}")
        assert response.status_code == 404
        assert str(someone_elses_run) in response.text  # the id they asked for, not the run


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
        body = await start_run(api, committed["request"].id)
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

    async def test_a_reader_that_leaves_mid_query_does_not_strand_a_connection(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """The cancellation lands *inside* the poll, and the connection still comes back.

        The previous test cancels at the sleep between polls, where no session is open —
        the easy case. This one cancels while a query is in flight, which is the case
        worth pinning: closing a session is itself an ``await``, so a cancellation that
        unwound the context manager without completing it would leave the connection
        checked out until the garbage collector noticed, one per abandoned stream.

        It holds today, which is why this is written as a characterisation rather than a
        fix. It is here because the property is easy to lose — any refactor that holds a
        session across a yield, or swallows the cancellation, breaks it — and because the
        browser suite's long-standing flake was chased through exactly this code before
        being traced to server *shutdown* instead (see ``tests/e2e/conftest.py``).
        """
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        pool = db_engine.sync_engine.pool
        settled = pool.checkedout()

        slow = _slow_run_state(delay_seconds=0.3)
        with mock.patch.object(sse_module.run_service, "run_state", slow):
            stream = event_stream(factory, job_id=job_id, poll_seconds=0.05)
            consumer = asyncio.create_task(anext(stream))  # type: ignore[arg-type]

            # Long enough for the query to be in flight and its connection checked out.
            await asyncio.sleep(0.05)
            assert pool.checkedout() > settled, "the poll should be holding a connection"

            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer

        assert pool.checkedout() == settled, "the cancelled poll kept its connection"

    async def test_a_quiet_run_still_says_the_server_is_alive(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """The heartbeat is the whole answer to "has it stalled?".

        A step that calls a model changes nothing in the database for minutes, so the
        stream emits no state frames -- which looks exactly like a dead connection. This
        used to be an SSE comment, invisible to ``EventSource``; a named event carrying the
        server's clock is the same keep-alive and also something the console can show.
        """
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        stream = event_stream(factory, job_id=job_id, poll_seconds=0.01, heartbeat_seconds=0.02)

        frames = [await anext(stream) for _ in range(6)]
        await stream.aclose()

        beats = [frame for frame in frames if "event: heartbeat" in frame]
        assert beats, frames
        payload = json.loads(beats[0].split("data: ", 1)[1].strip())
        assert datetime.fromisoformat(payload["at"]).tzinfo is not None

    async def test_the_state_frame_is_sent_once_while_nothing_changes(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """The guard on putting a clock in the state payload.

        The stream hashes each frame and sends it only when it differs. A time field would
        make every poll differ, so a run doing nothing visible would emit a frame a second
        -- and the heartbeat, which is meant to be the rare signal, would be drowned by it.
        """
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        stream = event_stream(factory, job_id=job_id, poll_seconds=0.01, heartbeat_seconds=0.02)

        frames = [await anext(stream) for _ in range(6)]
        await stream.aclose()

        assert len([frame for frame in frames if "event: state" in frame]) == 1

    async def test_a_terminal_run_emits_state_then_done(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
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
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
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
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        page = await api.get(f"/runs/{body['job_id']}")
        assert page.status_code == 200
        # The step list is server-rendered, so a browser with no script still sees it.
        assert 'data-step="plan"' in page.text
        assert 'id="awaiting-approval"' in page.text

    async def test_the_console_shows_the_steps_that_have_not_started(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """A run one step in should look like a run with the rest still to go.

        ``render`` is the last step and has no ``job_steps`` row this early, so its
        presence proves the console renders the declared workflow rather than only rows.
        """
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        page = await api.get(f"/runs/{body['job_id']}")
        declared = list(run_service.declared_steps(WORKFLOW_VERSION))

        assert 'data-step="render"' in page.text
        assert re.search(r'data-field="steps-total">\s*(\d+)\s*<', page.text) is not None
        assert re.findall(r'data-step="([^"]+)"', page.text) == declared

    async def test_the_console_says_what_it_is_waiting_for(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """The reason this page exists: a step can take five minutes and change nothing.

        The banner names the step, and the row carries the server's start time so the
        browser can tick an elapsed clock against it.
        """
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])

        page = await api.get(f"/runs/{job_id}")
        assert 'id="run-progress"' in page.text
        # Where to look when the clock passes ten minutes. The console cannot see the
        # worker, so pointing at it is the most it can honestly offer.
        assert "just worker" in page.text

        await driver.advance(job_id)
        page = await api.get(f"/runs/{job_id}")
        started = re.search(r'data-step="plan"\s+data-started-at="([^"]+)"', page.text)
        assert started is not None
        assert datetime.fromisoformat(started.group(1)).tzinfo is not None

    async def test_the_console_declares_the_status_it_was_rendered_for(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """What lets the page notice it has gone stale.

        Reaching a gate is not terminal, so the stream's ``done`` event never fires, and a
        run that stopped for a decision used to sit there with the status chip patched to
        AWAITING_APPROVAL and no banner and no buttons until the operator refreshed by
        hand. The script compares each frame against this and re-fetches when they differ.
        """
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])

        queued = await api.get(f"/runs/{job_id}")
        assert f'data-status="{JobStatus.QUEUED.value}"' in queued.text

        await driver.advance(job_id)
        gated = await api.get(f"/runs/{job_id}")
        assert f'data-status="{JobStatus.AWAITING_APPROVAL.value}"' in gated.text
        assert 'id="awaiting-approval"' in gated.text

    async def test_a_step_that_has_not_started_reads_zero_the_same_way(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """£0 beside £0.0000 reads as two different kinds of nothing."""
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        state = (await api.get(f"/api/runs/{body['job_id']}")).json()
        costs = {step["key"]: step["cost_gbp"] for step in state["steps"]}

        assert costs["render"] == costs["acquire"] == "0.0000"

    async def test_a_finished_run_stops_offering_reassurance(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """ "Working on..." under a report that is already written is noise, and wrong."""
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        await driver.advance(job_id)

        page = await api.get(f"/runs/{job_id}")
        assert 'id="run-progress"' not in page.text

    async def test_a_failed_step_shows_the_sentence_not_the_payload(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """What a live failure actually looked like on this page.

        The whole error dictionary -- code, context, message -- rendered as one
        unpunctuated line, with the only readable part buried in the middle of it.
        """
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == "plan")
            )
            assert row is not None
            row.status = JobStatus.FAILED
            row.error = {
                "code": "external_service_error",
                "message": "The Anthropic API call failed (APIConnectionError).",
                "context": {"provider": "anthropic", "retryable": True},
            }
            await session.commit()

        page = await api.get(f"/runs/{job_id}")
        assert "The Anthropic API call failed (APIConnectionError)." in page.text
        assert "external_service_error" in page.text
        assert "&#39;retryable&#39;" not in page.text

    async def test_the_console_falls_back_to_polling(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """The meta refresh is in the markup, contained so only a script-less browser runs it.

        The `noscript` wrapper is the load-bearing part: a declarative refresh is
        scheduled at parse time and removing the element afterwards does not cancel it,
        so a bare meta tag would reload the page underneath the event stream every few
        seconds — which is exactly what it used to do.
        """
        body = await start_run(api, committed["request"].id)
        await driver.advance(uuid.UUID(body["job_id"]))

        page = await api.get(f"/runs/{body['job_id']}")
        assert 'id="poll-fallback"' in page.text
        assert 'http-equiv="refresh"' in page.text
        fallback_at = page.text.index('id="poll-fallback"')
        opened_at = page.text.index("<noscript>")
        assert opened_at < fallback_at < page.text.index("</noscript>")

    async def test_a_finished_run_does_not_keep_refreshing(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        await driver.advance(job_id)

        page = await api.get(f"/runs/{job_id}")
        assert 'id="poll-fallback"' not in page.text
        assert 'id="view-report"' in page.text

    async def test_the_plan_page_shows_the_hash_it_will_submit(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        page = await api.get(f"/runs/{job_id}/plan")
        assert page.status_code == 200

        shown = _hidden_value(page.text, "payload_hash")
        assert shown == await driver.payload_hash_of(job_id, "critique_plan")

    async def test_the_plan_page_lists_the_section_spine(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """Gate 1 shows which sections the run owes, platform-filled ones marked as such."""
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        page = await api.get(f"/runs/{job_id}/plan")
        assert 'id="section-listing"' in page.text
        for key in SPINE_KEYS:
            assert key in page.text
        assert "platform-filled" in page.text

    async def test_approving_through_the_form_advances_the_gate(
        self, api: Any, committed: dict, driver: Driver, enqueued: EnqueueRecorder
    ) -> None:
        body = await start_run(api, committed["request"].id)
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
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        response = await api.post(
            f"/runs/{job_id}/gates/{GateKind.PLAN.value}",
            data={
                "payload_hash": await driver.payload_hash_of(job_id, "critique_plan"),
                "decision": Decision.APPROVED.value,
            },
        )

        assert response.status_code == 403
        assert await db_session.scalar(select(Approval).where(Approval.job_id == job_id)) is None

    async def test_a_decided_gate_shows_the_decision_rather_than_a_button(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)
        await driver.approve(job_id, gate=GateKind.PLAN, step="critique_plan")

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
            job_id, "revise"
        )

    async def test_the_review_page_renders_the_gate_two_dashboard(
        self, api: Any, committed: dict, driver: Driver, starved_section: None
    ) -> None:
        """The §2.4 surface: banner, validations, coverage, calculations and cost — all
        rendered on the server, so an approval cannot be made without them on the page."""
        job_id = await _to_second_gate(api, committed, driver)

        page = await api.get(f"/runs/{job_id}/review")
        assert page.status_code == 200

        assert 'id="triggers"' in page.text
        assert "low_source_coverage" in page.text
        assert "material_missing_section" in page.text

        assert 'id="validations"' in page.text
        assert "citation_accuracy" in page.text
        assert 'id="coverage"' in page.text
        assert 'id="calculations"' in page.text
        assert 'id="cost"' in page.text

        # The calculations table dates each figure and speaks the house style (gap R11):
        # the CHRW page showed `928567000.000000000000 USD` and six depreciation rates
        # with nothing saying which year each belonged to.
        assert ">Period</th>" in page.text
        assert "000000000000" not in page.text

        # This run recorded no disagreements, so that section honestly says nothing.
        assert 'id="disagreements"' not in page.text

    async def test_a_failed_section_says_what_it_was_dealt_and_why_it_refused(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """Gap A63's other half: the diagnosis was recorded and never displayed.

        `sections.writing._failed` puts the refusal on the row and the evidence tally in
        the step's output, and the page showed a chip carrying the section key. Five
        sections dying on a starved pack therefore looked like five blanks, and the only
        way to find out why was to read a worker log.
        """
        job_id = await _to_second_gate(api, committed, driver)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as writer:
            section = await writer.scalar(
                select(ReportSection).where(ReportSection.job_id == job_id).limit(1)
            )
            assert section is not None
            section.status = SectionStatus.FAILED
            section.low_confidence_reason = "a numeric claim needs at least one proposed citation"
            step = await writer.scalar(
                select(JobStep)
                .where(JobStep.job_id == job_id, JobStep.step_key == "draft")
                .order_by(JobStep.attempt.desc())
                .limit(1)
            )
            assert step is not None
            step.output_ref = {
                **(step.output_ref or {}),
                "builtin_sections": [
                    {
                        "section_key": section.section_key,
                        "status": "failed",
                        "attempts": 2,
                        "evidence_dealt": {"facts": 0, "calculations": 0, "excerpts": 3},
                        "problems": ["a numeric claim needs at least one proposed citation"],
                        "refusal_causes": {"citation": 2},
                    }
                ],
            }
            await writer.commit()

        page = await api.get(f"/runs/{job_id}/review")

        assert page.status_code == 200
        assert f'data-section="{section.section_key}"' in page.text
        # The tally, by kind. A pack of three excerpts and no facts is untruncated and
        # still starved, and a single total would hide exactly that.
        assert "0f ·" in page.text
        assert "3e" in page.text
        assert "a numeric claim needs at least one proposed citation" in page.text
        assert "citation&times;2" in page.text

    async def test_a_red_team_challenge_reads_as_a_challenge_not_a_fault(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """Gap R12, then gap R15.

        R12: every thesis row read "0 thesis (T1_REGULATORY)" — the ladder's placeholder
        fields, which it never compares, rendered as though they meant something.

        R15: those rows were also *counted as unresolved disagreements* and fired a fault
        trigger, so a run whose adversary did its job reported seven problems. The
        challenges now have their own section, with the objection at reading width and its
        evidence beneath it, and the amber banner counts source conflicts alone.
        """
        job_id = await _to_second_gate(api, committed, driver)
        # A real commit, not the rolled-back test session: the page reads through the
        # application's own engine, which cannot see a savepoint inside this test's
        # transaction.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as writer:
            writer.add(_planted_challenge(job_id))
            await writer.commit()

        page = await api.get(f"/runs/{job_id}/review")

        assert page.status_code == 200
        assert 'id="red-team"' in page.text
        assert "The terminal growth outruns the sector." in page.text
        assert "The recorded fade against the peer medians." in page.text
        assert "3 fact(s)" in page.text
        assert "severity 4" in page.text
        # R12's placeholders stay gone: they were never compared and never meant anything.
        assert "0 thesis" not in page.text
        # And the fault banner does not claim it. This is the run's only disagreement, so
        # an amber "1 unresolved disagreement" here would be the whole regression.
        assert 'id="escalations"' not in page.text

    async def test_a_challenge_can_be_settled_on_the_record(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """`settle_by_hand` existed from the first day of the ladder and nothing reached it.

        The page therefore showed two positions and no way to prefer either, which reads as
        a question the operator is failing to answer rather than a record they may add to.
        """
        job_id = await _to_second_gate(api, committed, driver)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as writer:
            writer.add(_planted_challenge(job_id))
            await writer.commit()

        page = await api.get(f"/runs/{job_id}/review")
        token = _hidden_value(page.text, CSRF_FIELD_NAME)
        challenge_id = re.search(r'data-challenge="([^"]+)"', page.text)
        assert challenge_id, "the challenge did not render"

        settled = await api.post(
            f"/runs/{job_id}/disagreements/{challenge_id.group(1)}/settle",
            data={
                CSRF_FIELD_NAME: token,
                "outcome": "chose_a",
                "rationale": "The fade is inside the peer range once the 2024 outlier is dropped.",
            },
            follow_redirects=False,
        )

        assert settled.status_code == 303
        after = await api.get(f"/runs/{job_id}/review")
        assert "the 2024 outlier" in after.text
        # Settled, so the form is gone from that row and the reason stands in its place.
        assert f'id="settle-{challenge_id.group(1)}"' not in after.text

    async def test_settling_without_a_reason_is_refused(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        """A decision that overrides a rule without saying why is the least reviewable row
        in the table, and the service says so. This is the surface honouring it."""
        job_id = await _to_second_gate(api, committed, driver)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as writer:
            writer.add(_planted_challenge(job_id))
            await writer.commit()

        page = await api.get(f"/runs/{job_id}/review")
        challenge_id = re.search(r'data-challenge="([^"]+)"', page.text)
        assert challenge_id

        refused = await api.post(
            f"/runs/{job_id}/disagreements/{challenge_id.group(1)}/settle",
            data={
                CSRF_FIELD_NAME: _hidden_value(page.text, CSRF_FIELD_NAME),
                "outcome": "chose_a",
                "rationale": "   ",
            },
        )

        assert refused.status_code == 400
        assert "needs a reason" in refused.text

    async def test_the_report_page_links_to_the_archived_download(
        self, api: Any, committed: dict, driver: Driver, db_session: Any
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        await driver.advance(job_id)

        report = await db_session.scalar(select(Report).where(Report.job_id == job_id))
        assert report is not None

        page = await api.get(f"/reports/{report.id}")
        assert page.status_code == 200
        assert f"/api/reports/{report.id}/download" in page.text
        assert 'id="immutable-badge"' in page.text

    async def test_the_review_page_links_to_the_document_preview(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)

        page = await api.get(f"/runs/{job_id}/review")
        assert 'id="document-preview"' in page.text
        assert f"/runs/{job_id}/preview" in page.text

    async def test_the_draft_preview_is_the_document_itself(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """Not a site page: the report's own HTML, cover, contents and disclaimer."""
        job_id = await _to_second_gate(api, committed, driver)

        page = await api.get(f"/runs/{job_id}/preview")
        assert page.status_code == 200
        assert page.text.startswith("<!DOCTYPE html>")
        assert "Research Note" in page.text
        assert "not</strong> regulated investment advice" in page.text
        assert 'id="contents"' in page.text
        # The draft has no view yet, and the preview says so rather than inventing one.
        assert "no view reached" in page.text

    async def test_a_run_with_no_sections_has_no_preview(self, api: Any, committed: dict) -> None:
        # Started but not yet picked up by the worker: the plan step is what creates the
        # section rows, so this run has none and there is no document to assemble.
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])

        page = await api.get(f"/runs/{job_id}/preview")
        assert page.status_code == 404
        assert "no document to preview" in page.text

    async def test_the_frozen_report_carries_the_exhibits_the_run_recorded(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any, db_session: Any
    ) -> None:
        """The acceptance path: rows recorded during the run become charts in the
        rendered report — the workflow's own render step, not just the assembler."""
        job_id = await _to_second_gate(api, committed, driver)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                Scenario(
                    request_id=committed["request"].id,
                    job_id=job_id,
                    key="base",
                    label="Base case",
                    description="The base case, as stated.",
                )
            )
            session.add(
                Calculation(
                    job_id=job_id,
                    name="value_per_share",
                    formula="value per share = equity value / shares outstanding",
                    function_ref="aer.calc.dcf:value_per_share",
                    code_version="chartcode123456",
                    inputs=[],
                    parameters={"method": "gordon_growth", "case": "base"},
                    output_value=Decimal("280.00"),
                    output_unit="USD/share",
                )
            )
            await session.commit()

        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        await driver.advance(job_id)

        report = await db_session.scalar(select(Report).where(Report.job_id == job_id))
        assert report is not None
        markdown = str(report.content["markdown"])
        # Beside the analysis it supports, not under a trailing "## Exhibits" pack: the
        # section that claims the chart carries it (gap N1), at the sub-heading level.
        assert "### Scenario bridge" in markdown
        assert "Rendered in the HTML and PDF editions" in markdown

        preview = await api.get(f"/reports/{report.id}/preview")
        assert "Scenario bridge" in preview.text
        assert "data:image/svg+xml;base64," in preview.text

    async def test_an_approved_run_freezes_all_three_notations(
        self, api: Any, committed: dict, driver: Driver, db_session: Any
    ) -> None:
        """Approval yields Markdown, HTML and PDF artefacts; every download serves the
        archived bytes, provably — the digest header, the artefact row and the body's own
        hash are one value. The PDF carries a bookmark for every section row."""
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        await driver.advance(job_id)

        report = await db_session.scalar(select(Report).where(Report.job_id == job_id))
        assert report is not None
        assert report.markdown_artefact_id is not None
        assert report.html_artefact_id is not None
        assert report.pdf_artefact_id is not None

        for fmt, sniff in (("md", b"# "), ("html", b"<!DOCTYPE html>"), ("pdf", b"%PDF")):
            response = await api.get(f"/api/reports/{report.id}/download/{fmt}")
            assert response.status_code == 200, fmt
            assert response.content.startswith(sniff), fmt
            digest = hashlib.sha256(response.content).hexdigest()
            assert response.headers["X-Artefact-SHA256"] == digest, fmt

        # The PDF is derived from exactly the archived HTML: re-finishing those bytes
        # is not asserted here (task tests do), but the bookmarks prove the whole spine
        # made it through — one per generated section, from the heading structure alone.
        pdf_response = await api.get(f"/api/reports/{report.id}/download/pdf")
        titles = _pdf_outline_titles(pdf_response.content)
        section_titles = list(
            await db_session.scalars(
                select(SectionDefinition.title)
                .join(ReportSection, ReportSection.section_definition_id == SectionDefinition.id)
                .where(ReportSection.job_id == job_id)
            )
        )
        assert len(section_titles) >= 18
        for title in section_titles:
            assert title in titles, title

        page = await api.get(f"/reports/{report.id}")
        assert 'id="download-pdf"' in page.text
        assert 'id="download-html"' in page.text

    async def test_an_unapproved_report_has_no_pdf_and_says_so(
        self, api: Any, committed: dict, db_engine: Any
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = Job(
                work_order_id=committed["request"].id,
                request_id=committed["request"].id,
                workflow_version="vertical_slice_v1",
                code_version="unapproved123456",
                status=JobStatus.SUCCEEDED,
            )
            session.add(job)
            await session.flush()
            report = Report(
                job_id=job.id,
                request_id=committed["request"].id,
                as_of_date=committed["request"].as_of_date,
                content={"markdown": "draft"},
                content_hash="0" * 64,
                immutable=False,
            )
            session.add(report)
            await session.commit()
            report_id = report.id

        response = await api.get(f"/api/reports/{report_id}/download/pdf")
        assert response.status_code == 404
        assert "never approved" in response.text

        page = await api.get(f"/reports/{report_id}")
        assert 'id="no-pdf"' in page.text
        assert 'id="download-pdf"' not in page.text

    async def test_no_licensed_geometry_reaches_the_preview_or_the_valuation_page(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        """ADR 0043's containment, at the surfaces: the preview carries no internal
        chart, and a run with no price rows shows no internal section at all."""
        job_id = await _to_second_gate(api, committed, driver)

        preview = await api.get(f"/runs/{job_id}/preview")
        assert "price_relative" not in preview.text
        assert "football_field_internal" not in preview.text

        valuation = await api.get(f"/runs/{job_id}/valuation")
        assert valuation.status_code == 200
        # No stored bars means the internal set is all placeholders, and a placeholder
        # price chart informs nobody — the section is absent, not empty.
        assert 'id="internal-charts"' not in valuation.text

    async def test_a_preview_of_a_run_that_is_not_yours_does_not_exist(
        self, api: Any, someone_elses_run: uuid.UUID
    ) -> None:
        page = await api.get(f"/runs/{someone_elses_run}/preview")
        assert page.status_code == 404
        # The *ownership* refusal, specifically. The other run has no sections either, so
        # a broken ownership check would still 404 — with the no-sections message — and a
        # status assertion alone could not tell the two guards apart.
        assert f"No run {someone_elses_run}" in page.text

    async def test_the_report_page_links_to_its_own_preview(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        await driver.advance(job_id)

        # Backdate the row before viewing: a report produced moments ago carries a
        # created_at that *rounds to the same minute as now*, so asserting the row's own
        # timestamp would also pass if the preview stamped the viewing time instead.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            report = await session.scalar(select(Report).where(Report.job_id == job_id))
            assert report is not None
            report.created_at = datetime(2020, 5, 4, 9, 30, tzinfo=UTC)
            report_id = report.id
            await session.commit()

        page = await api.get(f"/reports/{report_id}")
        assert 'id="report-preview"' in page.text
        assert f"/reports/{report_id}/preview" in page.text

        preview = await api.get(f"/reports/{report_id}/preview")
        assert preview.status_code == 200
        assert preview.text.startswith("<!DOCTYPE html>")
        # Stamped with the date the report was produced, not the date it was viewed.
        assert "2020-05-04 09:30 UTC" in preview.text

    async def test_every_page_carries_the_disclaimer(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await driver.advance(job_id)

        for path in (f"/runs/{job_id}", f"/runs/{job_id}/plan"):
            page = await api.get(path)
            assert "not regulated investment advice" in page.text

    async def test_the_request_page_offers_to_start_a_run(self, api: Any, committed: dict) -> None:
        page = await api.get(f"/requests/{committed['request'].id}")
        assert 'id="start-run"' in page.text

    async def test_it_links_to_the_run_once_one_exists(self, api: Any, committed: dict) -> None:
        body = await start_run(api, committed["request"].id)

        page = await api.get(f"/requests/{committed['request'].id}")
        assert 'id="open-run"' in page.text
        assert f"/runs/{body['job_id']}" in page.text


class TestTheObsidianSurface:
    """The report page's export door: honest states, and refusal wired through."""

    async def test_an_unapproved_report_says_only_approved_reports_export(
        self, api: Any, committed: dict, driver: Driver, db_session: Any
    ) -> None:
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        await driver.advance(job_id)
        report = await db_session.scalar(select(Report).where(Report.job_id == job_id))
        assert report is not None

        # The test app has no vault configured, and the page says so rather than
        # offering a button that would fail.
        page = await api.get(f"/reports/{report.id}")
        assert 'id="export-unconfigured"' in page.text
        assert 'id="export-obsidian-button"' not in page.text

        # No form renders in this state, but the page still set the CSRF cookie; the
        # double-submit pair is the cookie's own token.
        token = api.cookies.get("aer_csrf")
        assert token
        response = await api.post(
            f"/reports/{report.id}/export-obsidian", data={CSRF_FIELD_NAME: token}
        )
        assert response.status_code == 422
        assert "No Obsidian vault is configured" in response.text


class TestTheHistorySurfaces:
    """Task 49's pages and API, over directly seeded approved reports."""

    async def _seed_approved(
        self, committed: dict, db_engine: Any, *, ticker: str = "MSFT", exchange: str = "NASDAQ"
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """One company with one approved report and one draft; ids of both rows."""
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            company = Company(
                name="MICROSOFT CORP", cik="0000789019", ticker=ticker, exchange=exchange
            )
            session.add(company)
            await session.flush()

            approved_job = Job(
                work_order_id=committed["request"].id,
                request_id=committed["request"].id,
                workflow_version="vertical_slice_v1",
                code_version="historyseed12345",
                status=JobStatus.SUCCEEDED,
            )
            draft_job = Job(
                work_order_id=committed["request"].id,
                request_id=committed["request"].id,
                workflow_version="vertical_slice_v1",
                code_version="historyseed12345",
                status=JobStatus.SUCCEEDED,
            )
            session.add_all([approved_job, draft_job])
            await session.flush()

            approved = Report(
                job_id=approved_job.id,
                request_id=committed["request"].id,
                company_id=company.id,
                as_of_date=committed["request"].as_of_date,
                valuation_low=Decimal("180"),
                valuation_high=Decimal("220"),
                valuation_currency="USD",
                content={"markdown": "approved"},
                content_hash="c" * 64,
                approved_at=datetime(2022, 1, 15, 10, 0, tzinfo=UTC),
                immutable=True,
            )
            draft = Report(
                job_id=draft_job.id,
                request_id=committed["request"].id,
                company_id=company.id,
                as_of_date=committed["request"].as_of_date,
                content={"markdown": "draft"},
                content_hash="d" * 64,
                immutable=False,
            )
            session.add_all([approved, draft])
            await session.commit()
            return company.id, approved.id

    async def test_the_reports_page_groups_and_filters(
        self, api: Any, committed: dict, db_engine: Any
    ) -> None:
        company_id, _ = await self._seed_approved(committed, db_engine)

        page = await api.get("/reports")
        assert page.status_code == 200
        assert "Microsoft Corporation (MSFT)" in page.text
        assert ">Approved<" in page.text
        assert ">Draft<" in page.text  # the work list shows drafts, badged
        assert f"/companies/{company_id}" in page.text

        filtered = await api.get("/reports", params={"company": "zzz"})
        assert 'id="no-reports"' in filtered.text

    async def test_the_company_page_shows_history_and_only_history(
        self, api: Any, committed: dict, db_engine: Any
    ) -> None:
        company_id, approved_id = await self._seed_approved(committed, db_engine)

        page = await api.get(f"/companies/{company_id}")
        assert page.status_code == 200
        assert 'id="report-timeline"' in page.text
        assert f"/reports/{approved_id}" in page.text
        # The draft is not history: the one approved report is the whole timeline.
        assert page.text.count("as of 2") == 1
        assert 'id="valuation-history-chart"' in page.text
        assert "data:image/svg+xml;base64," in page.text
        assert "180 to 220 USD per share" in page.text

    async def test_the_history_api_serves_mine_and_refuses_theirs(
        self, api: Any, committed: dict, db_engine: Any, someone_elses_run: uuid.UUID
    ) -> None:
        company_id, approved_id = await self._seed_approved(committed, db_engine)

        mine = await api.get(f"/api/companies/{company_id}/history")
        assert mine.status_code == 200
        body = mine.json()
        assert body["ticker"] == "MSFT"
        assert [report["report_id"] for report in body["reports"]] == [str(approved_id)]

        # The other user's request created RIO on LSE; a company row for it is theirs,
        # not mine, and answers as if it did not exist.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            rio = Company(name="RIO TINTO PLC", cik="0000863064", ticker="RIO", exchange="LSE")
            session.add(rio)
            await session.commit()
            rio_id = rio.id

        theirs = await api.get(f"/api/companies/{rio_id}/history")
        assert theirs.status_code == 404


async def _to_second_gate(api: Any, committed: dict, driver: Driver) -> uuid.UUID:
    return await to_final_gate(api, committed["request"].id, driver)


def _pdf_outline_titles(pdf_bytes: bytes) -> list[str]:
    """Every bookmark title in the PDF, flattened."""
    titles: list[str] = []

    def walk(items: Any) -> None:
        for item in items:
            titles.append(str(item.title))
            walk(item.children)

    with pikepdf.open(BytesIO(pdf_bytes)) as pdf, pdf.open_outline() as outline:
        walk(outline.root)
    return titles


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


class TestStartingAgainAfterACancelledRun:
    """The dead end, at the surface an operator touches.

    Cancelling used to leave the request page offering only "open the run", with no way to
    start again and no way to remove it. This is the journey that was broken.
    """

    async def _cancelled(self, api: Any, committed: dict, db_engine: Any) -> uuid.UUID:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        await api.post(f"/api/runs/{job_id}/cancel", json={"reason": "wrong as-of date"})

        # The worker's next pass, which is what actually moves the job to CANCELLED.
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            job.status = JobStatus.CANCELLED
            await session.commit()
        return job_id

    async def test_the_request_page_offers_a_new_run(
        self, api: Any, committed: dict, db_engine: Any
    ) -> None:
        await self._cancelled(api, committed, db_engine)

        page = await api.get(f"/requests/{committed['request'].id}")

        assert 'id="start-run"' in page.text
        assert "cancelled" in page.text.lower()
        # The old run is still reachable — superseded, not erased.
        assert 'id="open-run"' in page.text

    async def test_starting_again_creates_a_different_run(
        self, api: Any, committed: dict, db_engine: Any, enqueued: EnqueueRecorder
    ) -> None:
        first = await self._cancelled(api, committed, db_engine)

        second = uuid.UUID((await start_run(api, committed["request"].id))["job_id"])

        assert second != first
        assert str(second) in enqueued.job_ids

    async def test_the_new_run_is_not_born_cancelled(
        self, api: Any, committed: dict, db_engine: Any
    ) -> None:
        # The cancellation belongs to the old job. A resurrected row would carry it and
        # stop again on its first step.
        await self._cancelled(api, committed, db_engine)
        second = (await start_run(api, committed["request"].id))["job_id"]

        assert (await api.get(f"/api/runs/{second}")).json()["status"] == JobStatus.QUEUED.value

    async def test_a_run_still_going_is_returned_rather_than_duplicated(
        self, api: Any, committed: dict
    ) -> None:
        first = await start_run(api, committed["request"].id)
        second = await start_run(api, committed["request"].id)

        assert first["job_id"] == second["job_id"]

    async def test_a_finished_report_is_not_superseded(
        self, api: Any, committed: dict, driver: Driver
    ) -> None:
        # One report per request still holds. Starting again on a run that produced one
        # would need a story about which report is current, and there is not one yet.
        job_id = await _to_second_gate(api, committed, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        assert await driver.advance(job_id) is JobStatus.SUCCEEDED

        assert (await start_run(api, committed["request"].id))["job_id"] == str(job_id)


class TestDeletingARequestWhoseRunWasCancelled:
    """Your junk test request, thrown away — through the API, with real spend behind it.

    This is the journey that was blocked. The planner runs before anyone presses stop, so a
    cancelled request nearly always has a cost row, and blocking on spend made the delete
    button theoretical. Since migration 0009 the spend outlives the request, so there is
    nothing left for the refusal to protect.
    """

    async def _cancelled_with_spend(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> uuid.UUID:
        body = await start_run(api, committed["request"].id)
        job_id = uuid.UUID(body["job_id"])
        # A real leg: the planner runs, is metered, and stops at the gate.
        assert await driver.advance(job_id) is JobStatus.AWAITING_APPROVAL

        await api.post(f"/api/runs/{job_id}/cancel", json={"reason": "just testing"})
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            job.status = JobStatus.CANCELLED
            await session.commit()
        return job_id

    async def test_the_request_can_be_deleted(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        await self._cancelled_with_spend(api, committed, driver, db_engine)

        response = await api.delete(f"/api/requests/{committed['request'].id}")

        assert response.status_code == 204
        assert (await api.get(f"/api/requests/{committed['request'].id}")).status_code == 404

    async def test_the_page_offers_the_button(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any
    ) -> None:
        await self._cancelled_with_spend(api, committed, driver, db_engine)

        page = await api.get(f"/requests/{committed['request'].id}")

        assert 'id="delete-request"' in page.text
        assert 'id="edit-request"' in page.text

    async def test_the_spend_is_not_deleted_with_it(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any, db_session: Any
    ) -> None:
        # The property the old refusal existed to protect, now guaranteed by the schema
        # instead. A monthly cap you can get under by deleting what you spent it on is not
        # a cap.
        await self._cancelled_with_spend(api, committed, driver, db_engine)
        before = await db_session.scalar(select(func.coalesce(func.sum(Cost.amount_gbp), 0)))
        assert before > 0

        await api.delete(f"/api/requests/{committed['request'].id}")

        after = await db_session.scalar(select(func.coalesce(func.sum(Cost.amount_gbp), 0)))
        assert after == before

    async def test_the_orphaned_spend_stays_explicable(
        self, api: Any, committed: dict, driver: Driver, db_engine: Any, db_session: Any
    ) -> None:
        await self._cancelled_with_spend(api, committed, driver, db_engine)
        await api.delete(f"/api/requests/{committed['request'].id}")

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.deleted")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert Decimal(event.payload["spend_gbp"]) > 0
        assert event.payload["ticker"] == "MSFT"
