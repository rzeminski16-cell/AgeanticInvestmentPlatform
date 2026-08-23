"""The §2.4 escalation triggers: any one pauses the run and raises the gate-2 banner.

Task 41. The pure engine first, against handwritten scenes — every trigger fired in
isolation and named, and a clean run firing nothing — then the service against seeded
rows, and finally the slice, whose final-gate pause must name what fired and whose sealed
payload hash must cover the triggers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Integer, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.disagreement import (
    DisagreementKind,
    Position,
    ResolutionOutcome,
    thesis_conflict,
)
from aer.core.enums import FactBasis, GateKind, JobStatus, Provider, SourceTier
from aer.core.escalation import (
    CONFIDENCE_FLOOR,
    COST_ALERT_RATIO,
    ConflictScene,
    CostScene,
    MetricScore,
    PolicyClamp,
    SectionScene,
    SourceScene,
    TriggerKind,
    fire_triggers,
)
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Artefact,
    Cost,
    Evaluation,
    JobStep,
    PlanSkillPin,
    ResearchPlan,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
)
from aer.db.models.plan_skill_pin import PLANNED
from aer.db.models.report_section import ReportSection
from aer.eval.metrics import Metric
from aer.services.disagreements import record_resolution, settle_by_hand
from aer.services.escalation import cost_scene_for_job, triggers_for_job
from aer.services.skills import save_skill
from aer.storage.local import LocalArtefactStore
from aer.workflow.workflows.vertical_slice_v1 import final_gate_payload
from tests.test_skill_frontmatter import MOAT_DURABILITY
from tests.test_workflow import approve, run_clearing_the_assumptions_gate, run_to_next_stop
from tests.workflow_fixtures import (
    AS_OF_DATE,
    StubSecClient,
    make_provider,
    seed_job,
    seed_request,
    seed_starved_section,
    seed_user,
)

pytestmark = pytest.mark.anyio


# ==========================================================================================
# The pure engine, against handwritten scenes
# ==========================================================================================


def _passing_metrics() -> tuple[MetricScore, ...]:
    """All eight run-time rows passing — built from the real Metric vocabulary, so these
    tests also hold the engine's string constants to the platform's own names."""
    at_least = {
        Metric.CITATION_ACCURACY: ("1", "0.98"),
        Metric.TEMPORAL_COMPLIANCE: ("1", "1"),
        Metric.LOOK_AHEAD_RECALL: ("1", "1"),
        Metric.SOURCE_COVERAGE: ("1", "0.9"),
        Metric.PRIMARY_SOURCE_RATIO: ("0.8", "0.6"),
        Metric.ASSUMPTION_COMPLETENESS: ("1", "1"),
    }
    at_most = {
        Metric.HALLUCINATED_CITATION_RATE: ("0", "0"),
        Metric.NUMERICAL_CONSISTENCY: ("0", "0.005"),
        Metric.FIGURE_PLAUSIBILITY: ("0", "0"),
    }
    rows = [
        MetricScore(metric=m.value, passed=True, value=Decimal(v), threshold=Decimal(t))
        for m, (v, t) in {**at_least, **at_most}.items()
    ]
    return tuple(rows)


def _clean_scene(**overrides: Any) -> dict[str, Any]:
    scene: dict[str, Any] = {
        "point_in_time": True,
        "metrics": _passing_metrics(),
        "sections": (
            SectionScene(
                key="executive_summary",
                status=SectionStatus.GENERATED.value,
                required=True,
                has_primary=True,
                covered=True,
                confidence=0.8,
            ),
            SectionScene(
                key="custom.moat_durability",
                status=SectionStatus.GENERATED.value,
                custom=True,
                has_primary=True,
                covered=True,
                confidence=0.7,
            ),
        ),
        "conflicts": (
            # An immaterial conflict the ladder settled by rule: recorded, published,
            # and not a banner.
            ConflictScene(topic="Revenue FY2022", kind=DisagreementKind.SOURCE_CONFLICT),
        ),
        "clamps": (),
        "sources": (SourceScene(name="10-K"),),
        "cost": CostScene(
            cap_gbp=Decimal("10"), estimated_gbp=Decimal("2"), actual_gbp=Decimal("1")
        ),
    }
    scene.update(overrides)
    return scene


def _kinds(scene: dict[str, Any]) -> list[TriggerKind]:
    return [trigger.kind for trigger in fire_triggers(**scene)]


class TestTheCleanRun:
    def test_a_clean_run_fires_nothing(self) -> None:
        assert fire_triggers(**_clean_scene()) == ()

    def test_not_exercised_metrics_fire_nothing(self) -> None:
        """A NULL verdict is 'nothing to measure', which is not a failure."""
        idle = tuple(
            MetricScore(metric=metric.metric, passed=None) for metric in _passing_metrics()
        )
        assert fire_triggers(**_clean_scene(metrics=idle)) == ()


class TestEachTriggerFiresAloneAndNamesItself:
    def test_a_required_section_without_a_primary_source(self) -> None:
        scene = _clean_scene(
            sections=(
                SectionScene(
                    key="executive_summary",
                    status=SectionStatus.GENERATED.value,
                    required=True,
                    has_primary=False,
                    covered=True,
                    confidence=0.8,
                ),
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.LOW_SOURCE_COVERAGE
        assert "executive_summary" in fired.evidence[0]

    def test_a_section_whose_own_floor_waives_the_primary_fires_nothing(self) -> None:
        """The deterministic appendix case: required, generated, citing no primary —
        and its evidence policy says none is owed. A trigger that ignored the floor
        would raise the banner on every clean run."""
        scene = _clean_scene(
            sections=(
                SectionScene(
                    key="validation_disagreements",
                    status=SectionStatus.GENERATED.value,
                    required=True,
                    has_primary=False,
                    covered=True,
                    requires_primary=False,
                ),
            )
        )
        assert fire_triggers(**scene) == ()

    def test_a_failed_primary_source_ratio(self) -> None:
        metrics = tuple(
            MetricScore(
                metric=row.metric,
                passed=False if row.metric == Metric.PRIMARY_SOURCE_RATIO.value else row.passed,
                value=Decimal("0.40")
                if row.metric == Metric.PRIMARY_SOURCE_RATIO.value
                else row.value,
                threshold=row.threshold,
            )
            for row in _passing_metrics()
        )
        [fired] = fire_triggers(**_clean_scene(metrics=metrics))
        assert fired.kind is TriggerKind.LOW_SOURCE_COVERAGE
        assert "0.4" in fired.evidence[0]

    def test_a_material_source_conflict(self) -> None:
        scene = _clean_scene(
            conflicts=(
                ConflictScene(
                    topic="Revenue FY2022",
                    kind=DisagreementKind.SOURCE_CONFLICT,
                    material=True,
                ),
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.CREDIBLE_SOURCE_CONFLICT
        assert fired.evidence == ("Revenue FY2022",)

    def test_a_conflict_a_person_settled_does_not_re_raise_the_banner(self) -> None:
        scene = _clean_scene(
            conflicts=(
                ConflictScene(
                    topic="Revenue FY2022",
                    kind=DisagreementKind.SOURCE_CONFLICT,
                    material=True,
                    settled_by_human=True,
                ),
            )
        )
        assert fire_triggers(**scene) == ()

    def test_a_post_dated_admissible_source_under_point_in_time(self) -> None:
        scene = _clean_scene(
            sources=(SourceScene(name="Q4 press release", post_dated=True, admissible=True),)
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.POTENTIAL_LOOK_AHEAD
        assert "Q4 press release" in fired.evidence[0]

    def test_the_same_source_is_no_look_ahead_when_point_in_time_is_off(self) -> None:
        scene = _clean_scene(
            point_in_time=False,
            sources=(SourceScene(name="Q4 press release", post_dated=True, admissible=True),),
        )
        assert fire_triggers(**scene) == ()

    def test_a_failed_temporal_metric_is_look_ahead_evidence(self) -> None:
        metrics = tuple(
            MetricScore(
                metric=row.metric,
                passed=False if row.metric == Metric.TEMPORAL_COMPLIANCE.value else row.passed,
                value=row.value,
                threshold=row.threshold,
                failures=("'undated note' was used while inadmissible",)
                if row.metric == Metric.TEMPORAL_COMPLIANCE.value
                else (),
            )
            for row in _passing_metrics()
        )
        [fired] = fire_triggers(**_clean_scene(metrics=metrics))
        assert fired.kind is TriggerKind.POTENTIAL_LOOK_AHEAD
        assert "undated note" in fired.evidence[0]

    def test_a_section_below_the_confidence_floor(self) -> None:
        scene = _clean_scene(
            sections=(
                SectionScene(
                    key="executive_summary",
                    status=SectionStatus.GENERATED.value,
                    required=True,
                    has_primary=True,
                    covered=True,
                    confidence=CONFIDENCE_FLOOR - 0.01,
                ),
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.HIGH_MODEL_UNCERTAINTY
        assert "0.49" in fired.evidence[0]

    def test_a_validator_dispute_is_uncertainty(self) -> None:
        metrics = tuple(
            MetricScore(
                metric=row.metric,
                passed=row.passed,
                value=row.value,
                threshold=row.threshold,
                disputes=("an advisory validator locates a candidate excerpt",)
                if row.metric == Metric.CITATION_ACCURACY.value
                else (),
            )
            for row in _passing_metrics()
        )
        [fired] = fire_triggers(**_clean_scene(metrics=metrics))
        assert fired.kind is TriggerKind.HIGH_MODEL_UNCERTAINTY
        assert "candidate excerpt" in fired.evidence[0]

    def test_a_required_section_that_was_not_generated(self) -> None:
        scene = _clean_scene(
            sections=(
                SectionScene(
                    key="executive_summary",
                    status=SectionStatus.FAILED.value,
                    required=True,
                ),
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.MATERIAL_MISSING_SECTION
        assert "not generated" in fired.evidence[0]
        assert "failed" in fired.evidence[0]

    def test_a_custom_section_below_its_floor(self) -> None:
        scene = _clean_scene(
            sections=(
                SectionScene(
                    key="custom.moat_durability",
                    status=SectionStatus.GENERATED.value,
                    custom=True,
                    has_primary=True,
                    covered=False,
                    shortfall="cites 1 of 3 source(s)",
                    confidence=0.7,
                ),
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.MATERIAL_MISSING_SECTION
        assert "cites 1 of 3" in fired.evidence[0]

    def test_a_skipped_custom_section_is_not_missing(self) -> None:
        """Skipped-not-applicable was excluded on purpose, with its reason recorded."""
        scene = _clean_scene(
            sections=(
                SectionScene(
                    key="custom.moat_durability",
                    status=SectionStatus.SKIPPED_NOT_APPLICABLE.value,
                    custom=True,
                    covered=False,
                ),
            )
        )
        assert fire_triggers(**scene) == ()

    def test_an_optional_builtin_is_never_material(self) -> None:
        scene = _clean_scene(
            sections=(SectionScene(key="appendix", status=SectionStatus.FAILED.value),)
        )
        assert fire_triggers(**scene) == ()

    def test_a_policy_clamp(self) -> None:
        scene = _clean_scene(
            clamps=(
                PolicyClamp(
                    skill_key="moat_durability",
                    field="max_tier",
                    requested="5",
                    effective="4",
                    reason="platform ceiling",
                ),
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.SKILL_POLICY_CLAMP
        assert "moat_durability" in fired.evidence[0]
        assert "max_tier" in fired.evidence[0]
        assert "platform ceiling" in fired.evidence[0]

    def test_actual_spend_over_eighty_percent_of_the_cap(self) -> None:
        scene = _clean_scene(
            cost=CostScene(cap_gbp=Decimal("10"), estimated_gbp=None, actual_gbp=Decimal("8.10"))
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.COST_ABOVE_THRESHOLD
        assert "8.10" in fired.evidence[0]

    def test_an_estimate_over_eighty_percent_fires_before_a_penny_moves(self) -> None:
        scene = _clean_scene(
            cost=CostScene(
                cap_gbp=Decimal("10"), estimated_gbp=Decimal("9"), actual_gbp=Decimal("0")
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.COST_ABOVE_THRESHOLD
        assert "estimated" in fired.evidence[0]

    def test_exactly_eighty_percent_does_not_fire(self) -> None:
        cap = Decimal("10")
        scene = _clean_scene(
            cost=CostScene(
                cap_gbp=cap, estimated_gbp=cap * COST_ALERT_RATIO, actual_gbp=cap * COST_ALERT_RATIO
            )
        )
        assert fire_triggers(**scene) == ()

    @pytest.mark.parametrize(
        "metric",
        [
            Metric.CITATION_ACCURACY,
            Metric.HALLUCINATED_CITATION_RATE,
            Metric.NUMERICAL_CONSISTENCY,
            Metric.FIGURE_PLAUSIBILITY,
        ],
    )
    def test_a_failed_validator_row(self, metric: Metric) -> None:
        metrics = tuple(
            MetricScore(
                metric=row.metric,
                passed=False if row.metric == metric.value else row.passed,
                value=Decimal("0.5") if row.metric == metric.value else row.value,
                threshold=row.threshold,
                failures=("claim 'Revenue was...' (below match threshold)",)
                if row.metric == metric.value
                else (),
            )
            for row in _passing_metrics()
        )
        [fired] = fire_triggers(**_clean_scene(metrics=metrics))
        assert fired.kind is TriggerKind.VALIDATION_FAILURE
        assert metric.value in fired.evidence[0]
        assert any("below match threshold" in line for line in fired.evidence)

    def test_an_injection_flagged_source(self) -> None:
        scene = _clean_scene(sources=(SourceScene(name="ir-page.html", injection_flagged=True),))
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.SUSPICIOUS_SOURCE
        assert "ir-page.html" in fired.evidence[0]

    def test_a_material_thesis_challenge(self) -> None:
        scene = _clean_scene(
            conflicts=(
                ConflictScene(
                    topic="Red team (growth): the CAGR rests on one filing",
                    kind=DisagreementKind.THESIS_CONFLICT,
                    material=True,
                ),
            )
        )
        [fired] = fire_triggers(**scene)
        assert fired.kind is TriggerKind.THESIS_DISAGREEMENT
        assert "Red team (growth)" in fired.evidence[0]

    def test_a_low_severity_challenge_is_recorded_but_raises_no_banner(self) -> None:
        scene = _clean_scene(
            conflicts=(
                ConflictScene(
                    topic="Red team (macro): rates could rise",
                    kind=DisagreementKind.THESIS_CONFLICT,
                    material=False,
                ),
            )
        )
        assert fire_triggers(**scene) == ()


class TestTheBannerShape:
    def test_all_ten_triggers_fire_together_in_the_tables_order(self) -> None:
        """§2.4's own row order, pinned across the whole vocabulary — the operator scans
        the banner top to bottom in the order the table taught them, every time."""
        failing = tuple(
            MetricScore(
                metric=row.metric,
                passed=False,
                value=Decimal("0.1"),
                threshold=row.threshold,
                failures=(f"{row.metric} failed",),
            )
            for row in _passing_metrics()
        )
        scene = _clean_scene(
            metrics=failing,
            sections=(
                SectionScene(
                    key="executive_summary",
                    status=SectionStatus.GENERATED.value,
                    required=True,
                    has_primary=False,
                    covered=False,
                    shortfall="cites 0 of 1 source(s)",
                    confidence=0.2,
                ),
            ),
            conflicts=(
                ConflictScene(
                    topic="Revenue FY2022",
                    kind=DisagreementKind.SOURCE_CONFLICT,
                    material=True,
                ),
                ConflictScene(
                    topic="Red team (valuation): peerless comparison",
                    kind=DisagreementKind.THESIS_CONFLICT,
                    material=True,
                ),
            ),
            clamps=(
                PolicyClamp(
                    skill_key="moat_durability", field="max_tier", requested="5", effective="4"
                ),
            ),
            sources=(
                SourceScene(
                    name="leak.html", post_dated=True, admissible=True, injection_flagged=True
                ),
            ),
            cost=CostScene(cap_gbp=Decimal("10"), estimated_gbp=None, actual_gbp=Decimal("9")),
        )
        assert _kinds(scene) == list(TriggerKind)

    def test_evidence_is_capped_with_an_honest_remainder(self) -> None:
        scene = _clean_scene(
            sources=tuple(
                SourceScene(name=f"page-{index}.html", injection_flagged=True) for index in range(9)
            )
        )
        [fired] = fire_triggers(**scene)
        assert len(fired.evidence) == 6
        assert fired.evidence[-1] == "and 4 more"

    def test_the_record_form_is_json_shaped(self) -> None:
        scene = _clean_scene(sources=(SourceScene(name="ir-page.html", injection_flagged=True),))
        [fired] = fire_triggers(**scene)
        record = fired.as_record()
        assert record["kind"] == "suspicious_source"
        assert isinstance(record["message"], str)
        assert record["message"]
        assert isinstance(record["evidence"], list)
        # It must survive canonical hashing, because it rides inside the approval hash.
        assert sha256_hex(canonical_json(record))


# ==========================================================================================
# The service, against seeded rows
# ==========================================================================================


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = await seed_user(db_session, email="escalation@example.invalid")
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    return {"session": db_session, "user": user, "request": request, "job": job}


async def _fired_kinds(scene: dict[str, Any]) -> list[str]:
    fired = await triggers_for_job(scene["session"], job=scene["job"], request=scene["request"])
    return [trigger.kind.value for trigger in fired]


async def _seed_source(
    scene: dict[str, Any],
    *,
    publication_date: date,
    quarantined: bool = False,
    injection_findings: list[dict[str, Any]] | None = None,
) -> SourceDocument:
    session: AsyncSession = scene["session"]
    payload = b"<html><body>Planted source for the trigger tests.</body></html>"
    artefact = Artefact(
        sha256=sha256_hex(payload),
        media_type="text/html",
        size_bytes=len(payload),
        storage_key=f"planted/{sha256_hex(payload)}",
    )
    session.add(artefact)
    await session.flush()

    extras: dict[str, Any] = {}
    if injection_findings:
        # Set only when present: an explicit None would travel as a JSON null, which the
        # flagged-has-findings check constraint cannot take the array length of.
        extras = {"injection_flagged": True, "injection_findings": injection_findings}
    document = SourceDocument(
        work_order_id=scene["request"].id,
        request_id=scene["request"].id,
        job_id=scene["job"].id,
        artefact_id=artefact.id,
        url=f"https://example.invalid/{artefact.sha256[:12]}.html",
        title="Planted document",
        provider=Provider.ISSUER_IR,
        source_tier=SourceTier.T2_ISSUER,
        retrieved_at=datetime.now(UTC),
        publication_date=publication_date,
        quarantined=quarantined,
        quarantine_reason="post-dated under point-in-time rules" if quarantined else None,
        **extras,
    )
    session.add(document)
    await session.flush()
    return document


def _position(reference: str, label: str) -> Position:
    return Position(
        reference=reference,
        label=label,
        value=Decimal("1"),
        unit="conclusion",
        tier=SourceTier.T6_UNVERIFIED,
        filed_date=AS_OF_DATE,
        basis=FactBasis.AS_REPORTED,
    )


class TestTheServiceReadsTheRecordedRows:
    async def test_a_run_with_nothing_recorded_fires_nothing(self, scene: dict[str, Any]) -> None:
        assert await _fired_kinds(scene) == []

    async def test_a_planted_look_ahead_source_escalates(self, scene: dict[str, Any]) -> None:
        """A post-dated source that escaped quarantine is exactly what the banner is for."""
        await _seed_source(scene, publication_date=AS_OF_DATE + timedelta(days=30))
        assert await _fired_kinds(scene) == [TriggerKind.POTENTIAL_LOOK_AHEAD.value]

    async def test_a_quarantined_look_ahead_source_is_already_handled(
        self, scene: dict[str, Any]
    ) -> None:
        """Quarantined and not overridden means nothing can cite it: no banner."""
        await _seed_source(
            scene, publication_date=AS_OF_DATE + timedelta(days=30), quarantined=True
        )
        assert await _fired_kinds(scene) == []

    async def test_an_injection_flagged_source_escalates(self, scene: dict[str, Any]) -> None:
        await _seed_source(
            scene,
            publication_date=AS_OF_DATE - timedelta(days=30),
            injection_findings=[{"signal": "hidden_text", "locator": "p:nth-child(9)"}],
        )
        assert await _fired_kinds(scene) == [TriggerKind.SUSPICIOUS_SOURCE.value]

    async def test_a_material_thesis_row_escalates_until_a_person_settles_it(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        row = await record_resolution(
            session,
            job_id=scene["job"].id,
            topic="Red team (growth): the growth rate rests on one filing",
            kind=DisagreementKind.THESIS_CONFLICT,
            resolution=thesis_conflict(
                first=_position("draft:base", "Base thesis"),
                second=_position("red_team:growth:abc", "Red team"),
                topic="growth durability",
                material=True,
            ),
        )
        assert row is not None
        assert await _fired_kinds(scene) == [TriggerKind.THESIS_DISAGREEMENT.value]

        await settle_by_hand(
            session,
            disagreement=row,
            outcome=ResolutionOutcome.CHOSE_A,
            actor=scene["user"],
            rationale="The base thesis survives; the challenge misreads the filing window.",
        )
        assert await _fired_kinds(scene) == []

    async def test_cost_rows_near_the_cap_escalate(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        cap = Decimal(str(scene["request"].max_cost_gbp))
        session.add(
            Cost(
                job_id=scene["job"].id,
                category="llm_output",
                provider="anthropic",
                model="claude-opus-5",
                units=Decimal("100000"),
                unit_type="tokens",
                amount_usd=cap * Decimal("1.2"),
                amount_gbp=cap * Decimal("0.9"),
                fx_rate=Decimal("0.79"),
            )
        )
        await session.flush()
        assert await _fired_kinds(scene) == [TriggerKind.COST_ABOVE_THRESHOLD.value]

        picture = await cost_scene_for_job(session, job=scene["job"], request=scene["request"])
        assert picture.cap_gbp == cap
        assert picture.actual_gbp == cap * Decimal("0.9")

    async def test_a_failed_evaluation_row_escalates(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        session.add(
            Evaluation(
                job_id=scene["job"].id,
                metric=Metric.CITATION_ACCURACY.value,
                value=Decimal("0.5"),
                threshold=Decimal("0.98"),
                passed=False,
                details={
                    "direction": "at_least",
                    "population": 2,
                    "failures": ["claim 'Revenue was...' (below match threshold)"],
                },
            )
        )
        await session.flush()
        fired = await triggers_for_job(session, job=scene["job"], request=scene["request"])
        assert [trigger.kind for trigger in fired] == [TriggerKind.VALIDATION_FAILURE]
        assert any("0.5" in line for line in fired[0].evidence)

    async def test_an_advisory_dispute_escalates_as_uncertainty(
        self, scene: dict[str, Any]
    ) -> None:
        """The advice never changed the verdict (ADR 0038); the banner shows the dispute."""
        session: AsyncSession = scene["session"]
        session.add(
            Evaluation(
                job_id=scene["job"].id,
                metric=Metric.CITATION_ACCURACY.value,
                value=Decimal("1"),
                threshold=Decimal("0.98"),
                passed=True,
                details={
                    "direction": "at_least",
                    "population": 1,
                    "failures": [],
                    "advisories": [
                        {
                            "kind": "excerpt_location",
                            "found": True,
                            "source_document_id": "planted",
                            "advisory": True,
                        }
                    ],
                },
            )
        )
        await session.flush()
        assert await _fired_kinds(scene) == [TriggerKind.HIGH_MODEL_UNCERTAINTY.value]

    async def test_a_required_section_below_its_floor_escalates(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        # A required section that *has* a source floor, named by the predicate rather
        # than taken from an unordered `LIMIT 1`. The arbitrary pick silently became a
        # deterministic section — those are seeded `min_sources: 0`, so there was no
        # floor to fall below and the trigger was right not to fire. Postgres moved the
        # pick when migration 0045 rewrote every budgeted definition row; the same
        # latent shape as the appendix-hash flake (P2).
        definition = await session.scalar(
            select(SectionDefinition)
            .where(
                SectionDefinition.required,
                SectionDefinition.evidence_policy["min_sources"].astext.cast(Integer) > 0,
            )
            .order_by(SectionDefinition.position, SectionDefinition.key)
            .limit(1)
        )
        assert definition is not None, "the migration seeds a required section with a floor"
        session.add(
            ReportSection(
                job_id=scene["job"].id,
                section_definition_id=definition.id,
                section_key=definition.key,
                position=definition.position,
                status=SectionStatus.GENERATED,
                content={"body": "A paragraph citing nothing."},
                confidence=0.8,
            )
        )
        await session.flush()
        fired = await _fired_kinds(scene)
        # Uncovered and unsourced at once: the coverage trigger names the missing primary,
        # the missing-section trigger names the floor. Both belong on the banner.
        assert TriggerKind.LOW_SOURCE_COVERAGE.value in fired
        assert TriggerKind.MATERIAL_MISSING_SECTION.value in fired

    async def test_a_pinned_clamp_escalates(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        version = await save_skill(session, source=MOAT_DURABILITY, actor=scene["user"])
        plan = ResearchPlan(
            request_id=scene["request"].id,
            workflow_version="vertical_slice_v1",
            plan={},
            planned_sources=[],
            known_risks=[],
            estimated_cost_gbp=Decimal("0.5"),
            estimated_runtime_seconds=60,
        )
        session.add(plan)
        await session.flush()
        scene["job"].plan_id = plan.id
        session.add(
            PlanSkillPin(
                work_order_id=plan.request_id,
                skill_id=version.skill_id,
                skill_version_id=version.id,
                status=PLANNED,
                min_sources=3,
                requires_primary=True,
                token_budget=8000,
                granted_tools=[],
                clamps=[
                    {
                        "field": "max_tier",
                        "requested": "5",
                        "effective": "4",
                        "reason": "platform ceiling",
                    }
                ],
                estimated_cost_gbp=Decimal("0.2"),
            )
        )
        await session.flush()

        fired = await triggers_for_job(session, job=scene["job"], request=scene["request"])
        assert [trigger.kind for trigger in fired] == [TriggerKind.SKILL_POLICY_CLAMP]
        assert "moat_durability" in fired[0].evidence[0]


# ==========================================================================================
# The slice: the pause names what fired, and the sealed hash covers it
# ==========================================================================================


@pytest.fixture
async def driven(
    db_session: AsyncSession,
    workflow_settings: Settings,
    workflow_store: LocalArtefactStore,
    sec_client: StubSecClient,
) -> dict[str, Any]:
    """A slice run driven to the final gate, with two triggers genuinely firing.

    Since task 44 every spine section carries a citation field, so the plain slice runs
    clean. The starved probe — a required section whose contract holds only prose —
    restores real §2.4 conditions: it generates with no citation, so the coverage and
    missing-section triggers both hold on a real run whose pause and payload must carry
    them.
    """
    provider = make_provider()
    await seed_starved_section(db_session)
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    args: dict[str, Any] = {
        "session": db_session,
        "job": job,
        "settings": workflow_settings,
        "provider": provider,
        "store": workflow_store,
        "sec_client": sec_client,
    }
    await run_to_next_stop(**args)
    await approve(db_session, job=job, gate=GateKind.PLAN, actor=user, step="plan")
    outcome = await run_clearing_the_assumptions_gate(actor=user, **args)
    return {
        "session": db_session,
        "user": user,
        "request": request,
        "job": job,
        "args": args,
        "outcome": outcome,
    }


class TestTheGatePausesNamingTheTriggers:
    async def test_the_pause_message_names_each_fired_trigger(self, driven: dict[str, Any]) -> None:
        assert driven["outcome"].status is JobStatus.AWAITING_APPROVAL

        step = await driven["session"].scalar(
            select(JobStep).where(
                JobStep.job_id == driven["job"].id, JobStep.step_key == "gate_final"
            )
        )
        assert step is not None
        detail = step.error or {}
        message = str(detail.get("message", ""))
        assert TriggerKind.LOW_SOURCE_COVERAGE.value in message
        assert TriggerKind.MATERIAL_MISSING_SECTION.value in message
        # Three, in the table's order. The starved probe's own confidence drops under the
        # §2.12 insufficiency ladder (task 45's writer marks thin findings low), so the
        # uncertainty condition genuinely holds alongside coverage and missing-section.
        assert detail.get("context", {}).get("triggers") == [
            TriggerKind.LOW_SOURCE_COVERAGE.value,
            TriggerKind.HIGH_MODEL_UNCERTAINTY.value,
            TriggerKind.MATERIAL_MISSING_SECTION.value,
        ]

    async def test_the_sealed_hash_covers_the_triggers(self, driven: dict[str, Any]) -> None:
        """The red-team step's hash and a fresh live read agree — with triggers inside."""
        payload = await final_gate_payload(driven["session"], job_id=driven["job"].id)
        assert [trigger["kind"] for trigger in payload["triggers"]] == [
            TriggerKind.LOW_SOURCE_COVERAGE.value,
            TriggerKind.HIGH_MODEL_UNCERTAINTY.value,
            TriggerKind.MATERIAL_MISSING_SECTION.value,
        ]
        # Exactly the probe, and nothing else. The deterministic sections also cite no
        # primary source, but their own policy waives one — a coverage trigger that named
        # them would be ignoring the floor each section actually declared.
        assert payload["triggers"][0]["evidence"] == [
            "required section 'starved_probe' cites no primary source"
        ]

        step = await driven["session"].scalar(
            select(JobStep).where(
                JobStep.job_id == driven["job"].id, JobStep.step_key == "red_team"
            )
        )
        assert step is not None
        sealed = str((step.output_ref or {})["payload_hash"])
        assert sealed == sha256_hex(canonical_json(payload))

    async def test_an_approval_made_with_the_banner_showing_still_stands(
        self, driven: dict[str, Any]
    ) -> None:
        """The banner informs the decision; it does not take the decision away."""
        await approve(
            driven["session"],
            job=driven["job"],
            gate=GateKind.FINAL,
            actor=driven["user"],
            step="red_team",
        )
        outcome = await run_to_next_stop(**driven["args"])
        assert outcome.status is JobStatus.SUCCEEDED
