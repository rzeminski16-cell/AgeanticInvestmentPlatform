"""Why a run stops is written where the run stops, by every site that stops it.

`aer.workflow.pauses.PauseReason` is the journey harness's inventory of pauses. It is only an
inventory while every ``StepPaused`` the workflow raises carries a member: a raise site that
forgets is a stopped state the harness cannot see, so this pins the sites to the enum by
reading the workflow's own source.
"""

from __future__ import annotations

import ast
import inspect

from aer.workflow.engine import StepPaused
from aer.workflow.pauses import BudgetScope, PauseReason
from aer.workflow.workflows import vertical_slice_v1


def test_a_pause_records_its_reason_beside_its_gate() -> None:
    paused = StepPaused("waiting", gate="PLAN", reason=PauseReason.GATE_WAITING)
    assert paused.context["gate"] == "PLAN"
    assert paused.context["reason"] == "gate_waiting"
    assert paused.reason is PauseReason.GATE_WAITING


def test_a_pause_without_a_reason_says_so_rather_than_omitting_the_key() -> None:
    paused = StepPaused("stopped", gate=None)
    assert paused.context == {"gate": None, "reason": None}


def _raise_sites() -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(vertical_slice_v1))
    sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        callee = node.exc.func
        if isinstance(callee, ast.Name) and callee.id == "StepPaused":
            sites.append(node.exc)
    return sites


def test_every_pause_the_workflow_raises_names_its_reason() -> None:
    sites = _raise_sites()
    assert len(sites) >= 10, "the workflow's pause sites were not found; the scan has drifted"
    unnamed = [site.lineno for site in sites if "reason" not in {kw.arg for kw in site.keywords}]
    assert not unnamed, f"StepPaused raised without a reason at lines {unnamed}"


def test_the_budget_scopes_are_what_the_console_reads_back() -> None:
    """`RunState.budget_scope` reads the refusal's ``scope`` off the step row; the values the
    guard writes are the enum's, so the two cannot drift apart."""
    assert {scope.value for scope in BudgetScope} == {"per_run", "monthly"}
