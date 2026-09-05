"""`aer replay-draft`: a run's archived section replies, read back under today's rules.

The confirmation run lost three sections to two rules that were then changed (ADR 0097's
amendment, ADR 0109), and the only proof on offer was another £10 run. These tests hold the
replay to the record: an archived reply is identified by the section it was asked for,
parsed the way the provider parsed it, and held to `validate_draft` and the agreement metric
as they stand — and a reply the run refused then is shown passing now, or not.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.custom_section import ProposedClaim
from aer.agents.section_writer import SectionDraft
from aer.cli import _print_draft_replay
from aer.db.models import AgentRun
from aer.errors import AerError
from aer.sections.registry import sections_for_job
from aer.sections.writing import execute_builtin_section
from aer.services.artefacts import store_artefact
from aer.services.draft_replay import DraftReplay, ReplayedReply, ReplyVerdict, replay_drafts
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
) -> AgentRun:
    """One writer call as the live provider archives it: the wire request, the SDK's dump.

    ``retry`` writes the refusal a second attempt is sent, which is how the replay tells the
    two attempts of one section apart: both are written in one transaction and carry its
    timestamp, which ``at`` pins here rather than leaving to the database's clock.
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
        job_step_id=scene["step"].id,
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
        unsourced = SectionDraft(
            content={"commentary": "Margins expanded by 340 basis points.", "figures": []},
            claims=[],
        )
        await _archived(scene, response=_reply(unsourced))

        reply = (await _replayed(scene)).replies[0]

        assert reply.verdict is ReplyVerdict.REFUSED
        assert any("340" in problem for problem in reply.problems)

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
        ),
    )

    _print_draft_replay(replay)
    out = capsys.readouterr().out

    assert "2 archived section reply(ies)" in out
    assert "reply 1 of 2" in out
    assert "recorded then: schema_rejected" in out
    assert "PASSES under today's rules: 14 claim(s)" in out
    assert "REFUSED — 1 problem(s):" in out
    assert "- Claim 3: A numeric claim names exactly one figure." in out
    assert "would report 1 disagreement(s)" in out
    assert "Summary: 1 of 2 clean; 0 reported by cited_figure_agreement; 1 refused" in out
