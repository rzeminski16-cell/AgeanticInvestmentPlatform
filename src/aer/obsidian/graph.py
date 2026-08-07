"""The link graph an export projects: peers, industries and catalysts, from rows alone.

Everything here is a deterministic read of approved runs and their confirmed gates. The
graph exists so the exporter can write a *closed* set of notes — every ``[[link]]`` it
puts in a file names another file it also writes — and closure is a property of what is
read, not of what happened to be exported before.

**Only confirmed state produces a link.** A proposed-but-unconfirmed peer set or sector
classification supports nothing, and that includes a line in a research journal; those
jobs simply contribute no edges, with a log line saying so. This is the same refusal the
comps and sector services make, applied to links instead of tables — skipping is not a
relaxation, because *using* the unconfirmed state is exactly what is being declined.

**The competitor relation is symmetric by construction.** An approved run of A naming B
as a comparable creates the edge in both companies' notes, whether or not B has ever
named A back — section 2.8's "symmetric ``competitors`` array maintained by the
exporter". The export covers the whole connected component of that relation, which is
what makes every competitor link resolve.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.sectors import ModelNotPermittedError, SectorProfile
from aer.db.models import Company, Job, ObsidianExport, Report, ResearchRequest
from aer.services.comps import PeerSetNotConfirmedError, confirmed_peer_set
from aer.services.history import (
    approved_reports_for,
    catalyst_outcomes_for,
    timing_deadline,
)
from aer.services.sectors import confirmed_classification

__all__ = ["CatalystView", "CompanyView", "LinkGraph", "RunView", "build_graph"]

_log = structlog.get_logger("aer.obsidian.graph")


@dataclass(frozen=True, slots=True)
class RunView:
    """One approved run, with the links its own gates confirmed."""

    report: Report
    request: ResearchRequest
    job: Job
    industry: SectorProfile | None
    peer_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class CompanyView:
    """A company in the component: its approved runs oldest first, and its relations.

    ``runs`` is empty for a stub — a company named as a comparable that has no approved
    research of its own yet. ``industry`` is the *latest* approved run's confirmed
    classification, because the company note states what the company is now; each run
    note carries its own run's classification, which is how methodology drift stays
    visible.
    """

    company: Company
    runs: tuple[RunView, ...]
    competitor_ids: tuple[uuid.UUID, ...]
    industry: SectorProfile | None


@dataclass(frozen=True, slots=True)
class CatalystView:
    """One expected event, aggregated across every approved run that proposed it.

    Identity is (company, label): two runs stating the same catalyst are refining one
    expectation, not creating two. ``expected_timing`` is the latest proposer's — the
    freshest stated view — and ``resolved_by`` is the earliest approved run of the same
    company whose as-of date lies beyond the parsed deadline. Purely calendar and rows:
    no wall clock, no judgement about whether the event occurred.
    """

    company: Company
    label: str
    expected_timing: str
    deadline: date | None
    thesis_refs: tuple[str, ...]
    proposals: tuple[tuple[RunView, str], ...]
    resolved_by: RunView | None


@dataclass(frozen=True, slots=True)
class LinkGraph:
    companies: dict[uuid.UUID, CompanyView]
    order: tuple[uuid.UUID, ...]
    catalyst_views: tuple[CatalystView, ...]
    industries: dict[str, SectorProfile]
    industry_members: dict[str, tuple[Company, ...]] = field(default_factory=dict)
    subject_industry: SectorProfile | None = None
    subject_peer_ids: tuple[uuid.UUID, ...] = ()


async def build_graph(
    session: AsyncSession, *, job: Job, report: Report, company: Company | None
) -> LinkGraph:
    """The connected component of the peer relation around the exported report."""
    edges = await _peer_edges(session)
    subject_peer_ids = await _confirmed_peers(session, job)
    subject_industry = await _confirmed_industry(session, job)

    seeds: set[uuid.UUID] = set(subject_peer_ids)
    if company is not None:
        seeds.add(company.id)
    component = _reachable(seeds, edges)

    rows = {
        row.id: row
        for row in await session.scalars(select(Company).where(Company.id.in_(component)))
    }
    # A peer identifier that is a well-formed UUID but no stored company cannot become a
    # resolvable note; it falls out of the graph here, together with its edges.
    component &= rows.keys()

    companies: dict[uuid.UUID, CompanyView] = {}
    for company_id in component:
        runs = await _runs_for(session, company_id=company_id)
        competitor_ids = tuple(
            sorted(
                (peer for peer in edges.get(company_id, set()) if peer in component),
                key=lambda peer: _company_sort_key(rows[peer]),
            )
        )
        companies[company_id] = CompanyView(
            company=rows[company_id],
            runs=runs,
            competitor_ids=competitor_ids,
            industry=runs[-1].industry if runs else None,
        )

    order = _ordered(companies, subject_id=company.id if company is not None else None)
    catalyst_views = await _catalyst_views(session, companies, order)

    industries: dict[str, SectorProfile] = {}
    if subject_industry is not None:
        industries[subject_industry.key] = subject_industry
    for view in companies.values():
        for run in view.runs:
            if run.industry is not None:
                industries[run.industry.key] = run.industry

    members = await _industry_members(session, companies, industries)

    _log.info(
        "obsidian.graph_built",
        report_id=str(report.id),
        companies=len(companies),
        catalyst_count=len(catalyst_views),
        industries=sorted(industries),
    )
    return LinkGraph(
        companies=companies,
        order=order,
        catalyst_views=catalyst_views,
        industries=industries,
        industry_members=members,
        subject_industry=subject_industry,
        subject_peer_ids=tuple(peer for peer in subject_peer_ids if peer in component),
    )


def _company_sort_key(company: Company) -> tuple[str, str]:
    return (company.ticker, company.name)


def _ordered(
    companies: dict[uuid.UUID, CompanyView], *, subject_id: uuid.UUID | None
) -> tuple[uuid.UUID, ...]:
    rest = sorted(
        (company_id for company_id in companies if company_id != subject_id),
        key=lambda company_id: _company_sort_key(companies[company_id].company),
    )
    head = [subject_id] if subject_id is not None and subject_id in companies else []
    return (*head, *rest)


def _reachable(seeds: set[uuid.UUID], edges: dict[uuid.UUID, set[uuid.UUID]]) -> set[uuid.UUID]:
    found = set(seeds)
    frontier = list(seeds)
    while frontier:
        current = frontier.pop()
        for neighbour in edges.get(current, set()):
            if neighbour not in found:
                found.add(neighbour)
                frontier.append(neighbour)
    return found


async def _peer_edges(session: AsyncSession) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Every confirmed comparable relation across every approved run, both directions."""
    reports = await session.scalars(
        select(Report).where(Report.immutable.is_(True), Report.company_id.is_not(None))
    )
    edges: dict[uuid.UUID, set[uuid.UUID]] = {}
    for report in reports:
        report_job = await session.get(Job, report.job_id)
        if report_job is None or report.company_id is None:  # pragma: no cover -- FK-guaranteed
            continue
        for peer_id in await _confirmed_peers(session, report_job):
            if peer_id == report.company_id:
                continue
            edges.setdefault(report.company_id, set()).add(peer_id)
            edges.setdefault(peer_id, set()).add(report.company_id)
    return edges


async def _confirmed_peers(session: AsyncSession, job: Job) -> tuple[uuid.UUID, ...]:
    try:
        peers = await confirmed_peer_set(session, job)
    except PeerSetNotConfirmedError:
        _log.warning("obsidian.peer_set_unconfirmed", job_id=str(job.id))
        return ()
    found: list[uuid.UUID] = []
    for peer in peers:
        try:
            found.append(uuid.UUID(peer.identifier))
        except ValueError:
            # An identifier that is not a company row (a ticker from a model proposal,
            # say) names nothing a note can link to.
            _log.warning(
                "obsidian.peer_identifier_unresolvable",
                job_id=str(job.id),
                identifier=peer.identifier,
            )
    return tuple(found)


async def _confirmed_industry(session: AsyncSession, job: Job) -> SectorProfile | None:
    try:
        profile, _ = await confirmed_classification(session, job)
    except ModelNotPermittedError:
        _log.warning("obsidian.classification_unconfirmed", job_id=str(job.id))
        return None
    return profile


async def _runs_for(session: AsyncSession, *, company_id: uuid.UUID) -> tuple[RunView, ...]:
    views: list[RunView] = []
    for prior in reversed(await approved_reports_for(session, company_id=company_id)):
        request = await session.get(ResearchRequest, prior.request_id)
        run_job = await session.get(Job, prior.job_id)
        if request is None or run_job is None:  # pragma: no cover -- FK-guaranteed rows
            continue
        views.append(
            RunView(
                report=prior,
                request=request,
                job=run_job,
                industry=await _confirmed_industry(session, run_job),
                peer_ids=await _confirmed_peers(session, run_job),
            )
        )
    return tuple(views)


@dataclass(slots=True)
class _Gathered:
    """One catalyst label mid-aggregation, before it freezes into a view."""

    timing: str = ""
    refs: list[str] = field(default_factory=list)
    proposals: list[tuple[RunView, str]] = field(default_factory=list)


async def _catalyst_views(
    session: AsyncSession,
    companies: dict[uuid.UUID, CompanyView],
    order: tuple[uuid.UUID, ...],
) -> tuple[CatalystView, ...]:
    views: list[CatalystView] = []
    for company_id in order:
        view = companies[company_id]
        gathered: dict[str, _Gathered] = {}
        for run in view.runs:
            outcomes = await catalyst_outcomes_for(
                session, prior=run.report, as_of=run.report.as_of_date
            )
            for outcome in outcomes:
                label = outcome.label.strip()
                if not label:
                    continue
                entry = gathered.setdefault(label, _Gathered())
                ref = f"run-{run.report.id}"
                if ref not in entry.refs:
                    entry.refs.append(ref)
                entry.proposals.append((run, outcome.rationale))
                # Oldest-to-newest walk, so the last assignment is the freshest view.
                entry.timing = outcome.expected_timing
        for label, entry in gathered.items():
            deadline = timing_deadline(entry.timing)
            views.append(
                CatalystView(
                    company=view.company,
                    label=label,
                    expected_timing=entry.timing,
                    deadline=deadline,
                    thesis_refs=tuple(entry.refs),
                    proposals=tuple(entry.proposals),
                    resolved_by=_resolver(view.runs, deadline),
                )
            )
    return tuple(views)


def _resolver(runs: tuple[RunView, ...], deadline: date | None) -> RunView | None:
    """The earliest approved run of the same company whose as-of lies past the deadline.

    Strictly past — a run dated on the deadline itself is still inside the window, the
    same boundary :func:`aer.services.history.catalyst_outcomes_for` draws.
    """
    if deadline is None:
        return None
    for run in runs:
        if run.report.as_of_date > deadline:
            return run
    return None


async def _industry_members(
    session: AsyncSession,
    companies: dict[uuid.UUID, CompanyView],
    industries: dict[str, SectorProfile],
) -> dict[str, tuple[Company, ...]]:
    """Who belongs in each industry note's back-link list.

    Component companies, plus companies previously exported to this vault whose latest
    confirmed classification matches — so regenerating an industry note during one
    company's export does not silently drop another company the vault already holds.
    Membership follows the *latest* approved run per company, matching the company note.
    """
    members: dict[str, list[tuple[tuple[str, str], Company]]] = {key: [] for key in industries}
    for view in companies.values():
        if view.industry is not None and view.industry.key in members:
            members[view.industry.key].append((_company_sort_key(view.company), view.company))

    exported_ids = set(
        await session.scalars(
            select(Report.company_id)
            .join(ObsidianExport, ObsidianExport.report_id == Report.id)
            .where(Report.company_id.is_not(None))
            .distinct()
        )
    )
    for company_id in sorted(
        {found for found in exported_ids if found is not None} - companies.keys(), key=str
    ):
        company = await session.get(Company, company_id)
        if company is None:  # pragma: no cover -- FK-guaranteed
            continue
        latest = await approved_reports_for(session, company_id=company_id)
        if not latest:
            continue
        latest_job = await session.get(Job, latest[0].job_id)
        if latest_job is None:  # pragma: no cover -- FK-guaranteed
            continue
        profile = await _confirmed_industry(session, latest_job)
        if profile is not None and profile.key in members:
            members[profile.key].append((_company_sort_key(company), company))

    return {
        key: tuple(company for _, company in sorted(entries, key=lambda pair: pair[0]))
        for key, entries in members.items()
    }
