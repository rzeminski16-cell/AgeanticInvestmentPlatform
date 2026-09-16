"""The journey inventory is complete, unique and honest about what it cannot build.

The harness's three assertions are only worth making over an inventory nobody wrote by hand.
These tests hold the generator to the code it reads: every non-terminal status has a family,
every gate a run can raise appears in every disposition, the two gate kinds no run opens
appear in none, and the two side lists name only rows that exist.
"""

from __future__ import annotations

import inspect
import re

from aer.core.enums import TERMINAL_JOB_STATUSES, GateKind, JobStatus
from aer.core.escalation import TriggerKind
from aer.web.gates import GATE_STEPS
from aer.workflow.pauses import LIVE_PAYLOAD_GATES
from aer.workflow.workflows import vertical_slice_v1
from tests.journey_harness import ASSERTIONS
from tests.journey_inventory import (
    STILL_RED,
    UNCONSTRUCTED,
    Disposition,
    Family,
    failed_step_codes,
    inventory,
    run_gates,
)

ROWS = inventory()
KEYS = {row.key for row in ROWS}


class TestTheInventoryComesFromCode:
    def test_every_non_terminal_status_has_a_row(self) -> None:
        covered = {row.status for row in ROWS}
        for status in JobStatus:
            if status not in TERMINAL_JOB_STATUSES:
                assert status in covered, f"{status.value} is a state nothing asserts a way out of"

    def test_the_run_gates_are_the_step_map_and_nothing_else(self) -> None:
        assert run_gates() == tuple(GATE_STEPS.values())
        assert GateKind.BUDGET not in run_gates()
        assert GateKind.THESIS not in run_gates()

    def test_every_run_gate_appears_in_every_disposition_it_can_be_left_in(self) -> None:
        for gate in run_gates():
            for disposition in Disposition:
                key = f"gate.{gate.value}.{disposition.value}"
                if disposition is Disposition.STALE_SEAL_DRIFT and gate in LIVE_PAYLOAD_GATES:
                    assert key not in KEYS, f"{key} is a state the engine cannot produce"
                else:
                    assert key in KEYS

    def test_the_live_payload_gates_are_the_ones_the_workflow_recomputes(self) -> None:
        """`LIVE_PAYLOAD_GATES` is a claim about the workflow, pinned to its own calls: a
        gate whose approval is checked against a recomputed live hash has no seal to drift."""
        source = inspect.getsource(vertical_slice_v1)
        calls = re.findall(
            r"_require_approval\(\s*context,\s*gate=GateKind\.(\w+),(.*?)\)", source, re.S
        )
        assert calls, "no gate call found; the pin's pattern has drifted"
        live = {GateKind[name] for name, rest in calls if "expected_hash=" in rest}
        assert live == set(LIVE_PAYLOAD_GATES), (
            f"the workflow recomputes {sorted(g.value for g in live)}; "
            f"LIVE_PAYLOAD_GATES says {sorted(g.value for g in LIVE_PAYLOAD_GATES)}"
        )

    def test_no_row_names_a_gate_no_run_opens(self) -> None:
        gates = {row.gate for row in ROWS if row.family is Family.GATE}
        assert GateKind.BUDGET not in gates
        assert GateKind.THESIS not in gates

    def test_every_error_code_the_engine_can_record_has_a_row(self) -> None:
        codes = failed_step_codes()
        assert "unexpected_error" in codes
        assert "budget_exceeded" not in codes, "a budget stop is a pause, and has its own family"
        assert "step_paused" not in codes
        for code in codes:
            assert f"failed.{code}" in KEYS

    def test_every_trigger_kind_has_a_final_gate_row(self) -> None:
        for kind in TriggerKind:
            assert f"final.trigger.{kind.value}" in KEYS

    def test_keys_are_unique(self) -> None:
        assert len(KEYS) == len(ROWS)


class TestTheSideListsNameRealRows:
    def test_still_red_names_only_inventory_rows(self) -> None:
        stale = set(STILL_RED) - KEYS
        assert not stale, f"STILL_RED names rows that no longer exist: {sorted(stale)}"

    def test_unconstructed_names_only_inventory_rows(self) -> None:
        stale = set(UNCONSTRUCTED) - KEYS
        assert not stale, f"UNCONSTRUCTED names rows that no longer exist: {sorted(stale)}"

    def test_a_row_is_not_both_red_and_unconstructed(self) -> None:
        both = set(STILL_RED) & set(UNCONSTRUCTED)
        assert not both, f"a row cannot be both red and unbuildable: {sorted(both)}"

    def test_every_reason_is_a_sentence(self) -> None:
        for key, reason in UNCONSTRUCTED.items():
            assert len(reason) > 20, f"{key}: say why"
        for key, by_assertion in STILL_RED.items():
            assert by_assertion, f"{key}: a red row names at least one red assertion"
            for name, reason in by_assertion.items():
                assert len(reason) > 20, f"{key}/{name}: say why"

    def test_still_red_names_only_the_three_assertions(self) -> None:
        for key, by_assertion in STILL_RED.items():
            unknown = set(by_assertion) - set(ASSERTIONS)
            assert not unknown, f"{key} records assertions that do not exist: {sorted(unknown)}"

    def test_a_row_red_on_its_control_records_nothing_about_pressing(self) -> None:
        """`press` is measured only once a control is found; a record claiming both is a
        record nobody measured."""
        for key, by_assertion in STILL_RED.items():
            assert not ("control" in by_assertion and "press" in by_assertion), key
