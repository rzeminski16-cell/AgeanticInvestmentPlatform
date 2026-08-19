"""Catalyst resolutions: the operator's answer to a closed window, never a model's (K4).

What these tests hold: a resolution attaches only to a catalyst an approved report
actually proposed; the reason is mandatory; re-recording corrects rather than duplicates;
a resolved catalyst leaves the knowledge page's open list, which since K4 means exactly
what its name says; and the vault projects the operator's record — outcome, reason, who —
into the catalyst note where the calendar sentence used to stand alone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import CatalystOutcomeKind, JobStatus
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    CatalystResolution,
    Company,
    Job,
    Report,
    ReportSection,
    SectionDefinition,
    SectionStatus,
    User,
)
from aer.errors import ValidationError
from aer.obsidian.graph import build_graph
from aer.services.catalysts import (
    known_catalyst_labels,
    record_catalyst_resolution,
    resolutions_for,
)
from aer.services.knowledge import knowledge_stats
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio

AS_OF = date(2026, 6, 30)
LABEL = "FY2026 results"


async def _company(session: AsyncSession, ticker: str = "CATR") -> Company:
    row = Company(
        name=f"{ticker} Resolutions plc",
        ticker=ticker,
        exchange="NASDAQ",
        cik=f"{abs(hash(ticker)) % 10**10:010d}",
    )
    session.add(row)
    await session.flush()
    return row


async def _approved_report_with_catalyst(
    session: AsyncSession, *, user: User, company: Company, label: str = LABEL
) -> Report:
    request = await seed_request(session, user=user, as_of_date=AS_OF)
    job = await seed_job(session, request=request)
    job.status = JobStatus.SUCCEEDED
    await session.flush()

    definition = await session.scalar(select(SectionDefinition).limit(1))
    assert definition is not None, "the migration seeds section definitions"
    session.add(
        ReportSection(
            job_id=job.id,
            section_definition_id=definition.id,
            section_key=definition.key,
            position=definition.position,
            status=SectionStatus.GENERATED,
            content={
                "catalyst_items": [
                    {
                        "label": label,
                        "expected_timing": "2026-09-30",
                        "rationale": "Full-year figures land.",
                    }
                ]
            },
        )
    )
    content: dict[str, Any] = {"sections": []}
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=AS_OF,
        immutable=True,
        approved_by=user.id,
        approved_at=datetime.now(UTC),
        content=content,
        content_hash=sha256_hex(canonical_json(content)),
    )
    session.add(report)
    await session.flush()
    return report


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = await seed_user(db_session, email="resolutions@example.invalid")
    company = await _company(db_session)
    report = await _approved_report_with_catalyst(db_session, user=user, company=company)
    return {"session": db_session, "user": user, "company": company, "report": report}


class TestRecording:
    async def test_a_resolution_lands_with_who_and_why(self, scene: dict[str, Any]) -> None:
        row = await record_catalyst_resolution(
            scene["session"],
            company_id=scene["company"].id,
            label=LABEL,
            outcome=CatalystOutcomeKind.OCCURRED,
            reason="Results published on 28 September; revenue in line.",
            actor=scene["user"],
        )

        assert row.outcome is CatalystOutcomeKind.OCCURRED
        assert row.recorded_by == "resolutions@example.invalid"
        held = await resolutions_for(scene["session"], company_id=scene["company"].id)
        assert set(held) == {LABEL}

    async def test_re_recording_corrects_rather_than_duplicates(
        self, scene: dict[str, Any]
    ) -> None:
        """Operator bookkeeping, not a gate decision: the current answer is one row."""
        session = scene["session"]
        for outcome, reason in (
            (CatalystOutcomeKind.OCCURRED, "First reading."),
            (CatalystOutcomeKind.SUPERSEDED, "The division was sold before the results."),
        ):
            await record_catalyst_resolution(
                session,
                company_id=scene["company"].id,
                label=LABEL,
                outcome=outcome,
                reason=reason,
                actor=scene["user"],
            )

        rows = list(
            await session.scalars(
                select(CatalystResolution).where(
                    CatalystResolution.company_id == scene["company"].id
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].outcome is CatalystOutcomeKind.SUPERSEDED
        assert "sold" in rows[0].reason

    async def test_a_blank_reason_is_refused(self, scene: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="reason"):
            await record_catalyst_resolution(
                scene["session"],
                company_id=scene["company"].id,
                label=LABEL,
                outcome=CatalystOutcomeKind.OCCURRED,
                reason="   ",
                actor=scene["user"],
            )

    async def test_a_label_nobody_forecast_is_refused(self, scene: dict[str, Any]) -> None:
        """Resolutions attach to the catalysts the research actually named."""
        with pytest.raises(ValidationError, match="nothing to resolve"):
            await record_catalyst_resolution(
                scene["session"],
                company_id=scene["company"].id,
                label="An event nobody proposed",
                outcome=CatalystOutcomeKind.OCCURRED,
                reason="It definitely happened.",
                actor=scene["user"],
            )

    async def test_known_labels_come_from_approved_reports(self, scene: dict[str, Any]) -> None:
        labels = await known_catalyst_labels(scene["session"], company_id=scene["company"].id)
        assert labels == {LABEL}


class TestTheOpenList:
    async def test_a_resolved_catalyst_leaves_the_open_windows(self, scene: dict[str, Any]) -> None:
        """Since K4, `closed_windows` means a closed window *nobody has answered*."""
        session = scene["session"]
        later = date(2026, 12, 31)  # past the 2026-09-30 window

        before = await knowledge_stats(session, as_of=later)
        assert [row.label for row in before.freshness.closed_windows] == [LABEL]

        await record_catalyst_resolution(
            session,
            company_id=scene["company"].id,
            label=LABEL,
            outcome=CatalystOutcomeKind.DID_NOT_OCCUR,
            reason="The results were delayed into the next quarter.",
            actor=scene["user"],
        )

        after = await knowledge_stats(session, as_of=later)
        assert after.freshness.closed_windows == ()


class TestTheProjection:
    async def test_the_catalyst_view_carries_the_operator_record(
        self, scene: dict[str, Any]
    ) -> None:
        session = scene["session"]
        await record_catalyst_resolution(
            session,
            company_id=scene["company"].id,
            label=LABEL,
            outcome=CatalystOutcomeKind.OCCURRED,
            reason="Results published on 28 September.",
            actor=scene["user"],
        )

        report = scene["report"]
        job = await session.get(Job, report.job_id)
        graph = await build_graph(session, job=job, report=report, company=scene["company"])

        views = [view for view in graph.catalyst_views if view.label == LABEL]
        assert len(views) == 1
        recorded = views[0].operator_resolution
        assert recorded is not None
        assert recorded.outcome is CatalystOutcomeKind.OCCURRED
        assert recorded.reason == "Results published on 28 September."

    async def test_an_unresolved_catalyst_projects_none(self, scene: dict[str, Any]) -> None:
        session = scene["session"]
        report = scene["report"]
        job = await session.get(Job, report.job_id)
        graph = await build_graph(session, job=job, report=report, company=scene["company"])

        views = [view for view in graph.catalyst_views if view.label == LABEL]
        assert views[0].operator_resolution is None
