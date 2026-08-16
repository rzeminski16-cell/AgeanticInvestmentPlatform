"""The eighteen-section spine, and the two sections the platform fills itself.

Migration 0023 is the deliverable under test: sixteen seeded rows arriving with no code
change beyond what fills them. The deterministic pair get the closest attention, because
they are the one place a section key may legitimately meet code — and the failure modes
worth pinning are quiet ones: a validation record that flatters the run, a zero-budget row
nobody registered a builder for rendering as an inexplicable blank, a fill that lands
after the payload was sealed.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.concepts import is_canonical_concept
from aer.core.disagreement import DisagreementKind, ResolutionOutcome, ResolutionRule, ResolvedBy
from aer.core.enums import Decision, GateKind
from aer.db.models import (
    Disagreement,
    Evaluation,
    JobStep,
    ReportSection,
    ResearchPlan,
    SectionDefinition,
    SectionStatus,
)
from aer.sections import deterministic as deterministic_sections
from aer.sections.deterministic import SectionStage, fill_deterministic_sections
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.skills.resolution import pinned_skills_for_plan
from aer.workflow.workflows.vertical_slice_v1 import final_gate_payload, plan_gate_payload
from tests.workflow_fixtures import SPINE_KEYS, seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio

DETERMINISTIC_KEYS = ("prior_research_comparison", "validation_disagreements")


@pytest.fixture
async def scene(
    db_session: AsyncSession,
    workflow_settings: Any,
    workflow_store: Any,
    sec_client: Any,
    provider: Any,
) -> dict[str, Any]:
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


async def _execute(scene: dict[str, Any]) -> None:
    await run_service.execute(
        scene["session"],
        job=scene["job"],
        settings=scene["settings"],
        provider=scene["provider"],
        store=scene["store"],
        sec_client=scene["sec_client"],
    )


async def _approve(scene: dict[str, Any], *, gate: GateKind, step: str) -> None:
    row = await scene["session"].scalar(
        select(JobStep).where(JobStep.job_id == scene["job"].id, JobStep.step_key == step)
    )
    assert row is not None
    await approval_service.record_decision(
        scene["session"],
        job=scene["job"],
        gate=gate,
        decision=Decision.APPROVED,
        actor=scene["user"],
        payload_hash=str((row.output_ref or {})["payload_hash"]),
    )


async def _to_second_gate(scene: dict[str, Any]) -> None:
    """Run to the gate-1 pause, approve, and run to the gate-2 pause."""
    await _execute(scene)
    await _approve(scene, gate=GateKind.PLAN, step="plan")
    await _execute(scene)


async def _section(scene: dict[str, Any], key: str) -> ReportSection:
    row = await scene["session"].scalar(
        select(ReportSection).where(
            ReportSection.job_id == scene["job"].id, ReportSection.section_key == key
        )
    )
    assert row is not None, f"no report_sections row for {key!r}"
    return row


class TestTheSeed:
    """What migration 0023 put in the database."""

    async def test_the_latest_builtin_definitions_are_the_spine_in_order(
        self, db_session: AsyncSession
    ) -> None:
        rows = list(
            await db_session.scalars(
                select(SectionDefinition)
                .where(SectionDefinition.origin == "builtin")
                .order_by(SectionDefinition.position, SectionDefinition.key)
            )
        )
        latest: dict[str, SectionDefinition] = {}
        for row in sorted(rows, key=lambda r: r.version):
            latest[row.key] = row
        ordered = sorted(latest.values(), key=lambda r: (r.position, r.key))

        assert [row.key for row in ordered] == list(SPINE_KEYS)
        assert len(ordered) == 18

    async def test_every_spine_section_is_required(self, db_session: AsyncSession) -> None:
        rows = await db_session.scalars(
            select(SectionDefinition).where(SectionDefinition.key.in_(SPINE_KEYS))
        )
        assert all(row.required for row in rows)

    async def test_the_deterministic_pair_declare_a_zero_budget_and_no_source_floor(
        self, db_session: AsyncSession
    ) -> None:
        rows = list(
            await db_session.scalars(
                select(SectionDefinition).where(SectionDefinition.key.in_(DETERMINISTIC_KEYS))
            )
        )
        assert len(rows) == 2
        for row in rows:
            assert row.token_budget == 0
            assert row.evidence_policy["min_sources"] == 0
            assert row.evidence_policy["requires_primary"] is False

    async def test_every_model_written_contract_can_carry_a_citation(
        self, db_session: AsyncSession
    ) -> None:
        """Each contract holds at least one array-of-objects field naming the citation keys.

        Those keys are how content cites, how the renderer footnotes, and how a section
        meets its evidence floor — a contract without one would make its section
        structurally unable to support a figure.
        """
        rows = await db_session.scalars(
            select(SectionDefinition)
            .where(
                SectionDefinition.key.in_([k for k in SPINE_KEYS if k not in DETERMINISTIC_KEYS])
            )
            .order_by(SectionDefinition.version)
        )
        # Highest version per key: a superseded contract's shortcomings are history, not
        # a defect — executive_summary v1 is exactly why v2 exists.
        latest = {row.key: row for row in rows}
        for row in latest.values():
            carriers = [
                subschema
                for subschema in row.output_contract["properties"].values()
                if subschema.get("type") == "array"
                and isinstance(subschema.get("items"), dict)
                and subschema["items"].get("type") == "object"
                and (
                    "calculation_id" in subschema["items"].get("properties", {})
                    or "source_document_id" in subschema["items"].get("properties", {})
                )
            ]
            assert carriers, f"{row.key} has no citation-carrying field"

    async def test_every_model_written_definition_declares_its_evidence_preferences(
        self, db_session: AsyncSession
    ) -> None:
        """Migration 0029's deliverable: the ranking preferences are rows, not code.

        Every model-written definition must carry a non-empty ``concept_priority`` and
        ``excerpt_keywords`` in its evidence policy, and every priority entry must be a
        canonical concept — a typo here matches no fact's concept and so would silently
        rank nothing, which is the starvation of gap A39 back under another name.
        """
        rows = await db_session.scalars(
            select(SectionDefinition)
            .where(
                SectionDefinition.key.in_([k for k in SPINE_KEYS if k not in DETERMINISTIC_KEYS])
            )
            .order_by(SectionDefinition.version)
        )
        latest = {row.key: row for row in rows}
        assert len(latest) == 16
        for row in latest.values():
            policy = row.evidence_policy or {}
            assert policy.get("concept_priority"), f"{row.key} declares no concept_priority"
            assert policy.get("excerpt_keywords"), f"{row.key} declares no excerpt_keywords"
            unknown = [
                name for name in policy["concept_priority"] if not is_canonical_concept(name)
            ]
            assert not unknown, f"{row.key} names non-canonical concept(s): {unknown}"

    async def test_a_negative_budget_is_still_refused(self, db_session: AsyncSession) -> None:
        db_session.add(
            SectionDefinition(
                key="negative_budget_probe",
                version=1,
                origin="builtin",
                title="Probe",
                position=Decimal(990),
                required=False,
                output_contract={"type": "object", "properties": {}},
                evidence_policy={},
                token_budget=-1,
                allowed_tools=[],
                applicability={},
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestTheGateOneListing:
    """The plan payload carries the spine as data, stored on the plan row."""

    async def test_the_stored_listing_is_the_spine_in_order(self, scene: dict[str, Any]) -> None:
        await _execute(scene)
        plan = await scene["session"].scalar(
            select(ResearchPlan).where(ResearchPlan.request_id == scene["request"].id)
        )
        assert plan is not None

        listing = plan.plan["section_listing"]
        assert [entry["key"] for entry in listing] == list(SPINE_KEYS)

        by_key = {entry["key"]: entry for entry in listing}
        assert by_key["validation_disagreements"]["deterministic"] is True
        assert by_key["validation_disagreements"]["token_budget"] == 0
        assert by_key["executive_summary"]["deterministic"] is False
        assert by_key["executive_summary"]["token_budget"] == 2000

        # Task 45: the spine spends for real, so gate 1 estimates it — a ceiling per
        # model-written section, an honest zero for the platform-filled pair, and the
        # plan's total covering the lot.
        assert Decimal(by_key["executive_summary"]["estimated_cost_gbp"]) > 0
        assert Decimal(by_key["validation_disagreements"]["estimated_cost_gbp"]) == 0
        spine_total = sum(Decimal(entry["estimated_cost_gbp"]) for entry in listing)
        assert Decimal(str(plan.estimated_cost_gbp)) > spine_total

    async def test_the_gate_payload_reads_the_stored_listing_not_a_re_resolution(
        self, scene: dict[str, Any]
    ) -> None:
        """A definition published after planning must not change what gate 1 hashes."""
        await _execute(scene)
        session = scene["session"]
        plan = await session.scalar(
            select(ResearchPlan).where(ResearchPlan.request_id == scene["request"].id)
        )
        assert plan is not None

        session.add(
            SectionDefinition(
                key="published_after_planning",
                version=1,
                origin="builtin",
                title="Published After Planning",
                position=Decimal(115),
                required=True,
                output_contract={"type": "object", "properties": {}},
                evidence_policy={},
                token_budget=1000,
                allowed_tools=[],
                applicability={},
            )
        )
        await session.flush()

        pins = await pinned_skills_for_plan(session, plan_id=plan.id)
        payload = plan_gate_payload(plan, pins)
        assert [entry["key"] for entry in payload["section_listing"]] == list(SPINE_KEYS)


class TestTheDeterministicSections:
    """Filled by code, at the stage that can fill them truthfully."""

    async def test_the_prior_research_section_states_the_honest_first_run(
        self, scene: dict[str, Any]
    ) -> None:
        await _to_second_gate(scene)
        section = await _section(scene, "prior_research_comparison")

        assert section.status is SectionStatus.GENERATED
        assert "first research run" in section.content["commentary"]
        assert scene["request"].company_name in section.content["commentary"]
        assert "comparisons" not in section.content

    async def test_the_draft_stage_fills_only_its_own_sections(self, scene: dict[str, Any]) -> None:
        """The validation record cannot be filled before the validators have run.

        Routing by stage is the mechanism: a DRAFT fill takes the comparison section and
        leaves the validation section pending for the validate step. A fill that ignored
        the stage would write a validation record claiming zero metrics were measured —
        briefly true, permanently misleading.
        """
        await _execute(scene)  # to gate 1: the plan step has created the section rows

        filled = await fill_deterministic_sections(
            scene["session"],
            job=scene["job"],
            request=scene["request"],
            stage=SectionStage.DRAFT,
        )

        assert filled == ["prior_research_comparison"]
        prior = await _section(scene, "prior_research_comparison")
        assert prior.status is SectionStatus.GENERATED
        validation = await _section(scene, "validation_disagreements")
        assert validation.status is SectionStatus.PENDING
        assert validation.content is None

    async def test_the_validation_section_mirrors_the_evaluation_rows(
        self, scene: dict[str, Any]
    ) -> None:
        """The section is a record of the rows, not a précis of them.

        Every metric row appears, under its own name, with the verdict the row actually
        holds. The failing arm of the verdict mapping is pinned separately below —
        the clean slice legitimately fails nothing, and a run contorted into failing
        would test the contortion rather than the builder.
        """
        await _to_second_gate(scene)
        section = await _section(scene, "validation_disagreements")
        assert section.status is SectionStatus.GENERATED

        rows = list(
            await scene["session"].scalars(
                select(Evaluation).where(Evaluation.job_id == scene["job"].id)
            )
        )
        assert rows, "the validate step wrote no evaluation rows"

        shown = {entry["metric"]: entry for entry in section.content["validations"]}
        assert set(shown) == {row.metric for row in rows}
        for row in rows:
            expected = {True: "pass", False: "fail", None: "not exercised"}[row.passed]
            assert shown[row.metric]["verdict"] == expected

        summary = section.content["summary"]
        assert f"{len(rows)} metric(s)" in summary
        passed = sum(1 for row in rows if row.passed is True)
        failed = sum(1 for row in rows if row.passed is False)
        unexercised = sum(1 for row in rows if row.passed is None)
        assert f"{passed} passed" in summary
        assert f"{failed} failed" in summary
        assert f"{unexercised} not exercised" in summary

    async def test_model_sections_are_untouched_by_the_deterministic_fill(
        self, scene: dict[str, Any]
    ) -> None:
        """The fill routes by budget and must leave every model section alone.

        The residue that motivates this: a fill that visited model sections would stamp
        failure reasons the draft loop later papers over with generated content — a
        section reading "no deterministic builder" in its warning banner while claiming
        to be fine.
        """
        await _to_second_gate(scene)
        for key in SPINE_KEYS:
            if key in DETERMINISTIC_KEYS:
                continue
            section = await _section(scene, key)
            assert section.status is SectionStatus.GENERATED
            assert section.low_confidence_reason is None

    def test_a_failing_row_is_shown_failing_with_its_direction(self) -> None:
        """The arm that could hide a failure, pinned directly.

        The clean slice fails nothing, so the integration test above never exercises
        this mapping arm — and a builder that showed "pass" for a failed row would be
        exactly the flattering record the section must never be.
        """
        row = Evaluation(
            job_id=uuid.uuid4(),
            metric="source_coverage",
            value=Decimal("0.5"),
            threshold=Decimal("0.9"),
            passed=False,
            details={},
        )
        shown = deterministic_sections._validation_row(row)
        assert shown["verdict"] == "fail"
        assert shown["score"] == "0.5"
        assert shown["threshold"] == "at least 0.9"

    def test_a_metric_this_code_does_not_know_still_renders_its_threshold(self) -> None:
        row = Evaluation(
            job_id=uuid.uuid4(),
            metric="a_future_metric",
            value=Decimal("1"),
            threshold=Decimal("2"),
            passed=True,
            details={},
        )
        shown = deterministic_sections._validation_row(row)
        assert shown["threshold"] == "2"
        assert shown["verdict"] == "pass"

    async def test_the_deterministic_sections_spend_nothing(self, scene: dict[str, Any]) -> None:
        await _to_second_gate(scene)
        for key in DETERMINISTIC_KEYS:
            section = await _section(scene, key)
            assert section.token_cost == 0

    async def test_the_validation_section_is_inside_what_gate_two_seals(
        self, scene: dict[str, Any]
    ) -> None:
        """Filled at the end of validate — before the red team computes the hash the
        approval records — so "approved with this validation record showing" is a
        verifiable statement."""
        await _to_second_gate(scene)
        red_team = await scene["session"].scalar(
            select(JobStep).where(JobStep.job_id == scene["job"].id, JobStep.step_key == "red_team")
        )
        assert red_team is not None
        assert red_team.output_ref is not None

        payload = await final_gate_payload(scene["session"], job_id=scene["job"].id)
        sealed = {entry["key"]: entry for entry in payload["sections"] if isinstance(entry, dict)}
        assert sealed["validation_disagreements"]["status"] == SectionStatus.GENERATED.value
        assert sealed["validation_disagreements"]["content"]["validations"]

    def test_a_settled_disagreement_names_the_rule_that_settled_it(self) -> None:
        row = Disagreement(
            topic="Revenue FY2021",
            kind=DisagreementKind.SOURCE_CONFLICT,
            position_a={},
            position_b={},
            resolution=ResolutionOutcome.CHOSE_A,
            rule=ResolutionRule.LOWER_TIER_WINS,
            resolved_by=ResolvedBy.RULE,
            resolution_rationale="Tier 1 beats tier 3.",
        )
        shown = deterministic_sections._disagreement_row(row)
        assert shown["resolution"] == "resolved by rule 'lower_tier_wins': position A selected"
        assert shown["rationale"] == "Tier 1 beats tier 3."

    def test_an_escalated_disagreement_is_described_in_terms_that_cannot_go_stale(self) -> None:
        """ "Escalated for human decision at approval" states what the run did — still true
        after the human decides, unlike any wording that claimed it was undecided."""
        row = Disagreement(
            topic="Operating margin basis",
            kind=DisagreementKind.SOURCE_CONFLICT,
            position_a={},
            position_b={},
            resolution=ResolutionOutcome.ESCALATED,
            rule=ResolutionRule.SAME_TIER_SAME_DATE,
            resolved_by=ResolvedBy.HUMAN,
            resolution_rationale="Same tier, same date, different value.",
        )
        shown = deterministic_sections._disagreement_row(row)
        assert shown["resolution"] == "escalated for human decision at approval"

    async def test_a_zero_budget_section_with_no_builder_fails_loudly(
        self, scene: dict[str, Any]
    ) -> None:
        """A row that says "code fills me" when no code does must not render as an
        inexplicable blank."""
        scene["session"].add(
            SectionDefinition(
                key="unbuilt_probe",
                version=1,
                origin="builtin",
                title="Unbuilt Probe",
                position=Decimal(920),
                required=False,
                output_contract={"type": "object", "properties": {}},
                evidence_policy={"min_sources": 0, "requires_primary": False},
                token_budget=0,
                allowed_tools=[],
                applicability={},
            )
        )
        await scene["session"].flush()

        await _to_second_gate(scene)
        section = await _section(scene, "unbuilt_probe")

        assert section.status is SectionStatus.FAILED
        assert section.low_confidence_reason is not None
        assert "no deterministic builder" in section.low_confidence_reason
