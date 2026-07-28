"""Report endpoints: read it, download it.

The download returns the **archived artefact**, not a re-render. A report is the document
a decision was made against; regenerating it on request would produce something that might
differ from what was approved, and the difference would be invisible. The stored bytes are
content-addressed, so what is served is provably what was frozen.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response
from starlette.status import HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.db.models import Artefact, Report, ResearchRequest, User
from aer.errors import AerError
from aer.storage.local import LocalArtefactStore

__all__ = ["ReportRead", "router"]

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportNotFoundError(AerError):
    """No such report, or it belongs to someone else."""

    code = "report_not_found"
    http_status = HTTP_404_NOT_FOUND


class ReportRead(BaseModel):
    """A report's metadata and its rendered body."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    job_id: uuid.UUID
    request_id: uuid.UUID
    as_of_date: str
    rating: str | None
    confidence: float | None
    content_hash: str
    immutable: bool
    sections: list[Any]
    markdown: str


@router.get("/{report_id}", response_model=ReportRead, summary="Retrieve a report")
async def read_report(report_id: uuid.UUID, session: DbSession, user: CurrentUser) -> ReportRead:
    report = await _owned(session, report_id=report_id, user=user)
    content = dict(report.content or {})
    return ReportRead(
        id=report.id,
        job_id=report.job_id,
        request_id=report.request_id,
        as_of_date=report.as_of_date.isoformat(),
        rating=report.rating,
        confidence=report.confidence,
        content_hash=report.content_hash,
        immutable=report.immutable,
        sections=list(content.get("sections", [])),
        markdown=str(content.get("markdown", "")),
    )


@router.get("/for-run/{job_id}", response_model=ReportRead, summary="The report a run produced")
async def read_report_for_run(
    job_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> ReportRead:
    report = await session.scalar(
        select(Report)
        .join(ResearchRequest, ResearchRequest.id == Report.request_id)
        .where(Report.job_id == job_id, ResearchRequest.user_id == user.id)
    )
    if report is None:
        message = f"No report for run {job_id}."
        raise ReportNotFoundError(message, context={"job_id": str(job_id)})

    return await read_report(report.id, session, user)


@router.get(
    "/{report_id}/download",
    summary="Download the archived Markdown",
    response_class=Response,
)
async def download_report(
    report_id: uuid.UUID, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Serve the archived Markdown artefact.

    Read from the content-addressed store rather than re-rendered. A re-render could
    differ from what was approved — by a section definition that has since changed, by a
    fact that has since been superseded — and the difference would be undetectable. The
    stored bytes hash to their own name, so what is served is provably what was frozen.
    """
    report = await _owned(session, report_id=report_id, user=user)

    if report.markdown_artefact_id is None:
        message = "This report has no archived Markdown. It was never rendered."
        raise ReportNotFoundError(message, context={"report_id": str(report_id)})

    artefact = await session.get(Artefact, report.markdown_artefact_id)
    if artefact is None:
        message = "The report's archived Markdown is missing from the database."
        raise ReportNotFoundError(message, context={"report_id": str(report_id)})

    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    body = await store.read(artefact.sha256)

    filename = f"research-{report.as_of_date.isoformat()}-{str(report.id)[:8]}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # The digest of exactly what was served, so a recipient can verify it against
            # the report record without trusting the transport.
            "X-Artefact-SHA256": artefact.sha256,
        },
    )


async def _owned(session: AsyncSession, *, report_id: uuid.UUID, user: User) -> Report:
    report: Report | None = await session.scalar(
        select(Report)
        .join(ResearchRequest, ResearchRequest.id == Report.request_id)
        .where(Report.id == report_id, ResearchRequest.user_id == user.id)
    )
    if report is None:
        message = f"No report {report_id}."
        raise ReportNotFoundError(message, context={"report_id": str(report_id)})
    return report


async def report_for_job(session: AsyncSession, job_id: uuid.UUID) -> Report | None:
    """The report a run produced, if it has one. Used by the web pages."""
    found: Report | None = await session.scalar(select(Report).where(Report.job_id == job_id))
    return found
