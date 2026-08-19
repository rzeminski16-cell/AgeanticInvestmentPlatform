"""Themes: model-proposed, human-confirmed, and only then edges (K1, ADR 0065).

What these tests hold, in the order the risk runs: an unconfirmed theme contributes
nothing anywhere — no row, no edge, no note; a confirmed one lands as shared identity a
second run's proposal joins rather than duplicates; the vault projects it with closure
intact across the *union* of the competitor and theme relations; and the statistics count
only what an approved report can reach.

The scene builder mirrors ``tests/test_knowledge_stats.py``'s: reports whose approval
state is known by construction, because every rule here keys on ``immutable``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import Company, Job, JobStep, Report, Theme, ThemeMembership, User
from aer.obsidian.graph import build_graph, theme_edges
from aer.services import approvals as approval_service
from aer.services import themes as theme_service
from aer.services.knowledge import knowledge_stats
from tests.workflow_fixtures import seed_job, seed_request

pytestmark = pytest.mark.anyio

AS_OF = date(2026, 6, 30)


async def _user(session: AsyncSession) -> User:
    row = User(email="themes@example.invalid", display_name="T", role=UserRole.ANALYST)
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


async def _run_with_slate(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    themes: list[dict[str, Any]],
    approve_gate: bool = True,
    approve_report: bool = True,
) -> tuple[Job, Report]:
    """One run that proposed a slate, with each approval separately controllable."""
    request = await seed_request(session, user=user, as_of_date=AS_OF)
    job = await seed_job(session, request=request)
    job.status = JobStatus.SUCCEEDED
    await session.flush()

    output: dict[str, Any] = {
        "subject": str(company.id),
        "subject_name": company.name,
        "themes": themes,
        "proposed_by": "test",
    }
    session.add(
        JobStep(
            job_id=job.id,
            step_key=theme_service.THEME_STEP,
            sequence=6,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{job.id}:{theme_service.THEME_STEP}",
            input_hash="0" * 64,
            output_ref=output,
        )
    )
    await session.flush()

    if approve_gate and themes:
        # Gates are passed in order; PLAN first, then the slate.
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
            gate=GateKind.THEME_SET,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash=sha256_hex(canonical_json(theme_service.theme_set_payload(output))),
        )

    content: dict[str, Any] = {"sections": []}
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=AS_OF,
        immutable=approve_report,
        approved_by=user.id if approve_report else None,
        approved_at=datetime.now(UTC) if approve_report else None,
        content=content,
        content_hash=sha256_hex(canonical_json(content)),
    )
    session.add(report)
    await session.flush()
    return job, report


def _slate(key: str = "ai-capex", label: str = "AI capital expenditure") -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "rationale": "Capacity spending on accelerated compute reaches its revenue.",
            "existing": False,
        }
    ]


@pytest.fixture
async def clean(db_session: AsyncSession) -> AsyncSession:
    await db_session.execute(
        text(
            "TRUNCATE research_requests, audit_events, users, artefacts, companies, themes "
            "RESTART IDENTITY CASCADE"
        )
    )
    return db_session


class TestSlugging:
    def test_spellings_collapse_to_one_identity(self) -> None:
        assert theme_service.slugged("AI Capex") == "ai-capex"
        assert theme_service.slugged("ai-capex") == "ai-capex"
        assert theme_service.slugged("  AI__Capex!  ") == "ai-capex"

    def test_all_punctuation_slugs_to_nothing(self) -> None:
        """The caller drops it; an empty identity must never found a row."""
        assert theme_service.slugged("!!!") == ""


class TestTheGateRefusal:
    async def test_a_proposed_slate_nobody_confirmed_refuses(self, clean: AsyncSession) -> None:
        """The comps rule, applied to themes: withheld and empty mean opposite things."""
        user = await _user(clean)
        company = await _company(clean, "THMA", "Theme Alpha plc")
        job, _report = await _run_with_slate(
            clean, user=user, company=company, themes=_slate(), approve_gate=False
        )

        with pytest.raises(theme_service.ThemeSetNotConfirmedError):
            await theme_service.confirmed_theme_set(clean, job)

    async def test_an_empty_slate_is_no_themes_not_a_refusal(self, clean: AsyncSession) -> None:
        user = await _user(clean)
        company = await _company(clean, "THMB", "Theme Beta plc")
        job, _report = await _run_with_slate(
            clean, user=user, company=company, themes=[], approve_gate=False
        )

        assert await theme_service.confirmed_theme_set(clean, job) == ()

    async def test_an_approval_of_a_different_slate_is_not_an_approval(
        self, clean: AsyncSession
    ) -> None:
        user = await _user(clean)
        company = await _company(clean, "THMC", "Theme Gamma plc")
        job, _report = await _run_with_slate(
            clean, user=user, company=company, themes=_slate(), approve_gate=True
        )
        # The proposal moves after the approval was recorded.
        step = await clean.scalar(
            select(JobStep).where(
                JobStep.job_id == job.id, JobStep.step_key == theme_service.THEME_STEP
            )
        )
        assert step is not None
        step.output_ref = {**step.output_ref, "themes": _slate(key="grid-buildout")}
        await clean.flush()

        with pytest.raises(theme_service.ThemeSetNotConfirmedError):
            await theme_service.confirmed_theme_set(clean, job)


class TestRecording:
    async def test_an_unconfirmed_slate_records_no_rows(self, clean: AsyncSession) -> None:
        user = await _user(clean)
        company = await _company(clean, "THMD", "Theme Delta plc")
        job, report = await _run_with_slate(
            clean, user=user, company=company, themes=[], approve_gate=False
        )

        recorded = await theme_service.record_confirmed_themes(clean, job=job, report=report)

        assert recorded == ()
        assert (await clean.scalar(select(Theme))) is None

    async def test_a_second_run_joins_the_theme_it_names(self, clean: AsyncSession) -> None:
        """Shared identity is the point: one key, however many runs name it."""
        user = await _user(clean)
        alpha = await _company(clean, "THME", "Theme Epsilon plc")
        beta = await _company(clean, "THMF", "Theme Zeta plc")
        job_a, report_a = await _run_with_slate(clean, user=user, company=alpha, themes=_slate())
        job_b, report_b = await _run_with_slate(
            clean, user=user, company=beta, themes=_slate(label="A rival spelling")
        )

        await theme_service.record_confirmed_themes(clean, job=job_a, report=report_a)
        await theme_service.record_confirmed_themes(clean, job=job_b, report=report_b)

        rows = list(await clean.scalars(select(Theme)))
        assert len(rows) == 1
        # The founder's label survives; a later spelling does not rename a shared identity.
        assert rows[0].label == "AI capital expenditure"
        memberships = list(await clean.scalars(select(ThemeMembership)))
        assert {membership.company_id for membership in memberships} == {alpha.id, beta.id}

    async def test_recording_twice_is_a_no_op(self, clean: AsyncSession) -> None:
        """A retried report step re-records the same set; the unique triple absorbs it."""
        user = await _user(clean)
        company = await _company(clean, "THMG", "Theme Eta plc")
        job, report = await _run_with_slate(clean, user=user, company=company, themes=_slate())

        await theme_service.record_confirmed_themes(clean, job=job, report=report)
        await theme_service.record_confirmed_themes(clean, job=job, report=report)

        memberships = list(await clean.scalars(select(ThemeMembership)))
        assert len(memberships) == 1


class TestTheEdges:
    async def test_a_membership_through_a_draft_is_not_an_edge(self, clean: AsyncSession) -> None:
        user = await _user(clean)
        alpha = await _company(clean, "THMH", "Theme Theta plc")
        beta = await _company(clean, "THMI", "Theme Iota plc")
        job_a, report_a = await _run_with_slate(clean, user=user, company=alpha, themes=_slate())
        job_b, report_b = await _run_with_slate(
            clean, user=user, company=beta, themes=_slate(), approve_report=False
        )
        await theme_service.record_confirmed_themes(clean, job=job_a, report=report_a)
        await theme_service.record_confirmed_themes(clean, job=job_b, report=report_b)

        edges = await theme_edges(clean)

        # One member reachable through an approved report is a theme with no pair yet.
        assert edges.get(alpha.id, set()) == set()
        assert beta.id not in edges

    async def test_two_approved_members_are_a_symmetric_edge(self, clean: AsyncSession) -> None:
        user = await _user(clean)
        alpha = await _company(clean, "THMJ", "Theme Kappa plc")
        beta = await _company(clean, "THMK", "Theme Lambda plc")
        for company in (alpha, beta):
            job, report = await _run_with_slate(clean, user=user, company=company, themes=_slate())
            await theme_service.record_confirmed_themes(clean, job=job, report=report)

        edges = await theme_edges(clean)

        assert edges[alpha.id] == {beta.id}
        assert edges[beta.id] == {alpha.id}

    async def test_the_export_component_walks_theme_edges(self, clean: AsyncSession) -> None:
        """Closure across the union: a theme-linked company is exported with the subject.

        Alpha and Beta share no peer set — only the theme joins them — so a walk over the
        competitor relation alone would leave Beta out and the theme note's link to it
        dangling. That is the broken-closure case this test exists to catch.
        """
        user = await _user(clean)
        alpha = await _company(clean, "THML", "Theme Mu plc")
        beta = await _company(clean, "THMM", "Theme Nu plc")
        for company in (alpha, beta):
            job, report = await _run_with_slate(clean, user=user, company=company, themes=_slate())
            await theme_service.record_confirmed_themes(clean, job=job, report=report)

        subject_job = await clean.scalar(
            select(Job).join(Report, Report.job_id == Job.id).where(Report.company_id == alpha.id)
        )
        subject_report = await clean.scalar(select(Report).where(Report.company_id == alpha.id))
        assert subject_job is not None
        assert subject_report is not None

        graph = await build_graph(clean, job=subject_job, report=subject_report, company=alpha)

        assert set(graph.companies) == {alpha.id, beta.id}
        assert [view.key for view in graph.theme_views] == ["ai-capex"]
        members = {company_id for company_id, _report, _why in graph.theme_views[0].members}
        assert members == {alpha.id, beta.id}


class TestTheStatistics:
    async def test_a_theme_counts_only_through_an_approved_report(
        self, clean: AsyncSession
    ) -> None:
        user = await _user(clean)
        alpha = await _company(clean, "THMN", "Theme Xi plc")
        job, report = await _run_with_slate(
            clean, user=user, company=alpha, themes=_slate(), approve_report=False
        )
        await theme_service.record_confirmed_themes(clean, job=job, report=report)

        stats = await knowledge_stats(clean, as_of=AS_OF)
        assert stats.size.theme_nodes == 0

        report.immutable = True
        report.approved_by = user.id
        report.approved_at = datetime.now(UTC)
        await clean.flush()

        stats = await knowledge_stats(clean, as_of=AS_OF)
        assert stats.size.theme_nodes == 1


class TestTheNormalisedSlate:
    async def test_a_messy_key_collapses_and_a_repeat_is_dropped(self, clean: AsyncSession) -> None:
        """The identity decisions, reached with exactly the input a model could send."""
        slate = await theme_service.normalised_slate(
            clean,
            [
                ("AI Capex", "AI capital expenditure", " why "),
                ("ai-capex", "A rival spelling", "again"),
                ("!!!", "Unusable", "slugs to nothing"),
            ],
        )

        assert [row["key"] for row in slate] == ["ai-capex"]
        assert slate[0]["rationale"] == "why"

    async def test_a_tracked_theme_is_marked_existing(self, clean: AsyncSession) -> None:
        """The reviewer weighs "joins" differently from "founds", so the flag is hashed."""
        clean.add(Theme(key="ai-capex", label="AI capital expenditure"))
        await clean.flush()

        slate = await theme_service.normalised_slate(
            clean, [("AI Capex", "x", "y"), ("grid-buildout", "Grid buildout", "z")]
        )

        assert [(row["key"], row["existing"]) for row in slate] == [
            ("ai-capex", True),
            ("grid-buildout", False),
        ]
