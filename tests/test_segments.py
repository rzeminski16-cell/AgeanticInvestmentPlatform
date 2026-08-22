"""The segment sweep: the annual report's dimensioned facts become rows a chart can read.

The live report's segment-mix exhibit rendered its placeholder because nothing read the
one document that states the breakdown. These tests pin the reader: dimensioned facts out
of the annual report's inline XBRL, persisted with their axis and member, and everything
that could not be used said out loud rather than dropped.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus, Provider, SourceTier
from aer.db.models import (
    Artefact,
    Company,
    FinancialFact,
    Job,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.services.segments import sweep_segment_facts
from aer.storage.local import LocalArtefactStore
from tests.ixbrl_fixtures import SEGMENT_AXIS, SEGMENT_TRUTH, WITH_SEGMENTS

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

FILED = date(2022, 9, 15)
ACCESSION = "0000789019-22-000091"


@pytest.fixture
def store(tmp_path: Any) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=4_194_304)


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    user = User(email="segments@example.invalid", display_name="S")
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Acme Holdings plc",
        ticker="ACME",
        exchange="NASDAQ",
        as_of_date=date(2023, 6, 30),
        point_in_time=True,
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = Job(
        work_order_id=request.id,
        request_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)

    # The CIK matches the fixture's entity identifier once the padding is stripped, which
    # is how the sweep ties an in-document fact to the company the run researches.
    company = Company(name="Acme Holdings plc", cik="0001234567", ticker="ACME", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    entry = await _record(db_session, store, request=request, job=job, payload=WITH_SEGMENTS)
    return {
        "session": db_session,
        "store": store,
        "request": request,
        "job": job,
        "company": company,
        "entry": entry,
    }


async def _record(
    session: AsyncSession,
    store: LocalArtefactStore,
    *,
    request: ResearchRequest,
    job: Job,
    payload: bytes,
    form: str = "10-K",
) -> dict[str, Any]:
    """Archive one filing and return it as an acquire-step ``filings`` output entry."""
    stored = await store.put_bytes(payload)
    artefact = Artefact(
        sha256=stored.sha256,
        media_type="application/xhtml+xml",
        size_bytes=stored.size_bytes,
        storage_key=store.storage_key_for(stored.sha256),
    )
    session.add(artefact)
    await session.flush()

    document = SourceDocument(
        work_order_id=request.id,
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url=f"https://www.sec.gov/Archives/edgar/data/1234567/{stored.sha256[:8]}.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        publication_date=FILED,
        publication_date_confidence=1.0,
        quarantined=False,
    )
    session.add(document)
    await session.flush()
    return {
        "source_document_id": str(document.id),
        "form": form,
        "accession": ACCESSION,
        "artefact_sha256": stored.sha256,
    }


async def _stored_segments(session: AsyncSession) -> list[FinancialFact]:
    return list(
        await session.scalars(
            select(FinancialFact).where(FinancialFact.dimension_axis.is_not(None))
        )
    )


class TestTheSweep:
    async def test_the_segment_facts_are_persisted_with_their_dimension(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await sweep_segment_facts(
            scene["session"],
            scene["store"],
            company=scene["company"],
            filings=[scene["entry"]],
        )

        assert outcome.facts_written == len(SEGMENT_TRUTH)
        rows = await _stored_segments(scene["session"])
        assert {row.dimension_axis for row in rows} == {SEGMENT_AXIS}
        assert {row.dimension_member: int(row.value) for row in rows} == SEGMENT_TRUTH

    async def test_a_year_long_duration_is_labelled_a_fiscal_year(
        self, scene: dict[str, Any]
    ) -> None:
        """Derived from the span, because an inline document does not state a fiscal
        period — and the FY label is what the segment exhibit selects on."""
        await sweep_segment_facts(
            scene["session"], scene["store"], company=scene["company"], filings=[scene["entry"]]
        )

        rows = await _stored_segments(scene["session"])
        assert all(row.fiscal_period == "FY" for row in rows)
        assert {row.fiscal_year for row in rows} == {2022}
        assert all(row.filed_date == FILED for row in rows)

    async def test_the_consolidated_figure_is_not_persisted_here(
        self, scene: dict[str, Any]
    ) -> None:
        """The aggregate's own path owns the consolidated lines. A second copy from this
        sweep would be the duplicate-observation problem reintroduced from a new door."""
        await sweep_segment_facts(
            scene["session"], scene["store"], company=scene["company"], filings=[scene["entry"]]
        )

        consolidated = list(
            await scene["session"].scalars(
                select(FinancialFact).where(FinancialFact.dimension_axis.is_(None))
            )
        )
        assert consolidated == []

    async def test_running_twice_stores_nothing_twice(self, scene: dict[str, Any]) -> None:
        first = await sweep_segment_facts(
            scene["session"], scene["store"], company=scene["company"], filings=[scene["entry"]]
        )
        second = await sweep_segment_facts(
            scene["session"], scene["store"], company=scene["company"], filings=[scene["entry"]]
        )

        assert first.facts_written == len(SEGMENT_TRUTH)
        assert second.facts_written == 0
        assert len(await _stored_segments(scene["session"])) == len(SEGMENT_TRUTH)

    async def test_a_run_with_no_annual_report_says_so(self, scene: dict[str, Any]) -> None:
        outcome = await sweep_segment_facts(
            scene["session"],
            scene["store"],
            company=scene["company"],
            filings=[{**scene["entry"], "form": "8-K"}],
        )

        assert outcome.facts_written == 0
        assert any("No annual report" in note for note in outcome.notes)

    async def test_a_document_that_is_not_inline_xbrl_costs_only_its_segments(
        self, scene: dict[str, Any]
    ) -> None:
        """An older filing, or one whose tagging is an attachment, is a note — never a
        failed run. The excerpts and the aggregate's figures arrived by their own paths."""
        plain = await _record(
            scene["session"],
            scene["store"],
            request=scene["request"],
            job=scene["job"],
            payload=b"<html><body><p>An annual report that tags nothing at all.</p></body></html>",
        )

        outcome = await sweep_segment_facts(
            scene["session"], scene["store"], company=scene["company"], filings=[plain]
        )

        assert outcome.facts_written == 0
        assert outcome.notes
        assert await _stored_segments(scene["session"]) == []

    async def test_a_dimensioned_extension_tag_is_counted_not_stored(
        self, scene: dict[str, Any]
    ) -> None:
        """And deliberately never shown to the confirmation gate: the statements came
        from the aggregate, and a breakdown the vocabulary cannot name is a count in the
        output rather than a reason to stop the run."""
        rewritten = WITH_SEGMENTS.replace(
            b'name="ifrs-full:Revenue" contextRef="D2022N"',
            b'name="acme:SegmentTurnover" contextRef="D2022N"',
        )
        assert rewritten != WITH_SEGMENTS
        entry = await _record(
            scene["session"],
            scene["store"],
            request=scene["request"],
            job=scene["job"],
            payload=rewritten,
        )

        outcome = await sweep_segment_facts(
            scene["session"], scene["store"], company=scene["company"], filings=[entry]
        )

        assert outcome.unmapped_tags == 1
        stored = await _stored_segments(scene["session"])
        assert {row.dimension_member for row in stored} == {"acme:SouthernMember"}

    async def test_a_fact_naming_a_different_registrant_is_not_this_company_s(
        self, scene: dict[str, Any]
    ) -> None:
        stranger = Company(
            name="Someone Else plc", cik="0009999999", ticker="ELSE", exchange="NASDAQ"
        )
        scene["session"].add(stranger)
        await scene["session"].flush()

        outcome = await sweep_segment_facts(
            scene["session"], scene["store"], company=stranger, filings=[scene["entry"]]
        )

        assert outcome.facts_written == 0
        assert await _stored_segments(scene["session"]) == []
