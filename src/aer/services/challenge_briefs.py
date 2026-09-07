"""Briefing the operator on a challenge the run could not settle (ADR 0095).

Gate 3 asks a person to keep the draft's position or accept the reviewer's objection, and
showed them two paragraphs of argument to choose between. This assembles what each choice
assumes and implies, and which side the briefer leans to, from rows that stopped changing
when the revise step sealed the gate-2 payload.

**Nothing here decides anything.** The briefs are stored on the step that wrote them, never
on ``disagreements.detail`` — that column is inside the approval hash, and an interpretation
inside it would be part of what the operator approves and would invalidate an approval every
time it was rewritten. The lean sits beside the controls; the controls are unchanged.

**An id the run does not hold is dropped**, on the same principle the red team's own claim
attribution is dropped: a brief keyed to a challenge this run never recorded is a brief
about nothing, and trusting the key would let a reply attach text to a row by guessing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.challenge_brief import (
    MAX_BRIEFS,
    ChallengeBriefAgent,
    ChallengeBriefInput,
    UnsettledChallenge,
)
from aer.core.disagreement import DisagreementKind, ResolutionOutcome, ResolvedBy
from aer.db.models import Claim, Disagreement, ReportSection, ResearchRequest
from aer.services.disagreements import disagreements_for_job

__all__ = ["BriefOutcome", "brief_unsettled_challenges", "briefs_from_output"]

_log = structlog.get_logger("aer.services.challenge_briefs")

# How much of a claim's text stands in for the draft's position. The same bound the red
# team uses for the same rows, so the two roles read the draft the same length.
_CLAIM_CHARS = 600


@dataclass(slots=True)
class BriefOutcome:
    """What the briefing step produced, in the shape its step output stores."""

    written: bool = False
    briefs: dict[str, dict[str, Any]] = field(default_factory=dict)
    considered: int = 0
    dropped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "written": self.written,
            "briefs": self.briefs,
            "considered": self.considered,
            "dropped": self.dropped,
        }


def briefs_from_output(output: object) -> dict[str, dict[str, Any]]:
    """The stored briefs, keyed by disagreement id, from a step output of any vintage.

    Defensive because it reads JSONB written by an earlier build: a run recorded before this
    role existed has no ``briefs`` key at all, and a page that raised on one would take the
    review gate down for every run that predates the feature.
    """
    if not isinstance(output, dict):
        return {}
    briefs = output.get("briefs")
    if not isinstance(briefs, dict):
        return {}
    return {str(key): value for key, value in briefs.items() if isinstance(value, dict)}


async def brief_unsettled_challenges(
    agent_context: AgentContext,
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    request: ResearchRequest,
) -> BriefOutcome:
    """One call over every unsettled challenge, or nothing at all when there are none.

    A run whose adversary raised nothing, or whose challenges a rule already settled, makes
    no call and spends nothing — the commonest shape of a clean run, and the one where a
    briefing would be a page of empty boxes.
    """
    challenges = await _unsettled(session, job_id=job_id)
    if not challenges:
        return BriefOutcome(written=False)

    known = {row.disagreement_id for row in challenges}
    replies = await ChallengeBriefAgent().run(
        agent_context,
        ChallengeBriefInput(
            company_name=request.company_name,
            ticker=request.ticker,
            challenges=challenges,
        ),
    )

    briefs: dict[str, dict[str, Any]] = {}
    dropped = 0
    for brief in replies.briefs:
        if brief.disagreement_id not in known:
            dropped += 1
            continue
        briefs[brief.disagreement_id] = brief.model_dump(mode="json", exclude={"disagreement_id"})

    _log.info(
        "challenge_briefs.written",
        job_id=str(job_id),
        considered=len(challenges),
        written=len(briefs),
        dropped=dropped,
    )
    return BriefOutcome(
        written=bool(briefs), briefs=briefs, considered=len(challenges), dropped=dropped
    )


async def _unsettled(session: AsyncSession, *, job_id: uuid.UUID) -> list[UnsettledChallenge]:
    """The run's thesis conflicts that still want a person, most serious first.

    Bounded, and the ordering is what the bound means: a red team that raised more than one
    sitting's worth is briefed on the ones the operator will reach first, and the rest keep
    their controls with no brief beside them — the page exactly as it was.
    """
    rows = [
        row
        for row in await disagreements_for_job(session, job_id)
        if row.kind is DisagreementKind.THESIS_CONFLICT
        and row.resolution is ResolutionOutcome.ESCALATED
        and row.resolved_by is not ResolvedBy.HUMAN
    ]
    rows.sort(key=lambda row: (not row.material, -int((row.detail or {}).get("severity", 0))))

    claims = await _claim_text(session, job_id=job_id)
    return [_as_challenge(row, claims=claims) for row in rows[:MAX_BRIEFS]]


def _as_challenge(row: Disagreement, *, claims: dict[str, str]) -> UnsettledChallenge:
    detail = row.detail or {}
    attacked = [claims[str(key)] for key in detail.get("claims", []) if str(key) in claims]
    return UnsettledChallenge(
        disagreement_id=str(row.id),
        dimension=str(detail.get("dimension") or ""),
        severity=int(detail.get("severity") or 0),
        material=bool(row.material),
        statement=str(detail.get("challenge") or row.topic),
        basis=str(detail.get("basis") or ""),
        # What the draft says on the point, which is the other half of the comparison. The
        # claim's own words: a challenge attacks an assertion, and "keeping assumes ..." is
        # unanswerable without the assertion in front of the briefer.
        draft_position=" ".join(attacked),
    )


async def _claim_text(session: AsyncSession, *, job_id: uuid.UUID) -> dict[str, str]:
    rows = await session.execute(
        select(Claim.id, Claim.text)
        .join(ReportSection, ReportSection.id == Claim.report_section_id)
        .where(ReportSection.job_id == job_id)
    )
    return {str(claim_id): str(text)[:_CLAIM_CHARS] for claim_id, text in rows}
