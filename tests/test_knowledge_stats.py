"""Measuring the knowledge graph (docs/archive/knowledge-graph.md, K5).

Every statistic is asserted against a graph whose shape is known by construction, because
a count is exactly the kind of figure that looks plausible while being wrong. The scene is
three companies: two researched and joined by a confirmed peer set, one a stub named as a
comparable and never researched itself, and one researched company standing alone.

The vault half is tested against a temporary directory rather than a real export, so the
drift check can be shown to notice a file the record does not account for — and to ignore
the operator's own notes, which is the half that would be a privacy failure rather than a
counting one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Company,
    JobStep,
    ObsidianExport,
    Report,
    ReportSection,
    SectionDefinition,
    SectionStatus,
    User,
)
from aer.services import approvals as approval_service
from aer.services import comps as comps_service
from aer.services.knowledge import knowledge_stats
from aer.services.sectors import CLASSIFY_STEP
from tests.api_fixtures import build_app, client_for
from tests.request_fixtures import research_request
from tests.workflow_fixtures import seed_job

pytestmark = pytest.mark.anyio

AS_OF = date(2026, 6, 30)
_TABLES = "research_requests, audit_events, users, artefacts, companies"


async def _user(session: AsyncSession) -> User:
    row = User(email="knowledge@example.invalid", display_name="K", role=UserRole.ANALYST)
    session.add(row)
    await session.flush()
    return row


async def _company(session: AsyncSession, ticker: str, name: str) -> Company:
    row = Company(
        name=name, ticker=ticker, exchange="NASDAQ", cik=f"{abs(hash(ticker)) % 10**10:010d}"
    )
    session.add(row)
    await session.flush()
    return row


async def _run(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    as_of: date = AS_OF,
    peers: list[Company] | None = None,
    catalysts: list[dict[str, str]] | None = None,
    approved: bool = True,
) -> Report:
    """One report on its own job, approved unless asked otherwise.

    A job carries at most one report, so a draft needs a job of its own rather than a
    second row against an approved run's.
    """
    request = research_request(
        user_id=user.id,
        company_name=company.name,
        ticker=company.ticker or "",
        exchange=company.exchange or "NASDAQ",
        as_of_date=as_of,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()

    job = await seed_job(session, request=request)
    job.status = JobStatus.SUCCEEDED
    await session.flush()

    if peers:
        output: dict[str, Any] = {
            "subject": str(company.id),
            "subject_period_end": as_of.isoformat(),
            "basis": "lfy",
            "proposed_by": "test",
            "peers": [
                {
                    "identifier": str(peer.id),
                    "name": peer.name,
                    "rationale": "Same industry group.",
                    "period_end": as_of.isoformat(),
                }
                for peer in peers
            ],
        }
        session.add(
            JobStep(
                job_id=job.id,
                step_key=comps_service.PEER_SET_STEP,
                sequence=4,
                status=JobStatus.SUCCEEDED,
                idempotency_key=f"{job.id}:{comps_service.PEER_SET_STEP}",
                input_hash="0" * 64,
                output_ref=output,
            )
        )
        await session.flush()
        # Gates are passed in order, so the peer set cannot be confirmed on its own.
        await approval_service.record_decision(
            session,
            job=job,
            gate=GateKind.PLAN,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash="1" * 64,
        )
        await approval_service.record_decision(
            session,
            job=job,
            gate=GateKind.PEER_SET,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash=sha256_hex(canonical_json(comps_service.peer_set_payload(output))),
        )

    if catalysts:
        # A catalyst is read from the generated section, not from the report body: the
        # section is what the run produced and what history walks.
        definition = await session.scalar(
            select(SectionDefinition).where(SectionDefinition.key == "catalysts")
        )
        assert definition is not None, "the migration seeds a catalysts section"
        session.add(
            ReportSection(
                job_id=job.id,
                section_definition_id=definition.id,
                section_key=definition.key,
                position=definition.position,
                status=SectionStatus.GENERATED,
                content={"catalysts": catalysts},
            )
        )
        await session.flush()

    content: dict[str, Any] = {"sections": []}
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=as_of,
        immutable=approved,
        approved_by=user.id if approved else None,
        approved_at=datetime.now(UTC) if approved else None,
        content=content,
        content_hash=sha256_hex(canonical_json(content)),
    )
    session.add(report)
    await session.flush()
    return report


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """Two researched companies joined by a peer set, one stub, one lone company."""
    await db_session.execute(
        text(
            "TRUNCATE research_requests, audit_events, users, artefacts, companies "
            "RESTART IDENTITY CASCADE"
        )
    )
    user = await _user(db_session)
    alpha = await _company(db_session, "ALPH", "Alpha plc")
    beta = await _company(db_session, "BETA", "Beta Inc")
    stub = await _company(db_session, "STUB", "Stub Corporation")
    lone = await _company(db_session, "LONE", "Lone Holdings")

    await _run(db_session, user=user, company=alpha, peers=[beta, stub])
    await _run(db_session, user=user, company=beta, peers=[alpha])
    await _run(db_session, user=user, company=lone)

    return {
        "session": db_session,
        "user": user,
        "alpha": alpha,
        "beta": beta,
        "stub": stub,
        "lone": lone,
    }


class TestSize:
    async def test_a_peer_never_researched_counts_as_a_stub(self, scene: dict[str, Any]) -> None:
        """The plainest measure of how much of the neighbourhood has been looked at."""
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert stats.size.companies == 4
        assert stats.size.researched == 3
        assert stats.size.stubs == 1

    async def test_approved_reports_are_counted_and_drafts_are_not(
        self, scene: dict[str, Any]
    ) -> None:
        session = scene["session"]
        await _run(session, user=scene["user"], company=scene["lone"])
        await _run(session, user=scene["user"], company=scene["lone"], approved=False)

        stats = await knowledge_stats(session, as_of=AS_OF)

        # Four approved (three from the scene, one added above); the draft is excluded.
        assert stats.size.approved_reports == 4


class TestShape:
    async def test_edges_are_counted_once_not_twice(self, scene: dict[str, Any]) -> None:
        """Alpha↔Beta and Alpha↔Stub — the relation is symmetric, so two pairs."""
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert stats.shape.edges == 2

    async def test_components_separate_the_lone_company(self, scene: dict[str, Any]) -> None:
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert stats.shape.components == 2
        assert stats.shape.largest_component == 3
        assert stats.shape.isolated == 1

    async def test_mean_degree_is_over_every_node(self, scene: dict[str, Any]) -> None:
        """Four nodes, four edge-ends: Alpha 2, Beta 1, Stub 1, Lone 0."""
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert str(stats.shape.mean_degree) == "1.00"

    async def test_an_empty_graph_measures_zero_rather_than_dividing_by_it(
        self, db_session: AsyncSession
    ) -> None:
        await db_session.execute(
            text(
                "TRUNCATE research_requests, audit_events, users, artefacts, companies "
                "RESTART IDENTITY CASCADE"
            )
        )

        stats = await knowledge_stats(db_session, as_of=AS_OF)

        assert stats.size.companies == 0
        assert str(stats.shape.mean_degree) == "0"
        assert str(stats.coverage.researched_ratio) == "0"


class TestCoverage:
    async def test_the_researched_ratio_counts_stubs_against_it(
        self, scene: dict[str, Any]
    ) -> None:
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert str(stats.coverage.researched_ratio) == "0.75"

    async def test_a_run_with_no_confirmed_sector_is_unclassified(
        self, scene: dict[str, Any]
    ) -> None:
        """This scene confirms no classification gate, so every researched company is."""
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert stats.coverage.unclassified == 3
        assert stats.size.industries == 0

    async def test_an_unconfirmed_specialist_sector_does_not_take_the_surface_down(
        self, scene: dict[str, Any]
    ) -> None:
        """The read that refuses a *caller acting on it* must not refuse one counting it.

        A run whose classifier proposed a specialist sector nobody confirmed raises for
        the valuation path, deliberately. Measuring is not acting: the company counts as
        unclassified, which is what the graph does with the same read.
        """
        session = scene["session"]
        job_id = await session.scalar(
            select(Report.job_id).where(Report.company_id == scene["lone"].id)
        )
        session.add(
            JobStep(
                job_id=job_id,
                step_key=CLASSIFY_STEP,
                sequence=3,
                status=JobStatus.SUCCEEDED,
                idempotency_key=f"{job_id}:{CLASSIFY_STEP}",
                input_hash="0" * 64,
                output_ref={"sector_key": "banks", "rationale": "Proposed, never confirmed."},
            )
        )
        await session.flush()

        stats = await knowledge_stats(session, as_of=AS_OF)

        assert stats.coverage.unclassified == 3
        assert stats.size.industries == 0


class TestFreshness:
    async def test_research_older_than_the_horizon_is_stale_and_named(
        self, scene: dict[str, Any]
    ) -> None:
        later = date(2027, 6, 30)  # a year past the scene's as-of

        stats = await knowledge_stats(scene["session"], as_of=later, stale_after_days=180)

        assert {row.ticker for row in stats.freshness.stale} == {"ALPH", "BETA", "LONE"}
        assert all(row.days_since == 365 for row in stats.freshness.stale)

    async def test_recent_research_is_not_stale(self, scene: dict[str, Any]) -> None:
        stats = await knowledge_stats(scene["session"], as_of=AS_OF, stale_after_days=180)

        assert stats.freshness.stale == ()
        assert stats.freshness.newest == AS_OF
        assert stats.freshness.oldest == AS_OF

    async def test_a_closed_catalyst_window_is_listed_without_claiming_it_happened(
        self, db_session: AsyncSession
    ) -> None:
        await db_session.execute(
            text(
                "TRUNCATE research_requests, audit_events, users, artefacts, companies "
                "RESTART IDENTITY CASCADE"
            )
        )
        user = await _user(db_session)
        company = await _company(db_session, "CATL", "Catalyst plc")
        await _run(
            db_session,
            user=user,
            company=company,
            catalysts=[
                {
                    "label": "FY2026 results",
                    "expected_timing": "2026-09-30",
                    "rationale": "Full-year figures land.",
                },
                {
                    "label": "Capital markets day",
                    "expected_timing": "2027-06-30",
                    "rationale": "Strategy refresh.",
                },
            ],
        )

        stats = await knowledge_stats(db_session, as_of=date(2026, 12, 31))

        assert [row.label for row in stats.freshness.closed_windows] == ["FY2026 results"]
        assert stats.size.catalyst_nodes == 2


class TestVaultHealth:
    async def test_an_approved_report_nobody_exported_is_named(self, scene: dict[str, Any]) -> None:
        """Export is manual, so nothing else would ever say so."""
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert len(stats.vault.unexported) == 3
        assert stats.vault.exported_reports == 0

    async def test_an_exported_report_leaves_the_unexported_list(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        session = scene["session"]
        report = await session.scalar(select(Report.id).limit(1))
        session.add(
            ObsidianExport(
                report_id=report,
                exported_at=datetime.now(UTC),
                files=["10-Companies/ALPH — Alpha plc.md"],
                generator_version="test",
            )
        )
        await session.flush()

        stats = await knowledge_stats(session, as_of=AS_OF)

        assert stats.vault.exported_reports == 1
        assert len(stats.vault.unexported) == 2
        assert stats.vault.recorded_files == 1

    async def test_drift_is_a_generated_file_no_export_recorded(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        session = scene["session"]
        (tmp_path / "10-Companies").mkdir()
        (tmp_path / "10-Companies" / "ALPH — Alpha plc.md").write_text("recorded", encoding="utf-8")
        (tmp_path / "10-Companies" / "GONE — Deleted plc.md").write_text("orphan", encoding="utf-8")

        report = await session.scalar(select(Report.id).limit(1))
        session.add(
            ObsidianExport(
                report_id=report,
                exported_at=datetime.now(UTC),
                files=["10-Companies/ALPH — Alpha plc.md"],
                generator_version="test",
            )
        )
        await session.flush()

        settings = Settings(
            http_user_agent="Test test@example.invalid", obsidian_vault_root=tmp_path
        )
        stats = await knowledge_stats(session, settings=settings, as_of=AS_OF)

        assert stats.vault.configured is True
        assert stats.vault.drifted == ("10-Companies/GONE — Deleted plc.md",)

    async def test_the_personal_directory_is_never_reported_as_drift(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        """A privacy failure rather than a counting one: the operator's notes are theirs."""
        (tmp_path / "99-Personal").mkdir()
        (tmp_path / "99-Personal" / "My own thinking.md").write_text("mine", encoding="utf-8")

        settings = Settings(
            http_user_agent="Test test@example.invalid", obsidian_vault_root=tmp_path
        )
        stats = await knowledge_stats(scene["session"], settings=settings, as_of=AS_OF)

        assert stats.vault.drifted == ()

    async def test_no_vault_configured_measures_the_database_regardless(
        self, scene: dict[str, Any]
    ) -> None:
        stats = await knowledge_stats(scene["session"], as_of=AS_OF)

        assert stats.vault.configured is False
        assert stats.vault.drifted == ()
        assert stats.size.companies == 4


class TestTheStructureSerialises:
    async def test_as_dict_carries_every_section(self, scene: dict[str, Any]) -> None:
        """The JSON endpoint and the CLI read this; a missing key is a broken surface."""
        body = (await knowledge_stats(scene["session"], as_of=AS_OF)).as_dict()

        assert set(body) == {
            "size",
            "shape",
            "coverage",
            "accuracy",
            "freshness",
            "vault",
            "measured_at",
        }
        assert body["size"]["stubs"] == 1
        assert body["shape"]["edges"] == 2


@pytest.mark.integration
class TestTheSurfaces:
    """The page and the JSON endpoint, against a scene the application's own session sees.

    Committed rather than seeded in the test's transaction, because the application opens
    a session of its own: an uncommitted scene would measure an empty graph and every
    assertion below would pass on zero.
    """

    @pytest.fixture
    async def committed(self, db_engine: Any) -> AsyncIterator[dict[str, Any]]:
        async with db_engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            user = await _user(session)
            alpha = await _company(session, "ALPH", "Alpha plc")
            stub = await _company(session, "STUB", "Stub Corporation")
            await _run(session, user=user, company=alpha, peers=[stub])
            await session.commit()
        try:
            yield {"user": user, "alpha": alpha, "stub": stub}
        finally:
            async with db_engine.begin() as connection:
                await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

    @pytest.fixture
    async def api(
        self, api_settings: Settings, db_engine: Any, fake_redis: Any, committed: dict[str, Any]
    ) -> AsyncIterator[Any]:
        async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
            yield client

    async def test_the_json_reports_the_graph_it_measured(self, api: Any) -> None:
        response = await api.get("/api/knowledge")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["size"]["companies"] == 2
        assert body["size"]["stubs"] == 1
        assert body["shape"]["edges"] == 1

    async def test_the_page_renders_the_same_counts(self, api: Any) -> None:
        """A template that raises is only ever found by rendering it."""
        response = await api.get("/knowledge")

        assert response.status_code == 200, response.text
        assert 'id="stat-stubs"' in response.text
        assert "Stub Corporation" not in response.text  # counts, not a company listing
