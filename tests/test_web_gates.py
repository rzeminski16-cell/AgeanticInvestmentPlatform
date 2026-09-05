"""The gate journey: honest about what has happened and what may still come.

The journey is the answer to two findings the requirements name — "nothing says where you
are in the sequence" and "the gate sequence is invisible" — and its one failure mode is
overstating: a conditional gate drawn as a certain future step is a lie about the workflow.
So these tests hold the boundary in both directions, and pin the gate-step map to the
workflow it describes.
"""

from __future__ import annotations

from typing import Any

from aer.core.enums import Decision, GateKind
from aer.web.gates import CONSEQUENCES, GATE_STEPS, JourneyState, journey
from aer.workflow.workflows.vertical_slice_v1 import build_steps


def _rows(**statuses: str) -> list[dict[str, Any]]:
    """Every declared step as the console's state dict renders it, with overrides."""
    return [{"key": step.key, "status": statuses.get(step.key, "QUEUED")} for step in build_steps()]


class TestTheMapMatchesTheWorkflow:
    def test_every_declared_gate_step_is_in_the_map(self) -> None:
        declared = {step.key: step.gate for step in build_steps() if step.gate}
        assert {key: gate.value for key, gate in GATE_STEPS.items()} == declared, (
            "GATE_STEPS and the workflow's declared gate steps disagree. A renamed gate "
            "step would otherwise vanish from every journey without a test noticing."
        )

    def test_every_gate_has_a_consequence(self) -> None:
        missing = sorted(gate.value for gate in GateKind if gate not in CONSEQUENCES)
        assert not missing, f"gates with no consequence sentence: {missing}"


class TestTheJourneyNeverOverstates:
    def test_a_fresh_run_promises_two_and_admits_five(self) -> None:
        built = journey(_rows(), decisions={}, pending=None)

        by_state = [entry.state for entry in built.entries]
        assert by_state.count(JourneyState.UPCOMING) == 2, "plan and review always fire"
        assert by_state.count(JourneyState.POSSIBLE) == 5, "the conditional five stay possible"
        assert built.remaining_certain == 2
        assert built.remaining_possible == 5
        assert built.remaining_display == "Between two and seven more decisions."

    def test_a_skipped_conditional_gate_reads_as_not_needed(self) -> None:
        built = journey(
            _rows(gate_plan="SUCCEEDED", gate_sector_specialist="SUCCEEDED"),
            decisions={GateKind.PLAN: Decision.APPROVED},
            pending=None,
        )

        states = {entry.gate: entry.state for entry in built.entries}
        assert states[GateKind.PLAN] is JourneyState.DECIDED
        assert states[GateKind.SECTOR_SPECIALIST] is JourneyState.NOT_NEEDED, (
            "a conditional gate whose step completed without a decision was one the run "
            "did not need — reporting it as anything else invents a decision nobody made"
        )

    def test_the_current_stop_is_a_certain_decision(self) -> None:
        built = journey(
            _rows(gate_plan="AWAITING_APPROVAL"),
            decisions={},
            pending=GateKind.PLAN,
        )

        first = built.entries[0]
        assert first.state is JourneyState.CURRENT
        assert built.remaining_certain == 2, "the current stop plus the review to come"

    def test_a_decided_gate_carries_its_decision_label(self) -> None:
        built = journey(
            _rows(gate_plan="SUCCEEDED"),
            decisions={GateKind.PLAN: Decision.APPROVED},
            pending=None,
        )

        assert built.entries[0].decision_label == "Approved"

    def test_a_finished_run_has_no_decisions_left(self) -> None:
        done = dict.fromkeys(GATE_STEPS, "SUCCEEDED")
        built = journey(
            _rows(**done),
            decisions={GateKind.PLAN: Decision.APPROVED, GateKind.FINAL: Decision.APPROVED},
            pending=None,
        )

        assert built.remaining_display == "No decisions left."

    def test_the_budget_gate_is_never_in_the_journey(self) -> None:
        """It exists only where a ceiling would be crossed; a journey listing it would
        promise a stop most runs never make."""
        built = journey(_rows(), decisions={}, pending=None)
        assert all(entry.gate is not GateKind.BUDGET for entry in built.entries)

    def test_one_remaining_certain_decision_reads_singular(self) -> None:
        nearly = dict.fromkeys((key for key in GATE_STEPS if key != "gate_final"), "SUCCEEDED")
        built = journey(
            _rows(**nearly),
            decisions={GateKind.PLAN: Decision.APPROVED},
            pending=None,
        )

        assert built.remaining_display == "One more decision."
