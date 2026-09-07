"""The P11 acceptance readout: one finished run, measured against the fix sequence.

`docs/archive/polish-phase-1.md` closes with a table of what must be true when the first
complete run's request is re-run against the fixed code. Half of that table is a live
run only an operator can pay for; the other half is a read of rows — and a read of rows
belongs in deterministic code, not in an afternoon with two terminals and the document
open. This module is that read: every check queries what the run recorded, recomputes
nothing a model produced, and states the measured value beside the requirement so the
diff *is* the output.

The checks encode the properties, not the baseline numbers. The doc's table says "86 of
86 citations" because that run had 86; the property is that every citation the report
rests on was verified or knowingly overridden. Where a later decision moved the ground —
ADR 0059 reinstated peer acquisition after P4 withdrew it, so "peer documents fetched: 0"
is no longer the requirement — the check holds the current invariant instead: whatever
was fetched, the report's own evidence chain reaches only the subject's documents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import (
    Citation,
    Claim,
    Company,
    Cost,
    Evaluation,
    Job,
    Report,
    ReportSection,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
)
from aer.errors import ValidationError
from aer.render.glance import glance_content
from aer.services.mandate import mandate_of
from aer.services.subject import subject_name

__all__ = ["AcceptanceCheck", "AcceptanceReadout", "acceptance_readout"]

_log = structlog.get_logger("aer.services.acceptance")


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    """One row of the readout: the requirement, what the rows say, and the verdict.

    ``passed`` is ``None`` for a figure with no requirement — spend is reported for the
    operator's own comparison against the baseline, because P7 and P8 recalibrated the
    budgets since that baseline was recorded and a hard band here would fail honest runs.
    """

    name: str
    required: str
    measured: str
    passed: bool | None


@dataclass(frozen=True, slots=True)
class AcceptanceReadout:
    job_id: uuid.UUID
    subject: str
    checks: tuple[AcceptanceCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed is not False for check in self.checks)


async def acceptance_readout(session: AsyncSession, *, job_id: uuid.UUID) -> AcceptanceReadout:
    """Measure one run against the P11 requirements. Rows only; nothing recomputed.

    Raises:
        ValidationError: If the job does not exist. A readout of a run that never
            happened would be a table of zeros that looks like a failing run.
    """
    job = await session.get(Job, job_id)
    if job is None:
        message = f"No run {job_id} exists to measure."
        raise ValidationError(message, context={"job_id": str(job_id)})
    request = await mandate_of(session, job)
    if request is None:  # pragma: no cover -- FK-guaranteed
        message = f"Run {job_id} has no research request."
        raise ValidationError(message, context={"job_id": str(job_id)})

    # The filer's own name, not the one typed into the form (gap A67).
    subject = f"{request.ticker} — {await subject_name(session, request)}"
    checks = (
        await _report_exists(session, job),
        await _sections_generated(session, job),
        await _citations_verified(session, job),
        await _blocking_metrics(session, job),
        await _cited_sources_are_subjects(session, job, request=request),
        await _front_page_is_whole(session, job, request=request),
        await _spend(session, job),
    )
    readout = AcceptanceReadout(job_id=job.id, subject=subject, checks=checks)
    _log.info(
        "acceptance.measured",
        job_id=str(job.id),
        passed=readout.passed,
        failing=[check.name for check in checks if check.passed is False],
    )
    return readout


async def _report_exists(session: AsyncSession, job: Job) -> AcceptanceCheck:
    report = await session.scalar(select(Report).where(Report.job_id == job.id))
    if report is None:
        return AcceptanceCheck(
            name="report",
            required="the run produced a report",
            measured=f"no report; run status {job.status.value}",
            passed=False,
        )
    state = "approved and immutable" if report.immutable else "awaiting approval"
    return AcceptanceCheck(
        name="report",
        required="the run produced a report",
        measured=state,
        passed=True,
    )


async def _sections_generated(session: AsyncSession, job: Job) -> AcceptanceCheck:
    """The doc's "15 of 16 or better": at most one section may have failed, none pending."""
    rows = await session.execute(
        select(ReportSection.status, func.count(ReportSection.id))
        .where(ReportSection.job_id == job.id)
        .group_by(ReportSection.status)
    )
    counted: dict[SectionStatus, int] = dict(rows.tuples().all())
    total = sum(counted.values())
    generated = counted.get(SectionStatus.GENERATED, 0)
    pending = counted.get(SectionStatus.PENDING, 0)
    return AcceptanceCheck(
        name="sections",
        required="all but at most one generated, none pending",
        measured=f"{generated} of {total} generated, {pending} pending",
        passed=total > 0 and pending == 0 and generated >= total - 1,
    )


async def _citations_verified(session: AsyncSession, job: Job) -> AcceptanceCheck:
    """Every citation verified, or knowingly overridden — never silently unverified."""
    rows = list(
        await session.execute(
            select(Citation.excerpt_verified, Citation.override_reason)
            .join(Claim, Claim.id == Citation.claim_id)
            .join(ReportSection, ReportSection.id == Claim.report_section_id)
            .where(ReportSection.job_id == job.id)
        )
    )
    verified = sum(1 for excerpt_verified, _ in rows if excerpt_verified)
    overridden = sum(1 for excerpt_verified, override in rows if not excerpt_verified and override)
    unaccounted = len(rows) - verified - overridden
    measured = f"{verified} of {len(rows)} verified"
    if overridden:
        measured += f", {overridden} overridden with a reason"
    if unaccounted:
        measured += f", {unaccounted} neither"
    return AcceptanceCheck(
        name="citations",
        required="every citation verified or overridden with a reason",
        measured=measured,
        passed=len(rows) > 0 and unaccounted == 0,
    )


async def _blocking_metrics(session: AsyncSession, job: Job) -> AcceptanceCheck:
    """The evaluation gate's own verdicts, including the two P11 names outright.

    ``passed IS NULL`` is the not-exercised state and does not fail a run; a metric that
    ran and failed does, whatever its name — the doc names ``numerical_consistency`` and
    ``presentation_integrity`` because those were the two that failed on the baseline.
    """
    rows = list(await session.scalars(select(Evaluation).where(Evaluation.job_id == job.id)))
    failing = sorted({row.metric for row in rows if row.passed is False})
    exercised = sum(1 for row in rows if row.passed is not None)
    measured = f"{exercised} metric(s) exercised"
    measured += f"; failing: {', '.join(failing)}" if failing else ", none failing"
    return AcceptanceCheck(
        name="metrics",
        required="every exercised blocking metric passed",
        measured=measured,
        passed=len(rows) > 0 and not failing,
    )


async def _cited_sources_are_subjects(
    session: AsyncSession, job: Job, *, request: ResearchRequest
) -> AcceptanceCheck:
    """The report's evidence chain reaches the subject's documents and nobody else's.

    This is the current form of the doc's "sources: Amazon only" row. ADR 0059 makes
    peer documents legitimate *in the database*; ADR 0061 makes them illegitimate *in
    the subject's evidence* — so the boundary measured is what the report actually
    cites. A document with no company attribution (macro, market-wide) is not a foreign
    issuer and does not fail the check.
    """
    rows = list(
        await session.execute(
            select(func.distinct(SourceDocument.company_id))
            .join(Citation, Citation.source_document_id == SourceDocument.id)
            .join(Claim, Claim.id == Citation.claim_id)
            .join(ReportSection, ReportSection.id == Claim.report_section_id)
            .where(ReportSection.job_id == job.id)
        )
    )
    cited = {company_id for (company_id,) in rows}
    foreign = {
        company_id
        for company_id in cited
        if company_id is not None and company_id != request.company_id
    }
    names: list[str] = []
    if foreign:
        names = sorted(await session.scalars(select(Company.name).where(Company.id.in_(foreign))))
    return AcceptanceCheck(
        name="cited_sources",
        required="cited documents belong to the subject (or carry no issuer)",
        measured=(
            f"{len(foreign)} foreign issuer(s) cited" + (f": {', '.join(names)}" if names else "")
        ),
        passed=not foreign,
    )


async def _front_page_is_whole(
    session: AsyncSession, job: Job, *, request: ResearchRequest
) -> AcceptanceCheck:
    """The at-a-glance block stands, built from the subject's own figures.

    ``glance_content`` is the shipping builder and refuses to mix issuers (P2); calling
    it here is a read of the same rows the rendered report read, so "the front page is
    whole and the subject's" is measured by the code that owns the refusal.
    """
    glance = await glance_content(session, job=job, request=request)
    if glance.refused is not None:
        return AcceptanceCheck(
            name="front_page",
            required="the at-a-glance block is present and the subject's",
            measured=f"withheld — {glance.refused}",
            passed=False,
        )
    rows = len((glance.content or {}).get("latest", []))
    return AcceptanceCheck(
        name="front_page",
        required="the at-a-glance block is present and the subject's",
        measured=f"present, {rows} headline row(s)",
        passed=rows > 0,
    )


async def _spend(session: AsyncSession, job: Job) -> AcceptanceCheck:
    """Reported, not judged: the baseline's £6.26 predates the P7/P8 recalibration."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.job_id == job.id)
    )
    return AcceptanceCheck(
        name="spend",
        required="reported for comparison against the baseline",
        measured=f"£{Decimal(total or 0):.2f}",
        passed=None,
    )
