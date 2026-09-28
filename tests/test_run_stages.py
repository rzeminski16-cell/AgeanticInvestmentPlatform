"""The run page's five stages, and who the ledger says did each step (page specification §7).

Pure functions over the step rows the console already holds, so the rail agrees with the
ledger beside it by construction. What is pinned here is the reading of those rows: a stage
stopped for the operator is a warning and never a failure, a stage is done only when every
one of its steps is, and a stage this run's workflow has no steps for is left out rather
than drawn as done or as waiting — neither would be true of it.
"""

from __future__ import annotations

from typing import Any

from aer.web.gates import GATE_STEPS
from aer.web.stages import STAGES, StageState, actor_of, spend_by_stage, stages_of
from aer.workflow.workflows.vertical_slice_v1 import build_steps


def _rows(**statuses: str) -> list[dict[str, Any]]:
    """Every declared step, QUEUED unless named."""
    return [
        {"key": step.key, "status": statuses.get(step.key, "QUEUED"), "cost_gbp": "0"}
        for step in build_steps()
    ]


class TestTheStagesCoverTheWorkflow:
    def test_every_step_the_workflow_declares_belongs_to_exactly_one_stage(self) -> None:
        """A step in no stage would be work the rail never reports; a step in two would be
        work it reports twice. Either is a rail that disagrees with the ledger."""
        staged = [key for stage in STAGES for key in stage.steps]

        assert sorted(staged) == sorted(step.key for step in build_steps())
        assert len(staged) == len(set(staged))

    def test_the_stages_are_contiguous_runs_of_the_workflow(self) -> None:
        """A stage that finished before the one before it would be a rail that contradicts
        itself, so each stage is a contiguous stretch of the declared order."""
        order = [step.key for step in build_steps()]
        staged = [key for stage in STAGES for key in stage.steps]

        assert staged == order


class TestReadingTheRows:
    def test_a_run_not_yet_started_is_waiting_everywhere(self) -> None:
        stages = stages_of(_rows(), done={})

        assert [stage.state for stage in stages] == [StageState.WAITING] * 5
        assert stages[0].detail == STAGES[0].waiting

    def test_a_gate_stops_its_stage_as_a_warning_not_a_failure(self) -> None:
        stages = stages_of(
            _rows(plan="SUCCEEDED", critique_plan="SUCCEEDED", gate_plan="AWAITING_APPROVAL"),
            done={},
        )

        assert stages[0].state is StageState.STOPPED
        assert stages[0].tone == "warning"
        assert stages[0].words == "Waiting for you"

    def test_a_failed_step_fails_its_stage_and_says_which(self) -> None:
        stages = stages_of(_rows(plan="SUCCEEDED", critique_plan="FAILED"), done={})

        assert stages[0].state is StageState.FAILED
        assert stages[0].detail.endswith("failed")

    def test_a_stage_part_way_through_counts_its_steps(self) -> None:
        stages = stages_of(_rows(plan="SUCCEEDED"), done={})

        assert stages[0].state is StageState.RUNNING
        assert stages[0].detail == "1 of 3 steps done"

    def test_a_finished_stage_says_what_the_handler_counted(self) -> None:
        plan = dict.fromkeys(STAGES[0].steps, "SUCCEEDED")

        stages = stages_of(_rows(**plan), done={"plan": "The plan approved"})

        assert stages[0].state is StageState.DONE
        assert stages[0].detail == "The plan approved"

    def test_a_stage_with_no_steps_in_this_run_is_left_out(self) -> None:
        """Neither done nor waiting would be true of a stage the run never has."""
        rows = [row for row in _rows() if row["key"] not in STAGES[2].steps]

        stages = stages_of(rows, done={})

        assert [stage.key for stage in stages] == ["plan", "acquire", "write", "approve"]
        assert [stage.number for stage in stages] == [1, 2, 3, 4]


class TestWhoDidIt:
    def test_a_decided_gate_is_the_operators_and_a_passed_one_is_codes(self) -> None:
        for gate_step in GATE_STEPS:
            assert actor_of(gate_step, decided=True) == "you"
            assert actor_of(gate_step, decided=False) == "code"

    def test_a_draft_is_a_models_and_a_fetch_is_codes(self) -> None:
        assert actor_of("draft") == "a model"
        assert actor_of("plan") == "a model"
        assert actor_of("acquire") == "code"
        assert actor_of("value") == "code"
        assert actor_of("render") == "code"


class TestWhatEachStageCost:
    def test_the_spend_is_summed_per_stage_and_the_free_ones_left_out(self) -> None:
        rows = _rows()
        for row in rows:
            row["cost_gbp"] = {"plan": "0.40", "critique_plan": "0.10", "draft": "3.25"}.get(
                row["key"], "0"
            )

        assert spend_by_stage(rows) == (("Plan", "£0.50"), ("Write", "£3.25"))

    def test_an_unreadable_cost_counts_as_nothing_rather_than_breaking_the_page(self) -> None:
        rows = _rows()
        rows[0]["cost_gbp"] = "not a number"

        assert spend_by_stage(rows) == ()
