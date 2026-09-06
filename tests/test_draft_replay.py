"""`aer replay-draft`: a run's archived section replies, read back under today's rules.

The confirmation run lost three sections to two rules that were then changed (ADR 0097's
amendment, ADR 0109), and the only proof on offer was another £10 run. These tests hold the
replay to the record: an archived reply is identified by the section it was asked for,
parsed the way the provider parsed it, and held to `validate_draft` and the agreement metric
as they stand — and a reply the run refused then is shown passing now, or not. A refused
reply is then handed to the salvage the draft step would hand its last attempt to, so the
readout tells a refusal that costs an edit from one that costs the section, and the sections
are rolled up the way the attempt loop reads them.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.contract_schema import draft_model_for
from aer.agents.custom_section import CustomSectionDraft, ProposedClaim
from aer.agents.section_writer import SectionDraft
from aer.cli import _print_draft_replay
from aer.core.enums import JobStatus
from aer.db.models import AgentRun, JobStep
from aer.errors import AerError
from aer.sections.registry import sections_for_job
from aer.sections.writing import execute_builtin_section
from aer.services import draft_replay
from aer.services.artefacts import store_artefact
from aer.services.draft_replay import (
    DraftReplay,
    ReplayedReply,
    ReplayedSection,
    ReplyVerdict,
    SectionOutcome,
    replay_drafts,
)
from tests.test_section_writer import (
    SECTION_KEY,
    _context,
    _good_draft,
    _scripted,
    build_writer_scene,
)

pytestmark = pytest.mark.anyio

ONE_TRANSACTION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    """The writer suite's run — one filed excerpt, one fact, one calculation, the seeded
    spine and a `draft` step to archive under — because a replay is of what that suite
    writes. With the subject resolved, as `acquire` leaves it (ADR 0061): the fact is dealt
    to a section only under the request's company, and a fact-backed claim is the case
    the live run lost."""
    built = await build_writer_scene(db_session, tmp_path)
    built["request"].company_id = built["fact"].company_id
    await db_session.flush()
    return built


# -- Archiving a reply by hand ---------------------------------------------------------------


def _header(section_key: str, title: str = "Cash Flow Analysis") -> str:
    return (
        f"Write the section {title!r} ({section_key}) for MICROSOFT CORP (MSFT), as of "
        "2022-09-30 under point-in-time rules."
    )


async def _archived(
    scene: dict[str, Any],
    *,
    response: dict[str, Any],
    section_key: str | None = SECTION_KEY,
    recorded: str = "end_turn",
    retry: bool = False,
    at: datetime = ONE_TRANSACTION,
    step: JobStep | None = None,
) -> AgentRun:
    """One writer call as the live provider archives it: the wire request, the SDK's dump.

    ``retry`` writes the refusal a second attempt is sent, which is how the replay tells the
    two attempts of one section apart: both are written in one transaction and carry its
    timestamp, which ``at`` pins here rather than leaving to the database's clock. ``step``
    is the run's `draft` step unless a test archives under another.
    """
    turn = [{"type": "text", "text": "Evidence listing.", "cache_control": {"type": "ephemeral"}}]
    if section_key is not None:
        instruction = _header(section_key)
        if retry:
            instruction += (
                "\n\nYour previous draft was refused for these reasons; fix them:\n- Claim 1: ..."
            )
        turn.append({"type": "text", "text": instruction})
    request = {"model": "claude-opus-5", "messages": [{"role": "user", "content": turn}]}
    session: AsyncSession = scene["session"]
    stored_request = await store_artefact(
        session, scene["store"], data=json.dumps(request).encode(), media_type="application/json"
    )
    stored_response = await store_artefact(
        session, scene["store"], data=json.dumps(response).encode(), media_type="application/json"
    )
    run = AgentRun(
        job_step_id=(step or scene["step"]).id,
        agent_role="report_writer",
        provider="anthropic",
        model="claude-opus-5",
        request_payload_ref=stored_request.artefact.id,
        response_payload_ref=stored_response.artefact.id,
        output_tokens=3_912,
        stop_reason=recorded,
        created_at=at,
    )
    session.add(run)
    await session.flush()
    return run


def _reply(draft: SectionDraft, *, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "stop_reason": stop_reason,
        "content": [
            {"type": "thinking", "thinking": "Reading the pack."},
            {"type": "text", "text": draft.model_dump_json()},
        ],
    }


def _draft_on_the_fact(scene: dict[str, Any]) -> SectionDraft:
    """A numeric claim standing on its fact and citing nothing: refused then, sound now."""
    return SectionDraft(
        content={
            "commentary": "Total revenue was $198,270 million for fiscal year 2022.",
            "figures": [],
        },
        claims=[
            ProposedClaim(
                statement="Total revenue was $198,270 million for fiscal year 2022.",
                kind="numeric",
                financial_fact_id=str(scene["fact"].id),
            )
        ],
    )


def _with_a_stray_numeral(scene: dict[str, Any]) -> SectionDraft:
    """A sound draft carrying one sentence the numeral rule refuses: the salvage's case.

    The shape both sections of the first live report were lost over (ADR 0057) — a whole
    billed draft, and one clause the rule had a quarrel with.
    """
    draft = _good_draft(scene)
    draft.content["commentary"] = (
        "Operating cash generation covered the capital programme. "
        "Margins expanded 340 basis points."
    )
    return draft


def _beyond_repair() -> SectionDraft:
    """A draft that is nothing but the refused sentence: removing it leaves no section."""
    return SectionDraft(
        content={"commentary": "Margins expanded by 340 basis points.", "figures": []},
        claims=[],
    )


async def _another_step(scene: dict[str, Any], key: str) -> JobStep:
    step = JobStep(
        job_id=scene["job"].id,
        step_key=key,
        sequence=1,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{scene['job'].id}:{key}",
        input_hash="1" * 64,
        started_at=datetime.now(UTC),
    )
    scene["session"].add(step)
    await scene["session"].flush()
    return step


async def _replayed(scene: dict[str, Any], section_key: str | None = None) -> DraftReplay:
    return await replay_drafts(
        scene["session"],
        scene["store"],
        scene["settings"],
        job_id=scene["job"].id,
        section_key=section_key,
    )


# -- What the replay says ----------------------------------------------------------------------


class TestAReplyTheRunMade:
    async def test_a_reply_that_drafted_replays_as_passing(self, scene: dict[str, Any]) -> None:
        """The fake provider's archive — the value under ``parsed`` — is read as well as the
        live one's, so a run replayed in a test can be replayed again."""
        await execute_builtin_section(
            _context(scene, _scripted([_good_draft(scene)])),
            section=scene["section"],
            request=scene["request"],
            focus="",
        )

        replay = await _replayed(scene)

        assert len(replay.replies) == 1
        reply = replay.replies[0]
        assert reply.section_key == SECTION_KEY
        assert reply.step_key == "draft"
        assert (reply.ordinal, reply.of) == (1, 1)
        assert reply.verdict is ReplyVerdict.PASSES
        assert reply.clean
        assert reply.claims == 1
        assert reply.recorded_stop_reason == "end_turn"
        assert replay.clean == 1

    async def test_a_reply_refused_then_is_read_under_todays_rules(
        self, scene: dict[str, Any]
    ) -> None:
        """The live run's `business_overview`, in miniature: a numeric claim naming a fact row
        and citing no excerpt was refused nine times over. The run recorded the refusal as
        `schema_rejected`; the replay reads the same bytes back and finds them sound."""
        await _archived(
            scene, response=_reply(_draft_on_the_fact(scene)), recorded="schema_rejected"
        )

        reply = (await _replayed(scene)).replies[0]

        assert reply.recorded_stop_reason == "schema_rejected"
        assert reply.verdict is ReplyVerdict.PASSES
        assert reply.claims == 1
        assert reply.output_tokens == 3_912

    async def test_a_reply_breaking_a_standing_rule_is_refused_with_the_reasons(
        self, scene: dict[str, Any]
    ) -> None:
        """Refused as written, and the salvage declines — removing the one sentence would
        leave the section blank — so this is the refusal that costs the section."""
        await _archived(scene, response=_reply(_beyond_repair()))

        reply = (await _replayed(scene)).replies[0]

        assert reply.verdict is ReplyVerdict.REFUSED
        assert any("340" in problem for problem in reply.problems)
        assert reply.repairs == ()
        assert not reply.kept

    async def test_a_refusal_the_salvage_repairs_is_an_edit_not_a_lost_section(
        self, scene: dict[str, Any]
    ) -> None:
        """The draft step's last resort, run on the archived reply: the refused sentence
        goes, the rest conforms, and the readout says so instead of counting a section
        lost that the run would have kept."""
        await _archived(scene, response=_reply(_with_a_stray_numeral(scene)))

        reply = (await _replayed(scene)).replies[0]

        assert reply.verdict is ReplyVerdict.REPAIRED
        assert any("340" in problem for problem in reply.problems)
        assert len(reply.repairs) == 1
        assert "removed" in reply.repairs[0]
        assert reply.claims == 1
        assert reply.kept
        assert not reply.clean

    async def test_a_custom_sections_reply_is_not_salvaged_because_its_path_does_not(
        self, scene: dict[str, Any]
    ) -> None:
        """`execute_custom_section` has no salvage pass, so a skill-origin section's refused
        reply is a lost section — and the replay answers for the path rather than flattering
        it with a repair the run would never make."""
        await _archived(scene, response=_reply(_with_a_stray_numeral(scene)))
        standing = await draft_replay._standing_rules(
            scene["session"],
            scene["settings"],
            job=scene["job"],
            request=scene["request"],
            section=scene["section"],
            pins=[],
        )
        as_custom = replace(
            standing,
            declared=CustomSectionDraft,
            narrowed=draft_model_for(CustomSectionDraft, standing.contract, name=SECTION_KEY),
        )
        [exchange] = await draft_replay._writer_exchanges(
            scene["session"], scene["store"], job_id=scene["job"].id
        )

        reply = draft_replay._replayed(
            exchange, ordinal=1, of=1, standing=as_custom, calculations={}
        )

        assert reply.verdict is ReplyVerdict.REFUSED
        assert reply.repairs == ()

    async def test_a_cited_figure_the_sentence_misstates_is_reported(
        self, scene: dict[str, Any]
    ) -> None:
        """Drafts, and would then fail `validate`: the two outcomes are kept apart because
        the run keeps them apart, and a section that passes one and not the other is the
        seven the live run reported."""
        misstated = SectionDraft(
            content={"commentary": "Revenue compounded strongly.", "figures": []},
            claims=[
                ProposedClaim(
                    statement="Revenue compounded at 25% a year.",
                    kind="numeric",
                    calculation_id=str(scene["calculation"].id),
                )
            ],
        )
        await _archived(scene, response=_reply(misstated))

        replay = await _replayed(scene)
        reply = replay.replies[0]

        assert reply.verdict is ReplyVerdict.PASSES
        assert len(reply.disagreements) == 1
        assert "revenue_cagr" in reply.disagreements[0]
        assert "25" in reply.disagreements[0]
        assert not reply.clean
        assert replay.reported == 1
        assert replay.clean == 0


class TestAReplyThatIsNotADraft:
    async def test_a_truncated_reply_has_no_draft_to_read(self, scene: dict[str, Any]) -> None:
        await _archived(
            scene, response={"stop_reason": "max_tokens", "content": []}, recorded="schema_rejected"
        )

        reply = (await _replayed(scene)).replies[0]

        assert reply.verdict is ReplyVerdict.UNREADABLE
        assert "ran out of room" in reply.problems[0]

    async def test_an_off_schema_reply_says_which_fields(self, scene: dict[str, Any]) -> None:
        off_schema = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"content": {"commentary": 5}, "claims": "no"}'}],
        }
        await _archived(scene, response=off_schema, recorded="schema_rejected")

        reply = (await _replayed(scene)).replies[0]

        assert reply.verdict is ReplyVerdict.UNREADABLE
        assert any("claims" in problem for problem in reply.problems)

    async def test_a_section_whose_rules_cannot_be_rebuilt_costs_only_its_own_replies(
        self, scene: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A skill-origin section whose pin the plan no longer holds has no standing policy
        to replay under. Its replies say so; the other sections' replies are still read."""
        from aer.services import draft_replay  # noqa: PLC0415 -- the module under patch

        async def unbuildable(*args: Any, **kwargs: Any) -> Any:
            message = "The skill pin this section was written under has vanished."
            raise AerError(message)

        monkeypatch.setattr(draft_replay, "_standing_rules", unbuildable)
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)))

        reply = (await _replayed(scene)).replies[0]

        assert reply.verdict is ReplyVerdict.UNIDENTIFIED
        assert "vanished" in reply.problems[0]

    async def test_a_reply_naming_no_section_is_unidentified(self, scene: dict[str, Any]) -> None:
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)), section_key=None)

        reply = (await _replayed(scene)).replies[0]

        assert reply.section_key is None
        assert reply.verdict is ReplyVerdict.UNIDENTIFIED


class TestWhichRepliesAreRead:
    async def test_a_sections_replies_are_numbered_in_the_order_they_were_made(
        self, scene: dict[str, Any]
    ) -> None:
        """Both attempts of one section are archived in one transaction and share its
        timestamp; the retry is the one carrying the refusal, and comes second whatever
        order the rows were written in."""
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)), retry=True)
        await _archived(
            scene, response={"stop_reason": "max_tokens", "content": []}, recorded="schema_rejected"
        )

        replies = (await _replayed(scene)).replies

        assert [(r.ordinal, r.of) for r in replies] == [(1, 2), (2, 2)]
        assert [r.verdict for r in replies] == [ReplyVerdict.UNREADABLE, ReplyVerdict.PASSES]

    async def test_a_later_step_comes_after_an_earlier_one(self, scene: dict[str, Any]) -> None:
        """Across transactions the clock decides: a revision's reply follows the draft's."""
        later = ONE_TRANSACTION + timedelta(minutes=20)
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)), at=later)
        await _archived(
            scene, response={"stop_reason": "max_tokens", "content": []}, recorded="schema_rejected"
        )

        replies = (await _replayed(scene)).replies

        assert [r.verdict for r in replies] == [ReplyVerdict.UNREADABLE, ReplyVerdict.PASSES]

    async def test_the_section_filter_keeps_only_that_sections_replies(
        self, scene: dict[str, Any]
    ) -> None:
        other = next(
            s.section_key
            for s in await sections_for_job(scene["session"], scene["job"].id)
            if s.section_key != SECTION_KEY
        )
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)))
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)), section_key=other)

        replay = await _replayed(scene, section_key=SECTION_KEY)

        assert replay.section_key == SECTION_KEY
        assert [r.section_key for r in replay.replies] == [SECTION_KEY]
        assert len((await _replayed(scene)).replies) == 2

    async def test_a_section_the_run_lacks_is_refused(self, scene: dict[str, Any]) -> None:
        with pytest.raises(AerError, match="has no section 'no_such_section'"):
            await _replayed(scene, section_key="no_such_section")

    async def test_a_run_that_does_not_exist_is_refused(self, scene: dict[str, Any]) -> None:
        with pytest.raises(AerError, match="No run"):
            await replay_drafts(
                scene["session"], scene["store"], scene["settings"], job_id=uuid.uuid4()
            )

    async def test_a_run_with_no_writer_replies_replays_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        assert (await _replayed(scene)).replies == ()


# -- How the sections come out ----------------------------------------------------------------


class TestHowTheSectionsComeOut:
    """The attempt loop's reading, from the verdicts: the first reply that passes, else the
    last readable one if the salvage repairs it, else lost."""

    async def test_the_first_passing_reply_drafts_the_section(self, scene: dict[str, Any]) -> None:
        await _archived(scene, response=_reply(_beyond_repair()))
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)), retry=True)

        [section] = (await _replayed(scene)).sections

        assert section == ReplayedSection(SECTION_KEY, SectionOutcome.DRAFTS, at_reply=2, of=2)

    async def test_a_section_no_reply_passes_stands_on_the_last_readable_one_repaired(
        self, scene: dict[str, Any]
    ) -> None:
        await _archived(scene, response=_reply(_beyond_repair()))
        await _archived(scene, response=_reply(_with_a_stray_numeral(scene)), retry=True)

        replay = await _replayed(scene)
        [section] = replay.sections

        assert section.outcome is SectionOutcome.REPAIRED
        assert (section.at_reply, section.of) == (2, 2)
        assert "removed" in section.repairs[0]
        assert replay.sections_counted(SectionOutcome.REPAIRED) == 1
        assert replay.sections_counted(SectionOutcome.LOST) == 0

    async def test_a_section_the_salvage_cannot_keep_is_lost_with_its_last_reasons(
        self, scene: dict[str, Any]
    ) -> None:
        await _archived(scene, response=_reply(_with_a_stray_numeral(scene)))
        await _archived(scene, response=_reply(_beyond_repair()), retry=True)

        [section] = (await _replayed(scene)).sections

        assert section.outcome is SectionOutcome.LOST
        assert (section.at_reply, section.of) == (2, 2)
        assert any("340" in problem for problem in section.problems)

    async def test_the_last_candidate_is_the_last_reply_that_parsed(
        self, scene: dict[str, Any]
    ) -> None:
        """A retry that ran out of room leaves no candidate; the loop salvages the attempt
        before it, exactly as `execute_builtin_section` keeps `last_candidate`."""
        await _archived(scene, response=_reply(_with_a_stray_numeral(scene)))
        await _archived(
            scene,
            response={"stop_reason": "max_tokens", "content": []},
            recorded="schema_rejected",
            retry=True,
        )

        [section] = (await _replayed(scene)).sections

        assert section.outcome is SectionOutcome.REPAIRED
        assert (section.at_reply, section.of) == (1, 2)

    async def test_a_section_with_no_readable_reply_is_lost(self, scene: dict[str, Any]) -> None:
        await _archived(
            scene, response={"stop_reason": "max_tokens", "content": []}, recorded="schema_rejected"
        )

        [section] = (await _replayed(scene)).sections

        assert section.outcome is SectionOutcome.LOST
        assert "ran out of room" in section.problems[0]

    async def test_a_revisions_reply_never_decides_a_section(self, scene: dict[str, Any]) -> None:
        """A refused revision leaves the approved draft standing (ADR 0098): the reply is
        read and shown, and counts for nothing in the roll-up."""
        revise = await _another_step(scene, "revise")
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)))
        await _archived(
            scene,
            response=_reply(_beyond_repair()),
            at=ONE_TRANSACTION + timedelta(minutes=20),
            step=revise,
        )

        replay = await _replayed(scene)

        assert [r.step_key for r in replay.replies] == ["draft", "revise"]
        assert [r.verdict for r in replay.replies] == [ReplyVerdict.PASSES, ReplyVerdict.REFUSED]
        assert [s.outcome for s in replay.sections] == [SectionOutcome.DRAFTS]

    async def test_a_reply_naming_no_section_is_in_no_roll_up(self, scene: dict[str, Any]) -> None:
        await _archived(scene, response=_reply(_draft_on_the_fact(scene)), section_key=None)

        assert (await _replayed(scene)).sections == ()


# -- The readout -------------------------------------------------------------------------------


def test_the_readout_says_what_each_reply_met(capsys: pytest.CaptureFixture[str]) -> None:
    job_id = uuid.uuid4()
    replay = DraftReplay(
        job_id=job_id,
        section_key=None,
        replies=(
            ReplayedReply(
                section_key="business_overview",
                step_key="draft",
                ordinal=1,
                of=2,
                model="claude-opus-5",
                output_tokens=3_912,
                recorded_stop_reason="schema_rejected",
                verdict=ReplyVerdict.PASSES,
                claims=14,
            ),
            ReplayedReply(
                section_key="business_overview",
                step_key="draft",
                ordinal=2,
                of=2,
                model="claude-opus-5",
                output_tokens=4_010,
                recorded_stop_reason="end_turn",
                verdict=ReplyVerdict.REFUSED,
                problems=("Claim 3: A numeric claim names exactly one figure.",),
                disagreements=("business_overview/quick_ratio#1 cites ... and states 0.93",),
            ),
            ReplayedReply(
                section_key="capital_allocation",
                step_key="draft",
                ordinal=1,
                of=1,
                model="claude-opus-5",
                output_tokens=3_300,
                recorded_stop_reason="end_turn",
                verdict=ReplyVerdict.REPAIRED,
                problems=("The content carries numeral(s) 2.4 which no numeric claim ...",),
                repairs=("One or more sentences were removed because ...",),
                claims=9,
            ),
        ),
    )

    _print_draft_replay(replay)
    out = capsys.readouterr().out

    assert "3 archived section reply(ies)" in out
    assert "reply 1 of 2" in out
    assert "recorded then: schema_rejected" in out
    assert "PASSES under today's rules: 14 claim(s)" in out
    assert "REFUSED — 1 problem(s):" in out
    assert "- Claim 3: A numeric claim names exactly one figure." in out
    assert "would report 1 disagreement(s)" in out
    assert "REPAIRED — refused for 1 problem(s), then kept by the salvage with 1 edit(s)" in out
    assert "+ One or more sentences were removed because ..." in out
    assert "business_overview — drafts at reply 1 of 2" in out
    assert "capital_allocation — drafts after repair at reply 1 of 1, 1 edit(s):" in out
    assert (
        "Summary: 1 of 3 replies clean; 1 repaired by the salvage; "
        "0 reported by cited_figure_agreement; 1 refused beyond repair"
    ) in out
    assert "Sections: 2 of 2 would draft (1 as written, 1 after repair); 0 lost." in out


def test_the_readout_names_a_lost_section(capsys: pytest.CaptureFixture[str]) -> None:
    replay = DraftReplay(
        job_id=uuid.uuid4(),
        section_key="capital_allocation",
        replies=(
            ReplayedReply(
                section_key="capital_allocation",
                step_key="draft",
                ordinal=1,
                of=1,
                model="claude-opus-5",
                output_tokens=3_300,
                recorded_stop_reason="end_turn",
                verdict=ReplyVerdict.REFUSED,
                problems=("The content carries numeral(s) 2.4 ...", "At most 1 is allowed ..."),
            ),
        ),
    )

    _print_draft_replay(replay)
    out = capsys.readouterr().out

    assert "capital_allocation — LOST: reply 1 of 1 is refused and the salvage declines" in out
    assert "2 problem(s), listed above" in out
    assert "Sections: 0 of 1 would draft (0 as written, 0 after repair); 1 lost." in out
