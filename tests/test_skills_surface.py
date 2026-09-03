"""The authoring surface: editor round-trip, composed preview, import diff, dry run.

Task 43. The properties the acceptance rests on, in the order an author meets them:
validate a file and be shown what it will actually run as, save it without the bytes
changing, import somebody else's with a diff you have to confirm, and try it against a
finished run without spending a run to find out.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.agents.custom_section import CustomSectionDraft, ProposedCitation, ProposedClaim
from aer.config import Settings
from aer.core.enums import FactBasis, JobStatus, Provider, SourceTier
from aer.core.hashing import sha256_hex
from aer.db.models import (
    Artefact,
    Calculation,
    Citation,
    Claim,
    Company,
    Cost,
    FinancialFact,
    Job,
    JobStep,
    ReportSection,
    ResearchPlan,
    SectionStatus,
    SkillVersion,
    SourceDocument,
    User,
)
from aer.extract.html import extract_html
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services.extractions import record_excerpt
from aer.services.skill_authoring import import_diff, import_payload_hash, validate_skill_source
from aer.services.skill_dry_run import DRY_RUN_WORKFLOW, DryRunRefusedError, dry_run_skill
from aer.services.skills import current_version, save_skill, set_enabled
from aer.skills.resolution import (
    compose_for_version,
    pinned_skills_for_job,
    resolve_skills_for_plan,
)
from aer.storage.local import LocalArtefactStore
from aer.web.csrf import CSRF_FIELD_NAME
from tests.api_fixtures import build_app, client_for
from tests.request_fixtures import research_request
from tests.test_skill_frontmatter import MOAT_DURABILITY
from tests.workflow_fixtures import declared_schema_name

pytestmark = pytest.mark.anyio

FILING = b"""<!DOCTYPE html><html><body>
<p>Total revenue was $198,270 million for fiscal year 2022.</p>
<p>Switching costs anchor the installed base; churn is described as minimal.</p>
</body></html>"""

CITED = "Total revenue was $198,270 million for fiscal year 2022."

# The §2.12 example with one source required rather than three, so the seeded scene's
# single filed document satisfies the composed floor and the dry run is about the dry run.
SKILL_SOURCE = MOAT_DURABILITY.replace("min_sources: 3", "min_sources: 1")

# A file that asks for everything it is not allowed: no sourcing, no primary, a tool the
# role does not hold, and a budget past the ceiling. Every one of these is clamped.
GREEDY_SOURCE = """\
---
aer_skill: 1
key: greedy_section
kind: custom_section
title: "Greedy Section"
version: 1
evidence_policy:
  min_sources: 0
  requires_primary: false
  max_tier: 4
output:
  summary: string
token_budget: 900000
allowed_tools: [search_facts, shell]
---

Write whatever you like.
"""


def _settings(tmp_path: Any) -> Settings:
    return Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )


# ==========================================================================================
# Validation and the composed-policy preview
# ==========================================================================================


class TestTheEditorPreview:
    def test_a_valid_file_previews_its_composed_policy(self, tmp_path: Any) -> None:
        preview = validate_skill_source(SKILL_SOURCE, settings=_settings(tmp_path))

        assert preview.valid
        assert preview.key == "moat_durability"
        assert preview.evidence_policy == {
            "min_sources": 1,
            "requires_primary": True,
            "max_tier": 4,
            "allow_forward_looking": True,
        }
        assert preview.granted_tools == ["search_facts", "search_sources"]
        assert preview.clamps == []
        assert Decimal(preview.estimated_cost_gbp or "0") > 0

    def test_every_clamp_is_shown_with_its_reason(self, tmp_path: Any) -> None:
        """The whole point of the preview: what the file asked for is not what it gets."""
        preview = validate_skill_source(GREEDY_SOURCE, settings=_settings(tmp_path))

        assert preview.valid
        clamped = {clamp["field"] for clamp in preview.clamps}
        assert clamped == {
            "evidence_policy.min_sources",
            "evidence_policy.requires_primary",
            "allowed_tools",
            "token_budget",
        }
        assert all(clamp["reason"] for clamp in preview.clamps)

        # And the effective policy is the floor, not the request.
        assert preview.evidence_policy is not None
        assert preview.evidence_policy["min_sources"] == 1
        assert preview.evidence_policy["requires_primary"] is True
        assert preview.granted_tools == ["search_facts"]
        assert preview.token_budget == _settings(tmp_path).custom_section_token_ceiling

    def test_an_invalid_file_reports_issues_against_their_lines(self, tmp_path: Any) -> None:
        broken = SKILL_SOURCE.replace("max_tier: 4", "max_tier: 9")

        preview = validate_skill_source(broken, settings=_settings(tmp_path))

        assert not preview.valid
        assert preview.issues
        [issue] = [item for item in preview.issues if "max_tier" in item["field"]]
        assert issue["line"] == broken.splitlines().index("  max_tier: 9") + 1

    def test_a_reserved_output_field_is_refused_at_authoring(self, tmp_path: Any) -> None:
        rated = SKILL_SOURCE.replace("  summary: string", "  summary: string\n  rating: string")

        preview = validate_skill_source(rated, settings=_settings(tmp_path))

        assert not preview.valid
        assert any("reserved" in issue["message"] for issue in preview.issues)

    def test_a_methodology_skill_previews_without_inventing_a_policy(self, tmp_path: Any) -> None:
        methodology = """\
---
aer_skill: 1
key: owner_operator
kind: methodology
title: "Owner-operator alignment"
version: 1
---

I weight owner-operator alignment heavily.
"""
        preview = validate_skill_source(methodology, settings=_settings(tmp_path))

        assert preview.valid
        assert preview.evidence_policy is None
        assert preview.clamps == []
        # What it says instead (ADR 0108): the roles that will read the text.
        assert preview.composes_into == ["planner", "report_writer"]
        assert preview.as_dict()["composes_into"] == ["planner", "report_writer"]


class TestThePreviewMatchesWhatARunComposes:
    async def test_the_preview_and_the_pinned_policy_agree(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        """The acceptance the editor rests on. Asserted against a *pin a plan produced*,
        not against the composer called twice — a preview that agreed with itself would
        prove nothing about what a run does."""
        settings = _settings(tmp_path)
        user = User(email="preview@example.invalid", display_name="Preview")
        db_session.add(user)
        await db_session.flush()

        await save_skill(db_session, source=GREEDY_SOURCE, actor=user)
        await set_enabled(db_session, key="greedy_section", enabled=True, actor=user)

        request = research_request(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=date(2022, 9, 30),
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
        )
        db_session.add(request)
        await db_session.flush()
        plan = ResearchPlan(
            request_id=request.id,
            workflow_version="vertical_slice_v1",
            plan={"summary": "s", "sections": []},
            planned_sources=[],
            estimated_cost_gbp=Decimal("0.10"),
            estimated_runtime_seconds=60,
        )
        db_session.add(plan)
        await db_session.flush()

        resolved = await resolve_skills_for_plan(
            db_session,
            request=request,
            work_order_id=request.id,
            settings=settings,
            router=Router(settings),
        )
        [pin] = [row for row in resolved.pins if row.token_budget is not None]
        preview = validate_skill_source(GREEDY_SOURCE, settings=settings, router=Router(settings))

        assert preview.evidence_policy == {
            "min_sources": pin.min_sources,
            "requires_primary": pin.requires_primary,
            "max_tier": pin.max_tier,
            "allow_forward_looking": pin.allow_forward_looking,
        }
        assert preview.granted_tools == sorted(pin.granted_tools or [])
        assert preview.token_budget == pin.token_budget
        assert preview.estimated_cost_gbp == str(pin.estimated_cost_gbp)
        assert [clamp["field"] for clamp in preview.clamps] == [
            clamp["field"] for clamp in pin.clamps or []
        ]


# ==========================================================================================
# The editor round-trip
# ==========================================================================================


class TestTheEditorRoundTrip:
    async def test_the_body_survives_byte_for_byte(self, db_session: AsyncSession) -> None:
        """A save that reformatted anything would change the content hash, and a content
        hash is what a report points at to say which skill shaped it."""
        user = User(email="round@example.invalid", display_name="Round")
        db_session.add(user)
        await db_session.flush()

        version = await save_skill(db_session, source=SKILL_SOURCE, actor=user)
        reloaded = await current_version(db_session, key="moat_durability")

        assert reloaded is not None
        assert reloaded.source == SKILL_SOURCE
        assert reloaded.body == SKILL_SOURCE.split("---\n", 2)[2].strip("\n")
        assert reloaded.content_hash == version.content_hash

    async def test_editing_writes_a_new_version_and_keeps_the_old(
        self, db_session: AsyncSession
    ) -> None:
        user = User(email="round2@example.invalid", display_name="Round")
        db_session.add(user)
        await db_session.flush()

        await save_skill(db_session, source=SKILL_SOURCE, actor=user)
        edited = SKILL_SOURCE.replace("min_sources: 1", "min_sources: 2")
        second = await save_skill(db_session, source=edited, actor=user)

        assert second.version == 2
        stored = await db_session.scalars(select(SkillVersion).order_by(SkillVersion.version))
        sources = [row.source for row in stored]
        assert sources == [SKILL_SOURCE, edited]


# ==========================================================================================
# Import: a diff, and a confirmation of that diff
# ==========================================================================================


class TestImportRequiresConfirmingADiff:
    async def test_a_new_key_diffs_against_nothing_and_says_so(
        self, db_session: AsyncSession
    ) -> None:
        diff = await import_diff(db_session, source=SKILL_SOURCE)

        assert diff.valid
        assert diff.is_new
        assert diff.current_version is None
        assert any(line.startswith("+aer_skill: 1") for line in diff.diff)
        assert diff.payload_hash

    async def test_an_existing_key_diffs_against_its_stored_source(
        self, db_session: AsyncSession
    ) -> None:
        user = User(email="import@example.invalid", display_name="Import")
        db_session.add(user)
        await db_session.flush()
        await save_skill(db_session, source=SKILL_SOURCE, actor=user)

        incoming = SKILL_SOURCE.replace("min_sources: 1", "min_sources: 4")
        diff = await import_diff(db_session, source=incoming)

        assert not diff.is_new
        assert diff.current_version == 1
        assert "-  min_sources: 1" in diff.diff
        assert "+  min_sources: 4" in diff.diff

    async def test_an_identical_file_is_reported_as_nothing_to_import(
        self, db_session: AsyncSession
    ) -> None:
        user = User(email="import2@example.invalid", display_name="Import")
        db_session.add(user)
        await db_session.flush()
        await save_skill(db_session, source=SKILL_SOURCE, actor=user)

        diff = await import_diff(db_session, source=SKILL_SOURCE)

        assert diff.is_identical
        assert diff.diff == []

    async def test_an_invalid_file_gets_issues_and_no_diff_to_confirm(
        self, db_session: AsyncSession
    ) -> None:
        diff = await import_diff(db_session, source="not a skill file")

        assert not diff.valid
        assert diff.issues
        assert diff.diff == []
        assert diff.payload_hash == ""

    def test_the_hash_covers_both_sides_of_the_replacement(self) -> None:
        """A confirmation names one specific replacement: this file, replacing that
        version. Hashing the incoming file alone would still confirm after somebody else
        saved a new version underneath."""
        first = import_payload_hash(key="k", incoming_hash="a" * 64, current_hash="b" * 64)
        moved = import_payload_hash(key="k", incoming_hash="a" * 64, current_hash="c" * 64)

        assert first != moved


# ==========================================================================================
# The dry run
# ==========================================================================================


@pytest.fixture
async def finished_run(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    """A completed run holding one filed excerpt, one fact and one calculation."""
    settings = _settings(tmp_path)
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    scene = await _seed_finished_run(db_session, store=store, email="dry@example.invalid")
    await save_skill(db_session, source=SKILL_SOURCE, actor=scene["user"])
    await set_enabled(db_session, key="moat_durability", enabled=True, actor=scene["user"])
    return {**scene, "session": db_session, "settings": settings, "store": store}


async def _seed_finished_run(
    session: AsyncSession, *, store: LocalArtefactStore, email: str, ticker: str = "MSFT"
) -> dict[str, Any]:
    """One finished run's worth of evidence: a filed excerpt, a fact and a calculation.

    Everything a dry run may read, and nothing it may write to — which is what makes
    "the source run is untouched" an assertion about rows rather than about intent.
    """
    # Reused where the caller has already seeded one under this address — the browser
    # suite's live server seeds its own operator before this runs.
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, display_name="Author")
        session.add(user)
        await session.flush()

    request = research_request(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker=ticker,
        exchange="NASDAQ",
        as_of_date=date(2022, 9, 30),
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()

    job = Job(
        work_order_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.SUCCEEDED,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()

    stored = await store.put_bytes(FILING)
    # Artefacts are content-addressed and globally unique by hash, so a second seeded run
    # over the same bytes shares the row rather than inserting a duplicate — which is what
    # the acquisition service does with a re-fetch of an unchanged document.
    artefact = await session.scalar(select(Artefact).where(Artefact.sha256 == stored.sha256))
    if artefact is None:
        artefact = Artefact(
            sha256=stored.sha256,
            media_type="text/html",
            size_bytes=stored.size_bytes,
            storage_key=store.storage_key_for(stored.sha256),
        )
        session.add(artefact)
        await session.flush()

    document = SourceDocument(
        work_order_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        publication_date=date(2022, 3, 1),
        publication_date_latest=date(2022, 3, 1),
        quarantined=False,
    )
    session.add(document)
    await session.flush()

    extracted = extract_html(FILING).text
    excerpt = extracted.locate(CITED)
    assert excerpt is not None
    await record_excerpt(
        session, source_document_id=document.id, extracted=extracted, excerpt=excerpt
    )

    # One listing is one company, whoever is researching it — the registry's uniqueness is
    # over (ticker, exchange), so a second scene on the same ticker shares the row exactly
    # as a second real request for it would. Scenes that must not share facts (the
    # ownership test seeds two people) pass different tickers.
    company = await session.scalar(
        select(Company).where(Company.ticker == ticker, Company.exchange == "NASDAQ")
    )
    if company is None:
        company = Company(
            name="MICROSOFT CORP", cik=sha256_hex(ticker)[:10], ticker=ticker, exchange="NASDAQ"
        )
        session.add(company)
        await session.flush()

    # The subject, as `acquire` records it (ADR 0061). A dry run reads the *source run's*
    # facts through the source run's request, so the stamp has to be on that request or
    # the rehearsal is offered nothing to cite.
    request.company_id = company.id
    document.company_id = company.id
    await session.flush()

    session.add(
        FinancialFact(
            company_id=company.id,
            source_document_id=document.id,
            concept="revenue",
            value=Decimal("198270000000"),
            unit="USD",
            period_end=date(2022, 6, 30),
            basis=FactBasis.AS_REPORTED,
            filed_date=date(2022, 7, 28),
        )
    )
    session.add(
        Calculation(
            job_id=job.id,
            name="revenue_cagr",
            formula="cagr = (end / start) ** (1 / years) - 1",
            function_ref="aer.calc.basic:cagr",
            code_version="test",
            inputs=[],
            output_value=Decimal("0.18"),
            output_unit="ratio",
        )
    )
    await session.flush()

    return {"user": user, "request": request, "job": job, "document": document}


def _draft_from(prompt: str) -> CustomSectionDraft:
    """The scripted draft, citing the ids the composed prompt actually offered."""
    fact_id = re.search(r'"fact_id": "([0-9a-f-]{36})"', prompt)
    calculation_id = re.search(r'"calculation_id": "([0-9a-f-]{36})"', prompt)
    pair = re.search(
        r'\{"extraction_id": "([0-9a-f-]{36})", "source_document_id": "([0-9a-f-]{36})"\}',
        prompt,
    )
    assert fact_id is not None, "the dry run must offer the source run's facts"
    assert calculation_id is not None, "the dry run must offer the source run's calculations"
    assert pair is not None, "the dry run must offer the source run's extractions"

    citation = ProposedCitation(extraction_id=pair.group(1))
    return CustomSectionDraft(
        content={
            "summary": (
                "Total revenue was $198,270 million for fiscal year 2022. Switching costs "
                "anchor the installed base; durability is judged at 8 years."
            ),
            "durability_years": 8,
        },
        claims=[
            ProposedClaim(
                statement=CITED,
                kind="numeric",
                financial_fact_id=fact_id.group(1),
                citations=[citation],
            ),
            ProposedClaim(
                statement="A durability of 8 years rests on the recorded growth calculation.",
                kind="numeric",
                calculation_id=calculation_id.group(1),
                citations=[citation],
            ),
        ],
    )


@pytest.fixture
def section_provider() -> FakeProvider:
    holder: dict[str, FakeProvider] = {}

    def answer(schema: type) -> Any:
        kind = declared_schema_name(schema)
        assert kind == "CustomSectionDraft", f"unexpected schema {schema.__name__}"
        return _draft_from(holder["provider"].calls[-1]["messages"][0]["content"])

    provider = FakeProvider(answer)
    holder["provider"] = provider
    return provider


async def _dry_run(scene: dict[str, Any], provider: FakeProvider) -> Any:
    return await dry_run_skill(
        scene["session"],
        key="moat_durability",
        source_job=scene["job"],
        settings=scene["settings"],
        provider=provider,
        router=Router(scene["settings"]),
        store=scene["store"],
    )


class TestTheDryRun:
    async def test_it_renders_the_section_as_the_report_would_carry_it(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        outcome = await _dry_run(finished_run, section_provider)

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.section_key == "custom.moat_durability"
        assert "Competitive Moat Durability" in outcome.markdown
        assert "198,270" in outcome.markdown
        assert outcome.claims_recorded == 2

    async def test_every_claim_it_records_carries_a_citation(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        """The §2.12 acceptance: the section appears *with its own cited evidence*. A
        dry run that drafted prose without claims would look identical on the page."""
        session: AsyncSession = finished_run["session"]
        outcome = await _dry_run(finished_run, section_provider)

        claims = list(
            await session.scalars(
                select(Claim)
                .join(ReportSection, ReportSection.id == Claim.report_section_id)
                .where(ReportSection.job_id == outcome.job_id)
            )
        )
        assert len(claims) == 2
        for claim in claims:
            citations = list(
                await session.scalars(select(Citation).where(Citation.claim_id == claim.id))
            )
            assert citations, f"{claim.text!r} rests on nothing"
            assert all(row.source_document_id == finished_run["document"].id for row in citations)

    async def test_it_reads_the_source_run_and_writes_only_its_own(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        """Isolation is structural: everything the dry run wrote carries its own job id."""
        session: AsyncSession = finished_run["session"]
        source_job: Job = finished_run["job"]

        outcome = await _dry_run(finished_run, section_provider)

        assert outcome.job_id != source_job.id
        dry_job = await session.get(Job, outcome.job_id)
        assert dry_job is not None
        assert dry_job.workflow_version == DRY_RUN_WORKFLOW

        # The source run has no sections, no claims and no steps of its own.
        sections = await session.scalars(
            select(ReportSection).where(ReportSection.job_id == source_job.id)
        )
        assert list(sections) == []
        claims = await session.scalar(
            select(func.count(Claim.id))
            .join(ReportSection, ReportSection.id == Claim.report_section_id)
            .where(ReportSection.job_id == source_job.id)
        )
        assert claims == 0
        steps = await session.scalars(select(JobStep).where(JobStep.job_id == source_job.id))
        assert list(steps) == []

        # And what it produced is on the dry run's job.
        written = await session.scalars(
            select(ReportSection).where(ReportSection.job_id == outcome.job_id)
        )
        assert [row.section_key for row in written] == ["custom.moat_durability"]

    async def test_it_cites_the_source_run_s_own_calculations(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        """The evidence is the chosen run's. A dry run scoping calculations to its own
        (empty) job would silently have no figures to cite, and the scripted draft's
        assertion about the prompt is what catches that."""
        outcome = await _dry_run(finished_run, section_provider)

        prompt = section_provider.calls[-1]["messages"][0]["content"]
        calculation = await finished_run["session"].scalar(
            select(Calculation).where(Calculation.job_id == finished_run["job"].id)
        )
        assert calculation is not None
        assert str(calculation.id) in prompt
        assert outcome.claims_recorded == 2

    async def test_the_spend_is_metered_against_the_request(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        """A rehearsal whose cost was invisible would be the one way under a cap."""
        session: AsyncSession = finished_run["session"]

        outcome = await _dry_run(finished_run, section_provider)

        assert outcome.cost_gbp > 0
        assert outcome.cost_gbp <= outcome.estimated_cost_gbp
        billed = await session.scalar(
            select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.job_id == outcome.job_id)
        )
        rows = await session.scalar(
            select(func.count()).select_from(Cost).where(Cost.job_id == outcome.job_id)
        )
        # `costs.amount_gbp` is NUMERIC(12, 6): each row is rounded to six places as it is
        # stored, while the running total sums the unrounded lines — and the sum of rounded
        # rows is not the rounding of the summed total. The first version quantized the
        # total and demanded equality, which held only while no row landed on a rounding
        # boundary; lengthening a prompt by one sentence moved a call's price half a
        # micro-pound and broke it. The honest bound is the storage rounding itself: at
        # most half a micro-pound per row, asserted with a whole one per row for slack.
        assert rows > 0
        assert abs(Decimal(billed) - outcome.cost_gbp) <= Decimal("0.000001") * rows

    async def test_a_run_past_its_cap_is_refused_before_the_call(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        session: AsyncSession = finished_run["session"]
        finished_run["request"].work_order.max_cost_gbp = Decimal("0.01")
        await session.flush()

        with pytest.raises(DryRunRefusedError):
            await _dry_run(finished_run, section_provider)

        assert section_provider.calls == []

    async def test_a_methodology_skill_has_no_section_to_try(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        session: AsyncSession = finished_run["session"]
        await save_skill(
            session,
            source="""\
---
aer_skill: 1
key: house_view
kind: house_view
title: "House view"
version: 1
---

Always express valuation in GBP.
""",
            actor=finished_run["user"],
        )

        with pytest.raises(Exception, match="composes into an existing agent"):
            await dry_run_skill(
                session,
                key="house_view",
                source_job=finished_run["job"],
                settings=finished_run["settings"],
                provider=section_provider,
                router=Router(finished_run["settings"]),
                store=finished_run["store"],
            )

    async def test_an_unknown_key_is_refused(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        with pytest.raises(Exception, match="No skill is named"):
            await dry_run_skill(
                finished_run["session"],
                key="nothing_here",
                source_job=finished_run["job"],
                settings=finished_run["settings"],
                provider=section_provider,
                router=Router(finished_run["settings"]),
                store=finished_run["store"],
            )

    async def test_the_pinned_policy_is_the_composed_one(
        self, finished_run: dict[str, Any], section_provider: FakeProvider
    ) -> None:
        """A rehearsal under a policy a plan could not have produced would rehearse
        something else."""
        session: AsyncSession = finished_run["session"]
        outcome = await _dry_run(finished_run, section_provider)

        version = await current_version(session, key="moat_durability")
        assert version is not None
        composed = compose_for_version(version, settings=finished_run["settings"])

        dry_job = await session.get(Job, outcome.job_id)
        assert dry_job is not None
        assert dry_job.plan_id is not None

        [pin] = await pinned_skills_for_job(session, job=dry_job)
        assert pin.min_sources == composed.evidence.min_sources
        assert pin.token_budget == composed.token_budget
        assert sorted(pin.granted_tools or []) == sorted(composed.allowed_tools)


# ==========================================================================================
# The HTTP surface: the JSON API and the server-rendered pages
# ==========================================================================================


@pytest.fixture
async def committed(clean_slate: None, db_engine: Any, tmp_path: Any) -> dict[str, Any]:
    """A user and a finished run, committed so the application's own session sees them."""
    settings = _settings(tmp_path)
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        scene = await _seed_finished_run(session, store=store, email="surface@example.invalid")
        await session.commit()
        return {**scene, "settings": settings, "store": store}


@pytest.fixture
async def clean_slate(db_engine: Any) -> AsyncIterator[None]:
    """Empty what this file's HTTP tests commit, before and after.

    These tests commit for real — the application runs its own session and cannot see an
    uncommitted transaction — so their rows outlive them, and the last test in the file
    has nobody to clean up for it.
    """
    await _truncate(db_engine)
    yield
    await _truncate(db_engine)


async def _truncate(engine: Any) -> None:
    """Clear what this file commits, without taking the platform's own rows with it.

    ``skills`` is **deleted, not truncated**. ``section_definitions`` carries a foreign
    key to it, and ``TRUNCATE ... CASCADE`` truncates every table referencing the one
    named whatever its ``ondelete`` says — so truncating skills would silently empty the
    seeded built-in sections and leave every other test file rendering empty reports. A
    delete respects the constraints, which is exactly what is wanted here: the projected
    custom definitions go first, and the built-ins are never touched.
    """
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text(
                "TRUNCATE research_requests, users, artefacts, companies, prompts "
                "RESTART IDENTITY CASCADE"
            )
        )
        await connection.execute(text("DELETE FROM section_definitions WHERE origin = 'skill'"))
        await connection.execute(text("DELETE FROM skills"))


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    section_provider: FakeProvider,
) -> AsyncIterator[Any]:
    app = build_app(
        api_settings,
        engine=db_engine,
        redis=fake_redis,
        provider=section_provider,
        store=committed["store"],
    )
    async for client in client_for(app):
        yield client


class TestTheSkillsApi:
    async def test_saving_validating_and_listing(self, api: Any) -> None:
        created = await api.post("/api/skills", json={"source": SKILL_SOURCE})
        assert created.status_code == 201
        assert created.json()["key"] == "moat_durability"
        assert created.json()["version"] == 1
        assert created.json()["enabled"] is False

        listed = await api.get("/api/skills")
        assert [row["key"] for row in listed.json()["skills"]] == ["moat_durability"]

    async def test_validate_writes_nothing_and_shows_the_clamps(self, api: Any) -> None:
        response = await api.post("/api/skills/validate", json={"source": GREEDY_SOURCE})

        body = response.json()
        assert body["valid"] is True
        assert {clamp["field"] for clamp in body["clamps"]} == {
            "evidence_policy.min_sources",
            "evidence_policy.requires_primary",
            "allowed_tools",
            "token_budget",
        }
        # Nothing was written: the key does not exist.
        assert (await api.get("/api/skills")).json()["skills"] == []

    async def test_an_invalid_file_reports_its_lines_without_a_500(self, api: Any) -> None:
        response = await api.post("/api/skills/validate", json={"source": "nonsense"})

        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert response.json()["issues"]

    async def test_editing_under_another_key_is_refused(self, api: Any) -> None:
        """An editor open on one skill must not write a version of another.

        The file is *edited* rather than resubmitted verbatim, so the refusal cannot come
        from the byte-identical guard — it has to be the key check that fires, and the
        message says which key was declared.
        """
        await api.post("/api/skills", json={"source": SKILL_SOURCE})
        edited = SKILL_SOURCE.replace("min_sources: 1", "min_sources: 2")

        response = await api.put("/api/skills/some_other_key", json={"source": edited})

        assert response.status_code == 422
        assert "open on 'some_other_key'" in response.json()["detail"]
        # And nothing was written under either key.
        [row] = (await api.get("/api/skills")).json()["skills"]
        assert row["key"] == "moat_durability"
        assert row["version"] == 1

    async def test_import_without_confirmation_writes_nothing(self, api: Any) -> None:
        response = await api.post("/api/skills/import", json={"source": SKILL_SOURCE})

        body = response.json()
        assert body["applied"] is False
        assert body["is_new"] is True
        assert body["diff"]
        assert (await api.get("/api/skills")).json()["skills"] == []

    async def test_import_with_the_confirmed_hash_applies(self, api: Any) -> None:
        shown = (await api.post("/api/skills/import", json={"source": SKILL_SOURCE})).json()

        applied = await api.post(
            "/api/skills/import",
            json={"source": SKILL_SOURCE, "payload_hash": shown["payload_hash"]},
        )

        assert applied.json()["applied"] is True
        assert [row["key"] for row in (await api.get("/api/skills")).json()["skills"]] == [
            "moat_durability"
        ]

    async def test_a_stale_confirmation_is_refused(self, api: Any) -> None:
        """The version moved underneath the diff, so the confirmation describes a
        replacement that is no longer the one being made."""
        shown = (await api.post("/api/skills/import", json={"source": SKILL_SOURCE})).json()
        await api.post("/api/skills", json={"source": SKILL_SOURCE})

        response = await api.post(
            "/api/skills/import",
            json={
                "source": SKILL_SOURCE.replace("min_sources: 1", "min_sources: 2"),
                "payload_hash": shown["payload_hash"],
            },
        )

        assert response.status_code == 422
        assert "since the diff was shown" in response.json()["detail"]

    async def test_enabling_is_its_own_decision(self, api: Any) -> None:
        await api.post("/api/skills", json={"source": SKILL_SOURCE})

        response = await api.post("/api/skills/moat_durability/enable", json={"enabled": True})

        assert response.json()["enabled"] is True

    async def test_a_dry_run_returns_the_rendered_section(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        await api.post("/api/skills", json={"source": SKILL_SOURCE})

        response = await api.post(
            "/api/skills/moat_durability/dry-run",
            json={"job_id": str(committed["job"].id)},
        )

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "generated"
        assert body["claims"] == 2
        assert "198,270" in body["markdown"]
        assert Decimal(body["cost_gbp"]) > 0
        assert body["source_job_id"] == str(committed["job"].id)
        assert body["job_id"] != str(committed["job"].id)

    async def test_a_dry_run_against_somebody_else_s_run_is_refused(
        self, api: Any, db_engine: Any, tmp_path: Any
    ) -> None:
        """A dry run reads a run's evidence and spends against its budget. Neither is
        somebody else's to lend."""
        await api.post("/api/skills", json={"source": SKILL_SOURCE})
        store = LocalArtefactStore(
            (tmp_path / "other"), max_bytes=_settings(tmp_path).max_artefact_bytes
        )
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            stranger = await _seed_finished_run(
                session, store=store, email="stranger@example.invalid", ticker="CTSO"
            )
            await session.commit()
            other_job_id = stranger["job"].id

        response = await api.post(
            "/api/skills/moat_durability/dry-run", json={"job_id": str(other_job_id)}
        )

        assert response.status_code == 404


class TestTheSkillsPages:
    async def test_the_library_lists_what_is_saved(self, api: Any) -> None:
        await api.post("/api/skills", json={"source": SKILL_SOURCE})

        page = await api.get("/skills")

        assert page.status_code == 200
        assert 'id="skill-moat_durability"' in page.text
        assert 'id="new-skill"' in page.text

    async def test_the_editor_opens_on_the_stored_source(self, api: Any) -> None:
        await api.post("/api/skills", json={"source": SKILL_SOURCE})

        page = await api.get("/skills/moat_durability")

        assert page.status_code == 200
        assert "Competitive Moat Durability" in page.text
        # The composed policy, rendered on the server rather than fetched by a script.
        assert 'id="composed"' in page.text
        assert 'id="token-budget"' in page.text

    async def test_validating_through_the_form_shows_the_clamps(self, api: Any) -> None:
        page = await api.get("/skills/new")
        response = await api.post(
            "/skills/validate",
            data={
                CSRF_FIELD_NAME: _hidden(page.text, CSRF_FIELD_NAME),
                "source": GREEDY_SOURCE,
                "key": "",
            },
        )

        assert response.status_code == 200
        assert 'id="clamps"' in response.text
        assert "token_budget" in response.text

    async def test_an_invalid_file_renders_its_issues_beside_their_lines(self, api: Any) -> None:
        page = await api.get("/skills/new")
        response = await api.post(
            "/skills/validate",
            data={
                CSRF_FIELD_NAME: _hidden(page.text, CSRF_FIELD_NAME),
                "source": SKILL_SOURCE.replace("max_tier: 4", "max_tier: 9"),
                "key": "",
            },
        )

        assert 'id="issues"' in response.text
        assert "line " in response.text

    async def test_saving_through_the_form_redirects_to_the_editor(self, api: Any) -> None:
        page = await api.get("/skills/new")

        response = await api.post(
            "/skills/save",
            data={
                CSRF_FIELD_NAME: _hidden(page.text, CSRF_FIELD_NAME),
                "source": SKILL_SOURCE,
                "key": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/skills/moat_durability"

    async def test_a_form_post_without_a_token_saves_nothing(self, api: Any) -> None:
        response = await api.post("/skills/save", data={"source": SKILL_SOURCE, "key": ""})

        assert response.status_code == 403
        assert (await api.get("/api/skills")).json()["skills"] == []

    async def test_the_import_page_shows_a_diff_before_it_writes(self, api: Any) -> None:
        page = await api.get("/skills/import")

        response = await api.post(
            "/skills/import",
            data={CSRF_FIELD_NAME: _hidden(page.text, CSRF_FIELD_NAME), "source": SKILL_SOURCE},
        )

        assert 'id="import-diff"' in response.text
        assert 'id="confirm-import"' in response.text
        assert (await api.get("/api/skills")).json()["skills"] == []

    async def test_the_dry_run_form_renders_the_section(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        """Author to rendered section without a line of JavaScript."""
        await api.post("/api/skills", json={"source": SKILL_SOURCE})
        page = await api.get("/skills/moat_durability")
        assert 'id="run-dry-run"' in page.text

        response = await api.post(
            "/skills/moat_durability/dry-run",
            data={
                CSRF_FIELD_NAME: _hidden(page.text, CSRF_FIELD_NAME),
                "job_id": str(committed["job"].id),
            },
        )

        assert response.status_code == 200
        assert 'id="dry-run-result"' in response.text
        assert 'id="dry-run-markdown"' in response.text
        assert "198,270" in response.text


def _hidden(html: str, name: str) -> str:
    found = re.search(rf'name="{re.escape(name)}"\s+value="([^"]*)"', html)
    assert found is not None, f"no hidden {name} field in the page"
    return found.group(1)
