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

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.custom_section import CustomSectionDraft
from aer.agents.planner import PlannedSection, PlannedSource, ResearchPlanDraft
from aer.agents.section_writer import SectionDraft
from aer.agents.worker import WorkerLead, WorkerReport, WorkerTurn
from aer.config import Settings
from aer.core.enums import GateKind, JobStatus, UserRole
from aer.db.models import Job, JobStep, ResearchRequest, SectionDefinition, User
from aer.fetch.client import FetchResult
from aer.providers.fake import FakeProvider
from aer.sources.base import ResolvedEntity
from aer.sources.sec.client import SecResponse
from aer.sources.sec.companyfacts import parse_company_facts
from aer.sources.sec.submissions import parse_submissions
from aer.storage.local import LocalArtefactStore
from aer.version import git_sha
from aer.workflow.workflows.vertical_slice_v1 import WORKFLOW_VERSION
from tests.schema_guard import refuse_unanswerable_schema
from tests.sec_fixtures import MSFT_CIK, fixture_bytes

# Read from the model rather than restated, so the fixture cannot drift away from the
# production ceiling it is standing in for.
DEFAULT_PER_RUN_BUDGET_GBP: Decimal = Settings.model_fields["per_run_budget_gbp"].default

COMPANY_FACTS_FIXTURE = "companyfacts_msft.json"
SUBMISSIONS_FIXTURE = "submissions_msft.json"

# A filing's primary document, small but real: paragraphs long enough to be excerpted, in
# the shape the acquisition path reads. Marker text would exercise the plumbing and prove
# nothing about the excerpts a citation is later verified against.
FILING_DOCUMENT = b"""<!DOCTYPE html><html><head><title>Annual report</title></head><body>
<p>Revenue increased across all three segments during the year, with commercial cloud
remaining the largest single contributor to the growth the company reported.</p>
<p>The company returned capital through a combination of dividends and share repurchases,
and describes its capital allocation priorities as unchanged from the prior year.</p>
<p>Risk factors include competition in cloud infrastructure, foreign exchange movement in
the currencies the company bills in, and the regulatory environment for large platforms.</p>
</body></html>"""

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
        self.submissions_calls: list[str] = []
        self.document_calls: list[str] = []

    async def resolve_entity(self, ticker: str, *, exchange: str | None = None) -> ResolvedEntity:
        """The fixture's filer for the subject's ticker, a distinct filer for anything else.

        It used to answer ``MSFT_CIK`` for every ticker, which was harmless while only the
        acquire step resolved anything. The peer proposal (ADR 0059) resolves the tickers a
        model returns, and a stub that maps them all onto the subject would have every peer
        refused as the subject under another listing — a run that differs from a live one
        for a reason that is entirely about the stub. Resolving is all discovery does with
        the answer now (ADR 0059, amended): nothing is fetched for a peer.
        """
        self.entity_calls.append(ticker)
        known = ticker.upper() == "MSFT"
        return ResolvedEntity(
            identifier=MSFT_CIK if known else _peer_cik(ticker),
            name="MICROSOFT CORP" if known else f"{ticker.upper()} CORP",
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

    async def fetch_submissions(self, cik: str) -> SecResponse[Any]:
        """The filing index, from the same fixture the submissions parser is tested on."""
        self.submissions_calls.append(cik)
        payload = fixture_bytes(SUBMISSIONS_FIXTURE)
        stored = await self._store.put_bytes(payload)
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        return SecResponse(
            data=parse_submissions(payload),
            fetch=_stub_fetch(url, stored, media_type="application/json"),
        )

    async def fetch_document(self, ref: Any) -> FetchResult:
        """One filing's primary document, as a small stand-in with real prose in it.

        Real prose rather than a marker string, because the acquisition path excerpts what
        it fetches and the citation verifier re-reads those excerpts: a document of
        placeholder text would exercise the plumbing and prove nothing about the excerpts.

        Distinct bytes per filing, as real filings are: the stub once served identical
        bytes for every ref, and once one source record per artefact per request was
        enforced (gap C4) that collapsed a scene's 10-K and 10-Q into a single document —
        a merge that can never happen to documents whose content differs.
        """
        self.document_calls.append(ref.url)
        body = FILING_DOCUMENT.replace(
            b"</body>", b"<p>Archive copy of " + ref.url.encode() + b".</p></body>"
        )
        stored = await self._store.put_bytes(body)
        return _stub_fetch(ref.url, stored, media_type="text/html")


def _peer_cik(ticker: str) -> str:
    """A stable ten-digit CIK for a ticker that is not the subject's.

    Derived by digest rather than by ``hash``, which is salted per process: an identifier
    that changed between runs would make the discovery step's own deduplication untestable
    and would occasionally collide with the subject's. Zero-padded to ten characters
    because `companies.cik` carries a check constraint for exactly that.
    """
    digest = hashlib.sha256(ticker.upper().encode()).hexdigest()
    return str(int(digest[:12], 16) % 10**9).rjust(10, "9")


def _stub_fetch(url: str, stored: Any, *, media_type: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        media_type=media_type,
        declared_media_type=media_type,
        headers={"content-type": media_type},
        redirect_chain=(),
        elapsed_ms=1.0,
        attempts=1,
        licence_note="US government work, public domain.",
        robots_allowed=True,
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
    # The SDK-backed schema check (gap A18). Every call a run makes now goes through the
    # same question the live run asked — on the schema that call actually composed, which
    # is stronger than checking the registered contracts alone, because the section writer
    # narrows its contract per section and that narrowing is where it broke.
    kwargs.setdefault("inspect_schema", refuse_unanswerable_schema)
    provider = FakeProvider(brain, **kwargs)
    brain.provider = provider
    return provider


def declared_schema_name(schema: type) -> str:
    """The role's declared contract name, whatever this call narrowed it to.

    A section writer asks for a subclass built from that section's own output contract
    (:mod:`aer.agents.contract_schema`), so the class the provider is handed is named for
    the section rather than for the role. A double matching on the exact name would answer
    nothing — and would report a bug that is not there.
    """
    # `SectionDraft` before `CustomSectionDraft`: the first is a subclass of the second,
    # and testing the general case first would call every built-in draft a custom one.
    for base in (SectionDraft, CustomSectionDraft):
        if issubclass(schema, base):
            return base.__name__
    return schema.__name__


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
        name = declared_schema_name(schema)
        if name == "ResearchPlanDraft":
            return planner_response()
        if name == "WorkerTurn":
            return worker_report_turn()
        if name == "SectionDraft":
            assert self.provider is not None, "bind the provider before the first call"
            return section_draft_for(self.provider.calls[-1])
        if name == "CustomSectionDraft":
            # A run with an enabled custom section reaches this; the custom-section agent
            # composes its contract and evidence with the same markers the writer uses,
            # so one builder answers both.
            assert self.provider is not None, "bind the provider before the first call"
            return custom_section_draft_for(self.provider.calls[-1])
        if name == "ChallengeBriefs":
            # One brief per challenge it was shown, keyed by the ids in the prompt. A
            # static answer could not key them, and a briefing keyed to nothing is exactly
            # what the service drops -- so the fake would silently exercise the drop path
            # on every run that has a challenge.
            assert self.provider is not None, "bind the provider before the first call"
            return challenge_briefs_for(self.provider.calls[-1])
        answer = _STATIC_ANSWERS.get(name)
        if answer is not None:
            return answer()
        message = f"The scripted brain has no answer for schema {name!r}."
        raise AssertionError(message)


# The roles whose scripted answer depends on nothing: no prompt to read back, no run ids to
# cite. A mapping rather than another `if` each, because the dispatcher above is the one
# place a reader looks to find out what the fake says, and a wall of branches stops being
# that at about six.


def assumption_proposal_draft() -> Any:
    """ADR 0046's two opinions.

    Both inside the deterministic bounds on purpose: a scripted answer that tripped them
    would exercise the refusal path on every workflow run, and the refusal has its own
    tests where the value is the point.
    """
    from aer.agents.assumptions import (  # noqa: PLC0415 -- keeps import light
        AssumptionProposalDraft,
        OpinionProposal,
    )

    return AssumptionProposalDraft(
        terminal_growth=OpinionProposal(
            value=Decimal("0.02"),
            justification="Scripted proposal: long-run nominal growth for a mature filer.",
            confidence=0.5,
        ),
        exit_multiple=OpinionProposal(
            value=Decimal("10"),
            justification="Scripted proposal: a mid-range EV/EBITDA for the sector.",
            confidence=0.5,
        ),
    )


def red_team_report() -> Any:
    """A scripted adversary that raises nothing. An honest "nothing found", not an absence."""
    from aer.agents.red_team import RedTeamReport  # noqa: PLC0415 -- keeps import light

    return RedTeamReport(challenges=[], coverage_note="Scripted adversary; no challenges raised.")


def plan_critique() -> Any:
    """A scripted plan critic that raises nothing (ADR 0091).

    The same honesty as the red team's script: "the plan survives" is a real answer, and
    the challenge-and-revise path has its own tests where the challenge is the point.
    """
    from aer.agents.plan_critic import PlanCritique  # noqa: PLC0415 -- keeps import light

    return PlanCritique(challenges=[], coverage_note="Scripted critic; no challenges raised.")


def validator_advisory() -> Any:
    """Advice that decides nothing.

    The slice's source is undated and, since task 45, has readable extracted text — so the
    date-adjudication assist genuinely fires, and the honest scripted answer is "nothing
    established".
    """
    from aer.agents.validator import ValidatorAdvisory  # noqa: PLC0415

    return ValidatorAdvisory(
        found=False,
        rationale="Scripted assist; the text establishes no publication date.",
        confidence=0.1,
    )


def peer_slate() -> Any:
    """One peer, whose ticker :class:`StubSecClient` resolves (ADR 0059).

    A slate rather than an empty list, because an empty one would leave every slice run
    exercising the fallback path and nothing exercising the real one. One entry rather
    than several: the stub resolves every ticker to the same CIK, so a second would be
    refused as a duplicate and the run would differ from a live one for a reason that is
    about the stub.
    """
    from aer.agents.peers import PeerSlate, ProposedPeer  # noqa: PLC0415 -- keeps import light

    return PeerSlate(
        peers=[
            ProposedPeer(
                ticker="PEER",
                name="Peer Corporation",
                rationale="Scripted proposal: sells comparable software to comparable buyers.",
            )
        ]
    )


def theme_slate() -> Any:
    """One scripted theme, so the THEME_SET gate fires on an ordinary run (K1).

    The same reasoning as the scripted peer: a fake that proposed nothing would leave the
    gate permanently skipped, with every driver exercising the bypass and nothing
    exercising the path a real run takes. One entry, because the reviewer-fatigue bound is
    the slate's own concern and one is enough to make the gate real.
    """
    from aer.agents.themes import ProposedTheme, ThemeSlate  # noqa: PLC0415 -- keeps import light

    return ThemeSlate(
        themes=[
            ProposedTheme(
                key="scripted-theme",
                label="Scripted theme",
                rationale="Scripted proposal: the subject sits squarely in this recurring story.",
            )
        ]
    )


def authored_verdict() -> Any:
    """A scripted authored half (ADR 0087): a plain sentence in the info tone.

    Deliberately unremarkable — the composed half is the one under test everywhere the
    verdict renders, and the authored sentence's job in the suite is to exist, carry a
    valid tone, and never be citable.
    """
    from aer.agents.verdict import AuthoredTone, AuthoredVerdict  # noqa: PLC0415

    return AuthoredVerdict(
        sentence="Scripted verdict; the record reads complete and unchallenged.",
        tone=AuthoredTone.INFO,
    )


def challenge_briefs_for(call: dict[str, Any]) -> Any:
    """A brief for each disagreement id in the prompt, read back off the call itself.

    Unremarkable prose and a lean towards the draft. What the suite needs from a brief is
    that it exists, keys to a real challenge, and never becomes a decision -- the wording
    is under test nowhere, and a scripted lean towards the challenge would make every
    fixture read as though the adversary had won.
    """
    from aer.agents.challenge_brief import (  # noqa: PLC0415 -- keeps import light
        ChallengeBrief,
        ChallengeBriefs,
        ChallengeSide,
    )

    text = " ".join(
        f"{message.get('cache_prefix') or ''} {message.get('content') or ''}"
        for message in call["messages"]
    )
    ids = re.findall(r"'disagreement_id':\s*'([0-9a-f-]{36})'", text)
    return ChallengeBriefs(
        briefs=[
            ChallengeBrief(
                disagreement_id=identifier,
                keeping_assumes="Scripted: the draft's reading of the record holds.",
                keeping_means="Scripted: the report keeps its position and notes the objection.",
                accepting_assumes="Scripted: the objection describes the record better.",
                accepting_means="Scripted: the report gives up the position it argued for.",
                leans=ChallengeSide.DRAFT,
                because="Scripted: the objection restates a risk the draft already carries.",
            )
            for identifier in ids
        ]
    )


_STATIC_ANSWERS: dict[str, Any] = {
    "AssumptionProposalDraft": assumption_proposal_draft,
    "AuthoredVerdict": authored_verdict,
    "PeerSlate": peer_slate,
    "PlanCritique": plan_critique,
    "RedTeamReport": red_team_report,
    "ThemeSlate": theme_slate,
    "ValidatorAdvisory": validator_advisory,
}


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

    content = _content_for(contract, calculation=calculation, fact=fact)

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
                "citations": [{"extraction_id": extraction["extraction_id"]}],
            }
        )

    return SectionDraft(content=content, claims=claims)


def custom_section_draft_for(call: dict[str, Any]) -> Any:
    """The same draft, in the custom-section envelope.

    The custom-section agent embeds its contract and its evidence listing with the same
    markers the built-in writer uses, so the contract-driven content builder is shared:
    a fake that answered user-authored sections differently from platform ones would be
    testing a distinction the platform does not make.
    """
    from aer.agents.custom_section import CustomSectionDraft  # noqa: PLC0415 -- light import

    contract = _contract_from_system(str(call["system"]))
    evidence = _evidence_from_messages(call["messages"])

    calculation = next((item for item in evidence if "calculation_id" in item), None)
    extraction = next((item for item in evidence if "extraction_id" in item), None)
    fact = next((item for item in evidence if "fact_id" in item), None)

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
                "citations": [{"extraction_id": extraction["extraction_id"]}],
            }
        )

    return CustomSectionDraft(
        content=_content_for(contract, calculation=calculation, fact=fact),
        claims=claims,
    )


def _content_for(
    contract: dict[str, Any],
    *,
    calculation: dict[str, Any] | None,
    fact: dict[str, Any] | None,
) -> dict[str, Any]:
    """Content satisfying a contract, field by field, from the run's own evidence.

    Strings carry no numerals, so the one numeral a draft contains — the calculation's
    value, in a figure row — is exactly covered by the one numeric claim, and the §2.12
    numeral rule holds by construction rather than by luck.
    """
    content: dict[str, Any] = {}
    for name, subschema in contract.get("properties", {}).items():
        if not isinstance(subschema, dict):
            continue
        declared = subschema.get("type")
        if declared == "string":
            content[name] = "Scripted analysis from the recorded evidence; see the figures."
        elif declared in {"number", "integer"}:
            content[name] = 8
        elif declared == "array" and _items_are_objects(subschema):
            content[name] = [_item_for(subschema, calculation=calculation, fact=fact)]
        elif declared == "array":
            content[name] = ["A scripted observation with no figure in it."]
    return content


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
        # Both blocks: since ADR 0048 the writer sends the evidence as the turn's cache
        # prefix, ahead of the ask, so a parser that read only `content` would find nothing
        # and quietly build a draft with no claims.
        for part in (message.get("cache_prefix") or "", message.get("content") or ""):
            if marker in part:
                line = part.split(marker, 1)[1].split("\n\n", 1)[0]
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
        spec = properties.get(name)
        if isinstance(spec, dict) and spec.get("type") == "array" and _items_are_objects(spec):
            # A nested object array — the period-series shape's ``values`` (gap R9).
            row[name] = [_item_for(spec, calculation=calculation, fact=fact)]
        elif name == "value":
            row[name] = str(calculation["value"]) if calculation is not None else "n/a"
        elif name == "unit":
            row[name] = str(calculation.get("unit", "ratio")) if calculation else "n/a"
        else:
            row[name] = f"Scripted {name.replace('_', ' ')} with no figure in it."
    if calculation is not None and "calculation_id" in properties and "value" in row:
        row["calculation_id"] = calculation["calculation_id"]
    if fact is not None and "source_document_id" in properties and "value" in row:
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

    Tests that need a fired §2.4 banner seed this rather than relying on any built-in
    being poor, because the spine's own sections all carry citation fields.

    **The starvation is the token budget, and it used to be the run's poverty.** The
    earlier version gave this section an ordinary budget and a prose-only contract, and
    relied on the run having nothing admissible to cite: the only source a slice run held
    was the undated companyfacts aggregate, quarantined out of every evidence listing. So
    the probe was starved by a platform defect rather than by anything about the probe.
    Acquiring the filings (A4) and dating the aggregate (ADR 0044) fixed that defect, the
    probe promptly cited a real 10-K, and three triggers stopped firing — a fixture
    quietly measuring the wrong thing, discovered only because it broke.

    A budget of one token admits no evidence unit at all: `_within_budget` keeps whole
    units, and the smallest of them costs more than that. So the section generates, cites
    nothing, and misses a floor it declared — which is what §2.4's coverage, uncertainty
    and missing-section conditions are each about, and is now true however well sourced
    the run around it becomes.
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
            # One token. The column's check constraint forbids zero, and zero means
            # something else anyway — a deterministic section that makes no model call.
            token_budget=1,
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
    # The platform's own default (`Settings.per_run_budget_gbp`), which is what a request
    # made through the form is capped at. Deliberately the same number: a fixture that
    # budgets more generously than production can never notice a step growing past the
    # ceiling real runs are held to, and this one did not — the draft step's estimate was
    # missing, so £2.50 admitted a run that measured over eight pounds.
    max_cost_gbp: Decimal = DEFAULT_PER_RUN_BUDGET_GBP,
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
        work_order_id=request.id,
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


# The conditional gates a run reaches on its way to the end, by the step each one pauses
# at and the gate an approval must name. **One mapping, imported by every driver**, because
# each suite used to hardcode its own sequence and ADR 0059 broke four of them at once: the
# peer set stopped being empty, the peer gate started firing on ordinary runs, and every
# driver that "knew" the order walked into a pause it had no case for.
#
# A test whose subject *is* one of these gates drives the run itself and asserts the pause.
CONDITIONAL_GATES: dict[str, tuple[GateKind, str]] = {
    "gate_peer_set": (GateKind.PEER_SET, "propose_peers"),
    "gate_theme_set": (GateKind.THEME_SET, "propose_themes"),
    "gate_assumptions": (GateKind.ASSUMPTIONS, "propose_assumptions"),
}


async def paused_at(session: AsyncSession, job_id: uuid.UUID) -> str | None:
    """The step key this run is waiting at, or ``None`` if it is not waiting.

    Read from the run's own steps rather than inferred from how far along it ought to be.
    Inferring is what made these drivers fragile: the failure arrived as "the
    propose_assumptions step has not run", which is a message about the wrong gate.
    """
    row = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.status == JobStatus.AWAITING_APPROVAL)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    return None if row is None else str(row.step_key)


def gate_for(step_key: str | None) -> tuple[GateKind, str] | None:
    """The gate and approving step for a pause a driver may clear, or ``None``.

    ``None`` for the final gate and for anything unrecognised, so a driver clears the
    intermediate stops and leaves the ones its test is about.
    """
    return CONDITIONAL_GATES.get(step_key or "")
