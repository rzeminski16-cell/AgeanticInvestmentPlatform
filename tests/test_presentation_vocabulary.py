"""Every state a person reads has a name, a tone, and only one of each.

The vocabulary is only worth having if it is complete. A mapping that covers seven of eight
statuses is worse than none: the seven read well, nobody notices the eighth is missing, and it
surfaces as `BUDGET_EXCEEDED` on the one screen that state reaches — which is how nineteen raw
enum values came to be in the interface in the first place.

So completeness is a red build rather than a habit. Add a member to any mapped enum without a
label and this fails, naming it, on the commit that adds it.

**The tone assertions are the half that is easy to skip and worth more.** A label makes a state
readable; the tone decides whether the reader thinks the platform is broken. A run stopped at
its cost ceiling rendered in failure red sends an operator looking for a fault that is not
there, and the platform's own console copy already argues the opposite in prose.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import pytest

from aer.core.enums import (
    AnalysisMode,
    Decision,
    GateKind,
    Grade,
    JobStatus,
    PremiseVerdict,
    ProcessQuality,
    RequestStatus,
    SkillKind,
    TransactionKind,
)
from aer.db.models.report_section import SectionStatus
from aer.web import vocabulary
from aer.web.overview.research import GATE_ASKS
from aer.web.portfolio.pages import GRADE_LABELS
from aer.web.vocabulary import (
    ANALYSIS_MODES,
    DECISIONS,
    GATES,
    GRADES,
    JOB_STATES,
    PREMISE_VERDICTS,
    PROCESS_QUALITIES,
    REQUEST_STATES,
    SECTION_STATES,
    SKILL_KINDS,
    TRANSACTION_KINDS,
    GateCertainty,
    HumanState,
    Tone,
)
from aer.workflow.workflows.vertical_slice_v1 import build_steps

# Every enum that reaches a screen, beside the mapping that names it. A new one is added here
# in the same commit that maps it; an enum with no row is an enum nobody decided about.
MAPPED: list[tuple[str, type[StrEnum], dict[Any, HumanState]]] = [
    ("job and step status", JobStatus, JOB_STATES),
    ("request status", RequestStatus, REQUEST_STATES),
    ("section status", SectionStatus, SECTION_STATES),
    ("gate decision", Decision, DECISIONS),
    ("portfolio grade", Grade, GRADES),
    ("transaction kind", TransactionKind, TRANSACTION_KINDS),
    ("skill kind", SkillKind, SKILL_KINDS),
    ("analysis mode", AnalysisMode, ANALYSIS_MODES),
    ("premise verdict", PremiseVerdict, PREMISE_VERDICTS),
    ("process quality", ProcessQuality, PROCESS_QUALITIES),
]


class TestNothingIsMissing:
    @pytest.mark.parametrize(("what", "enum", "mapping"), MAPPED, ids=[m[0] for m in MAPPED])
    def test_every_member_has_a_state(
        self, what: str, enum: type[StrEnum], mapping: dict[Any, HumanState]
    ) -> None:
        missing = sorted(member.value for member in enum if member not in mapping)
        assert not missing, (
            f"These {what} values have no entry in `aer.web.vocabulary`: {missing}. Each will "
            "render as its own enum name on whichever screen reaches it first, which is what "
            "put `AWAITING_APPROVAL` at the top of the run console."
        )

    @pytest.mark.parametrize(("what", "enum", "mapping"), MAPPED, ids=[m[0] for m in MAPPED])
    def test_no_entry_names_a_value_that_is_gone(
        self, what: str, enum: type[StrEnum], mapping: dict[Any, HumanState]
    ) -> None:
        members = set(enum)
        stale = sorted(str(key) for key in mapping if key not in members)
        assert not stale, f"{what} vocabulary names values that no longer exist: {stale}"

    def test_every_gate_has_its_words(self) -> None:
        """A gate with no phrase renders as "the run stopped at a gate and will not continue
        until you decide" — true of every gate, and useless about this one."""
        missing = sorted(gate.value for gate in GateKind if gate not in GATES)
        assert not missing, f"gates with no vocabulary: {missing}"

    def test_every_declared_step_has_a_human_name(self) -> None:
        """The console leads with the step's name in the operator's language.

        A step without one falls back to its technical key — honest, and exactly the state
        this vocabulary exists to end: nineteen technical tokens were the main content of
        the main page of the main tool.
        """
        declared = {step.key for step in build_steps()}
        missing = sorted(declared - set(vocabulary.STEP_WORDS))
        assert not missing, (
            f"These workflow steps have no entry in STEP_WORDS: {missing}. Each renders as "
            "its own key on the console, which is the state the vocabulary exists to end."
        )

    def test_no_step_name_is_its_key(self) -> None:
        retyped = sorted(
            key for key, label in vocabulary.STEP_WORDS.items() if label == key or "_" in label
        )
        assert not retyped, f"these step names are still keys: {retyped}"


class TestTheWordsAreUsable:
    @pytest.mark.parametrize(("what", "enum", "mapping"), MAPPED, ids=[m[0] for m in MAPPED])
    def test_no_label_is_a_raw_enum_name(
        self, what: str, enum: type[StrEnum], mapping: dict[Any, HumanState]
    ) -> None:
        """`SKIPPED_NOT_APPLICABLE` retyped as a label is not a translation."""
        del enum
        shouting = sorted(
            state.label for state in mapping.values() if state.label.isupper() or "_" in state.label
        )
        assert not shouting, (
            f"These {what} labels are still enum names: {shouting}. Sentence case, in the "
            "product's own words — the point of the mapping is that a reader never meets the "
            "schema."
        )

    @pytest.mark.parametrize(("what", "enum", "mapping"), MAPPED, ids=[m[0] for m in MAPPED])
    def test_a_detail_says_more_than_its_label(
        self, what: str, enum: type[StrEnum], mapping: dict[Any, HumanState]
    ) -> None:
        """A detail that restates its label teaches the reader to stop reading details."""
        del enum
        empty = sorted(
            state.label
            for state in mapping.values()
            if state.detail and state.detail.strip().rstrip(".").lower() == state.label.lower()
        )
        assert not empty, f"these {what} details only repeat their label: {empty}"

    def test_every_gate_asks_in_the_second_person(self) -> None:
        """The phrase lands inside "The run stopped so you could …", so it is a clause.

        A sentence there reads as two sentences run together, and a capital letter mid-line is
        the tell that somebody wrote it for a different surface.
        """
        wrong = sorted(
            gate.value
            for gate, words in GATES.items()
            if words.asks.endswith(".") or words.asks[:1].isupper()
        )
        assert not wrong, (
            f"these gate phrases are not clauses: {wrong}. They are dropped into "
            '"The run stopped so you could …", which supplies the capital and the full stop.'
        )

    def test_every_gate_question_is_a_question(self) -> None:
        wrong = sorted(
            gate.value for gate, words in GATES.items() if not words.question.endswith("?")
        )
        assert not wrong, f"these gate headings are not questions: {wrong}"


class TestTheTonesSayTheRightThing:
    """A label makes a state readable; a tone decides whether it reads as a fault."""

    def test_a_run_stopped_at_its_ceiling_is_a_refusal_not_a_failure(self) -> None:
        """The distinction the whole module exists for.

        A guardrail stopping a run before it overspends is the platform working. Rendering it
        in the same red as a crash sends the operator looking for a fault that is not there —
        and the console's own prose already argues the opposite: *"the next step would take
        this run past a spending cap, so it stopped before making the call rather than after
        paying for it."*
        """
        stopped = JOB_STATES[JobStatus.BUDGET_EXCEEDED]
        assert stopped.tone is Tone.REFUSAL
        assert stopped.tone is not Tone.FAILURE
        assert "fail" not in stopped.label.lower()

    def test_a_broken_run_is_a_failure(self) -> None:
        assert JOB_STATES[JobStatus.FAILED].tone is Tone.FAILURE

    def test_the_two_are_never_the_same_tone(self) -> None:
        assert JOB_STATES[JobStatus.FAILED].tone is not JOB_STATES[JobStatus.BUDGET_EXCEEDED].tone

    def test_a_rejected_request_is_not_a_failure(self) -> None:
        """Rejecting a plan is the operator using the gate, not the platform going wrong."""
        assert REQUEST_STATES[RequestStatus.REJECTED].tone is not Tone.FAILURE

    def test_a_section_that_never_ran_is_not_reported_as_zero(self) -> None:
        """Roadmap §4.3: *not generated* across the row, never zero coverage for an absence."""
        assert SECTION_STATES[SectionStatus.FAILED].label == "Not generated"

    def test_no_transaction_kind_has_an_opinion(self) -> None:
        """A purchase in success green is the screen judging the operator's dealing.

        A transaction is a thing that happened. Whether it was a good idea is the portfolio's
        arithmetic to imply and never the chip's.
        """
        opinionated = sorted(
            kind.value
            for kind, state in TRANSACTION_KINDS.items()
            if state.tone in {Tone.SUCCESS, Tone.FAILURE}
        )
        assert not opinionated, f"these transaction kinds carry a verdict: {opinionated}"

    def test_a_deposit_is_named_as_a_flow(self) -> None:
        """Roadmap §3.2's rule, carried early: a top-up must never read as performance."""
        assert "flow" in TRANSACTION_KINDS[TransactionKind.DEPOSIT].detail.lower()


class TestTheGradeVocabularyStaysDistinct:
    """`Typed` and `Attested` are two axes, and one word cannot carry both."""

    def test_the_attested_grade_is_called_typed(self) -> None:
        """The provenance vocabulary already spends *Attested* on a record class. A documented
        attestation is attested in that sense, so reusing the word here would make it mean
        neither."""
        assert GRADES[Grade.ATTESTED].label == "Typed"

    def test_it_matches_what_the_portfolio_screen_already_renders(self) -> None:
        """One vocabulary, not two. The portfolio's chip and this mapping must agree, or the
        same holding is called different things on two surfaces."""
        assert {grade: state.label for grade, state in GRADES.items()} == GRADE_LABELS


class TestTheGateSequenceIsHonest:
    def test_exactly_two_gates_always_fire(self) -> None:
        """The plan and the final review. Everything else depends on the company."""
        always = {gate for gate, words in GATES.items() if words.certainty is GateCertainty.ALWAYS}
        assert always == {GateKind.PLAN, GateKind.FINAL}

    def test_the_budget_gate_is_not_part_of_the_sequence(self) -> None:
        """It exists only where a cap would be crossed. A journey listing it as "to come"
        would promise a stop most runs never make."""
        assert GATES[GateKind.BUDGET].certainty is GateCertainty.ON_REFUSAL

    def test_the_thesis_gate_is_opened_by_a_finding_and_never_by_a_run(self) -> None:
        """The one gate no research run opens (ADRs 0078, 0103). It has words in this table
        because every gate must be nameable in one vocabulary, and a certainty of its own so
        that no run console can list it as a stop on the way to a report."""
        assert GATES[GateKind.THESIS].certainty is GateCertainty.ON_FINDING

    def test_the_rest_are_conditional(self) -> None:
        sometimes = {
            gate for gate, words in GATES.items() if words.certainty is GateCertainty.SOMETIMES
        }
        assert sometimes == {
            GateKind.UNMAPPED_CONCEPTS,
            GateKind.SECTOR_SPECIALIST,
            GateKind.PEER_SET,
            GateKind.THEME_SET,
            GateKind.ASSUMPTIONS,
        }


class TestTheWorkListReadsFromHere:
    def test_the_gate_phrases_are_not_a_second_copy(self) -> None:
        """`GATE_ASKS` was written before this module and is now derived from it.

        Two answers to "what is this gate asking" is the drift a shared vocabulary exists to
        prevent, and the copy that goes stale is always the one nobody is looking at.
        """
        assert {gate: words.asks for gate, words in GATES.items()} == GATE_ASKS


class TestALookupRefusesRatherThanGuessing:
    def test_an_unmapped_state_raises_with_the_remedy(self) -> None:
        """A placeholder would render, and the missing entry would ship.

        The completeness tests above mean this should never happen at run time; the raise is
        what makes sure it cannot happen *quietly* if one is ever skipped.
        """
        with pytest.raises(KeyError, match="no job vocabulary"):
            vocabulary._looked_up({}, JobStatus.RUNNING, "job")

    def test_an_unmapped_gate_raises_with_the_remedy(self) -> None:
        original = dict(GATES)
        try:
            GATES.pop(GateKind.PLAN)
            with pytest.raises(KeyError, match="no gate vocabulary"):
                vocabulary.gate_words(GateKind.PLAN)
        finally:
            GATES.clear()
            GATES.update(original)
