"""The shared research-run scene: a run, an archived filing, and an extracted excerpt.

A plain builder rather than a fixture, so more than one test module can use it without importing
a fixture out of another module — which pytest allows and then reports as a redefinition at every
call site that names it. Each module defines its own one-line ``scene`` fixture over this.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus, Provider, SourceTier
from aer.db.models import (
    Artefact,
    Job,
    ReportSection,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.extract.html import extract_html
from aer.services.extractions import record_excerpt
from aer.storage.local import LocalArtefactStore
from tests.request_fixtures import research_request
from tests.workflow_fixtures import AS_OF_DATE

__all__ = ["ANOTHER_YEAR", "CITED", "FILING", "build_scene"]

FILING = b"""<!DOCTYPE html><html><head><title>10-K</title></head><body>
<p>Total revenue was $198,270 million for fiscal year 2022.</p>
<p>Operating income was $83,383 million for fiscal year 2022.</p>
<p>Total revenue was $168,088 million for fiscal year 2021.</p>
</body></html>"""

CITED = "Total revenue was $198,270 million for fiscal year 2022."
ANOTHER_YEAR = "Total revenue was $168,088 million for fiscal year 2021."


async def build_scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    """A run with one drafted section, one archived filing, and one extracted excerpt."""
    user = User(email="cite@example.invalid", display_name="Cite")
    db_session.add(user)
    await db_session.flush()

    request = research_request(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = Job(
        work_order_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    definition = await db_session.scalar(select(SectionDefinition).limit(1))
    assert definition is not None, "the migration seeds section definitions"

    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content={"body": "Revenue grew."},
    )
    db_session.add(section)
    await db_session.flush()

    stored = await store.put_bytes(FILING)
    artefact = Artefact(
        sha256=stored.sha256,
        media_type="text/html",
        size_bytes=stored.size_bytes,
        storage_key=store.storage_key_for(stored.sha256),
    )
    db_session.add(artefact)
    await db_session.flush()

    document = SourceDocument(
        work_order_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        quarantined=False,
    )
    db_session.add(document)
    await db_session.flush()

    extracted = extract_html(FILING).text
    excerpt = extracted.locate(CITED)
    assert excerpt is not None

    extraction = await record_excerpt(
        db_session,
        source_document_id=document.id,
        extracted=extracted,
        excerpt=excerpt,
    )

    return {
        "user": user,
        "request": request,
        "job": job,
        "section": section,
        "document": document,
        "artefact": artefact,
        "extraction": extraction,
        "extracted": extracted,
        "store": store,
    }
