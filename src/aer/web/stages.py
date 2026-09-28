"""The run page's five stages, and who did each step (page specification §7).

**The rail is the only progress indicator** (§7.1): Plan · Acquire · Compute · Write · Approve,
each with a state and one line. It is read from the step rows the page already holds, so it
agrees with the ledger beside it by construction, and a stage is a contiguous run of steps in
the workflow's own order — a stage that finished before the one before it would be a rail that
contradicts itself.

The ledger beside it names who did each step (§7): the operator, the code, or a model — the
spec's two actors plus the one it left implicit, because a draft a model wrote is not the
code's and saying so is the point of the product.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final

from aer.web import figures, vocabulary

__all__ = ["STAGES", "Stage", "StageState", "actor_of", "spend_by_stage", "stages_of"]


@dataclass(frozen=True, slots=True)
class StageDefinition:
    key: str
    name: str
    steps: tuple[str, ...]
    waiting: str
    """What the stage does, said while it has not started."""


STAGES: Final[tuple[StageDefinition, ...]] = (
    StageDefinition(
        "plan",
        "Plan",
        ("plan", "critique_plan", "gate_plan"),
        "What to research, and roughly what it costs",
    ),
    StageDefinition(
        "acquire",
        "Acquire",
        (
            "acquire",
            "acquire_macro",
            "classify",
            "gate_sector_specialist",
            "propose_peers",
            "gate_peer_set",
            "propose_themes",
            "gate_theme_set",
            "extract",
            "gate_unmapped_concepts",
            "acquire_prices",
            "calculate",
            "research_company",
            "research_industry",
            "research_macro",
            "research_recent_developments",
            "research_technical_context",
        ),
        "Filings, prices and reading, each hashed as it is fetched",
    ),
    StageDefinition(
        "compute",
        "Compute",
        ("comps", "propose_assumptions", "gate_assumptions", "value"),
        "The comparables and the valuation, every figure recorded",
    ),
    StageDefinition(
        "write",
        "Write",
        ("draft", "validate", "red_team", "revise", "verdict", "brief_challenges"),
        "The draft, checked against the record and challenged",
    ),
    StageDefinition(
        "approve",
        "Approve",
        ("gate_final", "render"),
        "Citation, consistency and plausibility checks, then your approval",
    ),
)

# The steps a model does the work of: every one the workflow budgets a model call for. Kept
# as a list here rather than read from the workflow's estimates, because a refresh's steps
# are the same names at a different price and the answer to "who wrote this" does not move.
_MODEL_STEPS: Final[frozenset[str]] = frozenset(
    {
        "plan",
        "critique_plan",
        "propose_peers",
        "propose_themes",
        "research_company",
        "research_industry",
        "research_macro",
        "research_recent_developments",
        "research_technical_context",
        "propose_assumptions",
        "draft",
        "validate",
        "red_team",
        "revise",
        "verdict",
        "brief_challenges",
    }
)

_STOPPED: Final[frozenset[str]] = frozenset({"AWAITING_APPROVAL", "PAUSED", "BUDGET_EXCEEDED"})


class StageState(StrEnum):
    DONE = "done"
    RUNNING = "running"
    STOPPED = "stopped"
    """Stopped for the operator: a gate, a pause, a ceiling. Warning, never failure."""
    FAILED = "failed"
    WAITING = "waiting"


# The design system's families, one per state; the word beside the mark carries the meaning.
_TONES: Final[dict[StageState, str]] = {
    StageState.DONE: "success",
    StageState.RUNNING: "info",
    StageState.STOPPED: "warning",
    StageState.FAILED: "failure",
    StageState.WAITING: "muted",
}

_WORDS: Final[dict[StageState, str]] = {
    StageState.DONE: "Done",
    StageState.RUNNING: "Running",
    StageState.STOPPED: "Waiting for you",
    StageState.FAILED: "Failed",
    StageState.WAITING: "Not started",
}


@dataclass(frozen=True, slots=True)
class Stage:
    number: int
    key: str
    name: str
    state: StageState
    detail: str

    @property
    def tone(self) -> str:
        return _TONES[self.state]

    @property
    def words(self) -> str:
        return _WORDS[self.state]


def actor_of(step_key: str, *, decided: bool = True) -> str:
    """Who did a step, in the ledger's words: *you*, *a model* or *code*.

    ``decided`` is whether a gate step was the operator's: one the run passed through
    because there was nothing to confirm was the code's, and the ledger says so.
    """
    if step_key.startswith("gate_"):
        return "you" if decided else "code"
    if step_key in _MODEL_STEPS:
        return "a model"
    return "code"


def stages_of(
    step_rows: Sequence[Mapping[str, Any]], *, done: Mapping[str, str]
) -> tuple[Stage, ...]:
    """The five stages from the run's step rows.

    ``done`` is what each finished stage says, keyed by stage — the counts the handler has
    already read ("31 documents, each hashed"). A stage this run's workflow has no steps for
    is left out rather than drawn as done or as waiting: neither would be true of it.
    """
    by_key = {str(row.get("key", "")): row for row in step_rows}
    stages: list[Stage] = []
    for definition in STAGES:
        rows = [by_key[key] for key in definition.steps if key in by_key]
        if not rows:
            continue
        stages.append(_stage(len(stages) + 1, definition, rows, done=done))
    return tuple(stages)


def _stage(
    number: int,
    definition: StageDefinition,
    rows: Sequence[Mapping[str, Any]],
    *,
    done: Mapping[str, str],
) -> Stage:
    statuses = [str(row.get("status", "")) for row in rows]

    def labelled(status: str | set[str]) -> str:
        wanted = {status} if isinstance(status, str) else status
        row = next(row for row in rows if str(row.get("status", "")) in wanted)
        return vocabulary.step_label(str(row.get("key", "")))

    if "FAILED" in statuses:
        state, detail = StageState.FAILED, f"{labelled('FAILED')} failed"
    elif _STOPPED & set(statuses):
        state, detail = StageState.STOPPED, labelled(set(_STOPPED))
    elif "RUNNING" in statuses:
        state, detail = StageState.RUNNING, labelled("RUNNING")
    elif all(status == "SUCCEEDED" for status in statuses):
        state, detail = StageState.DONE, done.get(definition.key, "Done")
    elif any(status == "SUCCEEDED" for status in statuses):
        finished = sum(status == "SUCCEEDED" for status in statuses)
        state, detail = StageState.RUNNING, f"{finished} of {len(statuses)} steps done"
    else:
        state, detail = StageState.WAITING, definition.waiting
    return Stage(number, definition.key, definition.name, state, detail)


def spend_by_stage(step_rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    """What each stage has cost so far, in pounds, leaving out the ones that cost nothing."""
    by_key = {str(row.get("key", "")): row for row in step_rows}
    spent: list[tuple[str, str]] = []
    for definition in STAGES:
        total = sum(
            (_pounds_of(by_key[key]) for key in definition.steps if key in by_key), Decimal(0)
        )
        if total > 0:
            spent.append((definition.name, figures.pounds(total)))
    return tuple(spent)


def _pounds_of(row: Mapping[str, Any]) -> Decimal:
    try:
        return Decimal(str(row.get("cost_gbp") or 0))
    except InvalidOperation:
        return Decimal(0)
