"""`aer rehearse-section`: one built-in section drafted against a finished run's evidence.

The zero-spend replay answers what today's rules make of yesterday's replies; it cannot
say what the writer writes under today's prompts, and the confirmation run paid £10.82 to
find that out. A rehearsal answers it for one section at about thirty pence. These tests
hold it to the three properties the skill dry run has: the real execution path, on a job of
its own that never writes into the run it reads, metered and capped like everything else.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.section_writer import SectionDraft
from aer.cli import _print_rehearsal
from aer.core.enums import JobStatus
from aer.db.models import AgentRun, Cost, Job, JobStep, ReportSection, ResearchPlan, SectionStatus
from aer.providers.router import Router
from aer.sections.evidence import word_ceiling
from aer.sections.registry import resolve_sections
from aer.services.draft_replay import ReplyVerdict, replay_drafts
from aer.services.section_rehearsal import (
    REHEARSAL_ESTIMATE_GBP,
    REHEARSAL_STEP,
    REHEARSAL_WORKFLOW,
    RehearsalOutcome,
    RehearsalRefusedError,
    rehearse_section,
)
from tests.test_section_writer import SECTION_KEY, _good_draft, _scripted, build_writer_scene

pytestmark = pytest.mark.anyio


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    """The writer suite's run, with the subject resolved so the fact is dealt (ADR 0061)."""
    built = await build_writer_scene(db_session, tmp_path)
    built["request"].company_id = built["fact"].company_id
    await db_session.flush()
    return built


def _beyond_repair() -> SectionDraft:
    return SectionDraft(
        content={"commentary": "Margins expanded by 340 basis points.", "figures": []},
        claims=[],
    )


async def _rehearsed(scene: dict[str, Any], provider: Any, key: str = SECTION_KEY) -> Any:
    return await rehearse_section(
        scene["session"],
        section_key=key,
        source_job=scene["job"],
        settings=scene["settings"],
        provider=provider,
        router=Router(scene["settings"]),
        store=scene["store"],
    )


# -- The real execution path -----------------------------------------------------------------


class TestWhatARehearsalProduces:
    async def test_a_sound_draft_is_a_generated_section_with_its_numbers(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _rehearsed(scene, _scripted([_good_draft(scene)]))

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.section_key == SECTION_KEY
        assert outcome.title
        assert outcome.attempts == 1
        assert outcome.claims_recorded == 1
        assert outcome.words > 0
        assert outcome.word_ceiling == word_ceiling(outcome.word_budget)
        assert outcome.problems == []
        assert outcome.refusal_causes == {}
        assert outcome.evidence_dealt == {"facts": 1, "calculations": 1, "excerpts": 1}
        assert "capital programme" in outcome.markdown
        assert outcome.estimated_cost_gbp == REHEARSAL_ESTIMATE_GBP

    async def test_a_draft_refused_twice_is_a_failed_section_with_its_reasons(
        self, scene: dict[str, Any]
    ) -> None:
        """The number the rehearsal is for: how the writer fares under today's rules,
        including the causes counted across both attempts."""
        outcome = await _rehearsed(scene, _scripted([_beyond_repair(), _beyond_repair()]))

        assert outcome.status is SectionStatus.FAILED
        assert outcome.attempts == 2
        assert any("340" in problem for problem in outcome.problems)
        assert sum(outcome.refusal_causes.values()) >= 2
        assert outcome.claims_recorded == 0

    async def test_the_salvage_runs_and_its_edit_is_reported(self, scene: dict[str, Any]) -> None:
        stray = _good_draft(scene)
        stray.content["commentary"] = (
            "Operating cash generation covered the capital programme. "
            "Margins expanded 340 basis points."
        )

        outcome = await _rehearsed(scene, _scripted([stray, stray]))

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.edits is not None
        assert "removed" in outcome.edits
        assert "340" not in outcome.markdown

    async def test_the_source_runs_focus_and_guidance_are_the_brief(
        self, scene: dict[str, Any]
    ) -> None:
        """A rehearsal writes to the brief the run wrote to: the plan's approved focus
        line for the section reaches the writer's turn."""
        session: AsyncSession = scene["session"]
        plan = ResearchPlan(
            request_id=scene["request"].id,
            workflow_version="vertical_slice_v1",
            plan={
                "summary": "The approved plan",
                "sections": [{"key": SECTION_KEY, "focus": "Cash conversion against capex."}],
            },
            planned_sources=[],
            known_risks=[],
            estimated_cost_gbp=Decimal("9.31"),
            estimated_runtime_seconds=0,
        )
        session.add(plan)
        await session.flush()
        scene["job"].plan_id = plan.id
        provider = _scripted([_good_draft(scene)])

        await _rehearsed(scene, provider)

        assert "Cash conversion against capex." in json.dumps(provider.calls[-1], default=str)


# -- A job of its own ------------------------------------------------------------------------


class TestItWritesOnlyIntoItsOwnJob:
    async def test_the_rehearsal_is_its_own_marked_job_step_and_section(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        outcome = await _rehearsed(scene, _scripted([_good_draft(scene)]))

        job = await session.get(Job, outcome.job_id)
        assert job is not None
        assert job.id != scene["job"].id
        assert job.workflow_version == REHEARSAL_WORKFLOW
        assert job.status is JobStatus.SUCCEEDED
        assert job.plan_id is not None
        step = await session.scalar(select(JobStep).where(JobStep.job_id == job.id))
        assert step is not None
        assert step.step_key == REHEARSAL_STEP
        assert step.status is JobStatus.SUCCEEDED
        sections = list(
            await session.scalars(select(ReportSection).where(ReportSection.job_id == job.id))
        )
        assert [row.section_key for row in sections] == [SECTION_KEY]
        assert sections[0].status is SectionStatus.GENERATED

    async def test_the_source_run_is_read_and_never_written(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        source_id = scene["job"].id
        before = await session.scalar(
            select(func.count()).select_from(ReportSection).where(ReportSection.job_id == source_id)
        )

        await _rehearsed(scene, _scripted([_good_draft(scene)]))

        after = await session.scalar(
            select(func.count()).select_from(ReportSection).where(ReportSection.job_id == source_id)
        )
        assert after == before
        assert scene["section"].status is SectionStatus.PENDING
        assert scene["section"].content is None
        source_calls = await session.scalar(
            select(func.count())
            .select_from(AgentRun)
            .join(JobStep, JobStep.id == AgentRun.job_step_id)
            .where(JobStep.job_id == source_id)
        )
        assert source_calls == 0

    async def test_its_replies_are_archived_and_replayable(self, scene: dict[str, Any]) -> None:
        """The rehearsal's own record is a run's: `aer replay-draft <rehearsal job>` reads
        the archived reply back under whatever rule changes next, at no spend."""
        outcome = await _rehearsed(scene, _scripted([_good_draft(scene)]))

        replay = await replay_drafts(
            scene["session"], scene["store"], scene["settings"], job_id=outcome.job_id
        )

        assert len(replay.replies) == 1
        assert replay.replies[0].section_key == SECTION_KEY
        assert replay.replies[0].verdict is ReplyVerdict.PASSES


# -- Metered and capped ----------------------------------------------------------------------


class TestItIsMeteredAndCapped:
    async def test_the_spend_is_metered_under_the_rehearsals_job(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        outcome = await _rehearsed(scene, _scripted([_good_draft(scene)]))

        rows = list(await session.scalars(select(Cost).where(Cost.job_id == outcome.job_id)))
        assert rows, "a model call that wrote no cost row"
        assert outcome.cost_gbp == sum((row.amount_gbp for row in rows), Decimal(0))
        job = await session.get(Job, outcome.job_id)
        assert job is not None
        # The job's column carries four places; the ledger and the readout carry six.
        assert abs(job.total_cost_gbp - outcome.cost_gbp) < Decimal("0.0001")

    async def test_a_cap_the_estimate_would_break_refuses_before_the_call(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        scene["request"].work_order.max_cost_gbp = Decimal("0.10")
        await session.flush()
        provider = _scripted([_good_draft(scene)])

        with pytest.raises(RehearsalRefusedError, match="projected to cost"):
            await _rehearsed(scene, provider)

        assert provider.calls == []
        refused = await session.scalar(
            select(Job).where(Job.workflow_version == REHEARSAL_WORKFLOW)
        )
        assert refused is not None
        assert refused.status is JobStatus.BUDGET_EXCEEDED


# -- What cannot be rehearsed ----------------------------------------------------------------


class TestWhatItRefuses:
    async def test_an_unknown_key_names_the_sections_there_are(self, scene: dict[str, Any]) -> None:
        with pytest.raises(RehearsalRefusedError, match="No section is keyed 'no_such'") as caught:
            await _rehearsed(scene, _scripted([]), key="no_such")
        assert SECTION_KEY in str(caught.value)

    async def test_a_platform_filled_section_has_nothing_to_rehearse(
        self, scene: dict[str, Any]
    ) -> None:
        deterministic = [
            d
            for d in await resolve_sections(scene["session"], request=scene["request"])
            if d.token_budget == 0
        ]
        if not deterministic:
            pytest.skip("this spine seeds no platform-filled section")

        with pytest.raises(RehearsalRefusedError, match="filled by the platform"):
            await _rehearsed(scene, _scripted([]), key=deterministic[0].key)


# -- The readout -------------------------------------------------------------------------------


def test_the_readout_says_the_numbers_and_shows_the_section(
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = RehearsalOutcome(
        job_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        source_job_id=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        section_key="capital_allocation",
        title="Capital Allocation",
        status=SectionStatus.GENERATED,
        attempts=2,
        claims_recorded=14,
        words=702,
        word_budget=711,
        word_ceiling=888,
        problems=[],
        refusal_causes={"length": 1},
        edits="Shortened to fit the length allotted to this section.",
        confidence=0.55,
        insufficient_evidence=False,
        evidence_truncated=False,
        evidence_dealt={"facts": 12, "calculations": 9, "excerpts": 6},
        markdown="## Capital Allocation\n\nBuybacks continued.[^1]\n",
        footnote_count=1,
        cost_gbp=Decimal("0.2134"),
        estimated_cost_gbp=Decimal("0.60"),
    )

    _print_rehearsal(outcome)
    out = capsys.readouterr().out

    assert "capital_allocation (Capital Allocation) against run 22222222" in out
    assert "£0.2134 spent against an estimate of £0.60" in out
    assert "generated after 2 attempt(s); 14 claim(s) recorded" in out
    assert "702 words against a budget of 711 (refused past 888)" in out
    assert "12 fact(s), 9 calculation(s), 6 excerpt(s)" in out
    assert "refusals across the attempts: length x1" in out
    assert "Shortened to fit" in out
    assert "confidence 0.55" in out
    assert "Buybacks continued." in out
    assert "replay-draft 11111111-1111-4111-8111-111111111111" in out


def test_the_readout_of_a_failed_section_keeps_the_reasons_and_no_prose(
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = RehearsalOutcome(
        job_id=uuid.uuid4(),
        source_job_id=uuid.uuid4(),
        section_key="capital_allocation",
        title="Capital Allocation",
        status=SectionStatus.FAILED,
        attempts=2,
        claims_recorded=0,
        words=0,
        word_budget=711,
        word_ceiling=888,
        problems=["The content carries numeral(s) 2.4 which no numeric claim resolves."],
        refusal_causes={"numeral": 2},
        edits=None,
        confidence=None,
        insufficient_evidence=False,
        evidence_truncated=False,
        evidence_dealt=None,
        markdown="",
        footnote_count=0,
        cost_gbp=Decimal("0.41"),
        estimated_cost_gbp=Decimal("0.60"),
    )

    _print_rehearsal(outcome)
    out = capsys.readouterr().out

    assert "failed after 2 attempt(s)" in out
    assert "- The content carries numeral(s) 2.4" in out
    assert "as the report would carry it" not in out
