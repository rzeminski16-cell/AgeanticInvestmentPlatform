"""The judgement layer as the map reads it: theses, premises, decisions and verdicts, over
the companies an export or a drawing covers (ADR 0122 §1).

Deterministic reads of rows, under the rule the rest of the map runs on — only confirmed
state produces a node or an edge. Here that means: a thesis exists once it is written (a
retired one is kept, marked); a premise is a judgement somebody held (a withdrawn one is
kept, with its reason); a decision is recorded; a verdict is a review the operator
confirmed — a reviewer's proposal nobody confirmed is nobody's judgement and produces
nothing. Nothing here reads the vault back, and nothing here is evidence: no claim may
cite any of it (ADR 0122 §3).

**A decision points at a thesis version, not a thesis.** ADRs 0102 and 0104 never say
*version*, and none is stored: the version is the premises as they stood when the decision
was held — those whose own ``held_at`` is not after the decision's, and which had not been
withdrawn by then — reconstructed from the judgements' own clocks (ADR 0075's distinction
between when a view was held and when the platform heard of it). Stored twice, it would
drift.

**The monitor's reading is the premise's state, not a node.** ADR 0122 lists *premise ←
finding* among the edges; a node per reading would outnumber everything else within a
quarter of ordinary use, so a premise carries its latest reading and the finding pages
hold the rest.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from aer.core.enums import FindingKind
from aer.db.models import Company, Decision, Finding, Premise, Review, Thesis

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "SUBJECT_COMPANY",
    "DecisionView",
    "PremiseView",
    "ThesisView",
    "judgement_views",
    "latest_reading",
    "thesis_subjects",
    "version_at",
]

SUBJECT_COMPANY = "company"


@dataclass(frozen=True, slots=True)
class PremiseView:
    premise: Premise
    reading: Finding | None
    """The monitor's latest reading of it, or none while nothing has been read."""

    @property
    def is_withdrawn(self) -> bool:
        return self.premise.judgement.withdrawn_at is not None


@dataclass(frozen=True, slots=True)
class DecisionView:
    decision: Decision
    version: tuple[Premise, ...]
    """The premises as they stood when the decision was held — the thesis version."""
    verdict: Review | None
    """The confirmed review of the position this decision acted in, once it has closed."""


@dataclass(frozen=True, slots=True)
class ThesisView:
    thesis: Thesis
    company: Company
    premises: tuple[PremiseView, ...]
    decisions: tuple[DecisionView, ...]
    verdicts: tuple[Review, ...]
    """Every confirmed review that read this thesis, newest close first."""


def version_at(premises: Sequence[Premise], moment: datetime) -> tuple[Premise, ...]:
    """The premises held at ``moment``, in thesis order: written by then, not withdrawn yet."""
    return tuple(
        premise
        for premise in sorted(premises, key=lambda row: row.position)
        if premise.judgement.held_at <= moment
        and (premise.judgement.withdrawn_at is None or premise.judgement.withdrawn_at > moment)
    )


async def thesis_subjects(session: AsyncSession) -> dict[uuid.UUID, Company]:
    """Every company somebody holds a thesis on, by id — part of the map's universe once
    the judgement layer is in it, beside the researched and the named-as-comparable."""
    rows = await session.scalars(
        select(Company)
        .join(Thesis, Thesis.subject_id == Company.id)
        .where(Thesis.subject_kind == SUBJECT_COMPANY)
        .distinct()
    )
    return {row.id: row for row in rows}


async def judgement_views(
    session: AsyncSession,
    *,
    companies: dict[uuid.UUID, Company],
    user_id: uuid.UUID | None = None,
) -> tuple[ThesisView, ...]:
    """The theses on these companies, oldest first, with everything that hangs off each —
    premises with their latest reading, decisions pinned to their version, the verdicts.

    ``user_id`` narrows to one person's, which is what the vault wants: it is one
    operator's projection. The in-app drawing and the statistics are the installation's
    and pass nothing, as they do for every other kind of node.
    """
    if not companies:
        return ()
    query = (
        select(Thesis)
        .options(selectinload(Thesis.premises), selectinload(Thesis.decisions))
        .where(Thesis.subject_kind == SUBJECT_COMPANY, Thesis.subject_id.in_(list(companies)))
        # Oldest first by the operator's own clock — when the view was formed, else when
        # the platform heard of it — so the order is the record's and not the insert's.
        .order_by(func.coalesce(Thesis.written_at, Thesis.created_at), Thesis.id)
    )
    if user_id is not None:
        query = query.where(Thesis.user_id == user_id)
    views: list[ThesisView] = []
    for thesis in await session.scalars(query):
        premises: list[PremiseView] = []
        for premise in sorted(thesis.premises, key=lambda row: row.position):
            reading = await latest_reading(session, premise)
            premises.append(PremiseView(premise=premise, reading=reading))
        verdicts = tuple(
            await session.scalars(
                select(Review)
                .where(Review.thesis_id == thesis.id)
                .order_by(Review.closed_on.desc(), Review.judgement_id)
            )
        )
        decisions = tuple(
            DecisionView(
                decision=decision,
                version=version_at(thesis.premises, decision.judgement.held_at),
                verdict=_verdict_of(decision, verdicts),
            )
            for decision in sorted(
                thesis.decisions, key=lambda row: (row.judgement.held_at, row.judgement_id)
            )
        )
        views.append(
            ThesisView(
                thesis=thesis,
                company=companies[thesis.subject_id],
                premises=tuple(premises),
                decisions=decisions,
                verdicts=verdicts,
            )
        )
    return tuple(views)


async def latest_reading(session: AsyncSession, premise: Premise) -> Finding | None:
    """The monitor's newest reading of a premise, or none while nothing has been read."""
    found: Finding | None = await session.scalar(
        select(Finding)
        .where(Finding.judgement_id == premise.judgement_id, Finding.kind == FindingKind.READING)
        .order_by(Finding.created_at.desc(), Finding.id.desc())
        .limit(1)
    )
    return found


def _verdict_of(decision: Decision, verdicts: Sequence[Review]) -> Review | None:
    """The review of the episode the decision acted in: the same security, held inside the
    episode's window. A pass names no security and is scored by nothing."""
    if decision.security_id is None:
        return None
    held_on = decision.judgement.held_at.date()
    for review in verdicts:
        if review.security_id == decision.security_id and (
            review.opened_on <= held_on <= review.closed_on
        ):
            return review
    return None
