"""Briefing an unsettled challenge: what each side costs, and which way it leans.

ADR 0095. Gate 3 asked an operator to choose between two paragraphs of argument with
nothing to compare them by, which is what an operator reported after a live run. The brief
says what each choice assumes and what the report becomes either way, and leans.

Three properties carry the record's promises, and each has a class here: the schema has no
field a figure or a source could arrive in; a brief keyed to a challenge this run does not
hold is dropped rather than trusted; and the briefs move no hash, settle no row and reach
no report -- which is the whole difference between advice beside a decision and a decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.challenge_brief import (
    BRIEF_CEILING,
    MAX_BRIEFS,
    ChallengeBrief,
    ChallengeBriefs,
    ChallengeSide,
)
from aer.agents.registry import resolve_role
from aer.core.disagreement import (
    DisagreementKind,
    ResolutionOutcome,
    ResolutionRule,
    ResolvedBy,
)
from aer.core.enums import GateKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import Disagreement, JobStep
from aer.providers.fake import FakeProvider
from aer.services.challenge_briefs import (
    _unsettled,
    brief_unsettled_challenges,
    briefs_from_output,
)
from aer.storage.local import LocalArtefactStore
from aer.workflow.workflows.vertical_slice_v1 import final_gate_payload
from tests.schema_guard import refuse_unanswerable_schema
from tests.workflow_fixtures import make_provider, seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio


def _challenge(
    job_id: uuid.UUID,
    *,
    severity: int = 4,
    dimension: str = "growth",
    statement: str = "The growth claim outruns the recorded fact.",
    outcome: ResolutionOutcome = ResolutionOutcome.ESCALATED,
    settled_by: ResolvedBy = ResolvedBy.RULE,
    settled_by_user: uuid.UUID | None = None,
) -> Disagreement:
    """One red-team challenge in the shape `services.red_team` records them."""
    return Disagreement(
        job_id=job_id,
        topic=f"Red team ({dimension}): {statement}",
        kind=DisagreementKind.THESIS_CONFLICT,
        position_a={"label": "Base thesis"},
        position_b={"label": f"Red team challenge ({dimension}, severity {severity}/5)"},
        resolution=outcome,
        rule=ResolutionRule.THESIS_CONFLICT,
        resolved_by=settled_by,
        resolution_rationale="Escalated to the final gate.",
        detail={
            "challenge": statement,
            "basis": "It contradicts the fact it rests on.",
            "severity": severity,
            "dimension": dimension,
            "claims": [],
            "sections": [],
            "evidence": {"facts": [], "calculations": [], "sources": []},
        },
        # The row's own constraints: a settled disagreement points at no gate, and a
        # human resolution names the human.
        escalated_to_gate=GateKind.FINAL if outcome is ResolutionOutcome.ESCALATED else None,
        resolved_by_user_id=settled_by_user,
        resolved_at=datetime.now(UTC) if settled_by_user is not None else None,
        material=severity >= 4,
        fingerprint=sha256_hex(f"{dimension}:{severity}:{statement}"),
    )


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    await db_session.flush()
    step = JobStep(
        job_id=job.id,
        step_key="brief_challenges",
        sequence=1,
        idempotency_key=f"brief-{uuid.uuid4()}",
        input_hash="0" * 64,
    )
    db_session.add(step)
    await db_session.flush()
    return {
        "session": db_session,
        "user": user,
        "request": request,
        "job": job,
        "step": step,
        "store": LocalArtefactStore(tmp_path, max_bytes=1_000_000),
    }


def _context(scene: dict[str, Any], provider: Any, settings: Any) -> AgentContext:
    from aer.providers.router import Router  # noqa: PLC0415 -- one construction, one place

    return AgentContext(
        session=scene["session"],
        provider=provider,
        router=Router(settings),
        settings=settings,
        store=scene["store"],
        job_step=scene["step"],
    )


class TestTheSchemaIsTheEnforcement:
    """A type with no column for the forbidden thing (ADR 0074, ADR 0087, ADR 0095)."""

    def test_a_brief_has_nowhere_to_put_a_figure_or_a_source(self) -> None:
        with pytest.raises(PydanticValidationError):
            ChallengeBrief(
                disagreement_id="x",
                keeping_assumes="a",
                keeping_means="b",
                accepting_assumes="c",
                accepting_means="d",
                leans=ChallengeSide.DRAFT,
                because="e",
                source_document_id="s",  # type: ignore[call-arg]
            )

    def test_there_is_no_third_side_to_hide_in(self) -> None:
        """ "Either" would be chosen every time, and the operator would be no better off."""
        assert {side.value for side in ChallengeSide} == {"draft", "challenge"}

    def test_a_field_that_becomes_an_essay_is_refused(self) -> None:
        with pytest.raises(PydanticValidationError):
            ChallengeBrief(
                disagreement_id="x",
                keeping_assumes="a" * (BRIEF_CEILING + 1),
                keeping_means="b",
                accepting_assumes="c",
                accepting_means="d",
                leans=ChallengeSide.DRAFT,
                because="e",
            )

    def test_the_role_is_registered_against_its_record(self) -> None:
        """ADR 0035: the registry is where "a new role needs an ADR" is enforced."""
        definition = resolve_role("challenge_brief")

        assert definition.adr == "0095"
        assert definition.allowed_tools == frozenset()


class TestWhatItIsShownAndWhatItIsNot:
    async def test_a_settled_challenge_is_not_briefed(self, scene: dict[str, Any]) -> None:
        """A lean on a conflict a person already decided is noise arriving late."""
        session: AsyncSession = scene["session"]
        session.add(
            _challenge(
                scene["job"].id,
                outcome=ResolutionOutcome.CHOSE_A,
                settled_by=ResolvedBy.HUMAN,
                settled_by_user=scene["user"].id,
            )
        )
        await session.flush()

        assert await _unsettled(session, job_id=scene["job"].id) == []

    async def test_the_worst_are_briefed_first(self, scene: dict[str, Any]) -> None:
        """The bound is a sitting's worth, so the order is what the bound means."""
        session: AsyncSession = scene["session"]
        for severity, dimension in ((2, "macro"), (5, "valuation"), (4, "growth")):
            session.add(_challenge(scene["job"].id, severity=severity, dimension=dimension))
        await session.flush()

        shown = await _unsettled(session, job_id=scene["job"].id)

        assert [row.dimension for row in shown] == ["valuation", "growth", "macro"]

    async def test_more_than_a_sitting_is_bounded(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        for index in range(MAX_BRIEFS + 3):
            session.add(
                _challenge(scene["job"].id, dimension=f"d{index}", statement=f"Objection {index}.")
            )
        await session.flush()

        assert len(await _unsettled(session, job_id=scene["job"].id)) == MAX_BRIEFS


class TestAKeyTheRunDoesNotHoldIsDropped:
    async def test_a_brief_for_an_unknown_challenge_is_not_stored(
        self, scene: dict[str, Any], workflow_settings: Any
    ) -> None:
        """The same rule the red team's own claim attribution follows."""
        session: AsyncSession = scene["session"]
        session.add(_challenge(scene["job"].id))
        await session.flush()

        def invented(schema: type) -> Any:
            return ChallengeBriefs(
                briefs=[
                    ChallengeBrief(
                        disagreement_id=str(uuid.uuid4()),
                        keeping_assumes="a",
                        keeping_means="b",
                        accepting_assumes="c",
                        accepting_means="d",
                        leans=ChallengeSide.DRAFT,
                        because="e",
                    )
                ]
            )

        provider = FakeProvider(invented, inspect_schema=refuse_unanswerable_schema)
        outcome = await brief_unsettled_challenges(
            _context(scene, provider, workflow_settings),
            session,
            job_id=scene["job"].id,
            request=scene["request"],
        )

        assert outcome.dropped == 1
        assert outcome.briefs == {}
        assert outcome.written is False

    async def test_no_challenges_makes_no_call_at_all(
        self, scene: dict[str, Any], workflow_settings: Any
    ) -> None:
        """The commonest shape of a clean run, and a briefing would be empty boxes."""
        provider = make_provider()

        outcome = await brief_unsettled_challenges(
            _context(scene, provider, workflow_settings),
            scene["session"],
            job_id=scene["job"].id,
            request=scene["request"],
        )

        assert outcome.written is False
        assert provider.calls == []


class TestItIsAdviceBesideADecisionAndNotOne:
    async def test_the_briefs_are_not_on_the_disagreement_row(
        self, scene: dict[str, Any], workflow_settings: Any
    ) -> None:
        """`detail` is inside the approval hash. A brief written there would be approved."""
        session: AsyncSession = scene["session"]
        session.add(_challenge(scene["job"].id))
        await session.flush()

        await brief_unsettled_challenges(
            _context(scene, make_provider(), workflow_settings),
            session,
            job_id=scene["job"].id,
            request=scene["request"],
        )

        row = await session.scalar(
            select(Disagreement).where(Disagreement.job_id == scene["job"].id)
        )
        assert row is not None
        assert "leans" not in (row.detail or {})
        assert "keeping_assumes" not in (row.detail or {})

    async def test_the_gate_two_payload_does_not_move(
        self, scene: dict[str, Any], workflow_settings: Any
    ) -> None:
        """The load-bearing one: an approval taken before a briefing is still an approval."""
        session: AsyncSession = scene["session"]
        session.add(_challenge(scene["job"].id))
        await session.flush()
        before = canonical_json(await final_gate_payload(session, job_id=scene["job"].id))

        await brief_unsettled_challenges(
            _context(scene, make_provider(), workflow_settings),
            session,
            job_id=scene["job"].id,
            request=scene["request"],
        )

        after = canonical_json(await final_gate_payload(session, job_id=scene["job"].id))
        assert before == after


class TestReadingBackWhatWasStored:
    def test_a_run_from_before_this_existed_reads_as_no_briefs(self) -> None:
        """A page that raised on an older step output would take the gate down with it."""
        assert briefs_from_output({"written": True}) == {}
        assert briefs_from_output(None) == {}
        assert briefs_from_output({"briefs": "not a mapping"}) == {}

    def test_the_stored_shape_comes_back_keyed_by_challenge(self) -> None:
        stored = {"briefs": {"abc": {"leans": "draft"}, "bad": "not a mapping"}}

        assert briefs_from_output(stored) == {"abc": {"leans": "draft"}}
