"""Report history and the prior-run comparison: rows in, record out, nothing judged.

The scene builds a company with two approved reports — each with its own run carrying
catalysts and key risks as recorded section rows — plus one draft that must never count
as history, and then a new run to compare. Everything asserted here is a read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.charts import ValuationHistoryInput, ValuationRangePoint, valuation_history
from aer.core.enums import JobStatus, UserRole
from aer.db.models import (
    Calculation,
    Company,
    Job,
    Report,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    User,
)
from aer.sections.deterministic import BUILDERS
from aer.services.history import (
    approved_reports_for,
    catalyst_outcomes_for,
    company_for_user,
    prior_comparison_content,
    prior_risks_for,
    timing_deadline,
)
from tests.request_fixtures import research_request
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.anyio

APPROVED_AT = datetime(2022, 1, 15, 10, 0, tzinfo=UTC)


async def _request(
    session: AsyncSession, *, user_id: uuid.UUID, as_of: date, ticker: str = "MSFT"
) -> ResearchRequest:
    request = research_request(
        user_id=user_id,
        company_name="Microsoft Corporation",
        ticker=ticker,
        exchange="NASDAQ",
        as_of_date=as_of,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()
    return request


async def _job(session: AsyncSession, *, request_id: uuid.UUID) -> Job:
    job = Job(
        work_order_id=request_id,
        workflow_version="history_scene_v1",
        code_version="historycode1234",
        status=JobStatus.SUCCEEDED,
    )
    session.add(job)
    await session.flush()
    return job


async def _definition_id(session: AsyncSession, key: str) -> uuid.UUID:
    found = await session.scalar(
        select(SectionDefinition.id)
        .where(SectionDefinition.key == key)
        .order_by(SectionDefinition.version.desc())
        .limit(1)
    )
    assert found is not None, f"seeded definition {key} missing"
    return found


async def _approved_report(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    company: Company,
    as_of: date,
    rating: str | None = None,
    confidence: float | None = None,
    low: str | None = None,
    high: str | None = None,
    catalysts: list[dict[str, Any]] | None = None,
    risks: list[dict[str, Any]] | None = None,
) -> Report:
    job = await _job(session, request_id=request.id)
    if catalysts is not None:
        session.add(
            ReportSection(
                job_id=job.id,
                section_definition_id=await _definition_id(session, "catalysts"),
                section_key="catalysts",
                position=Decimal(410),
                status=SectionStatus.GENERATED,
                content={"commentary": "Prior catalysts.", "catalysts": catalysts},
            )
        )
    if risks is not None:
        session.add(
            ReportSection(
                job_id=job.id,
                section_definition_id=await _definition_id(session, "key_risks"),
                section_key="key_risks",
                position=Decimal(400),
                status=SectionStatus.GENERATED,
                content={"commentary": "Prior risks.", "risks": risks},
            )
        )
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=as_of,
        rating=rating,
        confidence=confidence,
        valuation_low=Decimal(low) if low is not None else None,
        valuation_high=Decimal(high) if high is not None else None,
        valuation_currency="USD" if low is not None else None,
        content={"markdown": "prior"},
        content_hash="a" * 64,
        approved_at=APPROVED_AT,
        immutable=True,
    )
    session.add(report)
    await session.flush()
    return report


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="history@example.invalid", display_name="History", role=UserRole.OWNER)
    other = User(email="other-h@example.invalid", display_name="Other", role=UserRole.OWNER)
    db_session.add_all([user, other])
    await db_session.flush()

    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    first_request = await _request(db_session, user_id=user.id, as_of=date(2021, 6, 30))
    older = await _approved_report(
        db_session,
        request=first_request,
        company=company,
        as_of=date(2021, 6, 30),
        rating="Cautious (non-binding)",
        confidence=0.55,
        low="180",
        high="220",
        catalysts=[
            {
                "label": "Cloud contract renewal",
                "expected_timing": "Q4 2021",
                "rationale": "Renewal window closes in the quarter.",
            },
            {
                "label": "Regulatory decision",
                "expected_timing": "H1 2023",
                "rationale": "Deadline set by the regulator.",
            },
            {
                "label": "Something eventually",
                "expected_timing": "medium term",
                "rationale": "No date was ever stated.",
            },
        ],
        risks=[
            {"risk": "Cloud price war", "why_it_matters": "Margins compress first."},
        ],
    )
    second_request = await _request(db_session, user_id=user.id, as_of=date(2021, 12, 31))
    newer = await _approved_report(
        db_session,
        request=second_request,
        company=company,
        as_of=date(2021, 12, 31),
        low="200",
        high="240",
        catalysts=[],
        risks=[],
    )

    # A draft: rendered, never approved. It must not exist as history.
    draft_request = await _request(db_session, user_id=user.id, as_of=date(2022, 3, 31))
    draft_job = await _job(db_session, request_id=draft_request.id)
    db_session.add(
        Report(
            job_id=draft_job.id,
            request_id=draft_request.id,
            company_id=company.id,
            as_of_date=date(2022, 3, 31),
            content={"markdown": "draft"},
            content_hash="b" * 64,
            immutable=False,
        )
    )
    await db_session.flush()

    new_request = await _request(db_session, user_id=user.id, as_of=AS_OF_DATE)
    new_job = await _job(db_session, request_id=new_request.id)

    return {
        "session": db_session,
        "user": user,
        "other": other,
        "company": company,
        "older": older,
        "newer": newer,
        "new_request": new_request,
        "new_job": new_job,
    }


class TestApprovedHistory:
    async def test_only_approved_reports_are_history(self, scene: dict[str, Any]) -> None:
        reports = await approved_reports_for(scene["session"], company_id=scene["company"].id)
        assert [report.id for report in reports] == [scene["newer"].id, scene["older"].id]

    async def test_the_before_bound_excludes_the_future(self, scene: dict[str, Any]) -> None:
        reports = await approved_reports_for(
            scene["session"], company_id=scene["company"].id, before=date(2021, 12, 31)
        )
        assert [report.id for report in reports] == [scene["older"].id]

    async def test_a_company_is_invisible_to_a_user_who_never_researched_it(
        self, scene: dict[str, Any]
    ) -> None:
        mine = await company_for_user(
            scene["session"], company_id=scene["company"].id, user_id=scene["user"].id
        )
        theirs = await company_for_user(
            scene["session"], company_id=scene["company"].id, user_id=scene["other"].id
        )
        assert mine is not None
        assert theirs is None


class TestCatalystOutcomes:
    async def test_timings_are_dated_against_the_new_as_of(self, scene: dict[str, Any]) -> None:
        outcomes = await catalyst_outcomes_for(
            scene["session"], prior=scene["older"], as_of=AS_OF_DATE
        )
        by_label = {outcome.label: outcome for outcome in outcomes}

        # AS_OF_DATE is mid-2022: Q4 2021 has closed, H1 2023 has not, prose never dates.
        assert by_label["Cloud contract renewal"].status == "passed"
        assert by_label["Regulatory decision"].status == "pending"
        assert by_label["Something eventually"].status == "undated"
        assert all(outcome.prior_report_id == scene["older"].id for outcome in outcomes)

    def test_the_timing_parser_reads_only_unambiguous_shapes(self) -> None:
        assert timing_deadline("2023-03-31") == date(2023, 3, 31)
        assert timing_deadline("2023") == date(2023, 12, 31)
        assert timing_deadline("FY2023") == date(2023, 12, 31)
        assert timing_deadline("Q2 2023") == date(2023, 6, 30)
        assert timing_deadline("H1 2023") == date(2023, 6, 30)
        assert timing_deadline("H2 2023") == date(2023, 12, 31)
        assert timing_deadline("the medium term") is None
        assert timing_deadline("2023-02-31") is None  # a date that does not exist


class TestTheComparisonSection:
    async def test_a_second_run_compares_against_the_record(self, scene: dict[str, Any]) -> None:
        content = await prior_comparison_content(
            scene["session"], job_id=scene["new_job"].id, request=scene["new_request"]
        )

        assert "2 prior approved report(s)" in content["commentary"]
        assert scene["newer"].as_of_date.isoformat() in content["commentary"]

        comparisons = content["comparisons"]
        assert comparisons, "a run with priors must produce rows"
        # Every row names the prior report it was read from — the property the task names.
        assert all(comparison["prior_report_id"] for comparison in comparisons)

        by_aspect = {comparison["aspect"]: comparison for comparison in comparisons}
        # The headline aspects compare against the most recent prior.
        assert by_aspect["Valuation range"]["prior"] == "200 to 240 USD per share"
        assert by_aspect["Valuation range"]["prior_report_id"] == str(scene["newer"].id)
        # The older report's catalysts and risks still walk in, with their own id.
        assert by_aspect["Catalyst — Cloud contract renewal"]["prior_report_id"] == str(
            scene["older"].id
        )
        assert "window has passed" in by_aspect["Catalyst — Cloud contract renewal"]["current"]
        assert by_aspect["Risk — Cloud price war"]["prior"] == "Margins compress first."

    async def test_the_current_valuation_reads_this_runs_own_rows(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        for sequence, (method, case, value) in enumerate(
            (
                ("gordon_growth", "base", "265.00"),
                ("exit_multiple", "base", "241.50"),
                # A scenario's number: the base-case range must not absorb it.
                ("gordon_growth", "bull", "999.00"),
            )
        ):
            session.add(
                Calculation(
                    job_id=scene["new_job"].id,
                    name="value_per_share",
                    formula="value per share = equity value / shares outstanding",
                    function_ref="aer.calc.dcf:value_per_share",
                    code_version="historycode1234",
                    inputs=[],
                    parameters={"method": method, "case": case},
                    output_value=Decimal(value),
                    output_unit="USD/share",
                    sequence=sequence,
                )
            )
        await session.flush()

        content = await prior_comparison_content(
            session, job_id=scene["new_job"].id, request=scene["new_request"]
        )
        by_aspect = {row["aspect"]: row for row in content["comparisons"]}
        assert by_aspect["Valuation range"]["current"] == "241.5 to 265 USD/share"

    async def test_the_registered_builder_delegates_to_the_service(
        self, scene: dict[str, Any]
    ) -> None:
        """The zero-budget section's registered builder produces the service's content —
        the wiring a stubbed builder would silently break."""
        builder = BUILDERS["prior_research_comparison"]
        content = await builder.build(scene["session"], scene["new_job"], scene["new_request"])
        assert "2 prior approved report(s)" in content["commentary"]
        assert content["comparisons"]

    async def test_a_first_run_states_so_in_one_sentence(self, scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id, as_of=AS_OF_DATE, ticker="RIO")
        request.company_name = "Rio Tinto plc"
        job = await _job(session, request_id=request.id)
        await session.flush()

        content = await prior_comparison_content(session, job_id=job.id, request=request)
        assert "first research run" in content["commentary"]
        assert "comparisons" not in content

    async def test_the_filers_own_name_is_used_not_the_one_typed(
        self, scene: dict[str, Any]
    ) -> None:
        """Gap A67. A live note opened "This is the first research run for M&T Banking
        Corporation" — the operator's typo for M&T *Bank* Corporation, three lines under
        a front matter that had the resolved name right. The request's ``company_name``
        is an input field, never checked against anything; the identity is the company
        row, written from the filer's own submission."""
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id, as_of=AS_OF_DATE)
        request.company_name = "Microsoft Corporationn"
        job = await _job(session, request_id=request.id)
        await session.flush()

        content = await prior_comparison_content(session, job_id=job.id, request=request)

        assert "MICROSOFT CORP" in content["commentary"]
        assert "Corporationn" not in content["commentary"]

    async def test_a_second_run_names_the_filer_too(self, scene: dict[str, Any]) -> None:
        """Both sentences, or the leak simply moves to the one nobody checked."""
        scene["new_request"].company_name = "Microsoft Corporationn"
        await scene["session"].flush()

        content = await prior_comparison_content(
            scene["session"], job_id=scene["new_job"].id, request=scene["new_request"]
        )

        assert "MICROSOFT CORP" in content["commentary"]
        assert "Corporationn" not in content["commentary"]

    async def test_an_unresolved_request_keeps_what_was_typed(self, scene: dict[str, Any]) -> None:
        """The fallback is not a compromise: before the entity resolves, the typed name
        is the only answer there is, and it is the honest one."""
        session: AsyncSession = scene["session"]
        request = await _request(session, user_id=scene["user"].id, as_of=AS_OF_DATE, ticker="ZZZZ")
        request.company_name = "Nothing Resolved Ltd"
        job = await _job(session, request_id=request.id)
        await session.flush()

        content = await prior_comparison_content(session, job_id=job.id, request=request)

        assert "Nothing Resolved Ltd" in content["commentary"]

    async def test_a_draft_never_reaches_the_comparison(self, scene: dict[str, Any]) -> None:
        """The draft report in the scene dates after both approved ones; if drafts
        counted, it would be the most recent prior and its as-of would lead the text."""
        content = await prior_comparison_content(
            scene["session"], job_id=scene["new_job"].id, request=scene["new_request"]
        )
        assert "2022-03-31" not in content["commentary"]

    async def test_prior_risks_carry_their_report_id(self, scene: dict[str, Any]) -> None:
        risks = await prior_risks_for(scene["session"], prior=scene["older"])
        assert risks == [
            {
                "risk": "Cloud price war",
                "why_it_matters": "Margins compress first.",
                "prior_report_id": str(scene["older"].id),
            }
        ]


class TestTheValuationHistoryChart:
    def test_it_is_byte_stable(self) -> None:
        data = ValuationHistoryInput(
            currency="USD",
            points=(
                ValuationRangePoint(as_of=date(2021, 6, 30), low=Decimal(180), high=Decimal(220)),
                ValuationRangePoint(as_of=date(2021, 12, 31), low=Decimal(200), high=Decimal(240)),
            ),
        )
        one = valuation_history(data, hashsalt="company-1")
        two = valuation_history(data, hashsalt="company-1")
        assert one.svg == two.svg
        assert one.exportable
        assert not one.placeholder
        assert "2 approved report(s)" in one.caption

    def test_no_recorded_ranges_render_the_placeholder(self) -> None:
        chart = valuation_history(ValuationHistoryInput(), hashsalt="company-1")
        assert chart.placeholder
        assert "No approved report has recorded a valuation range" in chart.caption
