"""Everything needed to run the vertical slice with no network and no model spend.

The workflow's one external dependency is EDGAR, and its one model dependency is the
planner. Both are substituted here: :class:`StubSecClient` serves the recorded-shape
fixture through the real artefact store, and
:class:`~aer.providers.fake.FakeProvider` answers the planner from a script.

**The stub is a stub of the client, not of the network.** It stores the fixture bytes in
the same content-addressed store the real path uses and hands back a
:class:`~aer.fetch.client.FetchResult` describing them, so the acquire step's provenance
recording, the extract step's read-back-by-hash and the citation verification all exercise
their real code. Stubbing at the HTTP layer would have meant reimplementing the fetcher;
stubbing the parsed result would have meant no artefact and nothing to cite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.planner import PlannedSection, PlannedSource, ResearchPlanDraft
from aer.agents.worker import WorkerLead, WorkerReport, WorkerTurn
from aer.config import Settings
from aer.core.enums import JobStatus, UserRole
from aer.db.models import Job, ResearchRequest, SectionDefinition, User
from aer.fetch.client import FetchResult
from aer.providers.fake import FakeProvider, ScriptedResponse
from aer.sources.base import ResolvedEntity
from aer.sources.sec.client import SecResponse
from aer.sources.sec.companyfacts import parse_company_facts
from aer.storage.local import LocalArtefactStore
from aer.version import git_sha
from aer.workflow.workflows.vertical_slice_v1 import WORKFLOW_VERSION
from tests.sec_fixtures import MSFT_CIK, fixture_bytes

COMPANY_FACTS_FIXTURE = "companyfacts_msft.json"

# Late enough that the fixture's FY2020 and FY2021 revenue are both admissible, so the
# slice has the two periods a growth rate needs.
AS_OF_DATE = date(2022, 6, 30)

# The eighteen-section spine in position order, as migrations 0006 and 0023 seed it.
# Test data, not a section registry: tests assert a run's sections against this list so a
# lost or reordered seed row fails visibly.
SPINE_KEYS = (
    "executive_summary",
    "investment_thesis",
    "business_overview",
    "segment_analysis",
    "industry_landscape",
    "management_governance",
    "historical_financial_analysis",
    "earnings_quality",
    "balance_sheet_liquidity",
    "cash_flow_analysis",
    "capital_allocation",
    "growth_outlook",
    "valuation_dcf",
    "scenarios_sensitivities",
    "key_risks",
    "catalysts",
    "prior_research_comparison",
    "validation_disagreements",
)


class StubSecClient:
    """The SEC client's surface, served from a fixture through the real artefact store."""

    def __init__(self, store: LocalArtefactStore, *, payload: bytes | None = None) -> None:
        self._store = store
        self._payload = payload if payload is not None else fixture_bytes(COMPANY_FACTS_FIXTURE)
        self.entity_calls: list[str] = []
        self.facts_calls: list[str] = []

    async def resolve_entity(self, ticker: str, *, exchange: str | None = None) -> ResolvedEntity:
        self.entity_calls.append(ticker)
        return ResolvedEntity(
            identifier=MSFT_CIK,
            name="MICROSOFT CORP",
            ticker=ticker,
            exchange=exchange,
        )

    async def fetch_company_facts(self, cik: str) -> SecResponse[Any]:
        """Store the bytes, then describe them exactly as a real fetch would.

        Written to the store first so the extract step's read-by-hash finds them. A stub
        that returned a hash for bytes nobody stored would pass the acquire step and fail
        the next one, several layers from the cause.
        """
        self.facts_calls.append(cik)
        stored = await self._store.put_bytes(self._payload)

        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        return SecResponse(
            data=parse_company_facts(self._payload),
            fetch=FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type="application/json",
                declared_media_type="application/json",
                headers={"content-type": "application/json"},
                redirect_chain=(),
                elapsed_ms=1.0,
                attempts=1,
                licence_note="US government work, public domain.",
                robots_allowed=True,
            ),
        )


def planner_response(*, section_keys: list[str] | None = None) -> ResearchPlanDraft:
    """A plan the fake provider returns. Names no figures, as the real one must not."""
    keys = section_keys or ["executive_summary", "historical_financial_analysis"]
    return ResearchPlanDraft(
        summary=(
            "Retrieve the company's XBRL facts from EDGAR, select the periods admissible "
            "at the as-of date, and compute reported revenue growth over them."
        ),
        sections=[
            PlannedSection(key=key, focus=f"What the filed history shows for {key}.")
            for key in keys
        ],
        planned_sources=[
            PlannedSource(
                provider="sec_edgar",
                tier="T1_REGULATORY",
                what="XBRL company facts",
                why="Establishes reported revenue for each filed period.",
            )
        ],
        known_risks=["Only one filing is retrieved, so restatements are not compared."],
        confidence=0.6,
    )


def make_provider(**kwargs: Any) -> FakeProvider:
    """A provider scripted to answer the planner and the research workers.

    The worker script reports immediately with no findings: findings must cite ids that
    exist in the run, and a static script cannot know them. Leads carry no ids, so a lead
    plus a coverage note is the honest all-purpose worker answer; the loop tests that
    exercise tools and findings script their own stateful providers.
    """
    return FakeProvider(
        {
            "ResearchPlanDraft": ScriptedResponse(planner_response(), output_tokens=400),
            "WorkerTurn": ScriptedResponse(worker_report_turn(), output_tokens=150),
        },
        **kwargs,
    )


def worker_report_turn() -> WorkerTurn:
    """A finished investigation with nothing to assert and one honest lead."""
    return WorkerTurn(
        requests=[],
        report=WorkerReport(
            findings=[],
            leads=[
                WorkerLead(
                    question="What does the filing say about segment concentration?",
                    why_it_matters="Concentration decides how brittle the revenue base is.",
                )
            ],
            coverage_note="Scripted fixture investigation; no evidence was searched.",
        ),
    )


async def seed_starved_section(session: AsyncSession) -> None:
    """A required section that can never meet its evidence floor.

    Its contract holds only prose fields, so the draft fills it with no citation at all —
    which makes both the §2.4 coverage and missing-section conditions genuinely hold on a
    run. Tests that need a fired banner seed this rather than relying on any built-in
    being poor, because the spine's own sections all carry citation fields.
    """
    session.add(
        SectionDefinition(
            key="starved_probe",
            version=1,
            origin="builtin",
            title="Starved Probe",
            position=Decimal(500),
            required=True,
            output_contract={
                "type": "object",
                "title": "Starved Probe",
                "required": ["commentary"],
                "properties": {"commentary": {"type": "string", "title": "Commentary"}},
            },
            evidence_policy={"min_sources": 1, "requires_primary": True},
            token_budget=1000,
            allowed_tools=[],
            applicability={},
        )
    )
    await session.flush()


async def seed_user(session: AsyncSession, *, email: str = "runner@example.invalid") -> User:
    user = User(email=email, display_name="Runner", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    return user


async def seed_request(
    session: AsyncSession,
    *,
    user: User,
    max_cost_gbp: Decimal = Decimal("2.50"),
    as_of_date: date = AS_OF_DATE,
) -> ResearchRequest:
    request = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=as_of_date,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp=max_cost_gbp,
    )
    session.add(request)
    await session.flush()
    return request


async def seed_job(session: AsyncSession, *, request: ResearchRequest) -> Job:
    job = Job(
        request_id=request.id,
        workflow_version=WORKFLOW_VERSION,
        code_version=git_sha() or "test",
        status=JobStatus.QUEUED,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    return job


@pytest.fixture
def workflow_settings(settings_env: pytest.MonkeyPatch, tmp_path: Any) -> Settings:
    """Settings for a workflow run: throwaway artefact root, no credentials."""
    from aer.config import load_settings  # noqa: PLC0415 -- after the environment is set

    settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
    settings_env.setenv("AER_SECRET_KEY", "workflow-test-signing-key")
    return load_settings()


@pytest.fixture
def workflow_store(workflow_settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(
        workflow_settings.artefact_root, max_bytes=workflow_settings.max_artefact_bytes
    )


@pytest.fixture
def sec_client(workflow_store: LocalArtefactStore) -> StubSecClient:
    return StubSecClient(workflow_store)


@pytest.fixture
def provider() -> FakeProvider:
    return make_provider()


def uuid_of(value: Any) -> uuid.UUID:
    return uuid.UUID(str(value))
