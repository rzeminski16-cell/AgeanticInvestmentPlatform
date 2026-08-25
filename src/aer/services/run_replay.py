"""Reproducing a whole run from what it wrote down.

Gap A12. :mod:`aer.eval.replay` re-runs *calculations* inside the evaluation gate, which
answers "does the arithmetic still come out the same". A report rests on three other things
as well, and none of them were checked together: the bytes its citations point at, the
excerpts those citations quote, and the model exchanges that produced its prose.

**"Reproduce this run" is a question about the record, not about the world.** Nothing here
fetches a URL or calls a model. Every leg is re-derived from stored rows and archived bytes,
so a replay costs nothing, needs no network, and gives the same answer in a year as today —
which is the only kind of reproduction worth the name for a system whose sources move.

Four legs, and each fails the replay on its own:

* **Calculations** re-execute from their own records and match what was stored, to the
  tolerance the evaluation gate applies. Not to the digit: ``output_value`` is
  ``NUMERIC(38, 12)``, so a stored ratio is a rounded one and an exact comparison would call
  every non-terminating quotient a divergence.
* **Citations** re-verify: the excerpt still appears in the artefact, read back by hash.
* **Artefacts** the run rests on are still present and still hash to their names.
* **Model calls** have both payloads archived, and those archives are still readable.

The fourth is the one that looks optional and is not. An ``agent_run`` whose archived
response has been garbage-collected leaves a section nobody can account for: the prose is in
the report, and the exchange that produced it is gone. That is the difference between a run
that can be audited and one that merely was.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.db.models import AgentRun, Artefact, JobStep, SourceDocument
from aer.eval.metrics import THRESHOLDS, Metric
from aer.eval.observations import ReplayObservation
from aer.eval.replay import replay_observations_for_job
from aer.storage.protocol import ArtefactStore
from aer.verify.citations import verify_job_citations

__all__ = ["RunReplay", "divergence_reason", "diverges", "replay_run"]

_log = structlog.get_logger("aer.services.run_replay")


@dataclass(frozen=True, slots=True)
class RunReplay:
    """What re-deriving a run from its own record found.

    Counts and failures are kept apart for every leg, because "nothing failed" and "nothing
    was checked" are different answers and a report that conflated them would call a run
    with no citations perfectly reproducible.
    """

    job_id: uuid.UUID
    calculations_checked: int = 0

    # Each entry is already a sentence: the calculation's name and what went wrong with it.
    # A bare name here meant the one surface that reports a divergence could not say whether
    # the record no longer runs or the twelfth decimal place moved.
    calculations_diverged: tuple[str, ...] = ()
    citations_checked: int = 0
    citations_failed: tuple[str, ...] = ()
    artefacts_checked: int = 0
    artefacts_unreadable: tuple[str, ...] = ()
    model_calls_checked: int = 0
    model_calls_unarchived: tuple[str, ...] = ()

    @property
    def reproduces(self) -> bool:
        return not (
            self.calculations_diverged
            or self.citations_failed
            or self.artefacts_unreadable
            or self.model_calls_unarchived
        )

    @property
    def checked(self) -> int:
        return (
            self.calculations_checked
            + self.citations_checked
            + self.artefacts_checked
            + self.model_calls_checked
        )

    def problems(self) -> tuple[str, ...]:
        """Every divergence, labelled with which leg it came from."""
        return (
            *(f"calculation {reason}" for reason in self.calculations_diverged),
            *(f"citation {name}" for name in self.citations_failed),
            *(f"artefact {digest} cannot be read back" for digest in self.artefacts_unreadable),
            *(f"agent run {name} has no archived exchange" for name in self.model_calls_unarchived),
        )


async def replay_run(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    job_id: uuid.UUID,
    settings: Settings,
) -> RunReplay:
    """Re-derive every checkable thing a run produced, and report what no longer holds."""
    observations = await replay_observations_for_job(session, job_id)
    diverged = tuple(
        f"{observation.name} ({divergence_reason(observation)})"
        for observation in observations
        if diverges(observation)
    )

    citations = await verify_job_citations(session, store, job_id=job_id, settings=settings)
    failed = tuple(
        f"{citation.id}: {outcome.reason or 'the excerpt is no longer in the artefact'}"
        for citation, outcome in citations
        if outcome.failed
    )

    checked_artefacts, unreadable = await _reread_artefacts(session, store, job_id=job_id)
    calls, unarchived = await _check_exchanges(session, job_id=job_id)

    report = RunReplay(
        job_id=job_id,
        calculations_checked=len(observations),
        calculations_diverged=diverged,
        citations_checked=len(citations),
        citations_failed=failed,
        artefacts_checked=checked_artefacts,
        artefacts_unreadable=unreadable,
        model_calls_checked=calls,
        model_calls_unarchived=unarchived,
    )
    _log.info(
        "run.replayed",
        job_id=str(job_id),
        checked=report.checked,
        reproduces=report.reproduces,
    )
    return report


# The tolerance a replay is judged against, read from the evaluation gate rather than
# chosen again here (gap R14). The two used to disagree: the gate compared within
# `numerical_consistency`'s threshold and this service compared with `!=`, so every ratio
# in a run "did not replay" while the gate on the same rows passed. `output_value` is
# `NUMERIC(38, 12)`, so a non-terminating quotient is stored rounded to twelve places and
# a recomputed one carries the full context precision — 113 of the 2026-08-24 MSFT run's
# 1,034 calculations failed on differences around 10⁻¹³, and every sum passed, which is
# what a rounding artefact looks like rather than a broken record.
_TOLERANCE: Final = THRESHOLDS[Metric.NUMERICAL_CONSISTENCY][0]


def diverges(observation: ReplayObservation) -> bool:
    """Whether this row genuinely no longer reproduces.

    Three ways, and the first two are absolute. A record that cannot be re-run at all has
    failed however close the arithmetic would have been, and one that replays in a different
    unit has not reproduced the calculation — 0.05 pure and 0.05 USD are the same digits and
    different claims.
    """
    return (
        observation.error is not None
        or not observation.unit_matches
        or observation.delta > _TOLERANCE
    )


def divergence_reason(observation: ReplayObservation) -> str:
    """What went wrong, in the words an operator can act on.

    The problem line used to read "calculation gross_margin#1 does not replay" and nothing
    else, so a record that no longer runs and one that moved in the twelfth decimal place
    were indistinguishable on the only surface that reports them.
    """
    if observation.error is not None:
        return f"did not re-run: {observation.error}"
    if not observation.unit_matches:
        return f"replayed in {observation.replayed_unit}, stored {observation.expected_unit}"
    return f"stored {observation.expected}, replayed {observation.replayed}"


async def _reread_artefacts(
    session: AsyncSession, store: ArtefactStore, *, job_id: uuid.UUID
) -> tuple[int, tuple[str, ...]]:
    """Read back every artefact this run's sources rest on, checking the digest.

    The store verifies the hash on each read, so a corrupt file raises rather than returning
    the wrong bytes — which is why this reads rather than stats. A file that is merely
    *present* proves nothing about what is in it.
    """
    digests = list(
        await session.scalars(
            select(Artefact.sha256)
            .join(SourceDocument, SourceDocument.artefact_id == Artefact.id)
            .where(SourceDocument.job_id == job_id)
            .distinct()
        )
    )

    unreadable: list[str] = []
    for digest in digests:
        try:
            await store.read(digest)
        except Exception:
            unreadable.append(digest)
    return len(digests), tuple(unreadable)


async def _check_exchanges(
    session: AsyncSession, *, job_id: uuid.UUID
) -> tuple[int, tuple[str, ...]]:
    """Every model call must still have both halves of its exchange archived.

    Checked by reference rather than by reading the bytes: the artefact rows are ``RESTRICT``,
    so a payload reference that still points at a row is a payload the store is not allowed
    to have dropped. A null reference is the real failure — a call that was made and never
    archived, which is a section of prose with no accounting behind it.
    """
    runs = list(
        await session.scalars(
            select(AgentRun)
            .join(JobStep, JobStep.id == AgentRun.job_step_id)
            .where(JobStep.job_id == job_id)
        )
    )
    missing = tuple(
        f"{run.agent_role}#{run.id}"
        for run in runs
        if run.request_payload_ref is None or run.response_payload_ref is None
    )
    return len(runs), missing
