"""The shared frame of the seven gates, and the journey every gate and the console show.

The requirements name the problem plainly: seven gates, seven layouts, one decision — an
operator who has learned one has not learned the next, and no gate says where it sits in the
run. This module owns what is identical across all seven: the journey (which decisions have
been made, which is this, which may still come), the consequence a gate's approval carries,
and the cost context that must appear identically on every one.

**The journey never overstates.** Five of the seven gates are conditional and the run does
not know which will fire until the deciding step has run — so a gate whose step has not been
reached is *possible*, never promised, and ``decisions_remaining`` is a range rather than a
number. The one gate that is never in the journey is the budget gate: it exists only where a
ceiling would otherwise be crossed, and listing it as "to come" would promise a stop most
runs never make.

The journey itself is pure — steps in, entries out — for the same reason ``web/verdict.py``
is: handlers gather the rows; this decides the words. ``frame_for`` is the one async
assembler, because every gate page needs the same five queries and seven copies of them is
how the gates drifted apart the first time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from aer.core.enums import Decision, GateKind
from aer.db.models import User
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.services.mandate import mandate_of
from aer.web import figures
from aer.web.vocabulary import DECISIONS, GATES, GateCertainty

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Job

__all__ = [
    "CONSEQUENCES",
    "GATE_STEPS",
    "Journey",
    "JourneyEntry",
    "JourneyState",
    "frame_for",
    "journey",
]

# The workflow's gate steps, in declared order, and the gate each one raises. Asserted
# against `build_steps()` by `tests/test_web_gates.py`, so a workflow that renames a gate
# step fails the suite rather than quietly dropping the gate from every journey.
GATE_STEPS: Final[dict[str, GateKind]] = {
    "gate_plan": GateKind.PLAN,
    "gate_sector_specialist": GateKind.SECTOR_SPECIALIST,
    "gate_peer_set": GateKind.PEER_SET,
    "gate_theme_set": GateKind.THEME_SET,
    "gate_unmapped_concepts": GateKind.UNMAPPED_CONCEPTS,
    "gate_assumptions": GateKind.ASSUMPTIONS,
    "gate_final": GateKind.FINAL,
}

# What approving each gate actually commits, said once and said identically wherever the
# gate is named. The decision panel renders this sentence; a template writing its own would
# be the seventh copy of a consequence the operator has to be able to trust.
CONSEQUENCES: Final[dict[GateKind, str]] = {
    GateKind.PLAN: (
        "Approving commits the estimated spend shown in the plan. Everything expensive in "
        "the run is downstream of this page."
    ),
    GateKind.UNMAPPED_CONCEPTS: (
        "Approving accepts proceeding without the unmapped lines. It does not assert that "
        "they are immaterial in general."
    ),
    GateKind.SECTOR_SPECIALIST: (
        "Confirming grants the analysis mandate: it decides which valuation methods this "
        "run may use, and which are refused."
    ),
    GateKind.PEER_SET: (
        "Confirming records the comparison set. It fetches no peer filings and no peer prices."
    ),
    GateKind.THEME_SET: (
        "Confirming decides how later searches and comparisons find this company. It "
        "changes nothing about the current valuation."
    ),
    GateKind.ASSUMPTIONS: (
        "Approving lets the valuation rest on every value shown here. It is the last stop "
        "before the figures are built."
    ),
    GateKind.FINAL: (
        "Approving freezes this draft as the report. Rejecting stops a run that has "
        "already spent what the ledger shows."
    ),
    GateKind.BUDGET: ("Approving lets this run spend past the ceiling you set for it."),
}


class JourneyState(StrEnum):
    """Where one gate stands in this run's journey, without overstating anything."""

    DECIDED = "decided"
    """A person decided it, and the decision is on the record."""

    CURRENT = "current"
    """The run is stopped here now, waiting for the operator."""

    NOT_NEEDED = "not_needed"
    """Its step ran and the run did not need to stop: nothing to confirm."""

    UPCOMING = "upcoming"
    """Not reached yet, and every run stops here."""

    POSSIBLE = "possible"
    """Not reached yet, and only some runs stop here. Never drawn as a promise."""


@dataclass(frozen=True, slots=True)
class JourneyEntry:
    gate: GateKind
    name: str
    state: JourneyState
    decision_label: str = ""
    """"Approved" or "Rejected" when decided; empty otherwise."""


@dataclass(frozen=True, slots=True)
class Journey:
    entries: tuple[JourneyEntry, ...]
    remaining_certain: int
    """Decisions still to make on every run: the current stop plus unreached always-gates."""
    remaining_possible: int
    """Conditional gates whose deciding step has not run: they may fire, or may not."""

    @property
    def remaining_display(self) -> str:
        """The range, in words, without promising a stop the run may never make."""
        certain, possible = self.remaining_certain, self.remaining_possible
        if not certain and not possible:
            return "No decisions left."
        if not possible:
            return f"{_worded(certain).capitalize()} more {_decisions(certain)}."
        if not certain:
            return f"Up to {_worded(possible)} more {_decisions(possible)}."
        most = certain + possible
        return f"Between {_worded(certain)} and {_worded(most)} more decisions."


_WORDS: Final = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def _worded(n: int) -> str:
    return _WORDS[n] if n < len(_WORDS) else str(n)


def _decisions(n: int) -> str:
    return "decision" if n == 1 else "decisions"


def journey(
    step_rows: Sequence[Mapping[str, Any]],
    *,
    decisions: Mapping[GateKind, Decision],
    pending: GateKind | None,
) -> Journey:
    """The run's decisions, in order, from rows the console already holds.

    ``step_rows`` is ``RunState.as_dict()["steps"]`` — every declared step with its recorded
    status — so this needs no query of its own and agrees with the step list beside it by
    construction. A gate step that ran to completion without a decision on the record is a
    gate the run did not need; one that has not reached ``SUCCEEDED`` and is not the current
    stop is still ahead, as a certainty or a possibility depending on its kind.
    """
    entries: list[JourneyEntry] = []
    certain = 0
    possible = 0

    for row in step_rows:
        gate = GATE_STEPS.get(str(row.get("key", "")))
        if gate is None:
            continue
        words = GATES[gate]
        decision = decisions.get(gate)
        if decision is not None:
            entries.append(
                JourneyEntry(gate, words.name, JourneyState.DECIDED, DECISIONS[decision].label)
            )
        elif pending is gate:
            entries.append(JourneyEntry(gate, words.name, JourneyState.CURRENT))
            certain += 1
        elif str(row.get("status", "")) == "SUCCEEDED":
            entries.append(JourneyEntry(gate, words.name, JourneyState.NOT_NEEDED))
        elif words.certainty is GateCertainty.ALWAYS:
            entries.append(JourneyEntry(gate, words.name, JourneyState.UPCOMING))
            certain += 1
        else:
            entries.append(JourneyEntry(gate, words.name, JourneyState.POSSIBLE))
            possible += 1

    return Journey(entries=tuple(entries), remaining_certain=certain, remaining_possible=possible)


async def frame_for(session: AsyncSession, *, job: Job, gate: GateKind) -> dict[str, Any]:
    """Everything the shared gate frame renders, identical on all seven gates.

    One assembler rather than seven, because "cost appears differently on each gate" and
    "nothing says where you are in the sequence" were findings about drift, and drift is
    prevented structurally or not at all.
    """
    state = await run_service.run_state(session, job_id=job.id)
    approvals = await approval_service.approvals_for_job(session, job.id)
    pending = await approval_service.pending_gate(session, job)
    request = await mandate_of(session, job)

    decided = next((row for row in approvals if row.gate is gate), None)
    decided_note = ""
    decided_label = ""
    if decided is not None:
        actor = await session.get(User, decided.actor_user_id)
        who = actor.display_name if actor is not None else "the operator"
        words = DECISIONS[decided.decision]
        decided_label = words.label
        decided_note = (
            f"{words.label} by {who} on {decided.decided_at.strftime('%d %B %Y')}. A "
            "decision is not a state to re-assert; changing it needs a new run."
        )

    return {
        "gate": gate.value,
        "gate_words": GATES[gate],
        "gate_consequence": CONSEQUENCES[gate],
        "journey": journey(
            state.as_dict()["steps"],
            decisions={row.gate: row.decision for row in approvals},
            pending=pending,
        ),
        "gate_cost": figures.cost_context(
            spent=state.spend_gbp,
            ceiling=request.max_cost_gbp if request is not None else None,
        ),
        "decided": decided_label or None,
        "decided_note": decided_note,
        "run_identity": (
            f"{request.company_name} · {request.ticker} · as at {request.as_of_date.isoformat()}"
            if request is not None
            else "Research run"
        ),
    }
