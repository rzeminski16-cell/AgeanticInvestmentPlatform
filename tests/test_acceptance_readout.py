"""The P11 acceptance readout: the diff half of the re-run, as code (polish P11).

One real FakeProvider run is driven through both gates to a rendered report, and the
readout is measured against it — first whole, then with each kind of record broken in
turn. Every breakage must fail its own check and only its own check, because a readout
that fails everything at once tells an operator nothing about what to look at.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import GateKind, UserRole
from aer.db.models import (
    Citation,
    Company,
    Evaluation,
    ReportSection,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.errors import ValidationError
from aer.render.glance import Glance
from aer.services.acceptance import acceptance_readout
from tests.api_fixtures import build_app, client_for
from tests.run_fixtures import Driver, to_final_gate
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"


class EnqueueRecorder:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: Any, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


@pytest.fixture
async def clean_slate(db_engine: Any) -> Any:
    await _truncate(db_engine)
    yield
    await _truncate(db_engine)


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="p11@example.invalid", display_name="P11", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = ResearchRequest(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            point_in_time=True,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        session.add(request)
        await session.commit()
        return {"user": user, "request": request}


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> EnqueueRecorder:
    recorder = EnqueueRecorder()
    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", recorder)
    monkeypatch.setattr("aer.web.pages.enqueue_run", recorder)
    return recorder


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    enqueued: EnqueueRecorder,
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


async def _completed_run(
    api: Any, db_engine: Any, settings: Settings, request_id: Any
) -> uuid.UUID:
    driver = Driver(db_engine, settings)
    job_id = await to_final_gate(api, request_id, driver)
    await driver.approve(job_id, gate=GateKind.FINAL, step="red_team")
    await driver.advance(job_id)
    return job_id


def _by_name(readout: Any) -> dict[str, Any]:
    return {check.name: check for check in readout.checks}


async def _checks(factory: Any, job_id: uuid.UUID) -> dict[str, Any]:
    async with factory() as session:
        return _by_name(await acceptance_readout(session, job_id=job_id))


async def _break_a_citation(factory: Any, job_id: uuid.UUID) -> None:
    """Unverified and unoverridden: the one state a citation must never sit in quietly."""
    async with factory() as session:
        citation = await session.scalar(select(Citation).limit(1))
        assert citation is not None, "the driven run recorded citations"
        was_verified = citation.excerpt_verified
        citation.excerpt_verified = False
        citation.override_reason = None
        await session.commit()
    checks = await _checks(factory, job_id)
    assert checks["citations"].passed is False
    assert "1 neither" in checks["citations"].measured
    assert checks["metrics"].passed is True
    assert checks["cited_sources"].passed is True
    async with factory() as session:
        await session.execute(
            update(Citation).where(Citation.id == citation.id).values(excerpt_verified=was_verified)
        )
        await session.commit()


async def _break_a_metric(factory: Any, job_id: uuid.UUID) -> None:
    async with factory() as session:
        evaluation = await session.scalar(
            select(Evaluation).where(Evaluation.passed.is_not(None)).limit(1)
        )
        assert evaluation is not None, "the driven run exercised the gate"
        broken_metric = evaluation.metric
        evaluation.passed = False
        await session.commit()
    checks = await _checks(factory, job_id)
    assert checks["metrics"].passed is False
    assert broken_metric in checks["metrics"].measured
    assert checks["citations"].passed is True
    async with factory() as session:
        await session.execute(
            update(Evaluation).where(Evaluation.id == evaluation.id).values(passed=True)
        )
        await session.commit()


async def _break_a_section(factory: Any, job_id: uuid.UUID) -> None:
    async with factory() as session:
        section = await session.scalar(
            select(ReportSection)
            .where(
                ReportSection.job_id == job_id,
                ReportSection.status == SectionStatus.GENERATED,
            )
            .limit(1)
        )
        assert section is not None
        section.status = SectionStatus.PENDING
        await session.commit()
    checks = await _checks(factory, job_id)
    assert checks["sections"].passed is False
    assert "1 pending" in checks["sections"].measured
    assert checks["citations"].passed is True
    async with factory() as session:
        await session.execute(
            update(ReportSection)
            .where(ReportSection.id == section.id)
            .values(status=SectionStatus.GENERATED)
        )
        await session.commit()


async def _break_a_cited_source(factory: Any, job_id: uuid.UUID) -> None:
    """A cited document retagged to another issuer must be named in the readout."""
    async with factory() as session:
        outsider = Company(
            name="Somebody Else Inc", ticker="ELSE", exchange="NASDAQ", cik="0009999999"
        )
        session.add(outsider)
        await session.flush()
        cited_document = await session.scalar(
            select(SourceDocument)
            .join(Citation, Citation.source_document_id == SourceDocument.id)
            .limit(1)
        )
        assert cited_document is not None, "the driven run cited a document"
        cited_document.company_id = outsider.id
        await session.commit()
    checks = await _checks(factory, job_id)
    assert checks["cited_sources"].passed is False
    assert "Somebody Else Inc" in checks["cited_sources"].measured
    assert checks["citations"].passed is True


class TestTheReadout:
    async def test_a_clean_run_passes_every_requirement(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        job_id = await _completed_run(api, db_engine, api_settings, committed["request"].id)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            readout = await acceptance_readout(session, job_id=job_id)

        assert readout.passed, [
            (check.name, check.measured) for check in readout.checks if check.passed is False
        ]
        checks = _by_name(readout)
        assert set(checks) == {
            "report",
            "sections",
            "citations",
            "metrics",
            "cited_sources",
            "front_page",
            "spend",
        }
        # Spend is reported, never judged: the baseline predates the P7/P8 recalibration.
        assert checks["spend"].passed is None
        assert checks["spend"].measured.startswith("£")

    async def test_a_withheld_front_page_fails_the_readout(
        self,
        api: Any,
        db_engine: Any,
        api_settings: Settings,
        committed: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The refusal cannot be reached through the shipping query — `visible_facts`
        filters foreign rows before the glance's redundant check sees them, which is ADR
        0061 working — so the readout's branch is pinned against the refusal directly."""
        job_id = await _completed_run(api, db_engine, api_settings, committed["request"].id)

        async def refused(*args: Any, **kwargs: Any) -> Glance:
            return Glance(content=None, refused="withheld for the test's reason")

        monkeypatch.setattr("aer.services.acceptance.glance_content", refused)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            checks = _by_name(await acceptance_readout(session, job_id=job_id))
        assert checks["front_page"].passed is False
        assert "withheld" in checks["front_page"].measured

    async def test_each_breakage_fails_its_own_check_and_only_its_own(
        self, api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
    ) -> None:
        """Four breakages in turn — an unverified citation, a failed metric, a pending
        section, a foreign cited issuer — each flipping exactly one row of the readout."""
        job_id = await _completed_run(api, db_engine, api_settings, committed["request"].id)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

        await _break_a_citation(factory, job_id)
        await _break_a_metric(factory, job_id)
        await _break_a_section(factory, job_id)
        await _break_a_cited_source(factory, job_id)

    async def test_a_run_that_never_happened_is_refused(
        self, clean_slate: None, db_engine: Any
    ) -> None:
        """A readout of nothing would be a table of zeros that looks like a failing run."""
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            with pytest.raises(ValidationError, match="exists"):
                await acceptance_readout(session, job_id=uuid.uuid4())
