"""What else rests on a belief: the map read back for the monitor and for Ask (ADR 0122 §2).

Two questions over the same rows. The monitor's, unsolicited, is narrow on purpose: when a
premise on one company breaks, which *other held positions* assert a premise on the same
metric **and** share a confirmed theme or sector with it — two holdings sharing *revenue
growth* share almost nothing (ADR 0122, *what is given up*), and a feed that surfaced every
one would be a feed nobody read. Ask's, asked deliberately, is the wider one: which positions
rest on the same premise as this company's, metric alone, because the operator asked.

Both read the record and only the record — theses, premises, the monitor's latest readings,
the book's held positions, the confirmed relations. No model call, nothing fetched, nothing
spent. And nothing here is evidence: a premise is what the operator believes, and no claim
may cite it (ADR 0122 §3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aer.core.enums import FindingKind
from aer.core.sectors import ModelNotPermittedError, SectorProfile
from aer.db.models import (
    Company,
    Finding,
    Job,
    Portfolio,
    Premise,
    Report,
    Theme,
    ThemeMembership,
    Thesis,
)
from aer.obsidian.judgements import SUBJECT_COMPANY
from aer.services.history import approved_reports_for
from aer.services.post_trade import positions_of
from aer.services.sectors import confirmed_classification
from aer.services.theses import latest_reading
from aer.services.thesis_monitor import resolve_metric

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "Related",
    "SharedBelief",
    "held_company_ids",
    "load_bearing_elsewhere",
    "metric_key",
    "shared_beliefs",
]


@dataclass(frozen=True, slots=True)
class Related:
    """Another held position whose thesis asserts a premise on the same metric."""

    company: Company
    thesis: Thesis
    premise: Premise
    reading: Finding | None
    shares: tuple[str, ...]
    """What joins the two, in words: the metric, and — for the monitor — a theme or sector."""

    @property
    def state(self) -> str:
        """The monitor's latest reading of the other premise, in ADR 0079's words."""
        if self.reading is not None and self.reading.status is not None:
            return self.reading.status.value
        return "not yet read"


@dataclass(frozen=True, slots=True)
class SharedBelief:
    """One of this company's premises, and the other held positions resting on the same."""

    thesis: Thesis
    premise: Premise
    metric: str
    others: tuple[Related, ...]


def metric_key(premise: Premise) -> str | None:
    """What a premise's metric names, as the monitor resolves it; the words normalised where
    the monitor cannot. ``None`` for a premise nothing can test."""
    if premise.metric is None:
        return None
    resolved = resolve_metric(premise.metric)
    if resolved is not None:
        return resolved.key
    return " ".join(premise.metric.lower().split())


async def held_company_ids(session: AsyncSession, *, user_id: uuid.UUID) -> set[uuid.UUID]:
    """The companies this person holds a position in, across every book not archived."""
    books = await session.scalars(
        select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.archived_at.is_(None))
    )
    held: set[uuid.UUID] = set()
    for book in books:
        for position in (await positions_of(session, portfolio=book)).held:
            if position.security.company_id is not None:
                held.add(position.security.company_id)
    return held


async def load_bearing_elsewhere(session: AsyncSession, *, finding: Finding) -> tuple[Related, ...]:
    """The monitor's narrow question: other held positions asserting a premise on the same
    metric as the one this reading is about, whose company shares a confirmed theme or sector
    with the reading's. Nothing for a finding that is not a reading of a premise."""
    if finding.kind is not FindingKind.READING or finding.premise is None or finding.thesis is None:
        return ()
    key = metric_key(finding.premise)
    if key is None:
        return ()
    subject = finding.thesis.subject_id
    subject_themes = await _themes_of(session, subject)
    subject_sector = await _sector_of(session, subject)

    related: list[Related] = []
    for thesis, premise, company in await _held_premises(session, user_id=finding.user_id):
        if company.id == subject or metric_key(premise) != key:
            continue
        shares = [f"the metric {finding.premise.metric}"]
        themes = await _themes_of(session, company.id)
        shares.extend(
            f"the theme {subject_themes[theme_id]}"
            for theme_id in sorted(
                set(subject_themes) & set(themes), key=lambda shared: subject_themes[shared]
            )
        )
        sector = await _sector_of(session, company.id)
        if sector is not None and subject_sector is not None and sector.key == subject_sector.key:
            shares.append(f"the sector {sector.label}")
        if len(shares) == 1:
            # The metric alone joins them, and the metric alone is not enough here.
            continue
        related.append(
            Related(
                company=company,
                thesis=thesis,
                premise=premise,
                reading=await latest_reading(session, premise),
                shares=tuple(shares),
            )
        )
    return tuple(related)


async def shared_beliefs(
    session: AsyncSession, *, user_id: uuid.UUID, company_id: uuid.UUID
) -> tuple[SharedBelief, ...]:
    """Ask's wider question: for each testable premise this company's theses assert, the
    other held positions asserting one on the same metric — the metric alone, because the
    operator asked. Premises nothing can test are not here: nothing can rest on them."""
    theses = await session.scalars(
        select(Thesis)
        .options(selectinload(Thesis.premises))
        .where(
            Thesis.user_id == user_id,
            Thesis.subject_kind == SUBJECT_COMPANY,
            Thesis.subject_id == company_id,
            Thesis.retired_at.is_(None),
        )
        .order_by(Thesis.created_at, Thesis.id)
    )
    elsewhere = [
        row for row in await _held_premises(session, user_id=user_id) if row[2].id != company_id
    ]
    beliefs: list[SharedBelief] = []
    for thesis in theses:
        for premise in sorted(thesis.premises, key=lambda row: row.position):
            if premise.judgement.withdrawn_at is not None:
                continue
            key = metric_key(premise)
            if key is None or premise.metric is None:
                continue
            others: list[Related] = []
            for other_thesis, other_premise, company in elsewhere:
                if metric_key(other_premise) != key:
                    continue
                others.append(
                    Related(
                        company=company,
                        thesis=other_thesis,
                        premise=other_premise,
                        reading=await latest_reading(session, other_premise),
                        shares=(f"the metric {premise.metric}",),
                    )
                )
            beliefs.append(
                SharedBelief(
                    thesis=thesis, premise=premise, metric=premise.metric, others=tuple(others)
                )
            )
    return tuple(beliefs)


async def _held_premises(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[tuple[Thesis, Premise, Company]]:
    """Every testable, still-held premise on a still-held thesis about a company still held."""
    held = await held_company_ids(session, user_id=user_id)
    if not held:
        return []
    theses = await session.scalars(
        select(Thesis)
        .options(selectinload(Thesis.premises))
        .where(
            Thesis.user_id == user_id,
            Thesis.subject_kind == SUBJECT_COMPANY,
            Thesis.subject_id.in_(list(held)),
            Thesis.retired_at.is_(None),
        )
        .order_by(Thesis.created_at, Thesis.id)
    )
    companies = {
        row.id: row
        for row in await session.scalars(select(Company).where(Company.id.in_(list(held))))
    }
    rows: list[tuple[Thesis, Premise, Company]] = []
    for thesis in theses:
        company = companies.get(thesis.subject_id)
        if company is None:
            continue
        rows.extend(
            (thesis, premise, company)
            for premise in sorted(thesis.premises, key=lambda row: row.position)
            if premise.judgement.withdrawn_at is None and premise.has_predicate
        )
    return rows


async def _themes_of(session: AsyncSession, company_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """The confirmed themes a company is a member of, by id, with their labels — through
    approved reports only, as every theme edge in the map is."""
    themes = await session.scalars(
        select(Theme)
        .join(ThemeMembership, ThemeMembership.theme_id == Theme.id)
        .join(Report, Report.id == ThemeMembership.report_id)
        .where(ThemeMembership.company_id == company_id, Report.immutable.is_(True))
        .distinct()
    )
    return {theme.id: theme.label for theme in themes}


async def _sector_of(session: AsyncSession, company_id: uuid.UUID) -> SectorProfile | None:
    """The latest approved run's confirmed sector, or none — unconfirmed is none, as it is
    for the graph and the statistics: reading is not acting on it."""
    reports = await approved_reports_for(session, company_id=company_id)
    if not reports:
        return None
    job = await session.get(Job, reports[0].job_id)
    if job is None:
        return None
    try:
        profile, _ = await confirmed_classification(session, job)
    except ModelNotPermittedError:
        return None
    return profile
