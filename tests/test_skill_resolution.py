"""Resolution pins exact versions, prices what it pins, and says why it skipped.

Task 36. The pure matrix first — every scope and applicability combination, no database —
then the pinning behaviour that gives the task its name: a plan holds ``skill_versions``
ids, so editing a skill after approval changes nothing about the run that pinned it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import JobStatus, RequestStatus, SkillKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.skill_applicability import ApplicabilityDecision, market_of, skill_applies
from aer.db.models import (
    Company,
    Job,
    JobStep,
    ReportSection,
    ResearchPlan,
    Skill,
    SkillVersion,
    User,
)
from aer.db.models.plan_skill_pin import PLANNED, SKIPPED_NOT_APPLICABLE
from aer.db.models.section_definition import SKILL
from aer.providers.router import Router
from aer.services.skills import save_skill, set_enabled
from aer.skills.resolution import (
    compose_for_version,
    estimate_custom_section_cost,
    guidance_from_pins,
    pinned_skills_for_job,
    pinned_skills_for_work_order,
    project_custom_section,
    resolve_skills_for_plan,
)
from aer.workflow.workflows.vertical_slice_v1 import plan_gate_payload
from tests.request_fixtures import research_request
from tests.test_skill_frontmatter import MOAT_DURABILITY
from tests.test_workflow import run_to_next_stop
from tests.workflow_fixtures import seed_job, seed_request, seed_user


def _applies(**overrides: Any) -> ApplicabilityDecision:
    given: dict[str, Any] = {
        "scope": "global",
        "markets": ("US", "UK"),
        "analysis_modes": (),
        "exclude_sectors": (),
        "ticker": "MSFT",
        "market": "US",
        "analysis_mode": "full",
        "sector_profile_keys": frozenset(),
    }
    given.update(overrides)
    return skill_applies(**given)


class TestTheApplicabilityMatrix:
    def test_a_global_skill_applies(self) -> None:
        assert _applies().applicable

    def test_markets_are_respected(self) -> None:
        decision = _applies(markets=("UK",), market="US")

        assert not decision.applicable
        assert "US listing" in decision.reason

    def test_lse_is_the_uk_market(self) -> None:
        assert market_of("LSE") == "UK"
        assert market_of("NASDAQ") == "US"

    def test_analysis_modes_are_respected_and_empty_means_all(self) -> None:
        assert _applies(analysis_modes=(), analysis_mode="quick").applicable
        decision = _applies(analysis_modes=("full",), analysis_mode="quick")
        assert not decision.applicable

    def test_a_company_scope_matches_its_ticker_and_no_other(self) -> None:
        assert _applies(scope="company:MSFT").applicable
        decision = _applies(scope="company:AAPL")
        assert not decision.applicable
        assert "AAPL" in decision.reason

    def test_a_sector_scope_needs_a_known_matching_classification(self) -> None:
        assert _applies(scope="sector:banks", sector_profile_keys=frozenset({"banks"})).applicable
        # Unknown classification: a sector-scoped skill does not run on hope.
        undecided = _applies(scope="sector:banks")
        assert not undecided.applicable
        assert "not yet known" in undecided.reason
        # Known and different.
        wrong = _applies(scope="sector:banks", sector_profile_keys=frozenset({"reits"}))
        assert not wrong.applicable

    def test_sector_exclusions_fire_only_on_a_known_classification(self) -> None:
        excluded = _applies(exclude_sectors=("banks",), sector_profile_keys=frozenset({"banks"}))
        assert not excluded.applicable
        assert "Excluded for the banks sector" in excluded.reason
        # Unknown classification must NOT fire an exclusion — that would quietly disable
        # a global skill for every first-time company.
        assert _applies(exclude_sectors=("banks",)).applicable

    def test_run_scoped_skills_are_never_auto_selected(self) -> None:
        decision = _applies(scope="run")

        assert not decision.applicable
        assert "explicitly" in decision.reason


class TestTheEstimateIsTheCeiling:
    def test_a_twelve_k_section_costs_what_the_cost_model_says(self) -> None:
        # §1.8: 12k in, 3k out on Sonnet ($3/$15 per Mtok) = $0.081 → £ at the rate given.
        estimate = estimate_custom_section_cost(
            model="claude-sonnet-5", token_budget=12_000, usd_to_gbp=Decimal("0.79")
        )

        assert estimate == Decimal("0.0640")

    def test_an_unknown_model_is_priced_at_the_dearest_known(self) -> None:
        cautious = estimate_custom_section_cost(
            model="claude-mystery-9", token_budget=12_000, usd_to_gbp=Decimal("1")
        )
        known = estimate_custom_section_cost(
            model="claude-sonnet-5", token_budget=12_000, usd_to_gbp=Decimal("1")
        )

        assert cautious > known


# ==========================================================================================
# Against the database: pins, projection, and the payload hash
# ==========================================================================================

pytestmark_db = pytest.mark.integration


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="resolution@example.invalid", display_name="Resolver")
    db_session.add(user)
    await db_session.flush()

    request = research_request(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=date(2023, 1, 1),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
        status=RequestStatus.APPROVED,
    )
    db_session.add(request)
    await db_session.flush()

    plan = ResearchPlan(
        request_id=request.id,
        workflow_version="test",
        plan={"summary": "s", "sections": []},
        planned_sources=[],
        estimated_cost_gbp=Decimal("0.10"),
        estimated_runtime_seconds=60,
    )
    db_session.add(plan)
    await db_session.flush()

    settings = Settings(http_user_agent="Test test@example.invalid")
    return {
        "user": user,
        "request": request,
        "plan": plan,
        "settings": settings,
        "router": Router(settings),
    }


OWNER_OPERATOR = """\
---
aer_skill: 1
key: owner_operator
kind: methodology
title: "Weight owner-operator alignment"
version: 1
---

I weight owner-operator alignment heavily. Say so where it bears on a thesis.
"""


async def _skill_named(db_session: AsyncSession, key: str) -> Skill:
    return (await db_session.scalars(select(Skill).where(Skill.key == key))).one()


async def _latest_version_of(db_session: AsyncSession, skill: Skill) -> SkillVersion:
    rows = await db_session.scalars(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill.id)
        .order_by(SkillVersion.version.desc())
    )
    return rows.first()


async def _replanned(db_session: AsyncSession, scene: dict[str, Any]) -> dict[str, Any]:
    """The scene with a fresh plan row — the shape of a genuine re-plan."""
    plan = ResearchPlan(
        request_id=scene["request"].id,
        workflow_version="test",
        plan={"summary": "s", "sections": []},
        planned_sources=[],
        estimated_cost_gbp=Decimal("0.10"),
        estimated_runtime_seconds=60,
    )
    db_session.add(plan)
    await db_session.flush()
    return {**scene, "plan": plan}


async def _resolve(db_session: AsyncSession, scene: dict[str, Any]) -> Any:
    return await resolve_skills_for_plan(
        db_session,
        request=scene["request"],
        work_order_id=scene["request"].id,
        settings=scene["settings"],
        router=scene["router"],
    )


@pytest.mark.integration
class TestPinningIsToAVersion:
    async def test_an_enabled_skill_is_pinned_with_its_composed_policy(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])

        resolved = await _resolve(db_session, scene)

        [pin] = resolved.pins
        assert pin.status == PLANNED
        assert pin.min_sources == 3
        assert pin.token_budget == 12_000
        assert pin.granted_tools == ["search_facts", "search_sources"]
        assert pin.estimated_cost_gbp > 0

    async def test_a_disabled_skill_is_not_pinned_at_all(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])

        resolved = await _resolve(db_session, scene)

        assert resolved.pins == []

    async def test_editing_after_pinning_changes_nothing_about_the_pin(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        first = await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        resolved = await _resolve(db_session, scene)
        [pin] = resolved.pins

        edited = MOAT_DURABILITY.replace("min_sources: 3", "min_sources: 5")
        second = await save_skill(db_session, source=edited, actor=scene["user"])

        assert pin.skill_version_id == first.id
        assert pin.skill_version_id != second.id
        # The pin's snapshot still says what was approved, not what the file says now.
        assert pin.min_sources == 3

    async def test_a_market_mismatch_is_a_skip_with_its_reason(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        uk_only = MOAT_DURABILITY.replace(
            "scope: global",
            "scope: global\napplicability:\n  markets: [UK]",
        )
        await save_skill(db_session, source=uk_only, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])

        resolved = await _resolve(db_session, scene)

        [pin] = resolved.pins
        assert pin.status == SKIPPED_NOT_APPLICABLE
        assert "US listing" in pin.reason
        assert pin.estimated_cost_gbp == 0
        assert resolved.definitions == []

    async def test_a_sector_excluded_skill_is_skipped_when_the_sector_is_known(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        # A bank, known from a prior run's acquisition.
        db_session.add(
            Company(
                name="CONTOSO BANK", cik="0009999999", ticker="MSFT", exchange="NASDAQ", sic="6022"
            )
        )
        await db_session.flush()

        no_banks = MOAT_DURABILITY.replace(
            "scope: global",
            "scope: global\napplicability:\n  exclude_sectors: [banks]",
        )
        await save_skill(db_session, source=no_banks, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])

        resolved = await _resolve(db_session, scene)

        [pin] = resolved.pins
        assert pin.status == SKIPPED_NOT_APPLICABLE
        assert "banks" in pin.reason


@pytest.mark.integration
class TestTheProjection:
    async def test_a_planned_section_becomes_a_skill_origin_definition(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])

        resolved = await _resolve(db_session, scene)

        [definition] = resolved.definitions
        assert definition.key == "custom.moat_durability"
        assert definition.origin == SKILL
        assert definition.skill_id is not None
        assert definition.token_budget == 12_000
        assert list(definition.output_contract["properties"]) == ["summary", "durability_years"]
        # The composed policy, not the requested one, is what the definition carries.
        assert definition.evidence_policy["requires_primary"] is True

    async def test_a_replan_over_unchanged_skills_reprojects_nothing(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        # Since ADR 0072 pins hang off the run root rather than the plan, so a second plan
        # on the same run finds the set already there. Nothing is reprojected, which is a
        # stronger form of idempotent than reprojecting to the same answer: the definition
        # the gate displayed is the definition the run keeps.
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])

        first = await _resolve(db_session, scene)
        second = await _resolve(db_session, await _replanned(db_session, scene))

        assert second.definitions == []
        assert [pin.id for pin in second.pins] == [pin.id for pin in first.pins]

    async def test_reprojection_of_the_same_content_is_idempotent(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        # The projection itself, asked twice, rather than through a re-plan that no longer
        # reaches it. Same content, same definition row — a second version for a skill
        # nobody edited would make the section's history a record of how often it ran.
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        skill = await _skill_named(db_session, "moat_durability")
        version = await _latest_version_of(db_session, skill)
        composed = compose_for_version(version, settings=scene["settings"])

        first = await project_custom_section(
            db_session, skill=skill, version=version, composed=composed
        )
        second = await project_custom_section(
            db_session, skill=skill, version=version, composed=composed
        )

        assert first.id == second.id
        assert first.version == second.version

    async def test_resolving_the_same_plan_twice_returns_the_existing_pins(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        # The retried-step shape: pins already flushed for this plan. Re-deciding could
        # disagree with what a gate page has already displayed, so the existing pins win.
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])

        first = await _resolve(db_session, scene)
        again = await _resolve(db_session, scene)

        assert [pin.id for pin in again.pins] == [pin.id for pin in first.pins]

    async def test_a_changed_contract_becomes_a_new_definition_version(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        first = await _resolve(db_session, scene)

        edited = MOAT_DURABILITY.replace("  durability_years: number", "  erosion_risks: string")
        await save_skill(db_session, source=edited, actor=scene["user"])
        second = await _resolve(db_session, await _replanned(db_session, scene))

        assert second.definitions[0].version == first.definitions[0].version + 1
        # The author placed it once; a contract edit is not a request to move it.
        assert second.definitions[0].position == first.definitions[0].position


@pytest.mark.integration
class TestTheGateCoversThePins:
    async def test_approving_one_set_of_skills_is_not_approving_another(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        await _resolve(db_session, scene)

        pins = await pinned_skills_for_work_order(
            db_session, work_order_id=scene["plan"].request_id
        )
        with_skill = sha256_hex(canonical_json(plan_gate_payload(scene["plan"], pins)))
        without = sha256_hex(canonical_json(plan_gate_payload(scene["plan"], [])))

        assert with_skill != without

    async def test_the_payload_names_the_version_and_the_clamps(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        greedy = MOAT_DURABILITY.replace("token_budget: 12000", "token_budget: 50000").replace(
            "allowed_tools: [search_facts, search_sources]",
            "allowed_tools: [search_facts, fetch_arbitrary_url]",
        )
        await save_skill(db_session, source=greedy, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        await _resolve(db_session, scene)

        pins = await pinned_skills_for_work_order(
            db_session, work_order_id=scene["plan"].request_id
        )
        payload = plan_gate_payload(scene["plan"], pins)

        [skill] = payload["skills"]
        assert skill["version"] == 1
        assert skill["token_budget"] == 12_000
        clamped_fields = {clamp["field"] for clamp in skill["clamps"]}
        assert "token_budget" in clamped_fields
        assert "allowed_tools" in clamped_fields
        assert skill["granted_tools"] == ["search_facts"]

    async def test_the_payload_names_where_a_prompt_kind_pin_composes(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """ADR 0108 §3: inside the hash, because approving the plan is approving where the
        operator's words reach. A section composes into no role but its own."""
        await save_skill(db_session, source=OWNER_OPERATOR, actor=scene["user"])
        await set_enabled(db_session, key="owner_operator", enabled=True, actor=scene["user"])
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        await _resolve(db_session, scene)

        pins = await pinned_skills_for_work_order(
            db_session, work_order_id=scene["plan"].request_id
        )
        by_key = {row["key"]: row for row in plan_gate_payload(scene["plan"], pins)["skills"]}

        assert by_key["owner_operator"]["composes_into"] == ["planner", "report_writer"]
        assert by_key["moat_durability"]["composes_into"] == []

    async def test_a_prompt_kind_pin_is_priced_for_every_call_that_reads_it(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Its text is input on the planner's call and on every model-written section; a
        cost the gate does not show is a cost nobody agreed to (ADR 0108)."""
        await save_skill(db_session, source=OWNER_OPERATOR, actor=scene["user"])
        await set_enabled(db_session, key="owner_operator", enabled=True, actor=scene["user"])

        resolved = await resolve_skills_for_plan(
            db_session,
            request=scene["request"],
            work_order_id=scene["request"].id,
            settings=scene["settings"],
            router=scene["router"],
            writer_calls=15,
        )

        [pin] = resolved.pins
        assert pin.estimated_cost_gbp > 0
        assert resolved.estimated_cost_gbp == pin.estimated_cost_gbp

    async def test_planned_prompt_kind_pins_reduce_to_guidance_and_nothing_else_does(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await save_skill(db_session, source=OWNER_OPERATOR, actor=scene["user"])
        await set_enabled(db_session, key="owner_operator", enabled=True, actor=scene["user"])
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        # A UK-only house view against a NASDAQ request: pinned as skipped, so not guidance.
        uk_only = OWNER_OPERATOR.replace("key: owner_operator", "key: uk_view").replace(
            "kind: methodology", "kind: house_view\napplicability:\n  markets: [UK]"
        )
        await save_skill(db_session, source=uk_only, actor=scene["user"])
        await set_enabled(db_session, key="uk_view", enabled=True, actor=scene["user"])
        await _resolve(db_session, scene)

        pins = await pinned_skills_for_work_order(
            db_session, work_order_id=scene["plan"].request_id
        )
        [item] = guidance_from_pins(pins)

        assert item.kind is SkillKind.METHODOLOGY
        assert item.key == "owner_operator"
        assert item.version == 1
        assert "owner-operator alignment" in item.body


@pytest.mark.integration
class TestTheRunKnowsItsSkills:
    async def test_pins_resolve_from_the_job(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await save_skill(db_session, source=MOAT_DURABILITY, actor=scene["user"])
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
        await _resolve(db_session, scene)

        job = Job(
            work_order_id=scene["request"].id,
            plan_id=scene["plan"].id,
            workflow_version="test",
            code_version="abc",
            status=JobStatus.SUCCEEDED,
        )
        db_session.add(job)
        await db_session.flush()

        pins = await pinned_skills_for_job(db_session, job=job)

        [pin] = pins
        assert pin.skill.key == "moat_durability"
        assert pin.skill_version.version == 1
        assert pin.skill_version.content_hash

    async def test_a_planless_job_has_no_skills_rather_than_an_error(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        job = Job(
            work_order_id=scene["request"].id,
            workflow_version="test",
            code_version="abc",
            status=JobStatus.SUCCEEDED,
        )
        db_session.add(job)
        await db_session.flush()

        assert await pinned_skills_for_job(db_session, job=job) == []


@pytest.mark.integration
class TestThePlanStepCarriesTheSkills:
    """The wiring through the real plan step: estimate, hash and the drafting boundary."""

    async def test_the_gate_estimate_and_hash_cover_the_pinned_section(
        self,
        db_session: AsyncSession,
        workflow_settings: Any,
        workflow_store: Any,
        sec_client: Any,
        provider: Any,
    ) -> None:
        user = await seed_user(db_session, email="skill-plan@example.invalid")
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)

        await save_skill(db_session, source=MOAT_DURABILITY, actor=user)
        await set_enabled(db_session, key="moat_durability", enabled=True, actor=user)

        outcome = await run_to_next_stop(
            db_session,
            job=job,
            settings=workflow_settings,
            provider=provider,
            store=workflow_store,
            sec_client=sec_client,
        )

        plan_id = outcome.outputs["plan"]["plan_id"]
        plan = await db_session.get(ResearchPlan, plan_id)
        assert plan is not None

        # The pre-run estimate includes the section's budget: strictly more than the
        # planner's own spend, by exactly the pin's estimate.
        pins = await pinned_skills_for_work_order(db_session, work_order_id=plan.request_id)
        [pin] = pins
        assert pin.status == PLANNED
        assert plan.estimated_cost_gbp > pin.estimated_cost_gbp

        # The recorded hash is the hash of the payload with the pins inside it — the
        # same one the review page and the API compute.
        # The critique step's record, not the plan step's (ADR 0091): the critique block
        # joins the plan body after the plan step sealed its own interim hash, and the
        # gate — like the review page and the API — verifies against the last step that
        # can change what it displays.
        row = await db_session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == "critique_plan")
        )
        assert row is not None
        recorded = (row.output_ref or {})["payload_hash"]
        assert recorded == sha256_hex(canonical_json(plan_gate_payload(plan, pins)))
        assert outcome.outputs["plan"]["skills_planned"] == ["moat_durability"]

        # The run knows its plan, and through it, its skills.
        assert job.plan_id == plan.id

        # Task 38: a pinned custom section is a section of the run from the plan step
        # on, so drafting reaches it under the <user_skill> execution contract.
        sections = await db_session.scalars(
            select(ReportSection.section_key).where(ReportSection.job_id == job.id)
        )
        assert "custom.moat_durability" in set(sections)

        # The boundary that matters is a run whose plan did NOT pin the skill. Its
        # projected definition sits in section_definitions permanently, and a blanket
        # query would sweep it into every later run — sections must come from the
        # plan's own pins, and a plan with none creates none.
        await set_enabled(db_session, key="moat_durability", enabled=False, actor=user)
        second_request = await seed_request(db_session, user=user)
        second_job = await seed_job(db_session, request=second_request)
        await run_to_next_stop(
            db_session,
            job=second_job,
            settings=workflow_settings,
            provider=provider,
            store=workflow_store,
            sec_client=sec_client,
        )
        second_sections = await db_session.scalars(
            select(ReportSection.section_key).where(ReportSection.job_id == second_job.id)
        )
        assert all(not key.startswith("custom.") for key in second_sections)
