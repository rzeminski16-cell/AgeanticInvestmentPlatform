"""The cross-section consistency check: same concept, same period, one value.

Gap C6. The live report's self-contradiction — a recorded ratio against the very lines
shown beside it — was caught by the red team, a model, hours after the disagreeing rows
were sitting in one database. These tests pin the deterministic replacement: the facts a
report *publishes* are grouped by what they measure, and a group holding two values lands
as an ordinary disagreements row, exactly as a source conflict found any other way would.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.disagreement import DisagreementKind
from aer.core.enums import ClaimKind, FactBasis
from aer.db.models import FinancialFact
from aer.services.citations import record_claim
from aer.services.consistency import check_report_consistency
from aer.services.disagreements import disagreements_for_job
from aer.storage.local import LocalArtefactStore
from tests.scene_fixtures import build_scene

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    return await build_scene(db_session, store)


@pytest.fixture
def store(tmp_path: Any) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=4_194_304)


async def _fact(
    session: AsyncSession,
    scene: dict[str, Any],
    *,
    value: str,
    period_end: date = date(2025, 9, 27),
    period_start: date | None = date(2024, 9, 29),
    fiscal_period: str = "FY",
    fiscal_year: int = 2025,
    concept: str = "revenue",
    unit: str = "USD",
    filed: date = date(2025, 10, 30),
) -> FinancialFact:
    company = scene.get("company")
    if company is None:
        from aer.db.models import Company  # noqa: PLC0415

        company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
        session.add(company)
        await session.flush()
        scene["company"] = company
    row = FinancialFact(
        company_id=company.id,
        source_document_id=scene["document"].id,
        concept=concept,
        value=Decimal(value),
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        fiscal_period=fiscal_period,
        fiscal_year=fiscal_year,
        basis=FactBasis.AS_REPORTED,
        filed_date=filed,
    )
    session.add(row)
    await session.flush()
    return row


async def _publish(session: AsyncSession, scene: dict[str, Any], fact: FinancialFact) -> None:
    await record_claim(
        session,
        section=scene["section"],
        kind=ClaimKind.NUMERIC,
        text=f"The recorded {fact.concept} is {fact.value} {fact.unit}.",
        financial_fact_id=fact.id,
    )


class TestTheCheck:
    async def test_two_published_values_for_one_span_record_a_conflict(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15)),
        )

        recorded = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert recorded == 1
        [row] = await disagreements_for_job(db_session, scene["job"].id)
        assert row.kind is DisagreementKind.SOURCE_CONFLICT
        assert row.topic == "revenue, FY2025"

    async def test_agreeing_values_record_nothing(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="245122000000", filed=date(2025, 11, 15)),
        )

        assert await check_report_consistency(db_session, job_id=scene["job"].id) == 0
        assert await disagreements_for_job(db_session, scene["job"].id) == []

    async def test_different_periods_are_not_a_contradiction(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The live failure's other half. An annual figure beside a quarterly one is a
        labelling problem — the period stamp (gap C1) — not a disagreement, and comparing
        them here would flag every well-labelled report."""
        await _publish(db_session, scene, await _fact(db_session, scene, value="391035000000"))
        await _publish(
            db_session,
            scene,
            await _fact(
                db_session,
                scene,
                value="94930000000",
                period_start=date(2025, 3, 30),
                period_end=date(2025, 6, 28),
                fiscal_period="Q3",
            ),
        )

        assert await check_report_consistency(db_session, job_id=scene["job"].id) == 0

    async def test_a_fact_nobody_published_is_not_compared(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The check is over what the report shows a reader, never the whole store —
        two rows nobody printed cannot contradict anyone."""
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        # Stored, never cited.
        await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15))

        assert await check_report_consistency(db_session, job_id=scene["job"].id) == 0

    async def test_a_figure_row_in_content_counts_as_published(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The second channel a value reaches the page by: a section figure row naming
        its fact — the same convention the numeral rule accepts as lineage."""
        cited = await _fact(db_session, scene, value="245122000000")
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15)),
        )
        scene["section"].content = {
            "figures": [
                {
                    "label": "Revenue",
                    "value": str(cited.value),
                    "financial_fact_id": str(cited.id),
                }
            ]
        }
        await db_session.flush()

        assert await check_report_consistency(db_session, job_id=scene["job"].id) == 1

    async def test_running_twice_records_the_conflict_once(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The fingerprint's dedupe holds here too: a resumed run must not double its
        disagreements."""
        await _publish(db_session, scene, await _fact(db_session, scene, value="245122000000"))
        await _publish(
            db_session,
            scene,
            await _fact(db_session, scene, value="244000000000", filed=date(2025, 11, 15)),
        )

        first = await check_report_consistency(db_session, job_id=scene["job"].id)
        second = await check_report_consistency(db_session, job_id=scene["job"].id)

        assert first == 1
        # The second pass finds the same fingerprint already recorded and adds nothing.
        assert len(await disagreements_for_job(db_session, scene["job"].id)) == 1
        assert second == 1  # the existing row is returned, not duplicated
