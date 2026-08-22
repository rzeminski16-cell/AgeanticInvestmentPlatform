"""The eighteen-section spine, and the two sections the platform fills itself.

Migration 0023 is the deliverable under test: sixteen seeded rows arriving with no code
change beyond what fills them. The deterministic pair get the closest attention, because
they are the one place a section key may legitimately meet code — and the failure modes
worth pinning are quiet ones: a validation record that flatters the run, a zero-budget row
nobody registered a builder for rendering as an inexplicable blank, a fill that lands
after the payload was sealed.
"""

from __future__ import annotations

import inspect
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
from aer.sections.render import render_section
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.services.evaluations import NUMERIC_CEILING
from aer.skills.resolution import pinned_skills_for_plan
from aer.workflow.workflows import vertical_slice_v1
from aer.workflow.workflows.vertical_slice_v1 import final_gate_payload, plan_gate_payload
from tests.workflow_fixtures import (
    SPINE_KEYS,
    gate_for,
    paused_at,
    seed_job,
    seed_request,
    seed_user,
)

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
    """Run to the gate-1 pause, approve, and run on to the gate-2 pause.

    **Every conditional gate in between is cleared by asking the run where it stopped**,
    rather than from a list of the pauses this scene is expected to meet. The list version
    broke the day the peer set stopped being empty (ADR 0059): a gate that had always
    passed straight through began firing, and a driver that knew the order walked into a
    pause it had no case for.
    """
    await _execute(scene)
    await _approve(scene, gate=GateKind.PLAN, step="plan")
    await _execute(scene)

    while (clearing := gate_for(await paused_at(scene["session"], scene["job"].id))) is not None:
        gate, step = clearing
        await _approve(scene, gate=gate, step=step)
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
        # Every version of the pair, not just the latest: a revision that grew a budget
        # would make the fill and the definition disagree about who writes the section.
        assert {row.key for row in rows} == set(DETERMINISTIC_KEYS)
        for row in rows:
            assert row.token_budget == 0
            assert row.evidence_policy["min_sources"] == 0
            assert row.evidence_policy["requires_primary"] is False

    async def test_the_bold_opener_has_a_structured_home(self, db_session: AsyncSession) -> None:
        """Migration 0037 (gap R6): the sections a live note bolded sentence openers in
        now write commentary as prose blocks, each with a lead-in the renderer
        emphasises — so the urge has a field and the notation never reaches a reader."""
        for key in ("growth_outlook", "scenarios_sensitivities"):
            row = await db_session.scalar(
                select(SectionDefinition)
                .where(SectionDefinition.key == key, SectionDefinition.origin == "builtin")
                .order_by(SectionDefinition.version.desc())
                .limit(1)
            )
            assert row is not None
            assert row.version >= 2
            commentary = row.output_contract["properties"]["commentary"]
            assert commentary["type"] == "array"
            assert commentary["items"]["required"] == ["text"]
            assert "lead_in" in commentary["items"]["properties"]
            # The rest of the contract is the previous version's, order included.
            assert "figures" in row.output_contract["properties"] or (
                "scenarios" in row.output_contract["properties"]
            )

    async def test_the_exhibit_claims_are_rows(self, db_session: AsyncSession) -> None:
        """Migration 0038 (gap N1): the sections that discuss an exhibit's subject claim
        it in their evidence policy, so a chart renders beside its analysis — mapped in
        data, because no section key may appear in the renderer's code."""
        expected = {
            "historical_financial_analysis": ["revenue_margin_history"],
            "segment_analysis": ["segment_mix"],
            "scenarios_sensitivities": ["scenario_bridge", "sensitivity_heatmap"],
            "valuation_dcf": ["football_field"],
        }
        for key, charts in expected.items():
            rows = list(
                await db_session.scalars(
                    select(SectionDefinition).where(
                        SectionDefinition.key == key, SectionDefinition.origin == "builtin"
                    )
                )
            )
            assert rows
            for row in rows:
                assert row.evidence_policy.get("exhibits") == charts

    async def test_the_financial_tables_read_across_periods(self, db_session: AsyncSession) -> None:
        """Migration 0039 (gap R9): the statement sections write period-indexed line
        items — the series shape the renderer lays out with periods across the top —
        slotted directly after the prose, with figures and commentary untouched."""
        for key in (
            "historical_financial_analysis",
            "balance_sheet_liquidity",
            "cash_flow_analysis",
        ):
            row = await db_session.scalar(
                select(SectionDefinition)
                .where(SectionDefinition.key == key, SectionDefinition.origin == "builtin")
                .order_by(SectionDefinition.version.desc())
                .limit(1)
            )
            assert row is not None
            assert row.version >= 2
            names = list(row.output_contract["properties"])
            assert names.index("financials") == names.index("commentary") + 1

            financials = row.output_contract["properties"]["financials"]
            item = financials["items"]
            assert item["required"] == ["label", "values"]
            entry = item["properties"]["values"]["items"]
            assert entry["required"] == ["period", "value"]
            # A cell names its stored figure and cites its source — the numeral rule
            # and the footnote both need their key.
            assert "financial_fact_id" in entry["properties"]
            assert "source_document_id" in entry["properties"]

    async def test_the_one_pager_claims_are_rows(self, db_session: AsyncSession) -> None:
        """Migration 0040 (gap O8): the view and its counterweight claim the one-page
        summary in data, for the same reason the exhibit claims do."""
        for key in ("executive_summary", "key_risks"):
            rows = list(
                await db_session.scalars(
                    select(SectionDefinition).where(
                        SectionDefinition.key == key, SectionDefinition.origin == "builtin"
                    )
                )
            )
            assert rows
            assert all(row.evidence_policy.get("one_pager") is True for row in rows)

    async def test_the_descriptive_sections_name_the_workhorse_route(
        self, db_session: AsyncSession
    ) -> None:
        """Migration 0041 (gap O1): the sections that describe rather than judge bill
        their writer at the workhorse route; the judgement sections name none and keep
        the report_writer route."""
        descriptive = (
            "business_overview",
            "segment_analysis",
            "industry_landscape",
            "management_governance",
            "capital_allocation",
            "catalysts",
        )
        rows = list(
            await db_session.scalars(
                select(SectionDefinition).where(SectionDefinition.origin == "builtin")
            )
        )
        assert rows
        for row in rows:
            stated = (row.evidence_policy or {}).get("writer_role")
            if row.key in descriptive:
                assert stated == "section_writer_workhorse", row.key
            else:
                assert stated is None, row.key

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
            if key == "valuation_dcf":
                # The draft step's own standalone note (gap A51c), in the reader's
                # register (gap R4) — not a stamp from the fill.
                assert "no valuation figures to interpret" in (section.low_confidence_reason or "")
                continue
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

    def test_a_clamped_score_renders_as_unbounded_not_as_twelve_nines(self) -> None:
        """Polish P9: the column's saturation value is not a measurement.

        An infinite replay delta is stored clamped at NUMERIC(20, 8)'s ceiling with the
        truth in the details — and the first published PDF printed the twelve nines
        against a threshold of 0.005, which reads as a crashed validator.
        """
        row = Evaluation(
            job_id=uuid.uuid4(),
            metric="numerical_consistency",
            value=NUMERIC_CEILING,
            threshold=Decimal("0.005"),
            passed=False,
            details={"value": "Infinity"},
        )
        shown = deterministic_sections._validation_row(row)
        assert shown["score"] == "unbounded (clamped at 1e12)"
        assert "9999" not in shown["score"]

    def test_the_summary_names_the_guarantees_measured_elsewhere(self) -> None:
        """Polish P9: four guarantees a reader cannot account for is worse than four
        they can see are covered by the CI evaluation gate."""
        summary = deterministic_sections._summary([], [])

        for name in (
            "custom_section_contract_conformance",
            "injection_resistance",
            "skill_privilege_containment",
            "unit_integrity",
        ):
            assert name in summary
        assert "CI evaluation gate" in summary

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

    async def test_the_red_teams_challenges_reach_the_section_it_wrote_over(
        self, scene: dict[str, Any]
    ) -> None:
        """Gap A41's mechanism: the fill is an overwrite, so refilling after the red team
        records its challenges replaces "no disagreements recorded" with the rows.

        A live report said none were recorded above eight recorded challenges, because
        the section was written by the validate step, one step before the red team ran.
        """
        await _to_second_gate(scene)
        before = await _section(scene, "validation_disagreements")
        assert "No disagreements" in before.content["summary"]

        scene["session"].add(
            Disagreement(
                job_id=scene["job"].id,
                topic="Red team (valuation): the margin path is asserted, not evidenced",
                kind=DisagreementKind.THESIS_CONFLICT,
                position_a={"claim": "margin holds"},
                position_b={"challenge": "depreciation outruns revenue"},
                resolution=ResolutionOutcome.ESCALATED,
                rule=ResolutionRule.THESIS_CONFLICT,
                resolved_by=ResolvedBy.RULE,
                resolution_rationale="Escalated to gate 2.",
                escalated_to_gate=GateKind.FINAL,
                fingerprint="a" * 64,
            )
        )
        await scene["session"].flush()

        refilled = await fill_deterministic_sections(
            scene["session"],
            job=scene["job"],
            request=scene["request"],
            stage=SectionStage.VALIDATE,
        )

        assert "validation_disagreements" in refilled
        after = await _section(scene, "validation_disagreements")
        assert "1 disagreement(s)" in after.content["summary"]
        assert after.content["disagreements"], "the recorded challenge never reached the section"

    def test_the_red_team_step_refills_after_recording_and_before_sealing(self) -> None:
        """The ordering itself, pinned at the source: record, refill, then hash.

        A refill moved after ``final_gate_payload`` would seal a payload the operator
        approves without the challenges in its disagreements section — the live failure
        with an extra step of indirection.
        """
        source = inspect.getsource(vertical_slice_v1._red_team)
        recorded = source.index("run_red_team(")
        refilled = source.index("fill_deterministic_sections(")
        sealed = source.index("final_gate_payload(")
        assert recorded < refilled < sealed

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

    def test_a_red_team_challenge_is_laid_out_once_with_footnotes_never_uuids(self) -> None:
        """Gap R5: the statement appears once, the evidence ids become citation keys the
        renderer footnotes, and neither a UUID nor the rationale blob reaches the reader.

        The live note printed each challenge three times — truncated in the topic, then
        twice inside the rationale — followed by ``Evidence: ef2bd367-…`` verbatim.
        """
        fact_id = str(uuid.uuid4())
        source_id = str(uuid.uuid4())
        statement = "The margin path is asserted, not evidenced."
        row = Disagreement(
            topic=f"Red team (competitive_position): {statement[:120]}",
            kind=DisagreementKind.THESIS_CONFLICT,
            position_a={},
            position_b={},
            resolution=ResolutionOutcome.ESCALATED,
            rule=ResolutionRule.THESIS_CONFLICT,
            resolved_by=ResolvedBy.RULE,
            resolution_rationale="Opposing conclusions; both are published.",
            detail={
                "challenge": statement,
                "basis": "Depreciation outruns revenue in every disclosed period.",
                "severity": 4,
                "dimension": "competitive_position",
                "evidence": {"facts": [fact_id], "calculations": [], "sources": [source_id]},
            },
        )

        shown = deterministic_sections._disagreement_row(row)

        assert shown["topic"] == "Red team \N{EM DASH} competitive position"
        assert shown["severity"] == "4/5"
        assert shown["challenge"] == statement
        assert shown["basis"].startswith("Depreciation outruns")
        assert shown["resolution"] == "escalated for human decision at approval"
        # The evidence rides the citation key the renderer turns into a footnote; no id
        # appears in any field a reader sees as text, and the blob fields are gone.
        assert shown["source_document_id"] == source_id
        readable = {key: value for key, value in shown.items() if key != "source_document_id"}
        assert all(fact_id not in value and source_id not in value for value in readable.values())
        assert "rationale" not in shown
        assert "kind" not in shown
        assert sum(value.count(statement) for value in readable.values()) == 1

    async def test_the_appendix_lays_challenges_out_as_a_table_with_footnotes(
        self, db_session: AsyncSession
    ) -> None:
        """Migration 0036's v2 contract and the row shape meet in the renderer: the
        contract orders the columns, and the evidence id becomes a footnote marker."""
        definition = await db_session.scalar(
            select(SectionDefinition)
            .where(SectionDefinition.key == "validation_disagreements")
            .order_by(SectionDefinition.version.desc())
            .limit(1)
        )
        assert definition is not None
        assert definition.version >= 2

        source_id = str(uuid.uuid4())
        row = Disagreement(
            topic="Red team (growth): the trajectory is asserted",
            kind=DisagreementKind.THESIS_CONFLICT,
            position_a={},
            position_b={},
            resolution=ResolutionOutcome.ESCALATED,
            rule=ResolutionRule.THESIS_CONFLICT,
            resolved_by=ResolvedBy.RULE,
            resolution_rationale="Opposing conclusions; both are published.",
            detail={
                "challenge": "The trajectory is asserted, not evidenced.",
                "basis": "No disclosed period supports the slope.",
                "severity": 4,
                "dimension": "growth",
                "evidence": {"facts": [], "calculations": [], "sources": [source_id]},
            },
        )
        content = {
            "summary": "One disagreement was recorded.",
            "disagreements": [deterministic_sections._disagreement_row(row)],
        }

        rendered = render_section(
            key="validation_disagreements",
            title=definition.title,
            contract=definition.output_contract,
            content=content,
        )

        assert "| Topic | Severity | Challenge | Basis | Resolution |" in rendered.markdown
        assert "[^1]" in rendered.markdown
        assert source_id not in rendered.markdown
        assert [str(ref) for ref in rendered.citations] == [f"source_document:{source_id}"]

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


class TestAFailedCheckNamesItsFindings:
    """Gap A60. The live run's coverage notice said `presentation_integrity` failed and
    nothing anywhere said what it found — the findings sat in the evaluation row's JSONB
    all along. The section now prints them, one row per finding, beside the metric table
    that announces the failure.
    """

    @staticmethod
    def _failed(metric: str, failures: list[str]) -> Evaluation:
        return Evaluation(
            job_id=uuid.uuid4(),
            metric=metric,
            value=Decimal(len(failures)),
            threshold=Decimal(0),
            passed=False,
            details={"failures": failures},
        )

    def test_each_finding_is_a_row_beside_its_metric(self) -> None:
        rows = deterministic_sections._failed_check_findings(
            [
                self._failed(
                    "presentation_integrity",
                    ["raw UUID 'ef2bd367…'", "unformatted integer '46822502000'"],
                )
            ]
        )

        assert rows == [
            {"metric": "presentation_integrity", "finding": "raw UUID 'ef2bd367…'"},
            {"metric": "presentation_integrity", "finding": "unformatted integer '46822502000'"},
        ]

    def test_a_passing_row_contributes_nothing(self) -> None:
        row = Evaluation(
            job_id=uuid.uuid4(),
            metric="citation_accuracy",
            value=Decimal(1),
            threshold=Decimal("0.98"),
            passed=True,
            details={"failures": ["must never appear"]},
        )

        assert deterministic_sections._failed_check_findings([row]) == []

    def test_a_wall_of_findings_is_bounded_with_the_count(self) -> None:
        many = [f"defect {index}" for index in range(25)]

        rows = deterministic_sections._failed_check_findings([self._failed("m", many)])

        assert len(rows) == deterministic_sections._FINDINGS_SHOWN + 1
        assert "and 15 more" in rows[-1]["finding"]

    def test_a_failure_with_no_recorded_findings_is_still_named(self) -> None:
        """A silent row here would recreate the very gap this table closes."""
        rows = deterministic_sections._failed_check_findings([self._failed("m", [])])

        assert len(rows) == 1
        assert "recorded no individual findings" in rows[0]["finding"]

    async def test_the_contract_declares_the_table_between_metrics_and_disagreements(
        self, db_session: AsyncSession
    ) -> None:
        """Migration 0050: the v3 contract, so the renderer lays the findings out."""
        definition = await db_session.scalar(
            select(SectionDefinition)
            .where(SectionDefinition.key == "validation_disagreements")
            .order_by(SectionDefinition.version.desc())
            .limit(1)
        )
        assert definition is not None
        assert definition.version >= 3

        properties = (definition.output_contract or {})["properties"]
        keys = list(properties)
        assert "failed_check_findings" in properties
        assert keys.index("validations") < keys.index("failed_check_findings")
        assert keys.index("failed_check_findings") < keys.index("disagreements")

    async def test_a_failing_runs_section_carries_the_findings_table(
        self, db_session: AsyncSession
    ) -> None:
        """The wiring, not just the helper: the built content must carry the table
        under the key the v3 contract declares, or the renderer never lays it out."""
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)
        db_session.add(
            Evaluation(
                job_id=job.id,
                metric="presentation_integrity",
                value=Decimal(1),
                threshold=Decimal(0),
                passed=False,
                details={"failures": ["raw UUID 'ef2bd367…'"]},
            )
        )
        await db_session.flush()

        content = await deterministic_sections._validation_disagreements(db_session, job, request)

        assert content["failed_check_findings"] == [
            {"metric": "presentation_integrity", "finding": "raw UUID 'ef2bd367…'"}
        ]
