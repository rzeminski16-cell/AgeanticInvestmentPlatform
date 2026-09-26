"""The one reader of a report's valuation (ROADMAP §3.19 item 76).

A report's valuation is its run's own base-case rows, one per terminal method, read back —
never a copy on the report row, which is what the reports list, the company page, the
planner's digest, the vault and the API each read and found empty. What is held here is what
the reader selects: the base case alone, the newest row for each method, both models' rows,
the currency from the row's unit, and nothing at all for a run that recorded none.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Calculation
from aer.services.report_valuation import (
    NOT_RECORDED,
    ReportValuation,
    valuation_of,
    valuations_for,
)
from tests.report_fixtures import record_valuation
from tests.workflow_fixtures import seed_job, seed_request, seed_user


async def _jobs(session: AsyncSession, count: int) -> list[uuid.UUID]:
    user = await seed_user(session, email="valuation-reader@example.invalid")
    request = await seed_request(session, user=user)
    return [(await seed_job(session, request=request)).id for _ in range(count)]


class TestWhatTheReaderSelects:
    async def test_both_methods_in_the_reports_order(self, db_session: AsyncSession) -> None:
        (job,) = await _jobs(db_session, 1)
        # Struck exit multiple first: the order a reader meets them is the report's own.
        await record_valuation(
            db_session,
            job_id=job,
            rows=(("exit_multiple", "base", "241.5"), ("gordon_growth", "base", "265")),
        )

        valuation = await valuation_of(db_session, job)

        assert [figure.label for figure in valuation.figures] == [
            "perpetuity growth",
            "exit multiple",
        ]
        assert valuation.spoken() == (
            "$265.00 (perpetuity growth) and $241.50 (exit multiple) a share"
        )
        # Each figure keeps the row that says so (invariant 3).
        for figure in valuation.figures:
            row = await db_session.get(Calculation, figure.calculation_id)
            assert row is not None
            assert row.output_value == figure.value

    async def test_the_newest_base_row_for_a_method_is_the_answer(
        self, db_session: AsyncSession
    ) -> None:
        """A valuation struck again, after an amended assumption, supersedes the first."""
        (job,) = await _jobs(db_session, 1)
        await record_valuation(
            db_session,
            job_id=job,
            rows=(
                ("gordon_growth", "base", "100"),
                ("exit_multiple", "base", "150"),
                ("gordon_growth", "base", "120"),
            ),
        )

        valuation = await valuation_of(db_session, job)

        assert [(figure.label, figure.value) for figure in valuation.figures] == [
            ("perpetuity growth", Decimal("120")),
            ("exit multiple", Decimal("150")),
        ]

    async def test_no_other_case_is_the_reports_answer(self, db_session: AsyncSession) -> None:
        """A grid cell, a scenario and an argued lever strike the same row names."""
        (job,) = await _jobs(db_session, 1)
        await record_valuation(
            db_session,
            job_id=job,
            rows=(
                ("gordon_growth", "sensitivity", "300"),
                ("exit_multiple", "argued", "400"),
                ("gordon_growth", "bull", "500"),
            ),
        )

        valuation = await valuation_of(db_session, job)

        assert valuation == ReportValuation()
        assert valuation.spoken() == NOT_RECORDED

    async def test_a_bank_reads_its_two_treatments(self, db_session: AsyncSession) -> None:
        (job,) = await _jobs(db_session, 1)
        await record_valuation(
            db_session,
            job_id=job,
            name="residual_income_per_share",
            discriminator="treatment",
            rows=(
                ("perpetual_growth", "base", "266.06"),
                ("fade_to_nothing", "base", "199.04"),
            ),
        )

        valuation = await valuation_of(db_session, job)

        assert valuation.spoken() == (
            "$199.04 (excess return competed away) and $266.06 (excess return in "
            "perpetuity) a share"
        )

    async def test_the_currency_is_what_stands_above_the_line(
        self, db_session: AsyncSession
    ) -> None:
        (job,) = await _jobs(db_session, 1)
        await record_valuation(
            db_session,
            job_id=job,
            currency="GBP",
            rows=(("gordon_growth", "base", "12.3"), ("exit_multiple", "base", "9")),
        )

        valuation = await valuation_of(db_session, job)

        assert valuation.currency == "GBP"
        assert valuation.spoken() == "£12.30 (perpetuity growth) and £9.00 (exit multiple) a share"


class TestManyReportsInOneRead:
    async def test_each_run_is_keyed_by_its_job_and_one_with_none_is_absent(
        self, db_session: AsyncSession
    ) -> None:
        valued, also_valued, unvalued = await _jobs(db_session, 3)
        for job, value in ((valued, "10"), (also_valued, "20")):
            await record_valuation(
                db_session,
                job_id=job,
                rows=(("gordon_growth", "base", value), ("exit_multiple", "base", value)),
            )

        found = await valuations_for(db_session, [valued, also_valued, unvalued, valued])

        assert set(found) == {valued, also_valued}
        assert found[valued].figures[0].value == Decimal("10")
        assert found[also_valued].figures[0].value == Decimal("20")

    async def test_asking_about_nothing_reads_nothing(self, db_session: AsyncSession) -> None:
        assert await valuations_for(db_session, []) == {}
        assert ReportValuation().currency == ""
