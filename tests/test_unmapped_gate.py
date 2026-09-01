"""The conditional gate: a tag the concept map cannot place stops the run.

The concept vocabulary is deliberately the top sixty rather than the whole taxonomy, so a
filing using something outside it is expected rather than exceptional. What must not happen
is that the overflow disappears: a run whose statements are quietly missing a line is worse
than one that stops and says which line.

Driven through the real workflow against a fixture that genuinely contains an unmapped tag,
because the property under test is "the gate fires on a real extraction", and a test that
hand-wrote the step's output would prove only that the gate reads a dictionary.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.db.models import Approval, Job, JobStep, User, WorkOrder
from aer.providers.fake import FakeProvider
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.storage.local import LocalArtefactStore
from aer.workflow.workflows.vertical_slice_v1 import (
    unmapped_gate_payload,
    unmapped_gate_required,
)
from tests.api_fixtures import build_app, client_for
from tests.request_fixtures import research_request
from tests.sec_fixtures import fixture_bytes
from tests.workflow_fixtures import (
    DEFAULT_PER_RUN_BUDGET_GBP,
    StubSecClient,
    gate_for,
    make_provider,
    paused_at,
)

pytestmark = pytest.mark.integration

# A companyfacts document with one us-gaap tag outside the concept map, and one filer
# extension element. Both are real shapes; the first is what the gate is for.
UNMAPPED_FIXTURE = "companyfacts_unmapped.json"
UNMAPPED_TAG = "us-gaap:AllocatedShareBasedCompensationExpense"

# After both fixtures' filing dates. The look-ahead filter is doing its job either way;
# an as-of date before them would leave every fact rejected and make `facts_written` say
# nothing about whether the gate discarded anything.
AS_OF_DATE = date(2024, 6, 30)

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"


@pytest.fixture
async def clean_slate(db_engine: Any) -> AsyncIterator[None]:
    """Truncate either side. A leftover run from another module would answer these queries."""
    await _truncate(db_engine)
    yield
    await _truncate(db_engine)


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    """A user and a request, committed so the application's own session sees them."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="unmapped@example.invalid", display_name="Gate", role=UserRole.OWNER)
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
            # The platform default, read rather than restated -- a fixture budgeting a
            # different ceiling than production drifts the moment either moves (A33).
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        session.add(request)
        await session.commit()
        return {"user": user, "request": request}


class Runner:
    """Advances a run to its next stopping point, committing as the worker would."""

    def __init__(self, engine: Any, settings: Settings, *, payload: bytes) -> None:
        self._factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        self._settings = settings
        self._store = LocalArtefactStore(
            settings.artefact_root, max_bytes=settings.max_artefact_bytes
        )
        self.provider: FakeProvider = make_provider()
        self.sec_client = StubSecClient(self._store, payload=payload)

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

    async def output_of(self, job_id: uuid.UUID, step: str) -> dict[str, Any] | None:
        async with self._factory() as session:
            row = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == step)
            )
            return None if row is None else dict(row.output_ref or {})

    async def waiting_at(self, job_id: uuid.UUID) -> str | None:
        """Which step this run is paused at, if any."""
        async with self._factory() as session:
            return await paused_at(session, job_id)

    async def approve(self, job_id: uuid.UUID, *, gate: GateKind, payload_hash: str) -> None:
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
                payload_hash=payload_hash,
            )
            await session.commit()


@pytest.fixture
def unmapped_runner(db_engine: Any, api_settings: Settings) -> Runner:
    return Runner(db_engine, api_settings, payload=fixture_bytes(UNMAPPED_FIXTURE))


@pytest.fixture
def mapped_runner(db_engine: Any, api_settings: Settings) -> Runner:
    """The ordinary case: a filing whose every tag the concept map knows."""
    return Runner(db_engine, api_settings, payload=fixture_bytes("companyfacts_msft.json"))


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    recorded: list[str] = []

    async def record(_redis: Any, job_id: uuid.UUID) -> None:
        recorded.append(str(job_id))

    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", record)
    monkeypatch.setattr("aer.web.pages.enqueue_run", record)
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


async def run_to_the_financials_gate(api: Any, runner: Runner, request_id: uuid.UUID) -> uuid.UUID:
    """Start a run, approve the plan, and let it stop wherever it stops next.

    "Next" excludes the gates an operator would clear on the way — the peer set (ADR 0059)
    and the assumptions (gap S2) — which are cleared below by reading the run's own state.
    An unmapped filing stops at the financials gate before either is reached, so on that
    path the loop never runs; a mapped filing meets both and would otherwise never arrive.
    """
    response = await api.post("/api/runs", json={"request_id": str(request_id)})
    assert response.status_code == 202, response.text
    job_id = uuid.UUID(response.json()["job_id"])

    await runner.advance(job_id)
    # The critique step's hash, not the plan step's (ADR 0091): the gate verifies against
    # the last step that can change what it displays.
    plan = await runner.output_of(job_id, "critique_plan")
    assert plan is not None
    await runner.approve(job_id, gate=GateKind.PLAN, payload_hash=str(plan["payload_hash"]))
    await runner.advance(job_id)

    # Every conditional gate between the plan and the financials one, cleared by asking the
    # run where it stopped. The peer set (ADR 0059) joined the assumptions gate here without
    # this file changing, and a driver that knew the sequence stopped one gate short — the
    # financials gate stayed QUEUED and every test in this module failed on it.
    while (clearing := gate_for(await runner.waiting_at(job_id))) is not None:
        gate, step = clearing
        produced = await runner.output_of(job_id, step)
        assert produced is not None, f"the {step} step has not run"
        await runner.approve(job_id, gate=gate, payload_hash=str(produced["payload_hash"]))
        await runner.advance(job_id)
    return job_id


class TestTheGateFiresOnARealExtraction:
    async def test_a_run_with_an_unmapped_tag_stops(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        state = (await api.get(f"/api/runs/{job_id}")).json()
        assert state["status"] == JobStatus.AWAITING_APPROVAL.value

        gate_step = next(s for s in state["steps"] if s["key"] == "gate_unmapped_concepts")
        assert gate_step["status"] == JobStatus.AWAITING_APPROVAL.value

    async def test_the_tag_is_named_rather_than_counted(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        """A count says something is missing. The tag says what to go and look at."""
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        body = (await api.get(f"/api/runs/{job_id}/financials")).json()
        assert body["required"] is True
        assert UNMAPPED_TAG in body["unmapped_tags"]

    async def test_the_facts_that_did_map_were_still_written(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        """Stopping is not discarding. The run resumes on the facts it already has."""
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        body = (await api.get(f"/api/runs/{job_id}/financials")).json()
        assert body["facts_written"] >= 1

    async def test_the_run_continues_once_it_is_confirmed(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        body = (await api.get(f"/api/runs/{job_id}/financials")).json()
        response = await api.post(
            f"/api/runs/{job_id}/gates/UNMAPPED_CONCEPTS/decide",
            json={"decision": "APPROVED", "payload_hash": body["payload_hash"]},
        )
        assert response.status_code == 202, response.text

        await unmapped_runner.advance(job_id)
        state = (await api.get(f"/api/runs/{job_id}")).json()
        gate_step = next(s for s in state["steps"] if s["key"] == "gate_unmapped_concepts")
        assert gate_step["status"] == JobStatus.SUCCEEDED.value

    async def test_confirming_a_different_set_of_tags_is_not_confirming_these(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        """The same rule as every other gate: the hash is what was approved."""
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        response = await api.post(
            f"/api/runs/{job_id}/gates/UNMAPPED_CONCEPTS/decide",
            json={"decision": "APPROVED", "payload_hash": "0" * 64},
        )
        assert response.status_code == 202, response.text

        status = await unmapped_runner.advance(job_id)
        assert status == JobStatus.AWAITING_APPROVAL


class TestTheGateStaysOutOfTheWayOtherwise:
    async def test_a_fully_mapped_filing_does_not_stop(
        self, api: Any, committed: dict, mapped_runner: Runner
    ) -> None:
        job_id = await run_to_the_financials_gate(api, mapped_runner, committed["request"].id)

        gate = await mapped_runner.output_of(job_id, "gate_unmapped_concepts")
        assert gate is not None
        assert gate["required"] is False

        approvals = await mapped_runner.output_of(job_id, "draft")
        assert approvals is not None, "the run should have reached the draft step"

    async def test_it_reports_not_required_rather_than_refusing_to_answer(
        self, api: Any, committed: dict, mapped_runner: Runner
    ) -> None:
        job_id = await run_to_the_financials_gate(api, mapped_runner, committed["request"].id)

        body = (await api.get(f"/api/runs/{job_id}/financials")).json()
        assert body["required"] is False
        assert body["unmapped_tags"] == []

    async def test_skipping_it_does_not_block_the_final_gate(
        self, api: Any, committed: dict, mapped_runner: Runner, db_engine: Any
    ) -> None:
        """A conditional gate nobody decided must not stop the gate after it."""
        job_id = await run_to_the_financials_gate(api, mapped_runner, committed["request"].id)

        sealed = await mapped_runner.output_of(job_id, "revise")
        assert sealed is not None
        await mapped_runner.approve(
            job_id, gate=GateKind.FINAL, payload_hash=str(sealed["payload_hash"])
        )
        assert await mapped_runner.advance(job_id) == JobStatus.SUCCEEDED

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            decided = list(
                await session.scalars(select(Approval.gate).where(Approval.job_id == job_id))
            )
        assert GateKind.UNMAPPED_CONCEPTS not in decided


class TestThePageShowsWhatItHashes:
    async def test_the_form_carries_the_hash_of_the_tags_it_displayed(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        page = await api.get(f"/runs/{job_id}/financials")
        assert page.status_code == 200
        assert UNMAPPED_TAG in page.text

        expected = (await api.get(f"/api/runs/{job_id}/financials")).json()["payload_hash"]
        assert expected in page.text

    async def test_the_page_shows_the_figure_behind_each_tag(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        """Gap R17: the gate asked a question it gave nobody the means to answer.

        A column of taxonomy element names cannot distinguish one extension carrying a
        company's headline profit measure from forty carrying segment breakdowns, and that
        distinction is the entire decision this gate exists to put to a person.
        """
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        page = await api.get(f"/runs/{job_id}/financials")

        assert page.status_code == 200
        assert UNMAPPED_TAG in page.text
        assert "Largest figure" in page.text
        assert "Of the biggest mapped line" in page.text
        # And the comparison: what the run did capture, beside what it could not place.
        assert 'id="mapped-concepts"' in page.text

    async def test_the_tables_are_filterable_without_being_broken_by_it(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        """A filing with forty extensions is a list nobody reads to the bottom.

        The control is hidden markup revealed by script, so a browser with scripting off
        gets a complete table and no dead search box.
        """
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        page = await api.get(f"/runs/{job_id}/financials")

        assert 'data-filters="#unmapped-tags"' in page.text
        assert 'hidden id="unmapped-filter-shell"' in page.text
        assert "/static/js/tables.js" in page.text

    async def test_a_run_that_does_not_need_it_gets_told_so(
        self, api: Any, committed: dict, mapped_runner: Runner
    ) -> None:
        job_id = await run_to_the_financials_gate(api, mapped_runner, committed["request"].id)

        page = await api.get(f"/runs/{job_id}/financials")
        assert page.status_code == 404
        assert "does not apply" in page.text

    async def test_the_console_offers_the_link_when_the_run_stopped_here(
        self, api: Any, committed: dict, unmapped_runner: Runner
    ) -> None:
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        console = await api.get(f"/runs/{job_id}")
        assert 'id="review-financials"' in console.text

    async def test_the_console_does_not_offer_it_to_a_run_that_went_through(
        self, api: Any, committed: dict, mapped_runner: Runner
    ) -> None:
        """A link to a page that says "nothing to confirm" is worse than no link."""
        job_id = await run_to_the_financials_gate(api, mapped_runner, committed["request"].id)

        console = await api.get(f"/runs/{job_id}")
        assert 'id="review-financials"' not in console.text

    async def test_another_operators_run_is_not_readable(
        self, api: Any, committed: dict, unmapped_runner: Runner, db_engine: Any
    ) -> None:
        """Handing the request to somebody else must close both surfaces, not just one."""
        job_id = await run_to_the_financials_gate(api, unmapped_runner, committed["request"].id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            stranger = User(
                email="stranger@example.invalid", display_name="Stranger", role=UserRole.ANALYST
            )
            session.add(stranger)
            await session.flush()
            order = await session.get(WorkOrder, committed["request"].id)
            assert order is not None
            order.user_id = stranger.id
            await session.commit()

        assert (await api.get(f"/api/runs/{job_id}/financials")).status_code == 404
        assert (await api.get(f"/runs/{job_id}/financials")).status_code == 404


class TestThePayloadItself:
    def test_it_is_required_on_tags_not_on_a_count(self) -> None:
        assert unmapped_gate_required({"unmapped_tags": ["us-gaap:Whatever"]}) is True
        assert unmapped_gate_required({"unmapped_tags": []}) is False
        assert unmapped_gate_required({}) is False

    def test_it_carries_only_what_the_operator_is_shown(self) -> None:
        """Anything in the payload but off the page would be hashed and never checked."""
        produced = {
            "unmapped_tags": ["us-gaap:Whatever"],
            "unmapped_concepts": [{"tag": "us-gaap:Whatever", "value": "12", "share": "0.5"}],
            "mapped_concepts": [{"concept": "revenue", "value": "24"}],
            "facts_written": 3,
            "exchange": "LSE",
            "load_errors": ["something arelle said"],
            "facts_rejected": 9,
        }
        payload = unmapped_gate_payload(produced)

        assert set(payload) == {
            "unmapped_tags",
            "unmapped_concepts",
            "refused_tags",
            "refused_concepts",
            "mapped_concepts",
            "facts_written",
            "exchange",
            "load_errors",
        }
        assert "facts_rejected" not in payload

    def test_a_run_recorded_before_the_figures_were_kept_still_renders(self) -> None:
        """The detail arrived on 2026-08-25 and older step outputs do not carry it.

        The page falls back to the tag list it always showed. An absent key rendering as a
        hole would make an old run's gate look broken rather than older.
        """
        payload = unmapped_gate_payload({"unmapped_tags": ["us-gaap:Whatever"]})

        assert payload["unmapped_concepts"] == []
        assert payload["mapped_concepts"] == []
        assert payload["unmapped_tags"] == ["us-gaap:Whatever"]
        # The refusals arrived on 2026-08-30 and older outputs do not carry them either.
        assert payload["refused_tags"] == []
        assert payload["refused_concepts"] == []


class TestARefusedTagAsksNothing:
    """Roadmap §2.7. A refusal is a decision already taken, so it is reported rather than
    put to the operator — and a run that stopped to ask about one would be asking a
    question this platform has already answered, repeatedly, until somebody stopped
    reading the list."""

    def test_a_refusal_alone_does_not_stop_a_run(self) -> None:
        produced = {
            "unmapped_tags": [],
            "refused_tags": ["us-gaap:ShareBasedCompensation…RiskFreeInterestRate"],
        }

        assert unmapped_gate_required(produced) is False

    def test_an_unplaced_tag_beside_a_refusal_still_stops_it(self) -> None:
        produced = {
            "unmapped_tags": ["us-gaap:Whatever"],
            "refused_tags": ["us-gaap:ShareBasedCompensation…RiskFreeInterestRate"],
        }

        assert unmapped_gate_required(produced) is True

    def test_the_reason_travels_with_the_refusal_into_the_payload(self) -> None:
        """What makes the row reviewable rather than another line of taxonomy noise."""
        row = {
            "tag": "us-gaap:ShareBasedCompensation…RiskFreeInterestRate",
            "refusal": "An option-pricing assumption from the footnote.",
        }
        payload = unmapped_gate_payload({"unmapped_tags": [], "refused_concepts": [row]})

        assert payload["refused_concepts"] == [row]
