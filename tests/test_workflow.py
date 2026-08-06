"""The vertical slice, end to end: request in, cited report out.

Every test here runs the *real* workflow — the real engine, the real point-in-time
selector, the real calculation kernel, the real renderer — against a fake provider and a
stubbed SEC client. Nothing is mocked between the steps, so what is asserted is what the
platform actually does.

Three properties get their own class because they are the ones that make the workflow
trustworthy rather than merely working: the budget guard stops a run **before** the
provider is called, a killed run resumes without repeating work, and the approval gates
enforce order and singularity.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents import planner
from aer.config import Settings
from aer.core.enums import ClaimKind, Decision, GateKind, JobStatus
from aer.db.models import (
    AgentRun,
    Approval,
    Calculation,
    Cost,
    FinancialFact,
    Job,
    JobStep,
    Report,
    ReportSection,
    ResearchPlan,
    SourceDocument,
)
from aer.errors import ValidationError
from aer.providers.fake import FakeProvider
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.services.citations import record_claim
from aer.storage.local import LocalArtefactStore
from tests.workflow_fixtures import (
    StubSecClient,
    seed_job,
    seed_request,
    seed_user,
)

pytestmark = pytest.mark.anyio


async def run_to_next_stop(
    session: AsyncSession,
    *,
    job: Job,
    settings: Settings,
    provider: FakeProvider,
    store: LocalArtefactStore,
    sec_client: StubSecClient,
    stop_after: str | None = None,
) -> run_service.RunOutcome:
    return await run_service.execute(
        session,
        job=job,
        settings=settings,
        provider=provider,
        store=store,
        sec_client=sec_client,
        stop_after=stop_after,
    )


async def approve(
    session: AsyncSession, *, job: Job, gate: GateKind, actor: object, step: str
) -> None:
    """Record an approval carrying the hash the run's own step produced."""
    row = await session.scalar(
        select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == step)
    )
    assert row is not None, f"the {step} step has not run"
    await approval_service.record_decision(
        session,
        job=job,
        gate=gate,
        decision=Decision.APPROVED,
        actor=actor,  # type: ignore[arg-type]
        payload_hash=str((row.output_ref or {})["payload_hash"]),
    )


@pytest.fixture
async def scenario(
    db_session: AsyncSession,
    workflow_settings: Settings,
    workflow_store: LocalArtefactStore,
    sec_client: StubSecClient,
    provider: FakeProvider,
) -> dict[str, object]:
    """A user, a request and a queued job, ready to run."""
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    return {
        "session": db_session,
        "user": user,
        "request": request,
        "job": job,
        "settings": workflow_settings,
        "store": workflow_store,
        "sec_client": sec_client,
        "provider": provider,
    }


class TestThePlannerAsksForWhatItValidates:
    """The prompt's length budgets and the schema's ceilings, checked against each other.

    A live run died here. The model wrote a 660-character ``focus`` against a 600-character
    ``max_length``, and because the API does not enforce ``max_length`` — the SDK moves it into
    the schema's description, where it is guidance — validation failed *after* the call had
    been paid for. A £0.05 planner call thrown away over forty words.

    The bounds are therefore two numbers per field: a ceiling that only catches a runaway
    blob, and a budget the prompt actually asks for, comfortably inside it. These tests are
    what keeps the gap real, because closing it is silent until it costs money.
    """

    _FIELDS = (
        ("summary", planner._SUMMARY_BUDGET, planner._SUMMARY_CEILING),
        ("focus", planner._FOCUS_BUDGET, planner._FOCUS_CEILING),
        ("what/why", planner._REASON_BUDGET, planner._REASON_CEILING),
    )

    @pytest.mark.parametrize(("field", "budget", "ceiling"), _FIELDS)
    def test_the_ceiling_leaves_real_headroom_over_the_budget(
        self, field: str, budget: int, ceiling: int
    ) -> None:
        """ "The model went thirty per cent over" must not be a failed run."""
        assert ceiling >= budget * 2, (
            f"{field} allows {ceiling} and asks for {budget}; a model that overruns its "
            "budget by half would kill the run after the call was paid for"
        )

    @pytest.mark.parametrize(("field", "budget", "ceiling"), _FIELDS)
    def test_the_prompt_states_the_budget(self, field: str, budget: int, ceiling: int) -> None:
        """The prompt is the only channel the model reliably reads a limit on.

        The schema's description carries ``{maxLength: 600}`` after the SDK's translation, and
        the run that failed proves that is not enough to rely on.
        """
        assert str(budget) in planner._SYSTEM_PROMPT

    def test_a_reply_at_the_stated_budget_validates(self) -> None:
        """The contract stated as one object: write what was asked for, and it is accepted."""
        draft = planner.ResearchPlanDraft(
            summary="s" * planner._SUMMARY_BUDGET,
            sections=[planner.PlannedSection(key="k", focus="f" * planner._FOCUS_BUDGET)],
            planned_sources=[
                planner.PlannedSource(
                    provider="sec_edgar",
                    tier="regulatory",
                    what="w" * planner._REASON_BUDGET,
                    why="y" * planner._REASON_BUDGET,
                )
            ],
        )
        assert len(draft.sections) == 1

    def test_the_bounds_that_carry_meaning_stay_strict(self) -> None:
        """Only the *presentational* bounds were loosened.

        ``confidence`` is a 0-to-1 judgement: a value outside it means the model misunderstood
        the field, and accepting it would put a number nobody can interpret in front of a
        reviewer. Failing there is right, and the distinction is the whole point — a length is
        a storage concern, a range is a meaning.
        """
        with pytest.raises(PydanticValidationError):
            planner.ResearchPlanDraft(
                summary="s",
                sections=[planner.PlannedSection(key="k", focus="f")],
                planned_sources=[
                    planner.PlannedSource(provider="p", tier="reg", what="w", why="y")
                ],
                confidence=1.7,
            )


class TestTheFirstLeg:
    """Plan, then stop for a human."""

    async def test_the_run_pauses_at_the_plan_gate(self, scenario: dict) -> None:
        outcome = await run_to_next_stop(**_args(scenario))

        assert outcome.status is JobStatus.AWAITING_APPROVAL
        assert outcome.is_waiting

    async def test_a_plan_was_stored(self, scenario: dict) -> None:
        await run_to_next_stop(**_args(scenario))
        session = scenario["session"]

        plan = await session.scalar(
            select(ResearchPlan).where(ResearchPlan.request_id == scenario["request"].id)
        )
        assert plan is not None
        assert plan.plan["summary"]
        assert plan.planned_sources
        assert plan.estimated_cost_gbp > 0

    async def test_the_sections_were_created_from_the_database(self, scenario: dict) -> None:
        """Two, because two rows are seeded. Not because two are named anywhere in code."""
        await run_to_next_stop(**_args(scenario))
        session = scenario["session"]

        rows = list(
            await session.scalars(
                select(ReportSection)
                .where(ReportSection.job_id == scenario["job"].id)
                .order_by(ReportSection.position)
            )
        )
        assert [row.section_key for row in rows] == [
            "executive_summary",
            "historical_financial_analysis",
        ]

    async def test_nothing_was_fetched_before_the_plan_was_approved(self, scenario: dict) -> None:
        """The gate is the point. A run that acquired first would have spent before asking."""
        await run_to_next_stop(**_args(scenario))
        assert scenario["sec_client"].facts_calls == []

    async def test_the_model_call_was_metered(self, scenario: dict) -> None:
        await run_to_next_stop(**_args(scenario))
        session = scenario["session"]

        spend = await session.scalar(
            select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(
                Cost.job_id == scenario["job"].id
            )
        )
        assert Decimal(str(spend)) > 0

    async def test_the_agent_run_records_the_model_and_archives_both_payloads(
        self, scenario: dict
    ) -> None:
        await run_to_next_stop(**_args(scenario))
        session = scenario["session"]

        agent_run = await session.scalar(select(AgentRun).where(AgentRun.agent_role == "planner"))
        assert agent_run is not None
        assert agent_run.provider == "fake"
        assert agent_run.request_payload_ref is not None
        assert agent_run.response_payload_ref is not None


class TestTheWholeRun:
    """Both gates approved: a frozen, cited report."""

    @pytest.fixture
    async def finished(self, scenario: dict) -> dict:
        session = scenario["session"]
        job = scenario["job"]

        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")

        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.FINAL, actor=scenario["user"], step="draft")

        outcome = await run_to_next_stop(**_args(scenario))
        return {**scenario, "outcome": outcome}

    async def test_it_succeeds(self, finished: dict) -> None:
        assert finished["outcome"].status is JobStatus.SUCCEEDED

    async def test_every_step_ran_exactly_once(self, finished: dict) -> None:
        session = finished["session"]
        rows = list(
            await session.scalars(
                select(JobStep)
                .where(JobStep.job_id == finished["job"].id)
                .order_by(JobStep.sequence)
            )
        )
        assert [row.step_key for row in rows] == [
            "plan",
            "gate_plan",
            "acquire",
            # Classification runs before anything is computed, because what kind of business
            # this is decides which valuation models may run.
            "classify",
            # Conditional, like the financials gate below: it passes straight through for a
            # company whose SIC code matches no specialist profile, which this one does not.
            "gate_sector_specialist",
            # Peers after classification, because what kind of business this is decides which
            # companies are comparable with it. Conditional too: this database holds no other
            # company sharing the subject's SIC group, so nothing is proposed and the gate
            # passes straight through.
            "propose_peers",
            "gate_peer_set",
            "extract",
            # A step even on the runs it does not apply to. It succeeds without stopping
            # when every tag mapped, which is what this fixture's filing does.
            "gate_uk_financials",
            # The task 37 wave: the calculation and the five research workers share their
            # dependency on the financials gate. Without a session factory in services —
            # this test's shape — the engine takes them one at a time, in declared order,
            # which is what makes this list deterministic.
            "calculate",
            "research_company",
            "research_industry",
            "research_macro",
            "research_recent_developments",
            "research_technical_context",
            "draft",
            "gate_final",
            "render",
        ]
        assert all(row.status is JobStatus.SUCCEEDED for row in rows)

    async def test_the_source_document_is_hashed_and_stored(self, finished: dict) -> None:
        session = finished["session"]
        document = await session.scalar(select(SourceDocument))
        assert document is not None
        await session.refresh(document, ["artefact"])
        assert document.artefact is not None
        assert len(document.artefact.sha256) == 64

    async def test_the_facts_are_point_in_time(self, finished: dict) -> None:
        """The fixture restates FY2020 revenue in a filing made after the as-of date.

        143,015,000,000 was filed in 2020 and is admissible; 142,000,000,000 restates the
        same period in a 2022 filing the as-of date excludes. Selecting the restatement
        would be look-ahead bias — the report would rest on a number nobody had at the time.
        """
        session = finished["session"]
        values = {
            fact.value
            for fact in await session.scalars(
                select(FinancialFact).where(FinancialFact.concept == "revenue")
            )
        }
        assert Decimal("143015000000") in values
        assert Decimal("142000000000") not in values

    async def test_the_figure_is_a_recorded_calculation(self, finished: dict) -> None:
        session = finished["session"]
        calculation = await session.scalar(select(Calculation))

        assert calculation is not None
        assert calculation.formula
        assert calculation.inputs
        assert calculation.code_version

    async def test_the_report_is_frozen_and_carries_its_hash(self, finished: dict) -> None:
        session = finished["session"]
        report = await session.scalar(select(Report).where(Report.job_id == finished["job"].id))

        assert report is not None
        assert report.immutable
        assert report.approved_by is not None
        assert len(report.content_hash) == 64

    async def test_the_archived_markdown_is_the_report(self, finished: dict) -> None:
        """Read back from the content-addressed store and compared, not re-rendered."""
        session = finished["session"]
        store: LocalArtefactStore = finished["store"]

        report = await session.scalar(select(Report).where(Report.job_id == finished["job"].id))
        assert report is not None
        assert report.markdown_artefact_id is not None

        from aer.db.models import Artefact  # noqa: PLC0415

        artefact = await session.get(Artefact, report.markdown_artefact_id)
        assert artefact is not None

        body = (await store.read(artefact.sha256)).decode("utf-8")
        assert body == report.content["markdown"]

    async def test_the_report_says_it_is_not_advice(self, finished: dict) -> None:
        session = finished["session"]
        report = await session.scalar(select(Report).where(Report.job_id == finished["job"].id))
        assert report is not None
        assert "not** regulated investment advice" in report.content["markdown"]

    async def test_it_spent_only_on_the_judgement_calls(self, finished: dict) -> None:
        """Six model calls in the whole run: the planner and the five research workers.

        The count is exact and the schemas are named, so a seventh call — a step quietly
        acquiring a model dependency — fails here rather than on a bill. Everything else
        in the slice is deterministic code.
        """
        schemas = [call["schema"] for call in finished["provider"].calls]
        assert schemas.count("ResearchPlanDraft") == 1
        assert schemas.count("WorkerTurn") == 5
        assert finished["provider"].call_count == 6


class TestTheBudgetGuard:
    """A cap that only warns is a cap that does not work."""

    @pytest.fixture
    async def penniless(
        self,
        db_session: AsyncSession,
        workflow_settings: Settings,
        workflow_store: LocalArtefactStore,
        sec_client: StubSecClient,
        provider: FakeProvider,
    ) -> dict:
        user = await seed_user(db_session)
        # Below the planner step's projected cost, so the guard fires on the first step.
        request = await seed_request(db_session, user=user, max_cost_gbp=Decimal("0.01"))
        job = await seed_job(db_session, request=request)
        return {
            "session": db_session,
            "user": user,
            "request": request,
            "job": job,
            "settings": workflow_settings,
            "store": workflow_store,
            "sec_client": sec_client,
            "provider": provider,
        }

    async def test_the_run_stops_in_budget_exceeded(self, penniless: dict) -> None:
        outcome = await run_to_next_stop(**_args(penniless))
        assert outcome.status is JobStatus.BUDGET_EXCEEDED

    async def test_the_provider_was_never_called(self, penniless: dict) -> None:
        """Checked before the step, not after. Afterwards tells you what you already spent."""
        await run_to_next_stop(**_args(penniless))
        assert penniless["provider"].call_count == 0

    async def test_nothing_was_spent(self, penniless: dict) -> None:
        await run_to_next_stop(**_args(penniless))
        session = penniless["session"]

        spend = await session.scalar(
            select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(
                Cost.job_id == penniless["job"].id
            )
        )
        assert Decimal(str(spend)) == 0

    async def test_the_step_records_why(self, penniless: dict) -> None:
        """A run that stopped for no stated reason is a run nobody can act on."""
        await run_to_next_stop(**_args(penniless))
        session = penniless["session"]

        row = await session.scalar(
            select(JobStep).where(JobStep.job_id == penniless["job"].id, JobStep.step_key == "plan")
        )
        assert row is not None
        assert row.error is not None
        assert row.error["code"] == "budget_exceeded"


class TestResumability:
    """A worker that dies mid-run must not repeat what it already did."""

    async def test_a_completed_step_is_not_re_executed(self, scenario: dict) -> None:
        session = scenario["session"]
        job = scenario["job"]

        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")

        # The worker gets as far as acquiring, then "dies".
        await run_to_next_stop(**_args(scenario), stop_after="acquire")
        assert scenario["sec_client"].facts_calls == ["0000789019"]

        # It restarts and resumes. The acquire step must not fetch a second time.
        await run_to_next_stop(**_args(scenario))
        assert scenario["sec_client"].facts_calls == ["0000789019"]

    async def test_the_resumed_run_reaches_the_second_gate(self, scenario: dict) -> None:
        session = scenario["session"]
        job = scenario["job"]

        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")
        await run_to_next_stop(**_args(scenario), stop_after="acquire")

        outcome = await run_to_next_stop(**_args(scenario))
        assert outcome.status is JobStatus.AWAITING_APPROVAL

        row = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == "gate_final")
        )
        assert row is not None
        assert row.status is JobStatus.AWAITING_APPROVAL

    async def test_the_planner_is_not_asked_twice(self, scenario: dict) -> None:
        """Resuming must not re-spend on a step that already produced an answer."""
        session = scenario["session"]
        job = scenario["job"]

        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")
        await run_to_next_stop(**_args(scenario))

        planner_calls = [
            call for call in scenario["provider"].calls if call["schema"] == "ResearchPlanDraft"
        ]
        assert len(planner_calls) == 1


class TestTheApprovalGates:
    async def test_an_approval_records_what_was_shown(self, scenario: dict) -> None:
        session = scenario["session"]
        job = scenario["job"]
        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")

        approval = await session.scalar(select(Approval).where(Approval.job_id == job.id))
        assert approval is not None
        assert len(approval.payload_hash) == 64

    async def test_approving_twice_is_refused(self, scenario: dict) -> None:
        session = scenario["session"]
        job = scenario["job"]
        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")

        with pytest.raises(ValidationError, match="already approved"):
            await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")

    async def test_the_second_gate_cannot_be_decided_before_the_first(self, scenario: dict) -> None:
        session = scenario["session"]
        job = scenario["job"]
        await run_to_next_stop(**_args(scenario))

        with pytest.raises(ValidationError, match="cannot be decided"):
            await approval_service.record_decision(
                session,
                job=job,
                gate=GateKind.FINAL,
                decision=Decision.APPROVED,
                actor=scenario["user"],
                payload_hash="f" * 64,
            )

    async def test_an_approval_without_a_hash_is_refused(self, scenario: dict) -> None:
        """Otherwise an approval says only that somebody clicked something."""
        session = scenario["session"]
        await run_to_next_stop(**_args(scenario))

        with pytest.raises(ValidationError, match="hash of what was displayed"):
            await approval_service.record_decision(
                session,
                job=scenario["job"],
                gate=GateKind.PLAN,
                decision=Decision.APPROVED,
                actor=scenario["user"],
                payload_hash="",
            )

    async def test_an_approval_of_different_content_does_not_open_the_gate(
        self, scenario: dict
    ) -> None:
        """The hash is the whole mechanism: an approval of something else is not one of this."""
        session = scenario["session"]
        job = scenario["job"]
        await run_to_next_stop(**_args(scenario))

        await approval_service.record_decision(
            session,
            job=job,
            gate=GateKind.PLAN,
            decision=Decision.APPROVED,
            actor=scenario["user"],
            payload_hash="a" * 64,
        )

        outcome = await run_to_next_stop(**_args(scenario))
        assert outcome.status is JobStatus.AWAITING_APPROVAL
        assert scenario["sec_client"].facts_calls == []

    async def test_a_rejection_stops_the_run(self, scenario: dict) -> None:
        session = scenario["session"]
        job = scenario["job"]
        await run_to_next_stop(**_args(scenario))

        row = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == "plan")
        )
        assert row is not None
        await approval_service.record_decision(
            session,
            job=job,
            gate=GateKind.PLAN,
            decision=Decision.REJECTED,
            actor=scenario["user"],
            payload_hash=str((row.output_ref or {})["payload_hash"]),
        )

        outcome = await run_to_next_stop(**_args(scenario))
        assert outcome.status is JobStatus.AWAITING_APPROVAL
        assert scenario["sec_client"].facts_calls == []


def _args(scenario: dict) -> dict:
    """The keyword arguments :func:`run_to_next_stop` takes, from a scenario."""
    return {
        "session": scenario["session"],
        "job": scenario["job"],
        "settings": scenario["settings"],
        "provider": scenario["provider"],
        "store": scenario["store"],
        "sec_client": scenario["sec_client"],
    }


class TestGateTwoWillNotOpenOnUnsupportedEvidence:
    """The rule §2.9 states, enforced where it bites: before a person is asked to approve.

    The run below is the real workflow, driven to the second gate, with one unsupported claim
    planted in the drafted section. What is under test is that the gate stops *on the evidence*
    rather than on the approval — an operator shown a draft to approve while the platform still
    has unverified citations in it would be approving something the platform cannot stand
    behind, without being told so.
    """

    @staticmethod
    async def _reach_the_draft(scenario: dict) -> None:
        session, job = scenario["session"], scenario["job"]
        await run_to_next_stop(**_args(scenario))
        await approve(session, job=job, gate=GateKind.PLAN, actor=scenario["user"], step="plan")
        await run_to_next_stop(**_args(scenario))

    async def test_a_run_with_no_claims_reaches_the_gate_as_before(self, scenario: dict) -> None:
        """The Phase 1 workflow makes no claims yet. The check must not invent a failure for a
        draft that asserts nothing — which is what an over-eager "every section needs a
        citation" rule would do."""
        await self._reach_the_draft(scenario)

        outcome = await run_to_next_stop(**_args(scenario))

        assert outcome.status is JobStatus.AWAITING_APPROVAL

    async def test_an_unsupported_claim_stops_the_run_before_the_approval(
        self, scenario: dict
    ) -> None:
        await self._reach_the_draft(scenario)
        session = scenario["session"]
        section = await session.scalar(
            select(ReportSection).where(ReportSection.job_id == scenario["job"].id)
        )
        assert section is not None
        await record_claim(
            session,
            section=section,
            kind=ClaimKind.FACTUAL,
            text="Revenue grew for the third consecutive year.",
        )

        outcome = await run_to_next_stop(**_args(scenario))
        step = await session.scalar(
            select(JobStep).where(
                JobStep.job_id == scenario["job"].id, JobStep.step_key == "gate_final"
            )
        )

        assert outcome.status is JobStatus.AWAITING_APPROVAL
        assert step is not None
        assert "no admissible citation" in str((step.error or {}).get("message", ""))

    async def test_the_report_is_not_rendered_while_a_claim_is_unsupported(
        self, scenario: dict
    ) -> None:
        """The consequence that matters. A gate that paused but let the render step through
        would publish the unsupported sentence anyway."""
        await self._reach_the_draft(scenario)
        session = scenario["session"]
        section = await session.scalar(
            select(ReportSection).where(ReportSection.job_id == scenario["job"].id)
        )
        assert section is not None
        await record_claim(
            session,
            section=section,
            kind=ClaimKind.FACTUAL,
            text="Revenue grew for the third consecutive year.",
        )

        await approve(
            session, job=scenario["job"], gate=GateKind.FINAL, actor=scenario["user"], step="draft"
        )
        await run_to_next_stop(**_args(scenario))

        assert await session.scalar(select(func.count()).select_from(Report)) == 0
