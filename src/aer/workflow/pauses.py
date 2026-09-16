"""Why a run stops, named.

A run that is not finished is stopped for a reason, and until now the reason was only
readable from the message a step wrote when it paused. That is fine for a person and useless
for an instrument: the journey harness (`docs/V1.0_Alpha/11-testing-strategy.md` §3.1) must
enumerate every state a run can stop in *from code*, so that a pause nobody can reach is a
dead branch the harness names, and a stopped state the harness cannot see is a state no test
asserts a way out of.

So the reasons are an enum, the workflow writes the member into the pause's context under
``reason``, and the budget guard's two scopes stop being string literals. Nothing here decides
anything; it is vocabulary, and the vocabulary is the inventory.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from aer.core.enums import GateKind

__all__ = ["LIVE_PAYLOAD_GATES", "BudgetScope", "PauseReason"]


class PauseReason(StrEnum):
    """One member per branch on which a workflow step raises ``StepPaused``."""

    GATE_WAITING = "gate_waiting"
    """No decision has been recorded for the gate the run reached."""

    GATE_REJECTED = "gate_rejected"
    """A decision was recorded and it was not an approval. The run stops here."""

    GATE_STALE_PAGE_MOVED = "gate_stale_page_moved"
    """The recorded approval matches neither the seal nor the page as it renders now."""

    GATE_STALE_SEAL_DRIFT = "gate_stale_seal_drift"
    """The page as it renders now matches the approval; the run's own seal has moved."""

    FINAL_TRIGGERS_FIRED = "final_triggers_fired"
    """An escalation trigger fired on the finished draft, before the final gate."""

    FINAL_UNVERIFIED_CITATIONS = "final_unverified_citations"
    """The draft carries a citation code could not confirm, or a claim nothing supports."""

    ROW_MISSING = "row_missing"
    """A step's own record is missing: a defensive stop, never an operator's decision."""


class BudgetScope(StrEnum):
    """Which ceiling stopped a run. The values are what ``job_steps.error.context`` carries."""

    PER_RUN = "per_run"
    MONTHLY = "monthly"


# The gates whose approval is verified against the payload as it renders *now*, recomputed
# from the rows the operator may still change, rather than against a hash the run sealed
# (gap A52 for the assumptions; readiness audit F-15 for the peer and theme slates, where an
# addition on the review page must not make the gate impassable). For these three, "the seal
# drifted from the page" cannot happen, because there is no seal to drift: the only stale
# disposition they have is the page moving after an approval. The journey harness reads this
# to generate no seal-drift row for them, and `tests/test_journey_inventory.py` pins it to
# the workflow's own calls so it cannot silently fall out of date.
LIVE_PAYLOAD_GATES: Final[frozenset[GateKind]] = frozenset(
    {GateKind.ASSUMPTIONS, GateKind.PEER_SET, GateKind.THEME_SET}
)
