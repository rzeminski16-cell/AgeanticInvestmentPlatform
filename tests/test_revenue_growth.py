"""The headline revenue growth rate spans fiscal years, whatever else the store holds.

Readiness audit 2026-09, blocking: `_revenue_growth` took the earliest and latest
consolidated revenue rows by period end with no fiscal-period filter, and the store holds
every quarter a 10-Q filed. A September run on a June-quarter filer would have compounded
an annual figure into a three-month one and recorded it, sourced, as the company's growth.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from aer.calc.engine import CalculationContext
from aer.db.models import FinancialFact, JobStep
from aer.workflow.engine import StepContext
from aer.workflow.workflows.vertical_slice_v1 import _revenue_growth
from tests.assumption_fixtures import a_year, seed_years
from tests.assumption_fixtures import scene as _scene  # noqa: F401 -- the fixture, by its own name

pytestmark = pytest.mark.integration


async def _quarter(scene: dict[str, Any], *, end: date, value: str) -> None:
    scene["session"].add(
        FinancialFact(
            company_id=scene["company"].id,
            source_document_id=scene["document"].id,
            concept="revenue",
            unit="USD",
            value=Decimal(value),
            period_start=date(end.year, end.month - 2, 1),
            period_end=end,
            fiscal_year=end.year,
            fiscal_period="Q3",
            filed_date=date(end.year, end.month + 1, 15),
        )
    )
    await scene["session"].flush()


async def test_a_later_quarter_is_not_the_end_of_the_series(scene: dict[str, Any]) -> None:
    await seed_years(
        scene,
        {date(2022, 12, 31): a_year(revenue="1000"), date(2023, 12, 31): a_year(revenue="1210")},
    )
    # A 10-Q filed after the last annual report: three months of revenue, newest by date.
    await _quarter(scene, end=date(2024, 9, 30), value="330")
    step = JobStep(
        job_id=scene["job"].id,
        step_key="calculate",
        sequence=0,
        idempotency_key="probe",
        input_hash="0" * 64,
    )
    context = StepContext(
        session=scene["session"], job=scene["job"], step=step, services={}, outputs={}
    )

    growth = await _revenue_growth(
        context, company_id=scene["company"].id, ledger=CalculationContext(code_version="test")
    )

    assert growth is not None
    # 1000 -> 1210 over the one fiscal year between the two annual reports is 21 %;
    # 1000 -> 330 over two calendar years would have been a decline of 43 % a year.
    assert Decimal(growth["value"]).quantize(Decimal("0.0001")) == Decimal("0.2100")
