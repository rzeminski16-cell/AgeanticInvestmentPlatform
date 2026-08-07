"""The link graph: symmetric competitors, industry back-links, catalysts with outcomes.

The scene is two researched companies and two named-but-never-researched ones. Alpha's
second run confirms a peer set (Beta, Gamma) and a sector; Beta's run *proposes* a peer
(Delta) and a sector that nobody ever confirmed — the unconfirmed halves must contribute
nothing, because a link in a research journal is still a use of unapproved state.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import frontmatter
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus, Provider, SourceTier
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Artefact,
    Company,
    Job,
    JobStep,
    Report,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.obsidian import (
    SENTINEL,
    CatalystNoteMeta,
    CompanyNoteMeta,
    IndustryNoteMeta,
    RunNoteMeta,
    SourceNoteMeta,
    export_report,
)
from aer.services import approvals as approval_service
from aer.services.comps import PEER_SET_STEP, peer_set_payload
from aer.services.sectors import CLASSIFY_STEP, classification_payload
from tests.workflow_fixtures import seed_job

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

A1_AS_OF = date(2022, 6, 30)
A2_AS_OF = date(2022, 12, 31)
B1_AS_OF = date(2022, 8, 15)

A1_APPROVED = datetime(2022, 7, 2, 10, 15, tzinfo=UTC)
A2_APPROVED = datetime(2023, 1, 5, 9, 0, tzinfo=UTC)
B1_APPROVED = datetime(2022, 8, 20, 16, 30, tzinfo=UTC)

A2_MARKDOWN = (
    "# Report\n\n"
    "## Prior Research Comparison\n\n"
    "Row one: the prior non-binding view was Constructive; this run records its own.\n"
    "Row two: the prior valuation range was 100 to 120 USD per share.\n\n"
    "## Sources\n\ntable\n"
)

_WIKI_LINK = re.compile(r"\[\[([^\]]+?)\]\]")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        http_user_agent="Ageiantic Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
        obsidian_vault_root=tmp_path / "vault",
        obsidian_personal_root=tmp_path / "personal",
    )


async def _request(
    session: AsyncSession, *, user: User, name: str, ticker: str, as_of: date
) -> ResearchRequest:
    request = ResearchRequest(
        user_id=user.id,
        company_name=name,
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


async def _report(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    company: Company,
    low: str,
    high: str,
    markdown: str,
    approved_at: datetime | None,
) -> Report:
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=request.as_of_date,
        rating="Constructive (non-binding)",
        confidence=0.6,
        valuation_low=Decimal(low),
        valuation_high=Decimal(high),
        valuation_currency="USD",
        content={"markdown": markdown},
        content_hash="e" * 64,
        approved_at=approved_at,
        immutable=approved_at is not None,
    )
    session.add(report)
    await session.flush()
    return report


async def _peer_step(
    session: AsyncSession,
    *,
    job: Job,
    peers: list[Company],
    extra_identifiers: list[str] | None = None,
) -> dict[str, Any]:
    """A confirmed-shape peer proposal; ``extra_identifiers`` are entries that resolve
    to no company row (a ticker, a foreign UUID) and must fall out of the link graph."""
    output: dict[str, Any] = {
        "subject": "SUBJ",
        "subject_period_end": "2022-06-30",
        "basis": "ttm",
        "proposed_by": "planner",
        "peers": [
            *[
                {
                    "identifier": str(peer.id),
                    "name": peer.name,
                    "rationale": "Same industry group",
                    "period_end": "2022-06-30",
                }
                for peer in peers
            ],
            *[
                {
                    "identifier": identifier,
                    "name": "Unresolvable peer",
                    "rationale": "Named by a model, matched to no stored company",
                    "period_end": "2022-06-30",
                }
                for identifier in (extra_identifiers or [])
            ],
        ],
    }
    session.add(
        JobStep(
            job_id=job.id,
            step_key=PEER_SET_STEP,
            sequence=4,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{job.id}:{PEER_SET_STEP}",
            input_hash="0" * 64,
            output_ref=output,
        )
    )
    await session.flush()
    return output


async def _classify_step(session: AsyncSession, *, job: Job, sector_key: str) -> dict[str, Any]:
    output: dict[str, Any] = {
        "sector_key": sector_key,
        "rationale": "Proposed by the scene.",
        "proposed_by": "sic_lookup",
        "allowed_models": [],
        "blocked_models": [],
        "warnings": [],
    }
    session.add(
        JobStep(
            job_id=job.id,
            step_key=CLASSIFY_STEP,
            sequence=3,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{job.id}:{CLASSIFY_STEP}",
            input_hash="0" * 64,
            output_ref=output,
        )
    )
    await session.flush()
    return output


async def _approve_plan(session: AsyncSession, *, job: Job, actor: User) -> None:
    await approval_service.record_decision(
        session,
        job=job,
        gate=GateKind.PLAN,
        decision=Decision.APPROVED,
        actor=actor,
        payload_hash="1" * 64,
    )


async def _confirm_peers(
    session: AsyncSession, *, job: Job, actor: User, output: dict[str, Any]
) -> None:
    await approval_service.record_decision(
        session,
        job=job,
        gate=GateKind.PEER_SET,
        decision=Decision.APPROVED,
        actor=actor,
        payload_hash=sha256_hex(canonical_json(peer_set_payload(output))),
    )


async def _confirm_sector(
    session: AsyncSession, *, job: Job, actor: User, output: dict[str, Any]
) -> None:
    await approval_service.record_decision(
        session,
        job=job,
        gate=GateKind.SECTOR_SPECIALIST,
        decision=Decision.APPROVED,
        actor=actor,
        payload_hash=sha256_hex(canonical_json(classification_payload(output))),
    )


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """Alpha with two runs and confirmed links; Beta with unconfirmed proposals."""
    analyst = User(email="links@example.invalid", display_name="Links")
    db_session.add(analyst)
    await db_session.flush()

    alpha = Company(name="Alpha plc", cik="0000000201", ticker="ALPH", exchange="NASDAQ")
    beta = Company(name="Beta plc", cik="0000000202", ticker="BETA", exchange="NASDAQ")
    gamma = Company(name="Gamma plc", cik="0000000203", ticker="GAMM", exchange="NASDAQ")
    delta = Company(name="Delta plc", cik="0000000204", ticker="DELT", exchange="NASDAQ")
    db_session.add_all([alpha, beta, gamma, delta])
    await db_session.flush()

    definition = await db_session.scalar(select(SectionDefinition).limit(1))
    assert definition is not None, "the migration seeds section definitions"

    # Alpha, first run: a confirmed utilities classification and three catalysts.
    a1_request = await _request(
        db_session, user=analyst, name="Alpha plc", ticker="ALPH", as_of=A1_AS_OF
    )
    a1_job = await seed_job(db_session, request=a1_request)
    a1_classify = await _classify_step(db_session, job=a1_job, sector_key="utilities")
    await _approve_plan(db_session, job=a1_job, actor=analyst)
    await _confirm_sector(db_session, job=a1_job, actor=analyst, output=a1_classify)
    db_session.add(
        ReportSection(
            job_id=a1_job.id,
            section_definition_id=definition.id,
            section_key=definition.key,
            position=definition.position,
            status=SectionStatus.GENERATED,
            content={
                "items": [
                    {
                        "label": "FY22 interim results",
                        "expected_timing": "2022-07-31",
                        "rationale": "Margin trajectory check.",
                    },
                    {
                        "label": "Capital markets day",
                        "expected_timing": "2022-12-31",
                        "rationale": "Strategy detail expected.",
                    },
                    {
                        "label": "Regulatory review outcome",
                        "expected_timing": "when the regulator reports",
                        "rationale": "Tariff reset risk.",
                    },
                    {
                        "label": "Refinancing decision",
                        "expected_timing": "2023-06-30",
                        "rationale": "Bond maturity wall.",
                    },
                ]
            },
        )
    )
    await db_session.flush()
    a1_report = await _report(
        db_session,
        job=a1_job,
        request=a1_request,
        company=alpha,
        low="100",
        high="120",
        markdown="# Report\n\n## Prior Research Comparison\n\nFirst run; nothing prior.\n",
        approved_at=A1_APPROVED,
    )

    # Alpha, second run: confirmed peers (Beta, Gamma) and a confirmed banks
    # classification — deliberately different from the first run's utilities, so the
    # latest-classification rule has something to decide.
    a2_request = await _request(
        db_session, user=analyst, name="Alpha plc", ticker="ALPH", as_of=A2_AS_OF
    )
    a2_job = await seed_job(db_session, request=a2_request)
    a2_peers = await _peer_step(
        db_session,
        job=a2_job,
        peers=[beta, gamma],
        extra_identifiers=["ACME", "00000000-0000-4000-8000-000000000999"],
    )
    a2_classify = await _classify_step(db_session, job=a2_job, sector_key="banks")
    await _approve_plan(db_session, job=a2_job, actor=analyst)
    await _confirm_peers(db_session, job=a2_job, actor=analyst, output=a2_peers)
    await _confirm_sector(db_session, job=a2_job, actor=analyst, output=a2_classify)

    # The second run restates one of the first run's catalysts with a fresher timing:
    # the aggregated note must carry both thesis references and the newer window.
    db_session.add(
        ReportSection(
            job_id=a2_job.id,
            section_definition_id=definition.id,
            section_key=definition.key,
            position=definition.position,
            status=SectionStatus.GENERATED,
            content={
                "items": [
                    {
                        "label": "Refinancing decision",
                        "expected_timing": "FY2023",
                        "rationale": "Maturity wall pushed out by the tender offer.",
                    },
                ]
            },
        )
    )
    await db_session.flush()

    artefact = Artefact(
        sha256="a" * 64, media_type="text/html", size_bytes=10, storage_key="aa/" + "a" * 62
    )
    db_session.add(artefact)
    await db_session.flush()
    db_session.add(
        SourceDocument(
            request_id=a2_request.id,
            job_id=a2_job.id,
            artefact_id=artefact.id,
            url="https://www.sec.gov/Archives/edgar/data/201/alpha-10k.htm",
            title="Alpha 10-K",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            retrieved_at=datetime(2022, 12, 30, 12, 0, tzinfo=UTC),
            quarantined=False,
        )
    )
    await db_session.flush()

    # Drafted, not approved: each test decides whether the second run exists as history.
    a2_report = await _report(
        db_session,
        job=a2_job,
        request=a2_request,
        company=alpha,
        low="110",
        high="130",
        markdown=A2_MARKDOWN,
        approved_at=None,
    )

    # Beta: approved run, but its peer proposal (Delta) and sector proposal (insurers)
    # were never confirmed. Neither may leave a trace in the vault.
    b1_request = await _request(
        db_session, user=analyst, name="Beta plc", ticker="BETA", as_of=B1_AS_OF
    )
    b1_job = await seed_job(db_session, request=b1_request)
    await _peer_step(db_session, job=b1_job, peers=[delta])
    await _classify_step(db_session, job=b1_job, sector_key="insurers")
    b1_report = await _report(
        db_session,
        job=b1_job,
        request=b1_request,
        company=beta,
        low="50",
        high="60",
        markdown="# Report\n",
        approved_at=B1_APPROVED,
    )

    return {
        "analyst": analyst,
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "delta": delta,
        "a1_report": a1_report,
        "a2_report": a2_report,
        "b1_report": b1_report,
        "definition": definition,
    }


async def _approve_a2(db_session: AsyncSession, scene: dict[str, Any]) -> None:
    scene["a2_report"].approved_at = A2_APPROVED
    scene["a2_report"].immutable = True
    await db_session.flush()


def _note(settings: Settings, relative: str) -> frontmatter.Post:
    assert settings.obsidian_vault_root is not None
    path = settings.obsidian_vault_root / relative
    assert path.exists(), f"expected {relative} in the vault"
    return frontmatter.loads(path.read_text(encoding="utf-8"))


class TestCompetitorSymmetry:
    async def test_links_are_symmetric_after_exporting_two_peers(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        alpha_note = _note(settings, "10-Companies/ALPH - Alpha plc.md")
        assert alpha_note["competitors"] == ["[[BETA - Beta plc]]", "[[GAMM - Gamma plc]]"]

        # Beta never confirmed a peer set of its own; its link to Alpha exists purely
        # because Alpha's approved run named it — the symmetric half.
        beta_note = _note(settings, "10-Companies/BETA - Beta plc.md")
        assert beta_note["competitors"] == ["[[ALPH - Alpha plc]]"]

        # Gamma was named but never researched: an honest stub, not a fabricated history.
        gamma_note = _note(settings, "10-Companies/GAMM - Gamma plc.md")
        assert gamma_note["competitors"] == ["[[ALPH - Alpha plc]]"]
        assert gamma_note["run_notes"] == []
        assert "no approved research" in gamma_note.content

        await export_report(db_session, settings=settings, report_id=scene["b1_report"].id)
        beta_again = _note(settings, "10-Companies/BETA - Beta plc.md")
        assert beta_again["competitors"] == ["[[ALPH - Alpha plc]]"]
        moc = _note(settings, "00-Meta/MOC-Companies.md")
        assert "[[ALPH - Alpha plc]]" in moc.content
        assert "[[BETA - Beta plc]]" in moc.content

    async def test_an_unconfirmed_peer_set_contributes_no_links(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Beta proposed Delta and nobody confirmed it: no Delta note, no Delta link."""
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)
        await export_report(db_session, settings=settings, report_id=scene["b1_report"].id)

        assert settings.obsidian_vault_root is not None
        for path in settings.obsidian_vault_root.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            assert "DELT" not in text, path
            assert "Delta" not in text, path
        assert not list(settings.obsidian_vault_root.rglob("*DELT*"))


class TestEveryLinkResolves:
    async def test_every_written_link_names_a_file_in_the_vault(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)
        await export_report(db_session, settings=settings, report_id=scene["b1_report"].id)

        assert settings.obsidian_vault_root is not None
        files = list(settings.obsidian_vault_root.rglob("*.md"))
        stems = {path.stem for path in files}
        seen = 0
        for path in files:
            for target in _WIKI_LINK.findall(path.read_text(encoding="utf-8")):
                name = target.split("|")[0].split("#")[0].strip()
                assert name in stems, f"{path.name} links [[{name}]], which no file resolves"
                seen += 1
        assert seen > 10, "the sweep must actually have walked a linked vault"


class TestCatalysts:
    async def test_resolution_arrives_with_the_newer_run(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        # First export: only the first run is approved, so every window is open.
        await export_report(db_session, settings=settings, report_id=scene["a1_report"].id)

        interim = _note(settings, "50-Catalysts/ALPH FY22 interim results.md")
        assert interim["status"] == "pending"
        assert "resolution" not in interim.metadata
        assert interim["thesis_refs"] == [f"run-{scene['a1_report'].id}"]
        # JSON-mode serialisation writes dates as strings; the model validates them back.
        assert date.fromisoformat(str(interim["deadline"])) == date(2022, 7, 31)

        undated = _note(settings, "50-Catalysts/ALPH Regulatory review outcome.md")
        assert undated["status"] == "undated"
        assert "resolution" not in undated.metadata

        # The second run is approved and exported: the passed window resolves to it.
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        interim = _note(settings, "50-Catalysts/ALPH FY22 interim results.md")
        assert interim["status"] == "passed"
        assert interim["resolution"] == "[[2022-12-31 ALPH]]"

        # A deadline equal to the newer run's as-of date has not strictly passed.
        markets_day = _note(settings, "50-Catalysts/ALPH Capital markets day.md")
        assert markets_day["status"] == "pending"
        assert "resolution" not in markets_day.metadata

        undated = _note(settings, "50-Catalysts/ALPH Regulatory review outcome.md")
        assert undated["status"] == "undated"

        # Restated across both runs: one note, both thesis references, the fresher window.
        refinancing = _note(settings, "50-Catalysts/ALPH Refinancing decision.md")
        assert refinancing["thesis_refs"] == [
            f"run-{scene['a1_report'].id}",
            f"run-{scene['a2_report'].id}",
        ]
        assert refinancing["expected_timing"] == "FY2023"
        assert date.fromisoformat(str(refinancing["deadline"])) == date(2023, 12, 31)
        assert refinancing["status"] == "pending"

    async def test_run_notes_link_their_own_catalysts(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        first_run = _note(settings, "20-Runs/2022-06-30 ALPH.md")
        assert set(first_run["catalyst_notes"]) == {
            "[[ALPH FY22 interim results]]",
            "[[ALPH Capital markets day]]",
            "[[ALPH Regulatory review outcome]]",
            "[[ALPH Refinancing decision]]",
        }
        second_run = _note(settings, "20-Runs/2022-12-31 ALPH.md")
        assert second_run["catalyst_notes"] == ["[[ALPH Refinancing decision]]"]


class TestIndustryLinks:
    async def test_links_follow_the_latest_confirmed_classification(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        banks = _note(settings, "30-Industries/Banks.md")
        assert banks["sector_key"] == "banks"
        assert banks["companies"] == ["[[ALPH - Alpha plc]]"]
        assert settings.obsidian_vault_root is not None
        banks_raw = (settings.obsidian_vault_root / "30-Industries" / "Banks.md").read_text(
            encoding="utf-8"
        )
        # Evergreen: an industry note carries the sentinel so a person can write below it.
        assert SENTINEL in banks_raw

        # The first run confirmed utilities; its note exists because that run links it,
        # but Alpha's membership follows the latest confirmed view.
        utilities = _note(settings, "30-Industries/Utilities and regulated networks.md")
        assert utilities["companies"] == []

        alpha_note = _note(settings, "10-Companies/ALPH - Alpha plc.md")
        assert alpha_note["industry_note"] == "[[Banks]]"
        first_run = _note(settings, "20-Runs/2022-06-30 ALPH.md")
        assert first_run["industry_note"] == "[[Utilities and regulated networks]]"
        second_run = _note(settings, "20-Runs/2022-12-31 ALPH.md")
        assert second_run["industry_note"] == "[[Banks]]"

        # Beta's insurers proposal was never confirmed: no link, no note.
        beta_note = _note(settings, "10-Companies/BETA - Beta plc.md")
        assert "industry_note" not in beta_note.metadata
        assert settings.obsidian_vault_root is not None
        assert not (settings.obsidian_vault_root / "30-Industries" / "Insurers.md").exists()

    async def test_membership_unions_previously_exported_companies(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Regenerating an industry note for one export must not drop companies the
        vault already holds from earlier exports of a disjoint component."""
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        epsilon = Company(name="Epsilon plc", cik="0000000205", ticker="EPSI", exchange="NASDAQ")
        db_session.add(epsilon)
        await db_session.flush()
        e_request = await _request(
            db_session,
            user=scene["analyst"],
            name="Epsilon plc",
            ticker="EPSI",
            as_of=date(2023, 3, 31),
        )
        e_job = await seed_job(db_session, request=e_request)
        e_classify = await _classify_step(db_session, job=e_job, sector_key="banks")
        await _approve_plan(db_session, job=e_job, actor=scene["analyst"])
        await _confirm_sector(db_session, job=e_job, actor=scene["analyst"], output=e_classify)
        e_report = await _report(
            db_session,
            job=e_job,
            request=e_request,
            company=epsilon,
            low="10",
            high="12",
            markdown="# Report\n",
            approved_at=datetime(2023, 4, 2, 9, 0, tzinfo=UTC),
        )

        await export_report(db_session, settings=settings, report_id=e_report.id)

        banks = _note(settings, "30-Industries/Banks.md")
        assert banks["companies"] == ["[[ALPH - Alpha plc]]", "[[EPSI - Epsilon plc]]"]
        assert settings.obsidian_vault_root is not None
        moc_raw = (settings.obsidian_vault_root / "00-Meta" / "MOC-Companies.md").read_text(
            encoding="utf-8"
        )
        assert "[[ALPH - Alpha plc]]" in moc_raw
        assert "[[EPSI - Epsilon plc]]" in moc_raw
        assert SENTINEL in moc_raw


class TestTheJournalStaysHonest:
    async def test_the_run_note_comparison_matches_the_report_rows(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        note = _note(settings, "20-Runs/2022-12-31 ALPH.md")
        note_section = _between(note.content, "## Prior research comparison")
        stored_section = _between(A2_MARKDOWN, "## Prior Research Comparison")
        assert note_section == stored_section
        assert "Row one:" in "\n".join(note_section)

    async def test_the_company_note_records_the_valuation_history(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        alpha_note = _note(settings, "10-Companies/ALPH - Alpha plc.md")
        assert "## Valuation history" in alpha_note.content
        assert "- 2022-06-30 — 100 to 120 USD per share — [[2022-06-30 ALPH]]" in alpha_note.content
        assert "- 2022-12-31 — 110 to 130 USD per share — [[2022-12-31 ALPH]]" in alpha_note.content

    async def test_frontmatter_for_every_note_kind_validates(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)

        models = {
            "run": RunNoteMeta,
            "company": CompanyNoteMeta,
            "source": SourceNoteMeta,
            "industry": IndustryNoteMeta,
            "catalyst": CatalystNoteMeta,
        }
        seen: set[str] = set()
        assert settings.obsidian_vault_root is not None
        for path in settings.obsidian_vault_root.rglob("*.md"):
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
            kind = post.metadata.get("aer_kind")
            if kind is None:
                continue
            assert kind in models, f"{path.name} declares unknown kind {kind!r}"
            models[kind].model_validate(post.metadata)
            seen.add(str(kind))
        assert seen == set(models), f"every note kind must appear; saw only {sorted(seen)}"

    async def test_a_report_without_a_company_row_still_exports(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The degenerate path: an approved report whose company was never resolved to a
        row still projects its run, a request-titled company note, and the metadata."""
        zeta_request = await _request(
            db_session,
            user=scene["analyst"],
            name="Zeta plc",
            ticker="ZETA",
            as_of=date(2023, 5, 31),
        )
        zeta_job = await seed_job(db_session, request=zeta_request)
        report = Report(
            job_id=zeta_job.id,
            request_id=zeta_request.id,
            company_id=None,
            as_of_date=zeta_request.as_of_date,
            rating=None,
            content={"markdown": "# Report\n"},
            content_hash="f" * 64,
            approved_at=datetime(2023, 6, 1, 8, 0, tzinfo=UTC),
            immutable=True,
        )
        db_session.add(report)
        await db_session.flush()

        await export_report(db_session, settings=settings, report_id=report.id)

        run_note = _note(settings, "20-Runs/2023-05-31 ZETA.md")
        assert run_note["company"] == "Zeta plc"
        company_note = _note(settings, "10-Companies/ZETA - Zeta plc.md")
        assert company_note["run_notes"] == ["[[2023-05-31 ZETA]]"]
        assert "industry_note" not in company_note.metadata

    async def test_a_second_export_is_idempotent_across_the_whole_graph(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _approve_a2(db_session, scene)
        first = await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)
        assert settings.obsidian_vault_root is not None
        before = {
            str(path.relative_to(settings.obsidian_vault_root)): path.read_bytes()
            for path in sorted(settings.obsidian_vault_root.rglob("*.md"))
        }

        second = await export_report(db_session, settings=settings, report_id=scene["a2_report"].id)
        after = {
            str(path.relative_to(settings.obsidian_vault_root)): path.read_bytes()
            for path in sorted(settings.obsidian_vault_root.rglob("*.md"))
        }
        assert before == after
        assert first.files == second.files


def _between(text: str, heading: str) -> list[str]:
    start = text.find(heading)
    assert start != -1, f"{heading!r} missing"
    tail = text[start + len(heading) :]
    following = re.search(r"\n## ", tail)
    body = tail[: following.start()] if following else tail
    return [line for line in body.strip().splitlines() if line.strip()]
