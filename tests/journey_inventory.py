"""Every state a research run can stop in, generated from the code that can stop it.

The instrument for ISSUE 1 of the V1.0 delivery plan (`docs/V1.0_Alpha/11-testing-strategy.md`
§3.1): *every state a run can stop in offers a labelled way forward, in the interface, without
the terminal.* The audit lost £14.41 on two runs that stopped somewhere the operator could not
leave, and the loss was not that the platform failed — it was that it stopped where nothing
led on.

**The inventory is read from code, never written by hand.** A row exists for every member of
the cross product that can actually occur: the non-terminal job statuses, the seven gates a
run can raise (read from the step map, so the two gate kinds no run opens — the budget stop is
a job status, the thesis gate is the monitor's — generate no row), the four dispositions a
gate can be left in, the budget guard's scopes, the escalation triggers, and every error code
the engine can record on a failed step. A new status, gate, trigger or error class therefore
adds a row without anybody remembering to, and the test module beside this one asserts the
coverage so a member that slips through fails the suite.

Two halves read it. `tests/e2e/journey.py` constructs each state in a real browser and makes
the three assertions; `audit/smoke.py --journey` makes the shape assertions in-process on the
fake scene. Both take their rows from :func:`inventory`.

Two lists are kept beside the generator, both keyed by row, both asserted against it:
:data:`STILL_RED` names the rows that fail today, by which of the three assertions fails and
why, and :data:`UNCONSTRUCTED` names the rows the harness cannot yet construct on the fake
scene with the reason. A row in neither must pass; a row in either must fail in exactly the
way it says.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from aer import errors
from aer.core.enums import TERMINAL_JOB_STATUSES, GateKind, JobStatus
from aer.core.escalation import TriggerKind
from aer.web.gates import GATE_STEPS
from aer.workflow.engine import StepPaused, WorkflowDefinitionError
from aer.workflow.pauses import LIVE_PAYLOAD_GATES, BudgetScope

__all__ = [
    "STILL_RED",
    "UNCONSTRUCTED",
    "CapState",
    "Disposition",
    "Family",
    "Press",
    "StoppedState",
    "failed_step_codes",
    "inventory",
    "run_gates",
]


class Family(StrEnum):
    """The kinds of stopped state, one per way the console's template branches."""

    QUEUED = "queued"
    STRANDED = "stranded"
    STEP_MODE = "step_mode"
    GATE = "gate"
    BUDGET = "budget"
    FAILED_STEP = "failed"
    FINAL_GATE = "final"
    PROBLEM_PAGE = "problem"


class Disposition(StrEnum):
    """What a gate the run reached has been left as. Computed by the platform, not stored."""

    PENDING = "pending"
    REJECTED = "rejected"
    STALE_PAGE_MOVED = "stale_page_moved"
    STALE_SEAL_DRIFT = "stale_seal_drift"


class CapState(StrEnum):
    RAISABLE = "raisable"
    AT_CEILING = "at_ceiling"


class Press(StrEnum):
    """What "pressing it moves" means for the row: the run's own record, or the page."""

    TRANSITION = "transition"
    """The job's fingerprint — status, newest step, approvals, cap, sibling runs — changes."""

    NAVIGATE = "navigate"
    """The control leads to the surface where the remedy lives; the run itself cannot move
    until something outside it is put right (a worker, a monthly budget, a platform ceiling)."""


@dataclass(frozen=True, slots=True)
class StoppedState:
    key: str
    family: Family
    status: JobStatus
    finding: str
    expected_controls: tuple[str, ...]
    press: Press
    gate: GateKind | None = None
    disposition: Disposition | None = None
    detail: str | None = None
    navigates_to: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


# The findings each red row is, in the audit's own numbering, so an xfail reason names its
# evidence rather than an opinion.
F_08: Final = "readiness audit F-08 — a run whose worker dies is left RUNNING with no exit"
F_16: Final = "readiness audit F-16 — a stale approval is a dead end"
F_21_OVERRIDE: Final = (
    "readiness audit F-21 — the citation override the gate message promises exists on no surface"
)
F_22: Final = "readiness audit F-22 — nothing re-measures a failed check on a finished run"
S_2_11: Final = "roadmap §2.11 — a stopped run has no way forward that is not the terminal"
VOCAB: Final = "roadmap §2.11 — code identifiers and shell commands reach the operator's pages"


def run_gates() -> tuple[GateKind, ...]:
    """The gates a research run can raise, in workflow order, read from the step map."""
    return tuple(GATE_STEPS.values())


def failed_step_codes() -> tuple[str, ...]:
    """Every code the engine can write on a failed step.

    ``_detail_of`` records an ``AerError``'s own code and ``unexpected_error`` for anything
    else. The budget error is a pause, not a failure, and has its own family; ``StepPaused``
    is the pause signal itself.
    """
    declared = {
        cls.code
        for cls in vars(errors).values()
        if isinstance(cls, type)
        and issubclass(cls, errors.AerError)
        and cls not in (errors.AerError, errors.BudgetExceededError)
    }
    declared.add(WorkflowDefinitionError.code)
    declared.discard(StepPaused.code)
    return (*sorted(declared), "unexpected_error")


_GATE_CONTROL: Final[dict[GateKind, str]] = {
    GateKind.PLAN: r"review the plan",
    GateKind.SECTOR_SPECIALIST: r"confirm the sector",
    GateKind.PEER_SET: r"confirm the peer set",
    GateKind.THEME_SET: r"confirm the themes",
    GateKind.UNMAPPED_CONCEPTS: r"confirm the financials",
    GateKind.ASSUMPTIONS: r"confirm the assumptions",
    GateKind.FINAL: r"review the draft",
}


def _gate_rows() -> list[StoppedState]:
    rows: list[StoppedState] = []
    for gate in run_gates():
        rows.append(
            StoppedState(
                key=f"gate.{gate.value}.{Disposition.PENDING.value}",
                family=Family.GATE,
                status=JobStatus.AWAITING_APPROVAL,
                gate=gate,
                disposition=Disposition.PENDING,
                finding=S_2_11,
                expected_controls=(_GATE_CONTROL[gate],),
                press=Press.TRANSITION,
            )
        )
        rows.append(
            StoppedState(
                key=f"gate.{gate.value}.{Disposition.REJECTED.value}",
                family=Family.GATE,
                status=JobStatus.AWAITING_APPROVAL,
                gate=gate,
                disposition=Disposition.REJECTED,
                finding=S_2_11,
                expected_controls=(r"start again", r"start a new run", r"run again"),
                press=Press.TRANSITION,
            )
        )
        stale = [Disposition.STALE_PAGE_MOVED]
        if gate not in LIVE_PAYLOAD_GATES:
            # A gate verified against the live payload has no seal to drift (see
            # `LIVE_PAYLOAD_GATES`); a row for it would be a dead branch the harness
            # invented rather than one the engine has.
            stale.append(Disposition.STALE_SEAL_DRIFT)
        for disposition in stale:
            rows.append(
                StoppedState(
                    key=f"gate.{gate.value}.{disposition.value}",
                    family=Family.GATE,
                    status=JobStatus.AWAITING_APPROVAL,
                    gate=gate,
                    disposition=disposition,
                    finding=F_16,
                    expected_controls=(
                        r"decide again",
                        r"decide on what it shows now",
                        r"approve again",
                        r"re-?seal",
                    ),
                    press=Press.TRANSITION,
                )
            )
    return rows


def _budget_rows() -> list[StoppedState]:
    return [
        StoppedState(
            key=f"budget.{BudgetScope.PER_RUN.value}.{CapState.RAISABLE.value}",
            family=Family.BUDGET,
            status=JobStatus.BUDGET_EXCEEDED,
            detail=f"{BudgetScope.PER_RUN.value}:{CapState.RAISABLE.value}",
            finding=S_2_11,
            expected_controls=(r"raise the ceiling", r"raise .* cap"),
            press=Press.TRANSITION,
        ),
        StoppedState(
            key=f"budget.{BudgetScope.PER_RUN.value}.{CapState.AT_CEILING.value}",
            family=Family.BUDGET,
            status=JobStatus.BUDGET_EXCEEDED,
            detail=f"{BudgetScope.PER_RUN.value}:{CapState.AT_CEILING.value}",
            finding=S_2_11,
            expected_controls=(r"raise the platform", r"platform.{0,20}ceiling", r"per-run budget"),
            press=Press.NAVIGATE,
            navigates_to=r"/settings",
        ),
        StoppedState(
            key=f"budget.{BudgetScope.MONTHLY.value}",
            family=Family.BUDGET,
            status=JobStatus.BUDGET_EXCEEDED,
            detail=BudgetScope.MONTHLY.value,
            finding=S_2_11,
            expected_controls=(r"monthly budget", r"change the month"),
            press=Press.NAVIGATE,
            navigates_to=r"/settings",
        ),
    ]


def _failed_rows() -> list[StoppedState]:
    rows = [
        StoppedState(
            key=f"failed.{code}",
            family=Family.FAILED_STEP,
            status=JobStatus.FAILED,
            detail=code,
            finding=VOCAB,
            expected_controls=(r"continue",),
            press=Press.TRANSITION,
        )
        for code in failed_step_codes()
    ]
    rows.append(
        StoppedState(
            key="failed.external_service_error.remedy",
            family=Family.FAILED_STEP,
            status=JobStatus.FAILED,
            detail="external_service_error:remedy",
            finding=S_2_11,
            expected_controls=(r"put .* right", r"set the key", r"platform", r"settings"),
            press=Press.NAVIGATE,
            navigates_to=r"/settings",
            notes=(
                "The remedy branch of the console's failed-step block offers no control by design.",
            ),
        )
    )
    return rows


def _final_rows() -> list[StoppedState]:
    rows = [
        StoppedState(
            key=f"final.trigger.{kind.value}",
            family=Family.FINAL_GATE,
            status=JobStatus.AWAITING_APPROVAL,
            gate=GateKind.FINAL,
            detail=kind.value,
            finding=S_2_11,
            expected_controls=(r"review the draft",),
            press=Press.TRANSITION,
        )
        for kind in TriggerKind
    ]
    rows.append(
        StoppedState(
            key="final.unverified_citations",
            family=Family.FINAL_GATE,
            status=JobStatus.AWAITING_APPROVAL,
            gate=GateKind.FINAL,
            detail="unverified_citations",
            finding=F_21_OVERRIDE,
            expected_controls=(r"review the draft", r"override", r"withdraw the claim"),
            press=Press.TRANSITION,
        )
    )
    return rows


def _problem_rows() -> list[StoppedState]:
    return [
        StoppedState(
            key=f"problem.{cls.code}",
            family=Family.PROBLEM_PAGE,
            status=JobStatus.AWAITING_APPROVAL,
            detail=cls.code,
            finding=S_2_11,
            expected_controls=(r"back to the run", r"open the review page", r"try again"),
            press=Press.NAVIGATE,
            navigates_to=r"/runs/",
            notes=(
                "`_problem` renders `runs/problem.html`, whose only control is 'All requests'.",
            ),
        )
        for cls in (errors.ValidationError, errors.ConflictError, errors.AerError)
    ]


def inventory() -> tuple[StoppedState, ...]:
    """Every stopped state, in a stable order."""
    rows: list[StoppedState] = [
        StoppedState(
            key="queued",
            family=Family.QUEUED,
            status=JobStatus.QUEUED,
            finding=VOCAB,
            expected_controls=(r"platform", r"worker", r"health"),
            press=Press.NAVIGATE,
            navigates_to=r"/settings|/platform|/health",
            notes=("The console tells the operator to watch `just worker`.",),
        ),
        StoppedState(
            key="stranded",
            family=Family.STRANDED,
            status=JobStatus.RUNNING,
            finding=F_08,
            expected_controls=(r"continue",),
            press=Press.TRANSITION,
        ),
        StoppedState(
            key="step_mode",
            family=Family.STEP_MODE,
            status=JobStatus.PAUSED,
            finding=S_2_11,
            expected_controls=(r"continue", r"next step"),
            press=Press.TRANSITION,
        ),
        *_gate_rows(),
        *_budget_rows(),
        *_failed_rows(),
        *_final_rows(),
        *_problem_rows(),
    ]
    keys = [row.key for row in rows]
    assert len(keys) == len(set(keys)), "the inventory generated two rows with one key"
    covered = {row.status for row in rows}
    missing = {status for status in JobStatus if status not in TERMINAL_JOB_STATUSES} - covered
    assert not missing, f"no inventory family covers {sorted(s.value for s in missing)}"
    return tuple(rows)


# Rows the harness cannot yet construct on the fake scene, with the reason. Each fails with
# "no path constructed" rather than skipping, and leaves this list only by gaining a builder
# or by its branch being shown dead and removed. Kept short on purpose: a row here is
# harness backlog, not platform backlog.
UNCONSTRUCTED: Final[dict[str, str]] = {
    **{
        f"gate.{gate.value}.{disposition.value}": (
            f"the default fake scene never raises the {gate.value} gate; it needs the "
            "unmapped-concepts scene or a bank"
        )
        for gate in (GateKind.UNMAPPED_CONCEPTS, GateKind.SECTOR_SPECIALIST)
        for disposition in Disposition
        if not (disposition is Disposition.STALE_SEAL_DRIFT and gate in LIVE_PAYLOAD_GATES)
    },
    **{
        f"final.trigger.{kind.value}": (
            f"no fake-scene fixture fires {kind.value} at the final gate yet"
        )
        for kind in TriggerKind
    },
    "final.unverified_citations": (
        "the scripted section brain cites only real excerpts; a brain that plants an "
        "unverifiable one is not written yet"
    ),
    "problem.conflict": (
        "a second decision on a decided gate needs a stale form re-posted with a live CSRF "
        "token, which the harness does not forge yet"
    ),
    "problem.aer_error": "no route on the run's pages catches a bare AerError on the fake scene",
}

# Rows that fail today, and on which of the three assertions, and why. Measured on 16
# September 2026 by running the harness on every row it can construct, not predicted:
# `tests/e2e/test_journey.py` marks exactly these rows `xfail(strict=True)`, and both halves
# fail a row whose measured red set differs from the recorded one in either direction, so a
# fix that works has to move this record as well as the page, and a fix that does not fails
# the build. The harness is green when this is empty.
#
# Recorded per assertion rather than per row because the fixes land in that order: Phase
# 1.2 puts the missing controls on the rejected and stale gates while the console still
# speaks in step keys, and a record that could only say "red" would not register it.
#
# What the measurement found, in one sentence: the console prints the workflow's step keys
# on every run and tells the operator to type a shell command on every unfinished one, so
# `text` is red on every constructible row but the problem page; `control` is red on the
# rejected and stale gates, the two budget ceilings, the failed step with a remedy, the
# queued run and the problem page; `press` was measured wherever a control was found, and
# every one of them moved the run.
_CONSOLE: Final = "the console prints the step keys and says to type `just worker`"
_CONSOLE_RESEAL: Final = "the console prints the step keys and says to type `aer reseal`"
_CONSOLE_FAILED: Final = "the console prints the step keys and the error's code"
_GATE_PAGE: Final[dict[GateKind, str]] = {
    GateKind.PLAN: "the plan page prints section keys and the source's identifier",
    GateKind.PEER_SET: "the peers page prints a module path",
    GateKind.THEME_SET: "the themes page prints a module path",
    GateKind.ASSUMPTIONS: "the assumptions page prints module paths and the assumptions' keys",
    GateKind.FINAL: "the review page prints module paths, metric names and section keys",
}


def _measured() -> dict[str, dict[str, str]]:
    red: dict[str, dict[str, str]] = {
        "queued": {
            "text": _CONSOLE,
            "control": "§2.11: nothing leads to the worker's health",
        },
        "stranded": {"text": _CONSOLE},
        "step_mode": {"text": _CONSOLE},
        f"budget.{BudgetScope.PER_RUN.value}.{CapState.RAISABLE.value}": {
            "text": f"{_CONSOLE} and the budget code",
        },
        f"budget.{BudgetScope.PER_RUN.value}.{CapState.AT_CEILING.value}": {
            "text": f"{_CONSOLE} and the budget code",
            "control": (
                "§2.11: the platform ceiling is named in a sentence whose only control is a "
                "bare 'settings' link"
            ),
        },
        f"budget.{BudgetScope.MONTHLY.value}": {
            "text": f"{_CONSOLE} and the budget code",
            "control": (
                "§2.11: the monthly budget is named in a sentence whose only control is a "
                "bare 'settings' link"
            ),
        },
        "failed.external_service_error.remedy": {
            "text": _CONSOLE_FAILED,
            "control": "§2.11: the remedy is stated and nothing leads to the settings it names",
        },
        "problem.validation_error": {
            "control": "§2.11: the problem page's only control is 'All requests'",
        },
    }
    for code in failed_step_codes():
        red[f"failed.{code}"] = {"text": _CONSOLE_FAILED}
    for gate in run_gates():
        if f"gate.{gate.value}.{Disposition.PENDING.value}" in UNCONSTRUCTED:
            continue
        page = _GATE_PAGE[gate]
        red[f"gate.{gate.value}.{Disposition.PENDING.value}"] = {"text": f"{_CONSOLE}; {page}"}
        red[f"gate.{gate.value}.{Disposition.REJECTED.value}"] = {
            "text": f"{_CONSOLE}; {page}",
            "control": "§2.11: after a rejection nothing offers to start again",
        }
        red[f"gate.{gate.value}.{Disposition.STALE_PAGE_MOVED.value}"] = {
            "text": f"{_CONSOLE}; {page}",
            "control": "F-16: nothing offers to decide again on what the page shows now",
        }
        if gate not in LIVE_PAYLOAD_GATES:
            red[f"gate.{gate.value}.{Disposition.STALE_SEAL_DRIFT.value}"] = {
                "text": f"{_CONSOLE_RESEAL}; {page}",
                "control": "F-16: the only remedy offered is a shell command",
            }
    return red


STILL_RED: Final[dict[str, dict[str, str]]] = _measured()
