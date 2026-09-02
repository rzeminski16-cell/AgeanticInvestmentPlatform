"""The monitor, walked once: a contradicted premise on the work list, the gate, the decision.

`test_thesis_monitor.py` proves the pass and drives the pages in-process. Nothing there
proves a person can do it — that the work list's row leads to the finding, that the gate's
two buttons submit against that finding, that a decided gate really loses its form in a
browser rather than in an assertion about HTML.

**No worker and no model.** The finding is seeded as the row a pass would have written,
because what is under test is the surface that closes it, not the pass that opened it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.core.enums import (
    FindingKind,
    JobStatus,
    PremiseComparator,
    PremiseStatus,
    RequestStatus,
)
from aer.db.models import Company, Finding, Job, User, WorkOrder
from aer.services import theses as thesis_service
from aer.services import thesis_monitor
from aer.services.theses import Predicate
from tests.db_fixtures import run_async

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


async def _seed_a_contradicted_premise(database_url: str) -> None:
    """A company, a thesis with a thresholded premise, and the finding a pass left on it."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(select(User).limit(1))
            assert user is not None, "the reset seeds a user"
            company = Company(
                name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
            )
            session.add(company)
            await session.flush()
            thesis = await thesis_service.write_thesis(
                session, user=user, company=company, title="Contoso keeps compounding"
            )
            premise = await thesis_service.add_premise(
                session,
                thesis=thesis,
                actor=user,
                statement="Revenue keeps growing above 25% a year.",
                basis="The segment disclosure.",
                predicate=Predicate(
                    metric="revenue growth",
                    comparator=PremiseComparator.AT_LEAST,
                    threshold=Decimal(25),
                    unit="percent",
                ),
                review_by=None,
            )
            # The pass the finding came from, so the gate has a run root to hang its
            # approval on — a decision is recorded against the pass that raised the question.
            order = WorkOrder(
                user_id=user.id,
                tool=thesis_monitor.TOOL,
                subject_kind=thesis_monitor.SUBJECT_THESIS,
                subject_id=thesis.id,
                as_of_date=date(2026, 2, 2),
                point_in_time=False,
                max_cost_gbp=Decimal("12.00"),
                status=RequestStatus.COMPLETED,
            )
            session.add(order)
            await session.flush()
            job = Job(
                work_order_id=order.id,
                workflow_version=thesis_monitor.WORKFLOW_VERSION,
                code_version="e2e",
                status=JobStatus.SUCCEEDED,
                started_at=datetime(2026, 2, 2, 3, tzinfo=UTC),
                finished_at=datetime(2026, 2, 2, 3, 1, tzinfo=UTC),
            )
            session.add(job)
            await session.flush()
            session.add(
                Finding(
                    thesis_id=thesis.id,
                    judgement_id=premise.judgement_id,
                    job_id=job.id,
                    kind=FindingKind.READING,
                    status=PremiseStatus.CONTRADICTED,
                    justification=(
                        "Revenue for the year to 31 December 2025 was 1120 against 1000 a year "
                        "earlier: 12%, below the 25% floor the premise carries."
                    ),
                    source_document_ids=[],
                    observed={
                        "metric": "revenue growth",
                        "value": "0.12",
                        "unit": "ratio",
                        "period_end": "2025-12-31",
                        "threshold": "0.25",
                        "threshold_unit": "ratio",
                        "comparator": "at least",
                        "holds": False,
                    },
                    window_from=date(2026, 2, 1),
                    window_to=date(2026, 2, 1),
                    opens_gate=True,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


class TestADecisionFromTheWorkList:
    def test_a_contradicted_premise_is_decided_at_its_gate(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run_async(_seed_a_contradicted_premise(database_url))

        # It is on the work list as a decision waiting, and the row leads to the finding.
        page.goto(live_server)
        row = page.locator('[data-tool="monitor"][data-attention]')
        expect(row).to_have_count(1)
        expect(row).to_contain_text("was contradicted")
        row.get_by_role("link", name="Decide what to do").click()
        page.wait_for_url("**/monitor/findings/*")

        # The gate: what code measured, what the model read, and two answers.
        expect(page.locator("#observed")).to_contain_text("does not hold")
        expect(page.locator("#thesis-gate")).to_be_visible()
        page.fill("#reason", "One soft year; the segment mix explains it.")
        page.click("#keep")
        page.wait_for_url("**/monitor/findings/*")

        # Decided: the form is gone, the act is on the record, and the premise stands.
        expect(page.locator("#thesis-gate")).to_have_count(0)
        expect(page.locator('[data-resolution="dismissed"]')).to_contain_text(
            "One soft year; the segment mix explains it."
        )
        page.click("#premise a")
        page.wait_for_url("**/theses/*")
        expect(page.locator('[data-premise="1"]')).to_have_attribute("data-withdrawn", "no")

        # And the work list no longer asks.
        page.goto(live_server)
        expect(page.locator('[data-tool="monitor"][data-attention]')).to_have_count(0)

    def test_the_monitor_opens_from_the_launcher(self, page: Page, live_server: str) -> None:
        page.goto(live_server)
        page.locator('[data-tool="monitor"] [data-field="action"]').click()
        page.wait_for_url("**/monitor")
        expect(page.get_by_text("No open thesis to monitor")).to_be_visible()
