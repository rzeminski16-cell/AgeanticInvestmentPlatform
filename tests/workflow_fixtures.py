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

import json
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
from aer.providers.fake import FakeProvider
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
    """A provider scripted to answer every role a slice run reaches.

    The planner and the workers answer from static scripts; the worker reports
    immediately with no findings, because findings must cite ids that exist in the run
    and a static script cannot know them. The section writer cannot be static either —
    its drafts must satisfy each section's own contract and cite the run's real ids — so
    it answers from :class:`ScriptedSectionBrain`, which reads the composed prompt back
    off the provider's own call log. The red team raises no challenges: its scripted
    verdict is an honest "nothing found", not an absence.
    """
    brain = ScriptedSectionBrain()
    provider = FakeProvider(brain, **kwargs)
    brain.provider = provider
    return provider


class ScriptedSectionBrain:
    """The fake's answer for every schema, with the section writer done properly.

    The Phase 1 placeholder that once filled built-in sections in production moved here
    when task 45 replaced it with the real writer — a placeholder belongs in the fake.
    For a ``SectionDraft`` it parses the evidence listing out of the prompt it was just
    sent (``provider.calls[-1]``, appended before the script is consulted), builds
    content satisfying the contract embedded in the system prompt, and proposes one
    numeric claim naming the run's real calculation with a citation that genuinely
    verifies — the extraction rows are real, so the deterministic verifier passes them.
    """

    def __init__(self) -> None:
        self.provider: FakeProvider | None = None

    def __call__(self, schema: type[Any]) -> Any:
        name = schema.__name__
        if name == "ResearchPlanDraft":
            return planner_response()
        if name == "WorkerTurn":
            return worker_report_turn()
        if name == "SectionDraft":
            assert self.provider is not None, "bind the provider before the first call"
            return section_draft_for(self.provider.calls[-1])
        if name == "RedTeamReport":
            from aer.agents.red_team import RedTeamReport  # noqa: PLC0415 -- keeps import light

            return RedTeamReport(
                challenges=[], coverage_note="Scripted adversary; no challenges raised."
            )
        if name == "ValidatorAdvisory":
            # The slice's source is undated and, since task 45, has readable extracted
            # text — so the date-adjudication assist genuinely fires. The honest scripted
            # answer is "nothing established": advice, deciding nothing.
            from aer.agents.validator import ValidatorAdvisory  # noqa: PLC0415

            return ValidatorAdvisory(
                found=False,
                rationale="Scripted assist; the text establishes no publication date.",
                confidence=0.1,
            )
        message = f"The scripted brain has no answer for schema {name!r}."
        raise AssertionError(message)


def section_draft_for(call: dict[str, Any]) -> Any:
    """A draft satisfying the call's own contract, citing the call's own evidence.

    Everything is read from the composed prompt: the contract from the system prompt
    (the writer embeds it as indented JSON at the end), the evidence ids from the user
    message's single-line evidence array. Content strings carry no numerals, so the one
    numeral in the draft — the calculation's value — is exactly covered by the one
    numeric claim, and the §2.12 numeral rule holds by construction.
    """
    from aer.agents.section_writer import SectionDraft  # noqa: PLC0415 -- keeps import light

    contract = _contract_from_system(str(call["system"]))
    evidence = _evidence_from_messages(call["messages"])

    calculation = next((item for item in evidence if "calculation_id" in item), None)
    extraction = next((item for item in evidence if "extraction_id" in item), None)
    fact = next((item for item in evidence if "fact_id" in item), None)

    content: dict[str, Any] = {}
    for name, subschema in contract.get("properties", {}).items():
        if not isinstance(subschema, dict):
            continue
        declared = subschema.get("type")
        if declared == "string":
            content[name] = "Scripted analysis from the recorded evidence; see the figures."
        elif declared == "array" and _items_are_objects(subschema):
            content[name] = [_item_for(subschema, calculation=calculation, fact=fact)]
        elif declared == "array":
            content[name] = ["A scripted observation with no figure in it."]

    # A formal claim is proposed only where it can carry a citation the verifier will
    # confirm; the figure rows above carry lineage either way, through their named ids.
    claims: list[dict[str, Any]] = []
    if calculation is not None and extraction is not None:
        claims.append(
            {
                "statement": (
                    f"The recorded {calculation.get('name', 'calculation')} is "
                    f"{calculation['value']} {calculation.get('unit', '')}.".strip()
                ),
                "kind": "numeric",
                "calculation_id": calculation["calculation_id"],
                "citations": [
                    {
                        "source_document_id": extraction["source_document_id"],
                        "extraction_id": extraction["extraction_id"],
                    }
                ],
            }
        )

    return SectionDraft(content=content, claims=claims)


def _contract_from_system(system: str) -> dict[str, Any]:
    """The contract the writer embedded: the first indented JSON object in the prompt.

    ``raw_decode`` rather than ``loads`` because the base agent composes more prompt
    after the writer's own text — the containment rule sits last — so the contract JSON
    is followed by prose the parser must ignore rather than choke on.
    """
    start = system.index('{\n  "type"')
    contract, _ = json.JSONDecoder().raw_decode(system, start)
    return dict(contract)


def _evidence_from_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    marker = "The run's evidence, as data:\n"
    for message in messages:
        content = message.get("content", "")
        if marker in content:
            line = content.split(marker, 1)[1].split("\n\n", 1)[0]
            return list(json.loads(line))
    return []


def _items_are_objects(subschema: dict[str, Any]) -> bool:
    items = subschema.get("items")
    return isinstance(items, dict) and items.get("type") == "object"


def _item_for(
    subschema: dict[str, Any],
    *,
    calculation: dict[str, Any] | None,
    fact: dict[str, Any] | None,
) -> dict[str, Any]:
    """One row satisfying the array's item schema, citing its figure where it carries one.

    Required text fields get numeral-free prose. A ``value`` field gets the calculation's
    value and the row names the calculation — the figure-row convention the renderer
    footnotes and the numeral rule accepts — plus the fact's source document where the
    schema declares the key. With no calculation in evidence the row says "n/a" and
    cites nothing, which is the honest empty state.
    """
    items: dict[str, Any] = subschema.get("items", {})
    properties: dict[str, Any] = items.get("properties", {})
    row: dict[str, Any] = {}
    for name in items.get("required", []):
        if name == "value":
            row[name] = str(calculation["value"]) if calculation is not None else "n/a"
        elif name == "unit":
            row[name] = str(calculation.get("unit", "ratio")) if calculation else "n/a"
        else:
            row[name] = f"Scripted {name.replace('_', ' ')} with no figure in it."
    if calculation is not None and "calculation_id" in properties and "value" in row:
        row["calculation_id"] = calculation["calculation_id"]
    if fact is not None and "source_document_id" in properties:
        row["source_document_id"] = fact["source_document_id"]
    return row


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
