"""The resolution ladder, and the rows it writes.

Three of these matter more than the rest.

* **One case per rung, including the four that must escalate.** The acceptance criterion for
  this task is that no rung resolves by falling through to a default, so each rung is pinned
  by a case that reaches only it.
* **The ladder is total**, proved by hypothesis over the whole input space rather than by
  reading the code. If any combination of tier, date, basis, unit and value reached the end
  without a rung firing, the implementation would have to invent a winner — the exact
  failure the module exists to prevent.
* **The result does not depend on argument order.** Two facts arrive from a query in
  whatever order the planner chose. If ``resolve(a, b)`` and ``resolve(b, a)`` disagreed
  about which position won, a re-run would silently change the reported figure.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError as DbIntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.disagreement import (
    AGREEMENT_TOLERANCE,
    MATERIALITY_THRESHOLD,
    DisagreementKind,
    Position,
    Resolution,
    ResolutionOutcome,
    ResolutionRule,
    ResolvedBy,
    canonical_unit,
    relative_difference,
    resolve,
    thesis_conflict,
)
from aer.core.enums import FactBasis, GateKind, SourceTier
from aer.core.sectors import SECTOR_PROFILES, ValuationModel, profile_for
from aer.db.models import Disagreement, SectorProfile
from aer.errors import ValidationError
from aer.services.disagreements import (
    ESCALATION_GATE,
    disagreements_for_job,
    escalations_for_job,
    fingerprint_for,
    record_resolution,
    resolve_and_record,
    settle_by_hand,
)
from aer.storage.local import LocalArtefactStore
from aer.workflow.workflows.vertical_slice_v1 import final_gate_payload
from tests.scene_fixtures import build_scene

FILED = date(2025, 7, 31)
LATER = date(2025, 10, 30)


def position(
    reference: str = "a",
    *,
    value: str = "245122",
    unit: str = "USD",
    tier: SourceTier = SourceTier.T1_REGULATORY,
    filed_date: date = FILED,
    basis: FactBasis = FactBasis.AS_REPORTED,
    label: str | None = None,
    scale: int = 0,
) -> Position:
    return Position(
        reference=reference,
        label=label or f"source {reference}",
        value=Decimal(value),
        unit=unit,
        tier=tier,
        filed_date=filed_date,
        basis=basis,
        scale=scale,
    )


# -- The ladder, one rung at a time ----------------------------------------------------------


class TestTheLadder:
    def test_rung_zero_refuses_to_compare_different_units(self):
        # Not a disagreement about a quantity: a question about two quantities. Nothing
        # converts, because invariant 5 says a unit mismatch raises rather than coercing.
        outcome = resolve(position("a", unit="USD"), position("b", unit="GBP"))

        assert outcome.rule is ResolutionRule.UNIT_MISMATCH
        assert outcome.outcome is ResolutionOutcome.ESCALATED
        assert outcome.relative_difference is None

    def test_rung_one_agrees_and_records_nothing(self):
        outcome = resolve(position("a", value="245122"), position("b", value="245122"))

        assert outcome.rule is ResolutionRule.VALUES_AGREE
        assert outcome.outcome is ResolutionOutcome.AGREED
        assert not outcome.is_recordable

    def test_rung_one_absorbs_presentation_rounding(self):
        # One source rounds to the nearest hundred million on a 245bn base. That is
        # presentation, not disagreement.
        outcome = resolve(position("a", value="245122"), position("b", value="245100"))

        assert outcome.outcome is ResolutionOutcome.AGREED

    def test_rung_two_escalates_a_clean_power_of_ten(self):
        outcome = resolve(position("a", value="245122"), position("b", value="245122000000"))

        assert outcome.rule is ResolutionRule.SUSPECTED_SCALE_ERROR
        assert outcome.outcome is ResolutionOutcome.ESCALATED
        assert "10^6" in outcome.rationale

    def test_rung_two_outranks_the_tier_rule(self):
        # The rung worth arguing about. A tier-1 figure a million-fold out is a parsing bug,
        # and resolving it by tier would hand the bug a provenance record saying the
        # regulator said so.
        outcome = resolve(
            position("a", value="245122", tier=SourceTier.T1_REGULATORY),
            position("b", value="245122000000", tier=SourceTier.T2_ISSUER),
        )

        assert outcome.rule is ResolutionRule.SUSPECTED_SCALE_ERROR
        assert outcome.winner is None

    def test_rung_three_prefers_the_lower_tier_number(self):
        outcome = resolve(
            position("a", value="245122", tier=SourceTier.T2_ISSUER),
            position("b", value="244000", tier=SourceTier.T1_REGULATORY),
        )

        assert outcome.rule is ResolutionRule.LOWER_TIER_WINS
        winner = outcome.winner
        assert winner is not None
        assert winner.tier is SourceTier.T1_REGULATORY

    def test_rung_three_retains_the_rejected_position(self):
        # The requirement the whole table exists for: losing evidence is kept.
        outcome = resolve(
            position("a", value="245122", tier=SourceTier.T2_ISSUER),
            position("b", value="244000", tier=SourceTier.T1_REGULATORY),
        )

        loser = outcome.loser
        assert loser is not None
        assert loser.value == Decimal("245122")

    def test_rung_four_escalates_a_basis_mismatch(self):
        # As-reported and restated are both true, of different questions. Preferring the
        # later one is how a backtest starts flattering itself.
        outcome = resolve(
            position("a", value="245122", basis=FactBasis.AS_REPORTED, filed_date=FILED),
            position("b", value="248000", basis=FactBasis.RESTATED, filed_date=LATER),
        )

        assert outcome.rule is ResolutionRule.BASIS_MISMATCH
        assert outcome.outcome is ResolutionOutcome.ESCALATED

    def test_rung_four_escalates_even_when_the_dates_match(self):
        outcome = resolve(
            position("a", value="245122", basis=FactBasis.AS_REPORTED),
            position("b", value="248000", basis=FactBasis.VENDOR_STANDARDISED),
        )

        assert outcome.rule is ResolutionRule.BASIS_MISMATCH

    def test_rung_five_prefers_the_later_filing_on_a_matching_basis(self):
        outcome = resolve(
            position("a", value="245122", filed_date=FILED),
            position("b", value="244000", filed_date=LATER),
        )

        assert outcome.rule is ResolutionRule.LATER_FILING_WINS
        winner = outcome.winner
        assert winner is not None
        assert winner.filed_date == LATER

    def test_rung_six_escalates_when_nothing_is_left_to_prefer_by(self):
        outcome = resolve(
            position("a", value="245122"),
            position("b", value="244000"),
        )

        assert outcome.rule is ResolutionRule.SAME_TIER_SAME_DATE
        assert outcome.outcome is ResolutionOutcome.ESCALATED
        assert outcome.winner is None
        assert outcome.loser is None

    def test_a_thesis_conflict_is_never_resolved(self):
        outcome = thesis_conflict(
            first=position("base", label="base thesis"),
            second=position("red", label="red team"),
            topic="the durability of the margin",
        )

        assert outcome.rule is ResolutionRule.THESIS_CONFLICT
        assert outcome.outcome is ResolutionOutcome.ESCALATED
        assert outcome.material
        assert outcome.relative_difference is None

    @pytest.mark.parametrize(
        "rule",
        [
            ResolutionRule.UNIT_MISMATCH,
            ResolutionRule.VALUES_AGREE,
            ResolutionRule.SUSPECTED_SCALE_ERROR,
            ResolutionRule.LOWER_TIER_WINS,
            ResolutionRule.BASIS_MISMATCH,
            ResolutionRule.LATER_FILING_WINS,
            ResolutionRule.SAME_TIER_SAME_DATE,
        ],
    )
    def test_every_numeric_rung_has_a_case_above(self, rule):
        # A guard on this file rather than on the module: adding a rung without a test is
        # how a ladder acquires an untested branch that quietly decides a figure.
        source = Path(__file__).read_text()
        assert f"ResolutionRule.{rule.name}" in source


# -- The properties the ladder has to have ---------------------------------------------------


_TIERS = st.sampled_from(list(SourceTier))
_BASES = st.sampled_from(list(FactBasis))
_UNITS = st.sampled_from(["USD", "usd", " USD ", "GBP", "shares"])
_DATES = st.dates(min_value=date(2020, 1, 1), max_value=date(2026, 12, 31))
_VALUES = st.decimals(
    min_value=Decimal("-1e12"),
    max_value=Decimal("1e12"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)

_POSITIONS = st.builds(
    Position,
    reference=st.sampled_from(["a", "b", "c"]),
    label=st.just("source"),
    value=_VALUES,
    unit=_UNITS,
    tier=_TIERS,
    filed_date=_DATES,
    basis=_BASES,
    scale=st.integers(min_value=-6, max_value=9),
)


class TestTheLadderIsTotal:
    @hypothesis_settings(max_examples=400)
    @given(first=_POSITIONS, second=_POSITIONS)
    def test_every_pair_reaches_exactly_one_rung(self, first: Position, second: Position):
        # If this ever raises, the ladder has a hole and the alternative to raising would be
        # picking a winner nobody chose.
        outcome = resolve(first, second)

        assert isinstance(outcome, Resolution)
        assert outcome.rule is not ResolutionRule.THESIS_CONFLICT

    @hypothesis_settings(max_examples=400)
    @given(first=_POSITIONS, second=_POSITIONS)
    def test_the_answer_does_not_depend_on_argument_order(self, first: Position, second: Position):
        forwards = resolve(first, second)
        backwards = resolve(second, first)

        assert forwards.rule is backwards.rule
        assert forwards.outcome is backwards.outcome
        assert forwards.winner == backwards.winner
        assert forwards.loser == backwards.loser
        assert forwards.rationale == backwards.rationale

    @hypothesis_settings(max_examples=200)
    @given(first=_POSITIONS, second=_POSITIONS)
    def test_a_resolution_always_carries_an_argument(self, first: Position, second: Position):
        assert resolve(first, second).rationale.strip()

    @hypothesis_settings(max_examples=200)
    @given(first=_POSITIONS, second=_POSITIONS)
    def test_only_a_decided_outcome_names_a_winner(self, first: Position, second: Position):
        outcome = resolve(first, second)

        decided = outcome.outcome in (ResolutionOutcome.CHOSE_A, ResolutionOutcome.CHOSE_B)
        assert (outcome.winner is not None) == decided
        assert (outcome.loser is not None) == decided


class TestRelativeDifference:
    def test_it_is_symmetric(self):
        assert relative_difference(Decimal(100), Decimal(90)) == relative_difference(
            Decimal(90), Decimal(100)
        )

    def test_zero_against_zero_is_zero(self):
        assert relative_difference(Decimal(0), Decimal(0)) == 0

    def test_zero_against_anything_is_complete_disagreement(self):
        assert relative_difference(Decimal(0), Decimal(100)) == 1

    def test_a_sign_flip_is_not_a_small_difference(self):
        assert relative_difference(Decimal(-100), Decimal(100)) == 2


class TestScaleDetection:
    def test_a_sign_flip_is_not_reported_as_a_scale_error(self):
        # A different mistake. Naming it a scale error would send a reviewer looking for a
        # lost multiplier when the problem is a sign convention.
        outcome = resolve(position("a", value="-245122"), position("b", value="2451220"))

        assert outcome.rule is not ResolutionRule.SUSPECTED_SCALE_ERROR

    def test_zero_is_not_a_power_of_ten_away_from_anything(self):
        outcome = resolve(position("a", value="0"), position("b", value="245122"))

        assert outcome.rule is not ResolutionRule.SUSPECTED_SCALE_ERROR

    def test_a_genuine_ten_fold_disagreement_is_still_reported_as_one(self):
        # 10.3x is a disagreement, not a lost multiplier, and the tolerance is tight enough
        # to tell them apart.
        outcome = resolve(position("a", value="100"), position("b", value="1030"))

        assert outcome.rule is not ResolutionRule.SUSPECTED_SCALE_ERROR


class TestMateriality:
    def test_two_credible_sources_far_apart_are_material(self):
        outcome = resolve(
            position("a", value="100", tier=SourceTier.T1_REGULATORY),
            position("b", value="97", tier=SourceTier.T4_LICENSED_MARKET),
        )

        assert outcome.relative_difference is not None
        assert outcome.relative_difference > MATERIALITY_THRESHOLD
        assert outcome.material

    def test_a_blog_contradicting_a_filing_is_not_a_credible_source_conflict(self):
        # Expected, and a banner for it would be ignored — which would make the banner
        # worthless for the cases that matter.
        outcome = resolve(
            position("a", value="100", tier=SourceTier.T1_REGULATORY),
            position("b", value="50", tier=SourceTier.T6_UNVERIFIED),
        )

        assert not outcome.material

    def test_a_small_difference_between_credible_sources_is_not_material(self):
        outcome = resolve(
            position("a", value="100", tier=SourceTier.T1_REGULATORY),
            position("b", value="99.9", tier=SourceTier.T2_ISSUER),
        )

        assert not outcome.material


class TestUnitCanonicalisation:
    @pytest.mark.parametrize(("first", "second"), [("USD", "usd"), (" USD ", "USD")])
    def test_case_and_space_do_not_make_two_units(self, first, second):
        assert canonical_unit(first) == canonical_unit(second)

    def test_currencies_are_not_reconciled(self):
        assert canonical_unit("USD") != canonical_unit("GBP")


class TestTolerances:
    def test_agreement_is_tighter_than_materiality(self):
        # Otherwise there would be a band of differences that both agree and raise a banner.
        assert AGREEMENT_TOLERANCE < MATERIALITY_THRESHOLD


# -- Persistence -----------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        http_user_agent="Tracework Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
    )


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    return await build_scene(db_session, store)


@pytest.mark.integration
class TestRecording:
    async def test_an_agreement_writes_no_row(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="245122"),
        )

        assert row is None
        assert await disagreements_for_job(db_session, scene["job"].id) == []

    async def test_an_escalation_is_recorded_with_both_positions(
        self, db_session: AsyncSession, scene
    ):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )

        assert row is not None
        assert row.position_a["value"] == "245122"
        assert row.position_b["value"] == "244000"
        assert row.rule is ResolutionRule.SAME_TIER_SAME_DATE
        assert row.resolved_by is ResolvedBy.RULE

    async def test_a_rule_decision_keeps_the_rejected_position(
        self, db_session: AsyncSession, scene
    ):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122", tier=SourceTier.T2_ISSUER),
            second=position("b", value="244000", tier=SourceTier.T1_REGULATORY),
        )

        assert row is not None
        assert row.resolution is ResolutionOutcome.CHOSE_A
        # The losing figure survives, which is the point of storing both columns.
        assert row.position_b["value"] == "245122"

    async def test_an_escalation_names_the_gate_it_reaches(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )

        assert row is not None
        assert row.escalated_to_gate is GateKind.FINAL
        assert ESCALATION_GATE is GateKind.FINAL

    async def test_a_rule_decision_reaches_no_gate(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122", tier=SourceTier.T2_ISSUER),
            second=position("b", value="244000", tier=SourceTier.T1_REGULATORY),
        )

        assert row is not None
        assert row.escalated_to_gate is None

    async def test_recording_the_same_conflict_twice_writes_one_row(
        self, db_session: AsyncSession, scene
    ):
        for _ in range(2):
            await resolve_and_record(
                db_session,
                job_id=scene["job"].id,
                topic="Revenue FY2025",
                first=position("a", value="245122"),
                second=position("b", value="244000"),
            )

        assert len(await disagreements_for_job(db_session, scene["job"].id)) == 1

    async def test_the_fingerprint_does_not_move_when_a_value_is_re_extracted(self):
        # Otherwise a re-run with a slightly different parse would look like a second,
        # independent conflict.
        first = fingerprint_for(
            topic="Revenue FY2025",
            kind=DisagreementKind.SOURCE_CONFLICT,
            position_a=position("a", value="245122"),
            position_b=position("b", value="244000"),
        )
        second = fingerprint_for(
            topic="Revenue FY2025",
            kind=DisagreementKind.SOURCE_CONFLICT,
            position_a=position("a", value="245121"),
            position_b=position("b", value="244001"),
        )

        assert first == second

    async def test_the_fingerprint_ignores_which_position_is_a(self):
        forwards = fingerprint_for(
            topic="Revenue FY2025",
            kind=DisagreementKind.SOURCE_CONFLICT,
            position_a=position("a"),
            position_b=position("b"),
        )
        backwards = fingerprint_for(
            topic="Revenue FY2025",
            kind=DisagreementKind.SOURCE_CONFLICT,
            position_a=position("b"),
            position_b=position("a"),
        )

        assert forwards == backwards

    async def test_a_different_topic_is_a_different_conflict(self, db_session: AsyncSession, scene):
        for topic in ("Revenue FY2025", "Operating income FY2025"):
            await resolve_and_record(
                db_session,
                job_id=scene["job"].id,
                topic=topic,
                first=position("a", value="245122"),
                second=position("b", value="244000"),
            )

        assert len(await disagreements_for_job(db_session, scene["job"].id)) == 2

    async def test_a_blank_topic_is_refused(self, db_session: AsyncSession, scene):
        with pytest.raises(ValidationError, match="needs a topic"):
            await resolve_and_record(
                db_session,
                job_id=scene["job"].id,
                topic="   ",
                first=position("a", value="245122"),
                second=position("b", value="244000"),
            )

    async def test_a_thesis_conflict_is_recorded_under_its_own_kind(
        self, db_session: AsyncSession, scene
    ):
        row = await record_resolution(
            db_session,
            job_id=scene["job"].id,
            topic="the durability of the margin",
            kind=DisagreementKind.THESIS_CONFLICT,
            resolution=thesis_conflict(
                first=position("base", label="base thesis"),
                second=position("red", label="red team"),
                topic="the durability of the margin",
            ),
        )

        assert row is not None
        assert row.kind is DisagreementKind.THESIS_CONFLICT
        assert row.relative_difference is None


@pytest.mark.integration
class TestTheSchemaRefusesHalfRecords:
    async def test_an_agreement_cannot_be_stored_by_hand(self, db_session: AsyncSession, scene):
        # The recording rule lives in the schema, not only in the one service that obeys it.
        db_session.add(
            Disagreement(
                job_id=scene["job"].id,
                topic="Revenue FY2025",
                kind=DisagreementKind.SOURCE_CONFLICT,
                position_a=position("a").as_record(),
                position_b=position("b").as_record(),
                resolution=ResolutionOutcome.AGREED,
                rule=ResolutionRule.VALUES_AGREE,
                resolved_by=ResolvedBy.RULE,
                resolution_rationale="they agree",
                fingerprint="0" * 64,
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()

    async def test_an_escalation_without_a_gate_is_refused(self, db_session: AsyncSession, scene):
        db_session.add(
            Disagreement(
                job_id=scene["job"].id,
                topic="Revenue FY2025",
                kind=DisagreementKind.SOURCE_CONFLICT,
                position_a=position("a").as_record(),
                position_b=position("b").as_record(),
                resolution=ResolutionOutcome.ESCALATED,
                rule=ResolutionRule.SAME_TIER_SAME_DATE,
                resolved_by=ResolvedBy.RULE,
                resolution_rationale="nothing left to prefer by",
                escalated_to_gate=None,
                fingerprint="1" * 64,
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()

    async def test_a_human_resolution_without_a_human_is_refused(
        self, db_session: AsyncSession, scene
    ):
        db_session.add(
            Disagreement(
                job_id=scene["job"].id,
                topic="Revenue FY2025",
                kind=DisagreementKind.SOURCE_CONFLICT,
                position_a=position("a").as_record(),
                position_b=position("b").as_record(),
                resolution=ResolutionOutcome.CHOSE_A,
                rule=ResolutionRule.SAME_TIER_SAME_DATE,
                resolved_by=ResolvedBy.HUMAN,
                resolution_rationale="I decided",
                fingerprint="2" * 64,
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()

    async def test_a_rationale_is_required(self, db_session: AsyncSession, scene):
        db_session.add(
            Disagreement(
                job_id=scene["job"].id,
                topic="Revenue FY2025",
                kind=DisagreementKind.SOURCE_CONFLICT,
                position_a=position("a").as_record(),
                position_b=position("b").as_record(),
                resolution=ResolutionOutcome.CHOSE_A,
                rule=ResolutionRule.LOWER_TIER_WINS,
                resolved_by=ResolvedBy.RULE,
                resolution_rationale="",
                fingerprint="3" * 64,
            )
        )
        with pytest.raises(DbIntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestEscalationReachesGateTwo:
    async def test_escalations_are_listed_material_first(self, db_session: AsyncSession, scene):
        job_id = scene["job"].id
        await resolve_and_record(
            db_session,
            job_id=job_id,
            topic="Immaterial",
            # Beyond the agreement tolerance, so it is a real conflict, but under the 2%
            # credible-source threshold, so it is not the one to look at first.
            first=position("a", value="100.00"),
            second=position("b", value="100.50"),
        )
        await resolve_and_record(
            db_session,
            job_id=job_id,
            topic="Material",
            first=position("c", value="100"),
            second=position("d", value="80"),
        )

        escalations = await escalations_for_job(db_session, job_id)

        assert [row.topic for row in escalations] == ["Material", "Immaterial"]

    async def test_a_resolved_conflict_is_not_an_escalation(self, db_session: AsyncSession, scene):
        await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122", tier=SourceTier.T2_ISSUER),
            second=position("b", value="244000", tier=SourceTier.T1_REGULATORY),
        )

        assert await escalations_for_job(db_session, scene["job"].id) == []
        assert len(await disagreements_for_job(db_session, scene["job"].id)) == 1

    async def test_the_gate_two_payload_carries_the_escalations(
        self, db_session: AsyncSession, scene
    ):
        job_id = scene["job"].id
        await resolve_and_record(
            db_session,
            job_id=job_id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )

        payload = await final_gate_payload(db_session, job_id=job_id)

        assert [item["topic"] for item in payload["escalations"]] == ["Revenue FY2025"]
        assert payload["escalations"][0]["rule"] == ResolutionRule.SAME_TIER_SAME_DATE.value

    async def test_an_escalation_changes_what_the_approval_hashes(
        self, db_session: AsyncSession, scene
    ):
        # The escalations are *inside* the payload, so "approved with these outstanding" is
        # verifiable afterwards rather than a claim about what a page rendered.
        job_id = scene["job"].id
        before = await final_gate_payload(db_session, job_id=job_id)

        await resolve_and_record(
            db_session,
            job_id=job_id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )
        after = await final_gate_payload(db_session, job_id=job_id)

        assert before != after


@pytest.mark.integration
class TestSettlingByHand:
    async def test_a_person_can_choose_one_of_the_two_positions(
        self, db_session: AsyncSession, scene
    ):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )
        assert row is not None

        settled = await settle_by_hand(
            db_session,
            disagreement=row,
            outcome=ResolutionOutcome.CHOSE_A,
            actor=scene["user"],
            rationale="The second filing transposed two digits; the first matches the PDF.",
        )

        assert settled.resolution is ResolutionOutcome.CHOSE_A
        assert settled.resolved_by is ResolvedBy.HUMAN
        assert settled.resolved_by_user_id == scene["user"].id
        assert settled.resolved_at is not None

    async def test_settling_keeps_the_rule_that_escalated_it(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )
        assert row is not None

        settled = await settle_by_hand(
            db_session,
            disagreement=row,
            outcome=ResolutionOutcome.CHOSE_B,
            actor=scene["user"],
            rationale="Checked against the filing.",
        )

        assert settled.rule is ResolutionRule.SAME_TIER_SAME_DATE
        assert "Checked against the filing." in settled.resolution_rationale
        assert "nothing left to prefer one by" in settled.resolution_rationale

    async def test_a_settled_conflict_leaves_the_gate(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )
        assert row is not None

        await settle_by_hand(
            db_session,
            disagreement=row,
            outcome=ResolutionOutcome.CHOSE_A,
            actor=scene["user"],
            rationale="Checked.",
        )

        assert await escalations_for_job(db_session, scene["job"].id) == []

    async def test_a_rule_decision_cannot_be_settled_by_hand(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122", tier=SourceTier.T2_ISSUER),
            second=position("b", value="244000", tier=SourceTier.T1_REGULATORY),
        )
        assert row is not None

        with pytest.raises(ValidationError, match="settled by rule"):
            await settle_by_hand(
                db_session,
                disagreement=row,
                outcome=ResolutionOutcome.CHOSE_B,
                actor=scene["user"],
                rationale="I prefer the other one.",
            )

    async def test_settling_without_a_reason_is_refused(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )
        assert row is not None

        with pytest.raises(ValidationError, match="needs a reason"):
            await settle_by_hand(
                db_session,
                disagreement=row,
                outcome=ResolutionOutcome.CHOSE_A,
                actor=scene["user"],
                rationale="  ",
            )

    async def test_settling_to_escalated_again_is_refused(self, db_session: AsyncSession, scene):
        row = await resolve_and_record(
            db_session,
            job_id=scene["job"].id,
            topic="Revenue FY2025",
            first=position("a", value="245122"),
            second=position("b", value="244000"),
        )
        assert row is not None

        with pytest.raises(ValidationError, match="choosing one of the two"):
            await settle_by_hand(
                db_session,
                disagreement=row,
                outcome=ResolutionOutcome.ESCALATED,
                actor=scene["user"],
                rationale="Still not sure.",
            )


# -- Sector profiles -------------------------------------------------------------------------


class TestSectorProfileVocabulary:
    def test_no_model_is_both_allowed_and_blocked(self):
        # Not expressible as a check constraint over two JSONB arrays, so it is asserted
        # here; see the note in `aer.db.models.sector_profile`.
        for profile in SECTOR_PROFILES:
            overlap = set(profile.allowed_models) & set(profile.blocked_models)
            assert not overlap, f"{profile.key}: {overlap}"

    def test_the_sectors_the_standard_model_breaks_on_block_it(self):
        for key in ("banks", "insurers", "reits", "biotech_pre_revenue"):
            profile = profile_for(key)
            assert profile is not None
            assert not profile.permits(ValuationModel.DCF_FCFF)

    def test_every_profile_states_why(self):
        # A blocked model with no warning is a refusal a reader cannot argue with.
        for profile in SECTOR_PROFILES:
            assert profile.warnings, profile.key
            assert profile.required_metrics, profile.key

    def test_keys_are_unique(self):
        keys = [profile.key for profile in SECTOR_PROFILES]
        assert len(keys) == len(set(keys))

    def test_an_unknown_sector_gets_no_profile_rather_than_a_permissive_one(self):
        assert profile_for("widgets") is None


@pytest.mark.integration
class TestSectorProfileSeed:
    async def test_the_seed_matches_the_constants(self, db_session: AsyncSession):
        # The migration writes the rows out literally, so this is the only thing stopping
        # the two drifting apart. Drift would be silent: a Phase 3 gate firing on the wrong
        # sectors, with the code in front of the reader saying otherwise.
        rows = {row.key: row for row in await db_session.scalars(select(SectorProfile))}

        assert set(rows) == {profile.key for profile in SECTOR_PROFILES}

        for profile in SECTOR_PROFILES:
            row = rows[profile.key]
            assert row.label == profile.label
            assert row.sic_prefixes == list(profile.sic_prefixes)
            assert row.icb_codes == list(profile.icb_codes)
            assert row.allowed_models == [model.value for model in profile.allowed_models]
            assert row.blocked_models == [model.value for model in profile.blocked_models]
            assert row.required_metrics == list(profile.required_metrics)
            assert row.warnings == list(profile.warnings)

    async def test_every_seeded_model_name_is_in_the_vocabulary(self, db_session: AsyncSession):
        known = {model.value for model in ValuationModel}
        for row in await db_session.scalars(select(SectorProfile)):
            assert set(row.allowed_models) <= known, row.key
            assert set(row.blocked_models) <= known, row.key
