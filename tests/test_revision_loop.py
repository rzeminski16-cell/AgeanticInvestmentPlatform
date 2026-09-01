"""The critique-and-revise loop (ADR 0091): routing, bounds, the plan critic, the memory.

Four layers. The **routing** is pure: material challenges find their sections through the
attribution the red team recorded, ordered worst first, and a challenge with no
attribution provokes nothing. The **revise pass** is tested against the database with the
writer stubbed, because what this layer owns is the bounds and the record — claims
replaced, custom sections stood aside from, every decision a note. The **plan critic**
runs through the real workflow against a scripted critic that objects, so the revision,
the gate-1 hash and the stored critique are all the production path's. The **memory** is
a query, and the test is that recurrence means "across runs", never "often in one".
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.custom_section import CustomSectionDraft, ProposedClaim
from aer.agents.plan_critic import PlanChallenge, PlanChallengeAspect, PlanCritique
from aer.core.disagreement import DisagreementKind, ResolutionOutcome, ResolutionRule, ResolvedBy
from aer.core.enums import ClaimKind, Decision, GateKind, JobStatus
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Claim,
    Disagreement,
    JobStep,
    ReportSection,
    ResearchPlan,
    RevisionNote,
    SectionDefinition,
    SectionStatus,
)
from aer.db.models.revision_note import SCOPE_DRAFT, SCOPE_PLAN
from aer.db.models.skill import Skill
from aer.providers.fake import FakeProvider
from aer.sections.evidence import Evidence, SectionExecution, record_draft_claims
from aer.services import revision as revision_service
from aer.services import runs as run_service
from aer.services.approvals import record_decision
from aer.services.lessons import recurring_lessons
from aer.services.revision import revise_challenged_sections, revisions_for_job
from aer.workflow.workflows.vertical_slice_v1 import plan_gate_payload
from tests.schema_guard import refuse_unanswerable_schema
from tests.workflow_fixtures import (
    ScriptedSectionBrain,
    declared_schema_name,
    seed_job,
    seed_request,
    seed_user,
)

pytestmark = pytest.mark.anyio


def _challenge_row(
    job_id: uuid.UUID,
    *,
    severity: int,
    dimension: str = "growth",
    sections: list[str] | None = None,
    statement: str = "The growth claim contradicts the recorded fact.",
) -> Disagreement:
    """One escalated red-team challenge, as the service records it."""
    return Disagreement(
        job_id=job_id,
        topic=f"Red team ({dimension}): {statement[:120]}",
        kind=DisagreementKind.THESIS_CONFLICT,
        position_a={"label": "Base thesis"},
        position_b={"label": f"Red team challenge ({dimension}, severity {severity}/5)"},
        resolution=ResolutionOutcome.ESCALATED,
        rule=ResolutionRule.THESIS_CONFLICT,
        resolved_by=ResolvedBy.RULE,
        resolution_rationale="Escalated to the final gate.",
        detail={
            "challenge": statement,
            "basis": "It contradicts the fact it rests on.",
            "severity": severity,
            "dimension": dimension,
            "claims": [],
            "sections": sections if sections is not None else [],
            "evidence": {"facts": [], "calculations": [], "sources": []},
        },
        escalated_to_gate=GateKind.FINAL,
        material=severity >= 4,
        fingerprint=sha256_hex(f"{dimension}:{severity}:{statement}"),
    )


class TestRoutingChallengesToSections:
    def test_material_challenges_group_by_section_worst_first(self) -> None:
        job_id = uuid.uuid4()
        rows = [
            _challenge_row(job_id, severity=4, dimension="growth", sections=["b"]),
            _challenge_row(
                job_id,
                severity=5,
                dimension="valuation",
                sections=["a"],
                statement="The terminal value has no basis.",
            ),
            # A quibble: recorded, shown, never a redraft.
            _challenge_row(
                job_id,
                severity=2,
                dimension="macro",
                sections=["a"],
                statement="Macro context is thin.",
            ),
        ]

        targets, material = revision_service._targets_of(rows)

        assert material == 2
        assert [target.section_key for target in targets] == ["a", "b"]
        assert targets[0].severity == 5
        assert targets[0].statements == ("The terminal value has no basis.",)

    def test_a_material_challenge_with_no_attribution_provokes_nothing(self) -> None:
        # Every challenge recorded before ADR 0091 lands here too: counted, not acted on.
        rows = [_challenge_row(uuid.uuid4(), severity=5, sections=[])]

        targets, material = revision_service._targets_of(rows)

        assert material == 1
        assert targets == []

    def test_a_non_red_team_disagreement_is_ignored(self) -> None:
        row = _challenge_row(uuid.uuid4(), severity=5, sections=["a"])
        row.detail = {"note": "an ordinary source conflict has no dimension"}

        targets, material = revision_service._targets_of([row])

        assert material == 0
        assert targets == []


@pytest.fixture
async def drafted(db_session: AsyncSession) -> dict[str, Any]:
    """A job with two generated built-in sections, one custom one, and a claim to replace."""
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)

    definitions = list(
        await db_session.scalars(
            select(SectionDefinition).where(SectionDefinition.origin == "builtin").limit(2)
        )
    )
    assert len(definitions) == 2, "the seeded spine is missing"

    skill = Skill(key="custom_probe", kind="custom_section", enabled=True)
    db_session.add(skill)
    await db_session.flush()
    custom = SectionDefinition(
        key="custom_probe",
        version=1,
        origin="skill",
        skill_id=skill.id,
        title="Custom Probe",
        position=900,
        required=False,
        output_contract={"type": "object", "properties": {"commentary": {"type": "string"}}},
        evidence_policy={},
        token_budget=100,
        allowed_tools=[],
        applicability={},
    )
    db_session.add(custom)
    await db_session.flush()

    sections: dict[str, ReportSection] = {}
    for definition in [*definitions, custom]:
        row = ReportSection(
            job_id=job.id,
            section_definition_id=definition.id,
            section_key=definition.key,
            position=definition.position,
            status=SectionStatus.GENERATED,
            content={"body": "drafted"},
        )
        db_session.add(row)
        sections[definition.key] = row
    await db_session.flush()

    claim = Claim(
        report_section_id=sections[definitions[0].key].id,
        kind=ClaimKind.FACTUAL,
        text="Revenue is growing and the trajectory is durable.",
    )
    db_session.add(claim)
    await db_session.flush()

    return {
        "session": db_session,
        "user": user,
        "request": request,
        "job": job,
        "builtin_keys": [definition.key for definition in definitions],
        "custom_key": custom.key,
        "sections": sections,
        "claim": claim,
    }


@pytest.fixture
def stubbed_writer(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The section writer replaced with a recorder: the revise pass owns everything else."""
    calls: list[dict[str, Any]] = []

    async def fake_execute(
        context: AgentContext,
        *,
        section: ReportSection,
        request: Any,
        focus: str = "",
        challenges: Any = (),
    ) -> SectionExecution:
        calls.append(
            {"section_key": section.section_key, "focus": focus, "challenges": list(challenges)}
        )
        return SectionExecution(section=section, status=SectionStatus.GENERATED, attempts=1)

    monkeypatch.setattr(revision_service, "execute_builtin_section", fake_execute)
    return calls


@pytest.fixture
def refusing_writer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A writer whose revision does not stand up, mutating the row the way the real one
    does: `_failed` clears the content and the confidence before returning."""
    refused: list[str] = []

    async def fake_execute(
        context: AgentContext,
        *,
        section: ReportSection,
        request: Any,
        focus: str = "",
        challenges: Any = (),
    ) -> SectionExecution:
        refused.append(section.section_key)
        section.content = None
        section.status = SectionStatus.FAILED
        section.confidence = None
        section.low_confidence_reason = "the revision did not pass"
        return SectionExecution(
            section=section,
            status=SectionStatus.FAILED,
            attempts=2,
            problems=["the revision did not pass"],
        )

    monkeypatch.setattr(revision_service, "execute_builtin_section", fake_execute)
    return refused


def _agent_context(scene: dict[str, Any]) -> AgentContext:
    # The stubbed writer reads nothing from it; the service itself only passes it on.
    return cast("AgentContext", SimpleNamespace(session=scene["session"]))


class TestTheRevisePass:
    async def test_a_material_challenge_buys_one_redraft_with_the_challenge_in_front(
        self, drafted: dict[str, Any], stubbed_writer: list[dict[str, Any]]
    ) -> None:
        session = drafted["session"]
        key = drafted["builtin_keys"][0]
        session.add(_challenge_row(drafted["job"].id, severity=5, sections=[key]))
        await session.flush()

        outcome = await revise_challenged_sections(
            _agent_context(drafted),
            session,
            job=drafted["job"],
            request=drafted["request"],
            focus_by_key={key: "the approved focus"},
        )

        assert [call["section_key"] for call in stubbed_writer] == [key]
        assert stubbed_writer[0]["focus"] == "the approved focus"
        assert stubbed_writer[0]["challenges"] == [
            "The growth claim contradicts the recorded fact."
        ]
        assert outcome.revised[0]["section_key"] == key
        assert outcome.revised[0]["kept_approved_draft"] is False

        # The service itself deletes nothing (ADR 0098). The replacement belongs to
        # `record_draft_claims`, which the stub stands in for and does not perform, so
        # the drafted claim is still here — and would be whatever the attempt did.
        remaining = list(
            await session.scalars(
                select(Claim).where(Claim.report_section_id == drafted["sections"][key].id)
            )
        )
        assert [row.text for row in remaining] == [drafted["claim"].text]

        notes = list(
            await session.scalars(
                select(RevisionNote).where(RevisionNote.job_id == drafted["job"].id)
            )
        )
        assert len(notes) == 1
        assert notes[0].scope == SCOPE_DRAFT
        assert notes[0].section_key == key
        assert notes[0].dimension == "growth"
        assert notes[0].disposition == "revised"

    async def test_a_quibble_buys_nothing(
        self, drafted: dict[str, Any], stubbed_writer: list[dict[str, Any]]
    ) -> None:
        session = drafted["session"]
        key = drafted["builtin_keys"][0]
        session.add(_challenge_row(drafted["job"].id, severity=3, sections=[key]))
        await session.flush()

        outcome = await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        assert stubbed_writer == []
        assert outcome.revised == []
        assert outcome.challenges_material == 0

    async def test_a_custom_section_is_stood_aside_from_and_the_note_says_so(
        self, drafted: dict[str, Any], stubbed_writer: list[dict[str, Any]]
    ) -> None:
        session = drafted["session"]
        session.add(_challenge_row(drafted["job"].id, severity=5, sections=[drafted["custom_key"]]))
        await session.flush()

        outcome = await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        assert stubbed_writer == []
        assert outcome.skipped_custom == [drafted["custom_key"]]
        note = await session.scalar(
            select(RevisionNote).where(RevisionNote.job_id == drafted["job"].id)
        )
        assert note is not None
        assert note.disposition == "skipped_custom"

    async def test_the_bound_holds_and_the_overflow_is_named(
        self, drafted: dict[str, Any], stubbed_writer: list[dict[str, Any]]
    ) -> None:
        session = drafted["session"]
        # Five distinct targeted sections; the bound is four. The two real sections plus
        # three more seeded rows keep every target resolvable.
        keys = list(drafted["builtin_keys"])
        definition_id = drafted["sections"][keys[0]].section_definition_id
        for index in range(3):
            key = f"probe_{index}"
            session.add(
                ReportSection(
                    job_id=drafted["job"].id,
                    section_definition_id=definition_id,
                    section_key=key,
                    position=500 + index,
                    status=SectionStatus.GENERATED,
                    content={"body": "drafted"},
                )
            )
            keys.append(key)
        await session.flush()
        for index, key in enumerate(keys):
            session.add(
                _challenge_row(
                    drafted["job"].id,
                    severity=4,
                    dimension="growth",
                    sections=[key],
                    statement=f"Challenge {index} against {key}.",
                )
            )
        await session.flush()

        outcome = await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        assert len(outcome.revised) == revision_service.MAX_REVISED_SECTIONS
        assert len(outcome.over_bound) == 1
        assert len(stubbed_writer) == revision_service.MAX_REVISED_SECTIONS

    async def test_the_gate_payload_carries_the_record(
        self, drafted: dict[str, Any], stubbed_writer: list[dict[str, Any]]
    ) -> None:
        session = drafted["session"]
        key = drafted["builtin_keys"][0]
        session.add(_challenge_row(drafted["job"].id, severity=4, sections=[key]))
        await session.flush()

        await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        recorded = await revisions_for_job(session, drafted["job"].id)
        assert recorded == [
            {"section_key": key, "dimension": "growth", "severity": 4, "disposition": "revised"}
        ]


class TestARefusedRevisionKeepsTheApprovedDraft:
    """ADR 0098. The loop that exists to improve a draft was the only way to lose one.

    Two sections of the MSFT run of 2026-08-31 drafted, validated and recorded their
    claims — 24 and 21 of them — and are four-byte nulls in the finished run, because the
    revision each was given was refused and the pass had already deleted their claims and
    redrafted over their content.
    """

    async def test_the_section_is_exactly_as_the_draft_step_left_it(
        self, drafted: dict[str, Any], refusing_writer: list[str]
    ) -> None:
        session = drafted["session"]
        key = drafted["builtin_keys"][0]
        section = drafted["sections"][key]
        section.confidence = 0.5
        section.low_confidence_reason = None
        session.add(_challenge_row(drafted["job"].id, severity=5, sections=[key]))
        await session.flush()

        outcome = await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        assert refusing_writer == [key], "the attempt is still made and still billed"
        assert section.status is SectionStatus.GENERATED
        assert section.content == {"body": "drafted"}
        assert section.confidence == 0.5
        assert section.low_confidence_reason is None
        assert outcome.revised[0]["kept_approved_draft"] is True

    async def test_its_claims_are_untouched(
        self, drafted: dict[str, Any], refusing_writer: list[str]
    ) -> None:
        session = drafted["session"]
        key = drafted["builtin_keys"][0]
        session.add(_challenge_row(drafted["job"].id, severity=5, sections=[key]))
        await session.flush()

        await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        remaining = list(
            await session.scalars(
                select(Claim).where(Claim.report_section_id == drafted["sections"][key].id)
            )
        )
        assert [row.text for row in remaining] == [drafted["claim"].text]

    async def test_the_record_says_the_attempt_happened_and_was_refused(
        self, drafted: dict[str, Any], refusing_writer: list[str]
    ) -> None:
        """Inside the gate-2 hash, so the operator approves knowing the challenge was
        answered by an attempt that did not stand up rather than silently."""
        session = drafted["session"]
        key = drafted["builtin_keys"][0]
        session.add(_challenge_row(drafted["job"].id, severity=4, sections=[key]))
        await session.flush()

        await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        assert await revisions_for_job(session, drafted["job"].id) == [
            {
                "section_key": key,
                "dimension": "growth",
                "severity": 4,
                "disposition": "revision_refused",
            }
        ]

    async def test_the_replacement_happens_when_the_new_claims_are_recorded(
        self, drafted: dict[str, Any]
    ) -> None:
        """The other half of the mechanism. Nothing deletes a section's claims
        speculatively; `record_draft_claims` replaces them at the moment there is
        something to replace them with."""
        session = drafted["session"]
        section = drafted["sections"][drafted["builtin_keys"][0]]
        replacement = CustomSectionDraft(
            content={"body": "revised"},
            claims=[ProposedClaim(statement="The trajectory is now less certain.", kind="opinion")],
        )

        recorded, _ = await record_draft_claims(
            session, section=section, draft=replacement, evidence=Evidence()
        )

        assert recorded == 1
        remaining = list(
            await session.scalars(select(Claim).where(Claim.report_section_id == section.id))
        )
        assert [row.text for row in remaining] == ["The trajectory is now less certain."]

    async def test_a_section_that_already_failed_has_nothing_to_keep(
        self, drafted: dict[str, Any], refusing_writer: list[str]
    ) -> None:
        """Restoring here would put a failure back and call it a kept draft. The refusal
        is the same refusal, and the record says `revised` — nothing was preserved."""
        session = drafted["session"]
        key = drafted["builtin_keys"][0]
        section = drafted["sections"][key]
        section.status = SectionStatus.FAILED
        section.content = None
        session.add(_challenge_row(drafted["job"].id, severity=5, sections=[key]))
        await session.flush()

        outcome = await revise_challenged_sections(
            _agent_context(drafted), session, job=drafted["job"], request=drafted["request"]
        )

        assert section.status is SectionStatus.FAILED
        assert outcome.revised[0]["kept_approved_draft"] is False
        assert outcome.revised[0]["status"] == "failed"


# ==========================================================================================
# The plan critic, through the real workflow
# ==========================================================================================


def _objecting_critic_provider() -> FakeProvider:
    """The scripted brain, with a critic that objects instead of waving through."""
    base = ScriptedSectionBrain()

    def brain(schema: type[Any]) -> Any:
        if declared_schema_name(schema) == "PlanCritique":
            return PlanCritique(
                challenges=[
                    PlanChallenge(
                        aspect=PlanChallengeAspect.COVERAGE,
                        severity=4,
                        statement=(
                            "The plan never answers the operator's focus question about "
                            "segment concentration."
                        ),
                        suggestion="Point a section's focus at segment concentration.",
                    ),
                    PlanChallenge(
                        aspect=PlanChallengeAspect.RISKS,
                        severity=2,
                        statement="The risk list does not name the restatement risk.",
                        suggestion="Name it.",
                    ),
                ],
                coverage_note="The plan misses a focus question.",
            )
        return base(schema)

    provider = FakeProvider(brain, inspect_schema=refuse_unanswerable_schema)
    base.provider = provider
    return provider


@pytest.fixture
async def critiqued(
    db_session: AsyncSession, workflow_settings: Any, workflow_store: Any, sec_client: Any
) -> dict[str, Any]:
    """A run driven to gate 1 with a critic that objects at severity 4."""
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    provider = _objecting_critic_provider()

    outcome = await run_service.execute(
        db_session,
        job=job,
        settings=workflow_settings,
        provider=provider,
        store=workflow_store,
        sec_client=sec_client,
    )
    return {
        "session": db_session,
        "user": user,
        "request": request,
        "job": job,
        "provider": provider,
        "outcome": outcome,
        "settings": workflow_settings,
        "store": workflow_store,
        "sec_client": sec_client,
    }


class TestThePlanCritique:
    async def test_the_challenge_buys_one_planner_revision(self, critiqued: dict[str, Any]) -> None:
        schemas = [call["schema"] for call in critiqued["provider"].calls]
        assert schemas.count("PlanCritique") == 1
        assert schemas.count("ResearchPlanDraft") == 2

        step = await critiqued["session"].scalar(
            select(JobStep).where(
                JobStep.job_id == critiqued["job"].id, JobStep.step_key == "critique_plan"
            )
        )
        assert step is not None
        produced = step.output_ref or {}
        assert produced["consulted"] is True
        assert produced["revised"] is True
        assert produced["challenges"] == 2
        assert produced["actionable"] == 1

    async def test_the_critique_is_inside_the_gate_hash_and_the_gate_clears_on_it(
        self, critiqued: dict[str, Any]
    ) -> None:
        session = critiqued["session"]
        job = critiqued["job"]
        plan = await session.get(ResearchPlan, job.plan_id)
        assert plan is not None

        critique = (plan.plan or {}).get("critique", {})
        assert critique["revised"] is True
        assert [item["aspect"] for item in critique["challenges"]] == ["coverage", "risks"]

        # The sealed hash covers the payload with the critique in it, and it is the hash
        # the gate accepts: approving on it moves the run past gate 1.
        step = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == "critique_plan")
        )
        assert step is not None
        sealed = str((step.output_ref or {})["payload_hash"])
        from aer.skills.resolution import pinned_skills_for_work_order  # noqa: PLC0415

        pins = await pinned_skills_for_work_order(session, work_order_id=plan.request_id)
        assert sealed == sha256_hex(canonical_json(plan_gate_payload(plan, pins)))

        await record_decision(
            session,
            job=job,
            gate=GateKind.PLAN,
            decision=Decision.APPROVED,
            actor=critiqued["user"],
            payload_hash=sealed,
        )
        outcome = await run_service.execute(
            session,
            job=job,
            settings=critiqued["settings"],
            provider=critiqued["provider"],
            store=critiqued["store"],
            sec_client=critiqued["sec_client"],
            stop_after="acquire",
        )
        acquired = await session.scalar(
            select(JobStep).where(
                JobStep.job_id == job.id,
                JobStep.step_key == "acquire",
                JobStep.status == JobStatus.SUCCEEDED,
            )
        )
        assert acquired is not None, outcome.status

    async def test_each_challenge_lands_as_a_note_with_its_disposition(
        self, critiqued: dict[str, Any]
    ) -> None:
        notes = {
            note.dimension: note
            for note in await critiqued["session"].scalars(
                select(RevisionNote).where(RevisionNote.job_id == critiqued["job"].id)
            )
        }
        assert notes["coverage"].scope == SCOPE_PLAN
        assert notes["coverage"].disposition == "revised"
        # Below the revision threshold: recorded, shown, not acted on.
        assert notes["risks"].disposition == "stood"


# ==========================================================================================
# The memory: recurrence counted across runs, and only counted
# ==========================================================================================


class TestLessons:
    async def test_recurrence_means_across_runs(self, db_session: AsyncSession) -> None:
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        first = await seed_job(db_session, request=request)
        second = await seed_job(db_session, request=request)

        # One class in both runs; another class three times inside a single run.
        for job, statement in ((first, "Growth claim contradicted a fact."), (second, "Again.")):
            db_session.add(
                RevisionNote(
                    job_id=job.id,
                    scope=SCOPE_DRAFT,
                    section_key="growth_outlook",
                    dimension="growth",
                    severity=4,
                    statement=statement,
                    disposition="revised",
                )
            )
        for index in range(3):
            db_session.add(
                RevisionNote(
                    job_id=first.id,
                    scope=SCOPE_PLAN,
                    dimension="sources",
                    severity=3,
                    statement=f"Sources challenge {index}.",
                    disposition="revised",
                )
            )
        await db_session.flush()

        recurring = await recurring_lessons(db_session)
        assert [(item.scope, item.dimension, item.jobs) for item in recurring] == [
            ("draft", "growth", 2)
        ]
        assert recurring[0].recurring

        everything = await recurring_lessons(db_session, minimum_jobs=1)
        assert {(item.scope, item.dimension) for item in everything} == {
            ("draft", "growth"),
            ("plan", "sources"),
        }
        one_run = next(item for item in everything if item.dimension == "sources")
        assert not one_run.recurring
