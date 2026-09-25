"""ADR 0135: the report argues both sides and takes neither, and code prices the argument.

The operator decided on 25 September 2026 that the report should not take sides: it sets out
the case for and the case against, and the view is theirs, in a thesis and a decision. What
is under test is what keeps that honest. A point may turn on one lever, and a lever's value is
already on the record — the model names it and writes no figure. Code strikes the report's own
model with that one input moved, records it as a perturbation nothing reads as the base case,
and prints what it gives beside the case.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.section_writer import SectionDraft
from aer.calc.dcf import ARGUED_CASE, PERTURBATION_CASES
from aer.config import Settings
from aer.core.cases import (
    DRIVERS,
    LEVER_FIELD,
    PRICED_FOR_FIELD,
    Anchor,
    LeverKey,
    Side,
    case_problems,
    levers_named,
)
from aer.core.enums import JobStatus
from aer.db.models import Calculation, JobStep, ReportSection, SectionDefinition, SectionStatus
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.render.view import view_content
from aer.sections.deterministic import (
    AUGMENTERS,
    CASES_KEY,
    price_drafted_cases,
    stored_fields,
)
from aer.sections.render import markdown_lines, render_section
from aer.sections.writing import execute_builtin_section
from aer.services.assumptions import assumptions_for_request, confirm, propose
from aer.services.calculations import indexed_calculations, lineage
from aer.services.cases import (
    LEVERS_BLOCK,
    LeverList,
    LeverOption,
    lever_list,
    price_the_cases,
)
from aer.storage.local import LocalArtefactStore
from tests.valued_run_fixtures import valued_run


def _point(lead_in: str, lever: str = "") -> dict[str, str]:
    point = {"lead_in": lead_in, "text": "An argument from the evidence."}
    if lever:
        point[LEVER_FIELD] = lever
    return point


def _cases(for_points: list[dict[str, str]], against: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "the_question": "Whether the margin the filings show is the margin a buyer pays for.",
        Side.FOR.value: for_points,
        Side.AGAINST.value: against,
    }


OFFERED = {
    "ebit_margin:lowest": "the operating margin at its lowest in the record",
    "revenue_growth:highest": "revenue growth at its fastest in the record",
}


class TestTheVocabulary:
    def test_a_key_round_trips(self) -> None:
        lever = LeverKey("ebit_margin", Anchor.LOWEST)
        assert lever.key == "ebit_margin:lowest"
        assert LeverKey.parse(lever.key) == lever

    def test_a_key_no_run_could_offer_does_not_parse(self) -> None:
        for text in ("ebit_margin", "ebit_margin:36%", "wacc:lowest", "terminal_growth:lowest"):
            assert LeverKey.parse(text) is None, text

    def test_every_driver_has_its_history_and_the_terminals_have_theirs(self) -> None:
        keys = {lever.key for lever in LeverKey.every()}
        assert len(keys) == len(DRIVERS) * 3 + 3
        assert "terminal_growth:exit_multiple_implies" in keys
        assert "terminal_growth:risk_free_rate" in keys
        assert "exit_multiple:growth_implies" in keys

    def test_no_key_carries_a_numeral(self) -> None:
        """What the model writes for a lever is a name. A figure in it would be the one
        number in the report nobody filed, computed or confirmed."""
        assert not any(character.isdigit() for lever in LeverKey.every() for character in lever.key)


class TestWhatADraftMustSatisfy:
    def test_two_cases_a_point_apart_are_weighed_alike(self) -> None:
        content = _cases([_point("A"), _point("B"), _point("C")], [_point("D"), _point("E")])
        assert case_problems(content, offered=OFFERED, none_because="") == []

    def test_a_case_two_points_heavier_has_taken_a_side(self) -> None:
        content = _cases([_point(str(n)) for n in range(4)], [_point("D"), _point("E")])
        problems = case_problems(content, offered=OFFERED, none_because="")
        assert len(problems) == 1
        assert "4 points" in problems[0]
        assert "2 points" in problems[0]

    def test_a_lever_off_the_list_is_refused_with_the_list_named(self) -> None:
        content = _cases(
            [_point("A", "ebit_margin:highest"), _point("B")], [_point("C"), _point("D")]
        )
        problems = case_problems(content, offered=OFFERED, none_because="")
        assert problems == [
            "Point 1 of the case for names the lever 'ebit_margin:highest', which is not on this "
            "run's list. Name one exactly as listed, or none."
        ]

    def test_a_run_with_no_list_refuses_every_lever_and_says_why(self) -> None:
        content = _cases([_point("A", "ebit_margin:lowest"), _point("B")], [_point("C")] * 2)
        problems = case_problems(content, offered={}, none_because="a bank's residual income.")
        assert len(problems) == 1
        assert "a bank's residual income." in problems[0]
        assert "make the point in words" in problems[0]

    def test_two_points_of_one_case_cannot_turn_on_one_lever(self) -> None:
        content = _cases(
            [_point("A", "ebit_margin:lowest"), _point("B", "ebit_margin:lowest")],
            [_point("C"), _point("D")],
        )
        problems = case_problems(content, offered=OFFERED, none_because="")
        assert any("already turns on" in problem for problem in problems)

    def test_the_two_cases_may_argue_over_the_same_lever(self) -> None:
        """The case for and the case against can disagree about one input: that is the
        disagreement a reader most needs priced."""
        content = _cases(
            [_point("A", "ebit_margin:lowest"), _point("B")],
            [_point("C", "ebit_margin:lowest"), _point("D")],
        )
        assert case_problems(content, offered=OFFERED, none_because="") == []

    def test_levers_are_listed_by_side_and_point(self) -> None:
        content = _cases(
            [_point("A"), _point("B", "revenue_growth:highest")],
            [_point("C", "ebit_margin:lowest"), _point("D")],
        )
        assert levers_named(content) == (
            (Side.FOR, 2, "B", "revenue_growth:highest"),
            (Side.AGAINST, 1, "C", "ebit_margin:lowest"),
        )


class TestTheListNeverReachesTheSection:
    def test_the_augmenters_working_is_not_stored(self) -> None:
        block = {LEVERS_BLOCK: {"options": [], "reason": "none"}, "case_for_priced": []}
        assert stored_fields(block) == {"case_for_priced": []}

    def test_the_cases_are_bound_in_the_registry(self) -> None:
        augmenter = AUGMENTERS[CASES_KEY]
        assert augmenter.note is not None
        assert augmenter.standalone is None, "the cases are always argued; code never writes them"


class TestTheRenderedCase:
    def test_a_priced_case_prints_its_levers_in_words_and_never_its_key(self) -> None:
        contract = {
            "type": "object",
            "properties": {
                "case_against": {
                    "type": "array",
                    "title": "The Case Against",
                    "items": {"type": "object", "properties": {}},
                },
                "case_against_priced": {
                    "type": "array",
                    "title": "The Case Against, Priced",
                    "items": {
                        "type": "object",
                        "properties": {
                            "point": {"type": "string"},
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "unit": {"type": "string"},
                            "calculation_id": {"type": "string"},
                        },
                    },
                },
            },
        }
        content = {
            "case_against": [
                {"lead_in": "Growth slows", "text": "The record says so.", "lever": "x:lowest"},
            ],
            "case_against_priced": [
                {
                    "point": "Growth slows",
                    "label": "The exit multiple the perpetuity method implies",
                    "value": "6.4",
                    "unit": "pure",
                    "calculation_id": "a",
                },
                {
                    "point": "",
                    "label": "Value per share by the exit multiple",
                    "value": "198.4",
                    "unit": "USD/shares",
                    "calculation_id": "b",
                },
            ],
        }
        rendered = "\n".join(
            markdown_lines(
                render_section(
                    key=CASES_KEY,
                    title="The Case For and the Case Against",
                    contract=contract,
                    content=content,
                ).fragments
            )
        )
        assert "x:lowest" not in rendered
        assert "| Point | Label | Value |" in rendered
        # The lead-in sits in its own column, so a word in it ("growth") cannot turn a
        # multiple into a percentage: the label, which code wrote, decides the notation.
        assert "6.4\N{MULTIPLICATION SIGN}" in rendered or "6.4x" in rendered
        assert "640%" not in rendered
        assert "$198.40" in rendered


# -- Against a real valued run ---------------------------------------------------------------


@pytest.fixture
async def valued(db_session: AsyncSession) -> dict[str, Any]:
    return await valued_run(db_session)


async def _section(scene: dict[str, Any], content: dict[str, Any]) -> ReportSection:
    """The cases section on the valued run, pinned to the contract migration 0090 seeded."""
    session: AsyncSession = scene["session"]
    definition = await session.scalar(
        select(SectionDefinition)
        .where(SectionDefinition.key == CASES_KEY)
        .order_by(SectionDefinition.version.desc())
    )
    assert definition is not None
    assert "case_for" in definition.output_contract["properties"]
    section = ReportSection(
        job_id=scene["job"].id,
        section_definition_id=definition.id,
        # Set, not left to load: the writer reads it, and a lazy load is IO it cannot do.
        definition=definition,
        section_key=CASES_KEY,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content=content,
    )
    session.add(section)
    await session.flush()
    return section


async def _rows(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Calculation)) or 0)


async def _base_per_share(session: AsyncSession, job_id: Any) -> dict[str, Decimal]:
    rows = await session.scalars(
        select(Calculation).where(
            Calculation.job_id == job_id, Calculation.name == "value_per_share"
        )
    )
    return {
        str(row.parameters["method"]): row.output_value
        for row in rows
        if row.parameters.get("case") == "base"
    }


@pytest.mark.integration
class TestTheListIsTheRecords:
    async def test_every_driver_offers_its_history_and_the_terminals_theirs(
        self, valued: dict[str, Any]
    ) -> None:
        levers = await lever_list(valued["session"], job=valued["job"])

        assert levers.reason == ""
        keys = set(levers.offered())
        for driver in DRIVERS:
            assert f"{driver}:lowest" in keys, driver
        assert "terminal_growth:risk_free_rate" in keys
        assert "exit_multiple:growth_implies" in keys

    async def test_an_observation_is_the_ratio_the_proposal_averages(
        self, valued: dict[str, Any]
    ) -> None:
        """The scene's operating margins are 24%, 25.2% and 26.2%; its confirmed margin is
        25%. The lowest is FY2022's, and the list says so."""
        levers = await lever_list(valued["session"], job=valued["job"])
        lowest = next(o for o in levers.options if o.key == "ebit_margin:lowest")

        assert lowest.value == Decimal("0.24")
        assert lowest.period == "FY2022"
        assert lowest.label == (
            "The operating margin at its lowest in the record, FY2022, in every forecast year"
        )

    async def test_a_lever_that_would_move_nothing_is_not_offered(
        self, valued: dict[str, Any]
    ) -> None:
        """Confirm the margin at its own lowest observation, and that lever moves nothing."""
        session: AsyncSession = valued["session"]
        for row in await assumptions_for_request(session, valued["request"].id):
            if row.name == "ebit_margin":
                replaced = await propose(
                    session,
                    request_id=valued["request"].id,
                    name="ebit_margin",
                    value=Decimal("0.24"),
                    unit="pure",
                    justification="The scene's lowest observed margin.",
                    proposed_by="test",
                )
                await confirm(session, assumption=replaced, actor=valued["user"])

        levers = await lever_list(session, job=valued["job"])
        assert "ebit_margin:lowest" not in levers.offered()

    async def test_the_note_lists_every_lever_with_its_value_and_asks_for_no_figure(
        self, valued: dict[str, Any]
    ) -> None:
        levers = await lever_list(valued["session"], job=valued["job"])
        note = levers.note()

        assert "- ebit_margin:lowest: The operating margin at its lowest" in note
        assert "24%" in note
        assert "write no figure" in note

    async def test_a_run_the_list_cannot_strike_says_why(self, valued: dict[str, Any]) -> None:
        """A bank's residual income has no discounted cash flow to move an input of."""
        session: AsyncSession = valued["session"]
        step = await session.scalar(
            select(JobStep).where(
                JobStep.job_id == valued["job"].id, JobStep.step_key == "propose_assumptions"
            )
        )
        assert step is not None
        step.output_ref = {"valuation_model": "residual_income"}
        await session.flush()

        levers = await lever_list(session, job=valued["job"])

        assert levers.options == ()
        assert "residual income" in levers.reason
        assert "No point may name a lever" in levers.note()

    async def test_the_check_refuses_a_key_the_run_does_not_offer(
        self, valued: dict[str, Any]
    ) -> None:
        augmenter = AUGMENTERS[CASES_KEY]
        block = {LEVERS_BLOCK: (await lever_list(valued["session"], job=valued["job"])).as_block()}

        accepted = _cases(
            [_point("Margins hold", "ebit_margin:highest"), _point("B")],
            [_point("Margins revert", "ebit_margin:lowest"), _point("D")],
        )
        refused = _cases([_point("A", "ebit_margin:median"), _point("B")], [_point("C")] * 2)

        assert augmenter.check(accepted, block) == []
        assert augmenter.check(refused, block)


@pytest.mark.integration
class TestALeverIsStruckAndRecorded:
    async def test_each_lever_prints_its_value_and_both_methods_each_footnoted(
        self, valued: dict[str, Any]
    ) -> None:
        session: AsyncSession = valued["session"]
        section = await _section(
            valued,
            _cases(
                [_point("Margins hold", "ebit_margin:highest"), _point("Growth")],
                [_point("Margins revert", "ebit_margin:lowest"), _point("Debt")],
            ),
        )

        assert await price_the_cases(session, job=valued["job"], section=section)

        against = section.content[Side.AGAINST.priced_field]
        assert [row["label"] for row in against] == [
            "The operating margin at its lowest in the record, FY2022, in every forecast year",
            "Value per share by perpetuity growth",
            "Value per share by the exit multiple",
        ]
        assert [row["point"] for row in against] == ["Margins revert", "", ""]
        assert all(row.get("calculation_id") for row in against)
        for row in against:
            recorded = await session.get(Calculation, row["calculation_id"])
            assert recorded is not None
            # The row keeps every digit; the ledger stores twelve places.
            assert abs(Decimal(row["value"]) - recorded.output_value) < Decimal("1e-11")

    async def test_a_margin_at_its_low_is_worth_less_by_both_methods(
        self, valued: dict[str, Any]
    ) -> None:
        """The lever moved the model, in the direction the argument says it would."""
        session: AsyncSession = valued["session"]
        section = await _section(
            valued,
            _cases([_point("A"), _point("B")], [_point("Low", "ebit_margin:lowest"), _point("C")]),
        )
        await price_the_cases(session, job=valued["job"], section=section)

        base = await _base_per_share(session, valued["job"].id)
        rows = section.content[Side.AGAINST.priced_field]
        argued = {
            "gordon_growth": Decimal(rows[1]["value"]),
            "exit_multiple": Decimal(rows[2]["value"]),
        }
        assert argued["gordon_growth"] < base["gordon_growth"]
        assert argued["exit_multiple"] < base["exit_multiple"]

    async def test_a_struck_answer_is_argued_and_rests_on_the_observation(
        self, valued: dict[str, Any]
    ) -> None:
        session: AsyncSession = valued["session"]
        section = await _section(
            valued,
            _cases([_point("A"), _point("B")], [_point("Low", "ebit_margin:lowest"), _point("C")]),
        )
        await price_the_cases(session, job=valued["job"], section=section)

        rows = section.content[Side.AGAINST.priced_field]
        answer = await session.get(Calculation, rows[1]["calculation_id"])
        assert answer is not None
        assert answer.parameters["case"] == ARGUED_CASE

        walked = (await lineage(session, answer.id)).walk()
        assert rows[0]["calculation_id"] in {node.identifier for node in walked}, (
            "the struck answer does not rest on the observation the table prints beside it"
        )

    async def test_nothing_reads_an_argued_row_as_the_base_case(
        self, valued: dict[str, Any]
    ) -> None:
        session: AsyncSession = valued["session"]
        before = {row.id for row in await indexed_calculations(session, job_id=valued["job"].id)}
        base = await _base_per_share(session, valued["job"].id)
        section = await _section(
            valued,
            _cases([_point("A"), _point("B")], [_point("Low", "ebit_margin:lowest"), _point("C")]),
        )
        await price_the_cases(session, job=valued["job"], section=section)

        after = await indexed_calculations(session, job_id=valued["job"].id, limit=10_000)
        assert not [row for row in after if row.parameters.get("case") in PERTURBATION_CASES]
        assert {row.id for row in after} >= before, "the strike displaced a base-case row"
        assert await _base_per_share(session, valued["job"].id) == base

        view = await view_content(session, job=valued["job"])
        assert not view.content.get("scenarios"), "an argued lever was printed as a scenario"

    async def test_an_unchanged_draft_is_not_struck_twice(self, valued: dict[str, Any]) -> None:
        session: AsyncSession = valued["session"]
        section = await _section(
            valued,
            _cases([_point("A"), _point("B")], [_point("Low", "ebit_margin:lowest"), _point("C")]),
        )
        assert await price_the_cases(session, job=valued["job"], section=section)
        counted = await _rows(session)

        assert not await price_the_cases(session, job=valued["job"], section=section)
        assert await _rows(session) == counted
        assert section.content[PRICED_FOR_FIELD] == ["case_against:1:ebit_margin:lowest"]

        # A refresh passes `force`: its figures are struck again on its own base case.
        assert await price_the_cases(session, job=valued["job"], section=section, force=True)
        assert await _rows(session) > counted

    async def test_a_terminal_lever_takes_the_base_cases_own_recorded_figure(
        self, valued: dict[str, Any]
    ) -> None:
        session: AsyncSession = valued["session"]
        section = await _section(
            valued,
            _cases(
                [_point("Growth lasts", "terminal_growth:exit_multiple_implies"), _point("B")],
                [_point("The multiple is rich", "exit_multiple:growth_implies"), _point("C")],
            ),
        )
        await price_the_cases(session, job=valued["job"], section=section)

        for side, name in (
            (Side.FOR, "implied_terminal_growth"),
            (Side.AGAINST, "implied_exit_multiple"),
        ):
            first = section.content[side.priced_field][0]
            recorded = await session.get(Calculation, first["calculation_id"])
            assert recorded is not None
            assert recorded.name == name
            assert recorded.parameters.get("case") == "base"

    async def test_the_workflow_hook_prices_only_the_cases_contract(
        self, valued: dict[str, Any]
    ) -> None:
        session: AsyncSession = valued["session"]
        await _section(
            valued,
            _cases([_point("A"), _point("B")], [_point("Low", "ebit_margin:lowest"), _point("C")]),
        )

        assert await price_drafted_cases(session, job=valued["job"])

    async def test_a_run_that_cannot_strike_prints_the_case_in_words(
        self, valued: dict[str, Any]
    ) -> None:
        """A lever the list no longer offers — here, the run's model changed under it —
        leaves the point standing in words and its table empty, never a figure from
        nowhere."""
        session: AsyncSession = valued["session"]
        step = await session.scalar(
            select(JobStep).where(
                JobStep.job_id == valued["job"].id, JobStep.step_key == "propose_assumptions"
            )
        )
        assert step is not None
        step.output_ref = {"valuation_model": "residual_income"}
        section = await _section(
            valued,
            _cases([_point("A"), _point("B")], [_point("Low", "ebit_margin:lowest"), _point("C")]),
        )

        await price_the_cases(session, job=valued["job"], section=section)

        assert section.content[Side.AGAINST.priced_field] == []
        assert section.content[Side.FOR.priced_field] == []


def test_a_lever_list_survives_the_block_round_trip() -> None:
    """The block is JSON: the list must come back the same after the writer carries it."""
    levers = LeverList(
        options=(
            LeverOption(
                key="ebit_margin:lowest",
                input="ebit_margin",
                words="the operating margin at its lowest in the record",
                value=Decimal("0.24"),
                period="FY2022",
            ),
        )
    )
    assert LeverList.from_block(levers.as_block()) == levers


@pytest.mark.integration
class TestTheWriterIsShownTheList:
    async def test_a_listed_lever_is_drafted_priced_and_the_list_never_stored(
        self, valued: dict[str, Any], tmp_path: Path
    ) -> None:
        """The whole path a lever takes: the writer is shown the list, names one by key, the
        check accepts it, the stored section keeps the key and not the list, and the draft
        step's hook strikes it once every section is written."""
        session: AsyncSession = valued["session"]
        job = valued["job"]
        step = JobStep(
            job_id=job.id,
            step_key="draft",
            sequence=10,
            status=JobStatus.RUNNING,
            attempt=0,
            idempotency_key=f"{job.id}:draft",
            input_hash="0" * 64,
            started_at=datetime.now(UTC),
        )
        session.add(step)
        await session.flush()
        section = await _section(valued, {})
        settings = Settings(
            http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
        )
        store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)

        shown: list[str] = []

        def answer(_schema: type) -> SectionDraft:
            prompt = json.dumps(provider.calls[-1], default=str)
            shown.append(prompt)
            # Named only if the writer was shown it: a lever the prompt never listed would be
            # the fake inventing one, and the check exists to refuse exactly that.
            key = "ebit_margin:lowest" if "ebit_margin:lowest" in prompt else ""
            return SectionDraft(
                content=_cases(
                    [_point("Margins hold"), _point("Growth lasts")],
                    [_point("Margins revert", key), _point("Debt matters")],
                ),
                claims=[],
            )

        provider = FakeProvider(answer)
        context = AgentContext(
            session=session,
            provider=provider,
            router=Router(settings),
            settings=settings,
            store=store,
            job_step=step,
        )

        execution = await execute_builtin_section(
            context, section=section, request=valued["request"]
        )

        assert execution.status is SectionStatus.GENERATED, execution.problems
        assert any("write no figure" in prompt for prompt in shown), "the list never reached it"
        assert LEVERS_BLOCK not in section.content, "the augmenter's working was stored"
        assert section.content[Side.AGAINST.value][0][LEVER_FIELD] == "ebit_margin:lowest"

        assert await price_drafted_cases(session, job=job)
        assert len(section.content[Side.AGAINST.priced_field]) == 3
