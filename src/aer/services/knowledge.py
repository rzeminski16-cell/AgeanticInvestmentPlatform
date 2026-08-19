"""Measuring the knowledge graph: how big, how connected, how fresh, how complete.

`docs/knowledge-graph.md` task K5. A knowledge base you cannot measure is one you cannot
tell is decaying, and until this existed there was no way to ask the platform how much it
knew — the only signal was the length of the file list an export happened to print.

**Everything here is a read of the database, never of the vault's content.** The vault is
a one-directional projection and reading it back as a source of truth is precisely the
contamination `docs/PLAN.md` §2.8 forbids. The one exception is deliberate and narrow:
the vault half of :class:`KnowledgeStats` lists *filenames* under the vault root to find
drift — files no export ever recorded writing. Names, never contents, and only to report
that the projection and the record disagree.

**The graph is the confirmed peer relation**, read through
:func:`aer.obsidian.graph.peer_edges`, plus every company carrying approved research of
its own. A company named as a comparable but never researched is a *stub*: a real node,
and the ratio of stubs to researched companies is the most honest single measure of how
much of the neighbourhood you have actually looked at.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.sectors import ModelNotPermittedError
from aer.db.models import Company, Job, ObsidianExport, Report, ResearchRequest, SourceDocument
from aer.obsidian.graph import peer_edges, reachable_from
from aer.services.history import approved_reports_for, catalyst_outcomes_for
from aer.services.sectors import confirmed_classification

__all__ = [
    "STALE_AFTER_DAYS",
    "GraphCoverage",
    "GraphFreshness",
    "GraphShape",
    "GraphSize",
    "KnowledgeStats",
    "OpenCatalyst",
    "StaleCompany",
    "VaultHealth",
    "knowledge_stats",
]

_log = structlog.get_logger("aer.services.knowledge")

# How long a company may go unrevisited before the graph calls its research stale. Six
# months is one reporting cycle plus a quarter: long enough that a company researched
# after its annual report is not nagged about before its next interim, short enough that
# a thesis nobody has revisited for two reporting periods is visible.
STALE_AFTER_DAYS: Final = 180

# The directories an export owns. A file anywhere else under the vault root — most
# obviously `99-Personal/` — is the operator's and is never counted as drift.
_GENERATED_DIRECTORIES: Final[tuple[str, ...]] = (
    "00-Meta",
    "10-Companies",
    "20-Runs",
    "30-Industries",
    "40-Themes",
    "50-Catalysts",
    "90-Sources",
)


@dataclass(frozen=True, slots=True)
class GraphSize:
    """How much is in the map.

    ``catalyst_nodes`` rather than the obvious plural, here and throughout: the plural is
    a *seeded section key*, and no module outside the seed migration may name one, or the
    next section becomes a code change instead of a row. Nothing here reads that section —
    the count comes from whichever sections carry catalyst-shaped items — so the name is
    the only thing that would have said otherwise. The rest of the codebase keeps the same
    distance by staying singular.
    """

    companies: int
    researched: int
    stubs: int
    approved_reports: int
    industries: int
    catalyst_nodes: int
    sources: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "companies": self.companies,
            "researched": self.researched,
            "stubs": self.stubs,
            "approved_reports": self.approved_reports,
            "industries": self.industries,
            "catalyst_nodes": self.catalyst_nodes,
            "sources": self.sources,
        }


@dataclass(frozen=True, slots=True)
class GraphShape:
    """How connected it is.

    ``edges`` counts unordered pairs: the relation is symmetric, so counting both
    directions would report twice as much connection as exists.
    """

    edges: int
    components: int
    largest_component: int
    isolated: int
    mean_degree: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "edges": self.edges,
            "components": self.components,
            "largest_component": self.largest_component,
            "isolated": self.isolated,
            "mean_degree": str(self.mean_degree),
        }


@dataclass(frozen=True, slots=True)
class GraphCoverage:
    """How much of what the map names has actually been looked at."""

    researched_ratio: Decimal
    unclassified: int
    single_member_industries: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "researched_ratio": str(self.researched_ratio),
            "unclassified": self.unclassified,
            "single_member_industries": self.single_member_industries,
        }


@dataclass(frozen=True, slots=True)
class StaleCompany:
    """A company whose newest approved research is older than the horizon."""

    company_id: uuid.UUID
    ticker: str
    name: str
    newest_as_of: date
    days_since: int


@dataclass(frozen=True, slots=True)
class OpenCatalyst:
    """A catalyst whose window has closed, with nothing recorded about what happened.

    Until K4 gives an operator somewhere to record an outcome, *every* passed catalyst is
    open — so this is the full backlog rather than a filtered one, and the count is
    honest about that.
    """

    company_id: uuid.UUID
    ticker: str
    label: str
    expected_timing: str
    report_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class GraphFreshness:
    """Whether the map is current, and what is waiting to be looked at."""

    newest: date | None
    oldest: date | None
    stale: tuple[StaleCompany, ...] = ()
    closed_windows: tuple[OpenCatalyst, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "newest": self.newest.isoformat() if self.newest else None,
            "oldest": self.oldest.isoformat() if self.oldest else None,
            "stale": [
                {
                    "company_id": str(row.company_id),
                    "ticker": row.ticker,
                    "name": row.name,
                    "newest_as_of": row.newest_as_of.isoformat(),
                    "days_since": row.days_since,
                }
                for row in self.stale
            ],
            "closed_windows": [
                {
                    "company_id": str(row.company_id),
                    "ticker": row.ticker,
                    "label": row.label,
                    "expected_timing": row.expected_timing,
                    "report_id": str(row.report_id),
                }
                for row in self.closed_windows
            ],
        }


@dataclass(frozen=True, slots=True)
class VaultHealth:
    """Whether the projection has kept up with the record.

    ``unexported`` is the figure that matters most on a young graph: export is manual, so
    an approved report nobody exported is knowledge the map does not have, and nothing
    else would ever say so.
    """

    configured: bool
    last_export_at: datetime | None
    exported_reports: int
    unexported: tuple[uuid.UUID, ...] = ()
    recorded_files: int = 0
    drifted: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "last_export_at": self.last_export_at.isoformat() if self.last_export_at else None,
            "exported_reports": self.exported_reports,
            "unexported": [str(item) for item in self.unexported],
            "recorded_files": self.recorded_files,
            "drifted": list(self.drifted),
        }


@dataclass(frozen=True, slots=True)
class KnowledgeStats:
    """Everything the knowledge surface reports, as one structure."""

    size: GraphSize
    shape: GraphShape
    coverage: GraphCoverage
    freshness: GraphFreshness
    vault: VaultHealth
    measured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "size": self.size.as_dict(),
            "shape": self.shape.as_dict(),
            "coverage": self.coverage.as_dict(),
            "freshness": self.freshness.as_dict(),
            "vault": self.vault.as_dict(),
            "measured_at": self.measured_at.isoformat(),
        }


async def knowledge_stats(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    as_of: date | None = None,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> KnowledgeStats:
    """Measure the graph.

    Args:
        settings: Supplies the vault root for the drift check. Without it the vault
            section reports ``configured=False`` and the database half is unaffected —
            the statistics are about the graph, and the vault is one projection of it.
        as_of: The date staleness and catalyst windows are judged against. Defaults to
            today; passed explicitly by tests so a fixture cannot age into failing.
    """
    today = as_of or datetime.now(UTC).date()
    edges = await peer_edges(session)
    researched = await _researched_companies(session)
    nodes = set(edges) | set(researched)

    size = await _size(session, nodes=nodes, researched=researched, today=today)
    shape = _shape(nodes=nodes, edges=edges)
    coverage = await _coverage(session, nodes=nodes, researched=researched)
    freshness = await _freshness(
        session, researched=researched, today=today, stale_after_days=stale_after_days
    )
    vault = await _vault_health(session, settings=settings)

    _log.info(
        "knowledge.measured",
        companies=size.companies,
        researched=size.researched,
        edges=shape.edges,
        components=shape.components,
        unexported=len(vault.unexported),
    )
    return KnowledgeStats(
        size=size, shape=shape, coverage=coverage, freshness=freshness, vault=vault
    )


# -- The database half ------------------------------------------------------------------


async def _researched_companies(session: AsyncSession) -> dict[uuid.UUID, Company]:
    """Every company carrying at least one approved report, by id."""
    rows = await session.scalars(
        select(Company)
        .join(Report, Report.company_id == Company.id)
        .where(Report.immutable.is_(True))
        .distinct()
    )
    return {row.id: row for row in rows}


async def _size(
    session: AsyncSession,
    *,
    nodes: set[uuid.UUID],
    researched: dict[uuid.UUID, Company],
    today: date,
) -> GraphSize:
    approved = await session.scalar(select(func.count(Report.id)).where(Report.immutable.is_(True)))
    industries: set[str] = set()
    catalyst_keys: set[tuple[uuid.UUID, str]] = set()
    for company_id in researched:
        for prior in await approved_reports_for(session, company_id=company_id):
            job = await session.get(Job, prior.job_id)
            if job is not None:
                industries |= await _industry_keys(session, job)
            for outcome in await catalyst_outcomes_for(session, prior=prior, as_of=today):
                catalyst_keys.add((company_id, outcome.label))

    sources = await session.scalar(
        select(func.count(func.distinct(SourceDocument.id)))
        .join(ResearchRequest, ResearchRequest.id == SourceDocument.request_id)
        .join(Report, Report.request_id == ResearchRequest.id)
        .where(Report.immutable.is_(True))
    )
    return GraphSize(
        companies=len(nodes),
        researched=len(researched),
        stubs=len(nodes - set(researched)),
        approved_reports=int(approved or 0),
        industries=len(industries),
        catalyst_nodes=len(catalyst_keys),
        sources=int(sources or 0),
    )


async def _industry_keys(session: AsyncSession, job: Job) -> set[str]:
    """The confirmed sector key for a run, or nothing.

    An unconfirmed specialist classification raises for a *caller that would act on it* —
    that refusal decides which valuation models may run. Measuring is not acting, and a
    statistic that crashes the whole surface is worse than one that counts the company as
    unclassified, which is what the graph itself does with the same read.
    """
    try:
        profile, _ = await confirmed_classification(session, job)
    except ModelNotPermittedError:
        _log.warning("knowledge.classification_unconfirmed", job_id=str(job.id))
        return set()
    return {profile.key} if profile is not None else set()


def _shape(*, nodes: set[uuid.UUID], edges: dict[uuid.UUID, set[uuid.UUID]]) -> GraphShape:
    """Connectivity, over the graph's own nodes.

    Components are found by walking the same reachability the exporter uses, so "one
    component" here and "the notes one export writes" are the same set by construction.
    """
    pairs = {
        frozenset((node, peer))
        for node, neighbours in edges.items()
        for peer in neighbours
        if node != peer
    }
    seen: set[uuid.UUID] = set()
    sizes: list[int] = []
    for node in sorted(nodes, key=str):
        if node in seen:
            continue
        component = reachable_from({node}, edges) & nodes
        seen |= component
        sizes.append(len(component))

    degree_total = sum(len(edges.get(node, set()) & nodes) for node in nodes)
    mean_degree = (
        (Decimal(degree_total) / Decimal(len(nodes))).quantize(Decimal("0.01"))
        if nodes
        else Decimal(0)
    )
    return GraphShape(
        edges=len(pairs),
        components=len(sizes),
        largest_component=max(sizes) if sizes else 0,
        isolated=sum(1 for node in nodes if not (edges.get(node, set()) & nodes)),
        mean_degree=mean_degree,
    )


async def _coverage(
    session: AsyncSession, *, nodes: set[uuid.UUID], researched: dict[uuid.UUID, Company]
) -> GraphCoverage:
    ratio = (
        (Decimal(len(researched)) / Decimal(len(nodes))).quantize(Decimal("0.01"))
        if nodes
        else Decimal(0)
    )
    members: dict[str, set[uuid.UUID]] = {}
    unclassified = 0
    for company_id in researched:
        keys: set[str] = set()
        for prior in await approved_reports_for(session, company_id=company_id):
            job = await session.get(Job, prior.job_id)
            if job is None:  # pragma: no cover -- FK-guaranteed
                continue
            keys |= await _industry_keys(session, job)
        if not keys:
            unclassified += 1
        for key in keys:
            members.setdefault(key, set()).add(company_id)

    return GraphCoverage(
        researched_ratio=ratio,
        unclassified=unclassified,
        single_member_industries=sum(1 for holders in members.values() if len(holders) == 1),
    )


async def _freshness(
    session: AsyncSession,
    *,
    researched: dict[uuid.UUID, Company],
    today: date,
    stale_after_days: int,
) -> GraphFreshness:
    newest: date | None = None
    oldest: date | None = None
    stale: list[StaleCompany] = []
    passed: list[OpenCatalyst] = []

    for company_id, company in researched.items():
        priors = await approved_reports_for(session, company_id=company_id)
        if not priors:  # pragma: no cover -- researched means at least one
            continue
        dates = [prior.as_of_date for prior in priors]
        latest = max(dates)
        earliest = min(dates)
        newest = latest if newest is None else max(newest, latest)
        oldest = earliest if oldest is None else min(oldest, earliest)

        age = (today - latest).days
        if age > stale_after_days:
            stale.append(
                StaleCompany(
                    company_id=company_id,
                    ticker=company.ticker or "",
                    name=company.name,
                    newest_as_of=latest,
                    days_since=age,
                )
            )

        for prior in priors:
            for outcome in await catalyst_outcomes_for(session, prior=prior, as_of=today):
                if outcome.status == "passed":
                    passed.append(
                        OpenCatalyst(
                            company_id=company_id,
                            ticker=company.ticker or "",
                            label=outcome.label,
                            expected_timing=outcome.expected_timing,
                            report_id=prior.id,
                        )
                    )

    return GraphFreshness(
        newest=newest,
        oldest=oldest,
        stale=tuple(sorted(stale, key=lambda row: (-row.days_since, row.ticker))),
        closed_windows=tuple(sorted(passed, key=lambda row: (row.ticker, row.label))),
    )


# -- The vault half ---------------------------------------------------------------------


async def _vault_health(session: AsyncSession, *, settings: Settings | None) -> VaultHealth:
    exports = list(
        await session.scalars(select(ObsidianExport).order_by(ObsidianExport.exported_at))
    )
    exported_report_ids = {row.report_id for row in exports}
    approved = set(await session.scalars(select(Report.id).where(Report.immutable.is_(True))))
    recorded: set[str] = {name for row in exports for name in row.files}

    root = settings.obsidian_vault_root if settings is not None else None
    return VaultHealth(
        configured=root is not None,
        last_export_at=exports[-1].exported_at if exports else None,
        exported_reports=len(exported_report_ids),
        unexported=tuple(sorted(approved - exported_report_ids, key=str)),
        recorded_files=len(recorded),
        drifted=_drifted(root, recorded=recorded),
    )


def _drifted(root: Path | None, *, recorded: set[str]) -> tuple[str, ...]:
    """Generated-directory files under the vault root that no export recorded writing.

    Filenames only — never contents — and only inside the directories an export owns, so
    the operator's own notes cannot be reported as drift however they are filed. A hit
    means the projection and the record disagree: a note left behind by a deleted report,
    or one an earlier generator wrote under a name this one no longer uses.
    """
    if root is None or not root.is_dir():
        return ()
    found: list[str] = []
    for directory in _GENERATED_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(root).as_posix()
            if relative not in recorded:
                found.append(relative)
    return tuple(found)
