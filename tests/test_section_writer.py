"""The section writer: ADR 0042's role, exercised at its execution boundary.

The shape mirrors the custom-section unit suite deliberately — the two boundaries share
one discipline (:mod:`aer.sections.evidence`), so what is worth pinning here is what is
*specific* to the writer: the toolless registry row, the planner's focus reaching the
prompt, the absence of any ``<user_skill>`` block, and the failure ladder driven by the
definition row's own policy rather than a pin.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext, schema_problems
from aer.agents.custom_section import (
    CLAIM_BASIS_BUDGET,
    CLAIM_BASIS_CEILING,
    CLAIM_STATEMENT_BUDGET,
    CLAIM_STATEMENT_CEILING,
    CustomSectionAgent,
    CustomSectionInput,
    ProposedClaim,
)
from aer.agents.registry import resolve_role
from aer.agents.section_writer import SectionDraft, SectionWriterAgent, SectionWriterInput
from aer.config import Settings
from aer.core.enums import AnalysisMode, FactBasis, JobStatus, Provider, SourceTier
from aer.db.models import (
    AgentRun,
    Artefact,
    Calculation,
    Claim,
    Company,
    Cost,
    FinancialFact,
    Job,
    JobStep,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.errors import ValidationError
from aer.extract.html import extract_html
from aer.providers.fake import FakeProvider, ScriptedResponse
from aer.providers.protocol import SpentButUnusableError, Usage
from aer.providers.router import Router
from aer.sections.evidence import degradation_note
from aer.sections.registry import create_report_sections, resolve_sections, sections_for_job
from aer.sections.writing import execute_builtin_section, policy_of_definition
from aer.services.extractions import record_excerpt
from aer.storage.local import LocalArtefactStore
from aer.verify.citations import verify_job_citations
from aer.workflow.workflows.vertical_slice_v1 import WORKFLOW_VERSION
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.anyio

FILING = b"""<!DOCTYPE html><html><head><title>10-K</title></head><body>
<p>Cash flow from operations funded the year's capital programme in full.</p>
<p>Total revenue was $198,270 million for fiscal year 2022.</p>
</body></html>"""

CITED = "Total revenue was $198,270 million for fiscal year 2022."

# The section the unit tests draft against: a spine row whose contract requires a
# commentary and a figures table — the shape most of the spine shares.
SECTION_KEY = "cash_flow_analysis"


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    """A run holding one filed excerpt, one fact, one calculation and the seeded spine."""
    user = User(email="writer-exec@example.invalid", display_name="Writer")
    db_session.add(user)
    await db_session.flush()

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
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = Job(
        request_id=request.id,
        workflow_version=WORKFLOW_VERSION,
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    step = JobStep(
        job_id=job.id,
        step_key="draft",
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:draft",
        input_hash="0" * 64,
        started_at=datetime.now(UTC),
    )
    db_session.add(step)
    await db_session.flush()

    settings = Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)

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
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        # Before the as-of date: a post-dated source is a look-ahead violation the
        # admissibility rules rightly refuse, and this suite is not about that.
        publication_date=date(2022, 6, 15),
        quarantined=False,
    )
    db_session.add(document)
    await db_session.flush()

    extracted = extract_html(FILING).text
    excerpt = extracted.locate(CITED)
    assert excerpt is not None
    extraction = await record_excerpt(
        db_session, source_document_id=document.id, extracted=extracted, excerpt=excerpt
    )

    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    fact = FinancialFact(
        company_id=company.id,
        source_document_id=document.id,
        concept="revenue",
        value=Decimal("198270000000"),
        unit="USD",
        period_end=date(2022, 6, 30),
        basis=FactBasis.AS_REPORTED,
        filed_date=date(2022, 6, 15),
    )
    db_session.add(fact)

    calculation = Calculation(
        job_id=job.id,
        name="revenue_cagr",
        formula="cagr = (end / start) ** (1 / years) - 1",
        function_ref="aer.calc.basic:cagr",
        code_version="test",
        inputs=[],
        output_value=Decimal("0.18"),
        output_unit="ratio",
    )
    db_session.add(calculation)
    await db_session.flush()

    definitions = await resolve_sections(db_session, request=request)
    await create_report_sections(db_session, job_id=job.id, definitions=definitions)
    sections = await sections_for_job(db_session, job.id)
    section = next(s for s in sections if s.section_key == SECTION_KEY)

    return {
        "session": db_session,
        "request": request,
        "job": job,
        "step": step,
        "settings": settings,
        "store": store,
        "document": document,
        "extraction": extraction,
        "fact": fact,
        "calculation": calculation,
        "section": section,
    }


def _context(scene: dict[str, Any], provider: FakeProvider) -> AgentContext:
    return AgentContext(
        session=scene["session"],
        provider=provider,
        router=Router(scene["settings"]),
        settings=scene["settings"],
        store=scene["store"],
        job_step=scene["step"],
    )


def _scripted(drafts: list[SectionDraft | ScriptedResponse]) -> FakeProvider:
    remaining = list(drafts)

    def answer(schema: type) -> Any:
        # A subclass, not the class: the call narrows `content` to this section's contract,
        # so what the provider is handed is built for the section. Still the role's
        # envelope, which is what this double is asserting.
        assert issubclass(schema, SectionDraft)
        return remaining.pop(0)

    return FakeProvider(answer)


def _refusing(scene: dict[str, Any]) -> FakeProvider:
    """A provider whose reply was billed and cannot be used.

    Raised from inside ``complete_structured``, which is where the real one raises it: the
    call completed, the usage is real, and only the schema is unhappy.
    """
    usage = Usage(input_tokens=2_500, output_tokens=1_800, model="claude-opus-5")

    def answer(schema: type) -> Any:
        raise SpentButUnusableError(
            "claude-opus-5's reply could not be read as SectionDraft.",
            usage=usage,
            request_payload={"model": "claude-opus-5", "messages": [{"role": "user"}]},
            response_payload={"stop_reason": "end_turn"},
            latency_ms=1_200.0,
            context={"schema": schema.__name__},
        )

    return FakeProvider(answer)


def _good_draft(scene: dict[str, Any]) -> SectionDraft:
    return SectionDraft(
        content={
            "commentary": "Operating cash generation covered the capital programme.",
            "figures": [
                {
                    "label": "Revenue CAGR",
                    "value": "0.18",
                    "unit": "ratio",
                    "calculation_id": str(scene["calculation"].id),
                    "source_document_id": str(scene["document"].id),
                }
            ],
        },
        claims=[
            {
                "statement": "The recorded revenue CAGR is 0.18 (ratio).",
                "kind": "numeric",
                "calculation_id": str(scene["calculation"].id),
                "citations": [
                    {
                        "source_document_id": str(scene["document"].id),
                        "extraction_id": str(scene["extraction"].id),
                    }
                ],
            }
        ],
    )


def _undeclared_field_draft() -> ScriptedResponse:
    """A reply carrying a field the section's contract does not declare.

    **Marked unchecked, because the API could not have sent this.** The narrowed schema
    goes to the wire with `additionalProperties: false`, so the model is structurally
    unable to add `surprise` — and since gap A18 the fake enforces that too. What is under
    test here is not whether such a reply can arrive but whether the executor *notices* if
    one ever does, which is defence in depth and is worth keeping: the schema mode is the
    outer wall, and this is the check behind it.
    """
    return ScriptedResponse(
        SectionDraft(
            content={"commentary": "Prose.", "figures": [], "surprise": "not in the contract"},
            claims=[],
        ),
        unchecked=True,
    )


async def _run(scene: dict[str, Any], provider: FakeProvider, *, focus: str = "") -> Any:
    return await execute_builtin_section(
        _context(scene, provider),
        section=scene["section"],
        request=scene["request"],
        focus=focus,
    )


class TestTheRegistryRow:
    def test_the_writer_holds_no_tools(self) -> None:
        """The whole of ADR 0042, as one assertion."""
        definition = resolve_role("report_writer")
        assert definition.allowed_tools == frozenset()
        assert definition.adr == "0042"
        assert definition.output_schema() is SectionDraft


class TestAValidDraft:
    async def test_it_is_recorded_with_a_citation_the_verifier_confirms(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _run(scene, _scripted([_good_draft(scene)]))

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.attempts == 1
        assert outcome.claims_recorded == 1
        assert outcome.insufficient_evidence is False
        assert scene["section"].low_confidence_reason is None

        claim = await scene["session"].scalar(
            select(Claim).where(Claim.report_section_id == scene["section"].id)
        )
        assert claim is not None
        assert claim.calculation_id == scene["calculation"].id

        verdicts = await verify_job_citations(
            scene["session"],
            scene["store"],
            job_id=scene["job"].id,
            settings=scene["settings"],
        )
        assert verdicts, "the claim's citation must be checked"
        assert all(citation.excerpt_verified for citation, _ in verdicts)

    async def test_the_focus_reaches_the_prompt_and_no_user_skill_block_exists(
        self, scene: dict[str, Any]
    ) -> None:
        provider = _scripted([_good_draft(scene)])
        await _run(scene, provider, focus="Concentrate on the cash conversion cycle.")

        [call] = provider.calls
        composed = call["system"] + "".join(m["content"] for m in call["messages"])
        assert "Concentrate on the cash conversion cycle." in composed
        assert "<user_skill>" not in composed


class TestTheFailureLadder:
    async def test_a_contract_violation_is_retried_once_then_succeeds(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _run(scene, _scripted([_undeclared_field_draft(), _good_draft(scene)]))
        assert outcome.status is SectionStatus.GENERATED
        assert outcome.attempts == 2

    async def test_two_bad_drafts_fail_the_section_with_the_reasons_recorded(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _run(
            scene, _scripted([_undeclared_field_draft(), _undeclared_field_draft()])
        )
        assert outcome.status is SectionStatus.FAILED
        assert scene["section"].status is SectionStatus.FAILED
        assert "surprise" in str(scene["section"].low_confidence_reason)

    async def test_a_claim_naming_an_unknown_calculation_is_refused(
        self, scene: dict[str, Any]
    ) -> None:
        rogue = _good_draft(scene)
        rogue.claims[0].calculation_id = str(uuid.uuid4())
        outcome = await _run(scene, _scripted([rogue, rogue]))
        assert outcome.status is SectionStatus.FAILED
        assert "does not hold" in str(scene["section"].low_confidence_reason)

    async def test_a_content_id_outside_the_evidence_is_refused(
        self, scene: dict[str, Any]
    ) -> None:
        """The closed world covers content, not just claims — a figure row naming a
        fabricated calculation would otherwise satisfy the numeral rule on trust."""
        fabricated = SectionDraft(
            content={
                "commentary": "Prose without figures.",
                "figures": [
                    {
                        "label": "Invented",
                        "value": "0.99",
                        "unit": "ratio",
                        "calculation_id": str(uuid.uuid4()),
                    }
                ],
            },
            claims=[],
        )
        outcome = await _run(scene, _scripted([fabricated, fabricated]))
        assert outcome.status is SectionStatus.FAILED
        assert "evidence does not hold" in str(scene["section"].low_confidence_reason)

    async def test_a_bare_numeral_with_no_lineage_is_refused(self, scene: dict[str, Any]) -> None:
        # A single-sentence field: removing the sentence would empty it, so the ADR 0057
        # salvage declines and the refusal stands exactly as it always did.
        bare = SectionDraft(
            content={"commentary": "Margins improved by 42% on the year.", "figures": []},
            claims=[],
        )
        outcome = await _run(scene, _scripted([bare, bare]))
        assert outcome.status is SectionStatus.FAILED
        assert "42" in str(scene["section"].low_confidence_reason)

    async def test_a_draft_failing_only_the_numeral_rule_is_salvaged_not_discarded(
        self, scene: dict[str, Any]
    ) -> None:
        """ADR 0057. Both sections the live report lost died over one flagged token each
        — a whole billed draft discarded for a single clause. The offending sentence now
        goes instead, the narrowed draft is revalidated in full, and the section stands
        with the removal on the record."""
        stray = _good_draft(scene)
        stray.content["commentary"] = (
            "Operating cash generation covered the capital programme. "
            "Margins expanded 340 basis points."
        )
        outcome = await _run(scene, _scripted([stray, stray]))

        assert outcome.status is SectionStatus.GENERATED
        assert (
            scene["section"].content["commentary"]
            == "Operating cash generation covered the capital programme."
        )
        assert "340" not in str(scene["section"].content)
        reason = str(scene["section"].low_confidence_reason)
        assert "removed" in reason
        assert scene["section"].confidence is not None


class TestTheEnforcedBudgets:
    """Gaps R4 and O4: advisory rules drift, so the budgets are refusals in code."""

    async def test_a_draft_that_dwells_on_its_gaps_is_refused_with_the_instruction(
        self, scene: dict[str, Any]
    ) -> None:
        """A third of the live report's prose described missing evidence. Rule 6 said
        "one clause and move on"; this is the enforcement — a second gap sentence costs
        the draft a retry, with the fix stated."""
        dwelling = _good_draft(scene)
        dwelling.content["commentary"] = (
            "Segment figures are not disclosed in the filings. "
            "Regional detail is not available either. "
            "Operating cash generation covered the capital programme."
        )
        sound = _good_draft(scene)
        provider = _scripted([dwelling, sound])

        outcome = await _run(scene, provider)

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.attempts == 2
        retry = provider.calls[1]
        composed = "".join(m["content"] for m in retry["messages"])
        assert "sentences describe missing evidence" in composed
        assert "one clause" in composed

    async def test_one_gap_sentence_is_allowed(self, scene: dict[str, Any]) -> None:
        stated = _good_draft(scene)
        stated.content["commentary"] = (
            "Segment figures are not disclosed in the filings. "
            "Operating cash generation covered the capital programme."
        )
        outcome = await _run(scene, _scripted([stated]))

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.attempts == 1

    async def test_a_draft_past_the_word_ceiling_is_refused_naming_the_budget(
        self, scene: dict[str, Any]
    ) -> None:
        definition = scene["section"].definition
        definition.evidence_policy = {**(definition.evidence_policy or {}), "word_budget": 20}
        # Pinned to standard depth so the budget the refusal names is the one stated —
        # the scene's default FULL mode scales it, which is O5 working as intended.
        scene["request"].analysis_mode = AnalysisMode.STANDARD
        await scene["session"].flush()

        sprawling = _good_draft(scene)
        sprawling.content["commentary"] = " ".join(["word"] * 60)
        tight = _good_draft(scene)
        provider = _scripted([sprawling, tight])

        outcome = await _run(scene, provider)

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.attempts == 2
        retry = "".join(m["content"] for m in provider.calls[1]["messages"])
        assert "budget of 20" in retry

    async def test_a_quick_run_resolves_the_core_spine_only(self, scene: dict[str, Any]) -> None:
        """Gap O5: depth decides the section set through each row's own applicability
        (migration 0035), so no code names a section key and the control controls."""
        scene["request"].analysis_mode = AnalysisMode.QUICK
        quick = {d.key for d in await resolve_sections(scene["session"], request=scene["request"])}
        scene["request"].analysis_mode = AnalysisMode.FULL
        full = {d.key for d in await resolve_sections(scene["session"], request=scene["request"])}

        assert quick < full
        assert "executive_summary" in quick
        assert "valuation_dcf" in quick
        assert "segment_analysis" in full - quick
        assert "industry_landscape" in full - quick

    def test_depth_scales_the_budgets_and_never_the_floor(self) -> None:
        from aer.sections.evidence import SectionPolicy  # noqa: PLC0415

        base = SectionPolicy(
            min_sources=2,
            requires_primary=True,
            max_tier_rank=4,
            allow_forward_looking=False,
            token_budget=1000,
            word_budget=400,
        )
        quick = base.scaled(AnalysisMode.QUICK)
        full = base.scaled(AnalysisMode.FULL)

        assert (quick.token_budget, quick.word_budget) == (600, 240)
        assert (full.token_budget, full.word_budget) == (1400, 560)
        assert base.scaled(AnalysisMode.STANDARD) is base
        # Depth is how much work a run does, never how little support a claim stands on.
        assert quick.min_sources == full.min_sources == 2
        assert quick.max_tier_rank == full.max_tier_rank == 4

    async def test_a_draft_at_its_budget_is_not_refused(self, scene: dict[str, Any]) -> None:
        """The ceiling leaves headroom over the stated budget, as the claim ceilings do:
        a draft two words over the target costs nothing."""
        definition = scene["section"].definition
        definition.evidence_policy = {**(definition.evidence_policy or {}), "word_budget": 200}
        await scene["session"].flush()

        outcome = await _run(scene, _scripted([_good_draft(scene)]))

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.attempts == 1


class TestTheDegradationLadder:
    def test_truncation_never_reaches_the_reader_s_banner(self) -> None:
        """Gap R2. "Truncated to its token budget" printed under all sixteen sections of
        a live report, because every section's evidence now exceeds its budget — a banner
        that always shows says nothing, in the platform's voice, in the reader's
        document. The fact stays on the execution outcome and the log for the operator;
        the banner is for genuine evidence shortfalls only."""
        assert degradation_note([]) is None
        assert degradation_note(["Only 1 distinct source(s) were cited."]) == (
            "Insufficient evidence: Only 1 distinct source(s) were cited."
        )
        note = degradation_note(["a shortfall"])
        assert note is not None
        assert "token budget" not in note
        assert "truncated" not in note

    async def test_thin_evidence_generates_under_a_banner_never_fabricates(
        self, scene: dict[str, Any]
    ) -> None:
        thin = SectionDraft(
            content={"commentary": "The evidence cannot support an analysis.", "figures": []},
            claims=[],
        )
        outcome = await _run(scene, _scripted([thin]))

        assert outcome.status is SectionStatus.GENERATED
        assert outcome.insufficient_evidence is True
        reason = str(scene["section"].low_confidence_reason)
        assert "Insufficient evidence" in reason
        # Both arms of the floor, named separately: citing nothing falls short of the
        # source minimum and of the primary requirement, and a banner that reported only
        # one would be a check quietly switched off.
        assert "distinct source(s)" in reason
        assert "primary source" in reason
        assert scene["section"].confidence is not None
        assert scene["section"].confidence <= 0.3

    async def test_the_policy_comes_from_the_definition_row(self, scene: dict[str, Any]) -> None:
        policy = policy_of_definition(scene["section"].definition)
        assert policy.token_budget == scene["section"].definition.token_budget
        assert policy.min_sources == 1
        assert policy.requires_primary is True
        # The seeded tier name resolves to its rank; an unknown name would fall to 5.
        assert policy.max_tier_rank == 4

    async def test_a_declared_fact_basis_reaches_the_policy(self, scene: dict[str, Any]) -> None:
        definition = scene["section"].definition
        definition.evidence_policy = {**(definition.evidence_policy or {}), "fact_basis": "annual"}

        assert policy_of_definition(definition).fact_basis == "annual"

    async def test_a_mistyped_basis_costs_the_preference_not_the_section(
        self, scene: dict[str, Any]
    ) -> None:
        # The same fallback rule max_tier applies: a definition row with a typo in a
        # preference should lose that preference, not refuse to build a policy.
        definition = scene["section"].definition
        definition.evidence_policy = {**(definition.evidence_policy or {}), "fact_basis": "anual"}

        assert policy_of_definition(definition).fact_basis == "any"


class TestSpentPerSection:
    async def test_the_call_is_metered_against_the_draft_step(self, scene: dict[str, Any]) -> None:
        provider = _scripted([_good_draft(scene)])
        context = _context(scene, provider)
        await execute_builtin_section(
            context, section=scene["section"], request=scene["request"], focus=""
        )

        assert context.spend_gbp > 0
        run = await scene["session"].scalar(
            select(AgentRun).where(AgentRun.job_step_id == scene["step"].id)
        )
        assert run is not None
        assert run.agent_role == "report_writer"
        rows = list(await scene["session"].scalars(select(Cost).where(Cost.agent_run_id == run.id)))
        # One row per price line — input tokens and output tokens are separate ledger
        # entries — all against the draft step.
        assert rows
        assert all(row.job_step_id == scene["step"].id for row in rows)

    async def test_a_reply_the_schema_refuses_is_still_metered(self, scene: dict[str, Any]) -> None:
        """**A call that failed is not a call that was free.**

        The reply was generated and billed; only its usability is in question. The budget
        cap reads the `costs` table, so spend the table cannot see is spend the cap cannot
        cap — and a worker that re-asks after a refusal was, until this, spending money the
        ledger never recorded. `stop_reason` says which kind of failure it was, so these
        rows are findable rather than sitting under whatever the API happened to report.
        """
        provider = _refusing(scene)
        context = _context(scene, provider)

        await execute_builtin_section(
            context, section=scene["section"], request=scene["request"], focus=""
        )

        run = await scene["session"].scalar(
            select(AgentRun).where(AgentRun.job_step_id == scene["step"].id)
        )
        assert run is not None
        assert run.stop_reason == "schema_rejected"
        rows = list(await scene["session"].scalars(select(Cost).where(Cost.agent_run_id == run.id)))
        assert rows
        assert context.spend_gbp > 0

    async def test_the_failed_exchange_is_archived_like_any_other(
        self, scene: dict[str, Any]
    ) -> None:
        """ "Why did it say that?" is asked far more often about the replies that failed."""
        context = _context(scene, _refusing(scene))

        await execute_builtin_section(
            context, section=scene["section"], request=scene["request"], focus=""
        )

        run = await scene["session"].scalar(
            select(AgentRun).where(AgentRun.job_step_id == scene["step"].id)
        )
        assert run is not None
        assert run.request_payload_ref is not None
        assert run.response_payload_ref is not None


class TestTheClaimLengthsAreAskedForNotJustEnforced:
    """The planner's lesson (`TestThePlannerAsksForWhatItValidates`), applied where it was not.

    `max_length` reaches the model as *description text* — the SDK moves it there, because
    the API's schema mode rejects the constraint itself — so it is guidance, not a rule the
    server applies. A reply that overruns it is therefore structurally perfect, paid for,
    and thrown away.

    That is exactly what a live run did: `historical_financial_analysis` came back with
    twenty-two claims over the 600-character `statement` bound, `balance_sheet_liquidity`
    with twenty, `management_governance` with nine. All three sections were lost, at two
    attempts each, from a prompt that never mentioned a length. The planner had already
    been fixed this way; the section writer inherited its bounds without the lesson.
    """

    _PAIRS = (
        ("statement", CLAIM_STATEMENT_BUDGET, CLAIM_STATEMENT_CEILING),
        ("basis", CLAIM_BASIS_BUDGET, CLAIM_BASIS_CEILING),
    )

    @pytest.mark.parametrize(("field", "budget", "ceiling"), _PAIRS)
    def test_the_ceiling_leaves_real_headroom_over_the_budget(
        self, field: str, budget: int, ceiling: int
    ) -> None:
        """ "It went half again over" must not be a lost section."""
        assert ceiling >= budget * 2, (
            f"{field} allows {ceiling} and asks for {budget}; a model that overruns its "
            "budget by half would lose the section after the call was paid for"
        )

    @pytest.mark.parametrize(("field", "budget", "ceiling"), _PAIRS)
    def test_the_prompt_states_the_budget(self, field: str, budget: int, ceiling: int) -> None:
        # The only channel the model reliably reads a limit on. Asserted against the
        # rendered prompt rather than the template, so an unformatted placeholder fails.
        rendered = SectionWriterAgent().system_prompt(
            SectionWriterInput(
                section_key="business_overview",
                title="Business overview",
                company_name="Microsoft Corporation",
                ticker="MSFT",
                as_of_date="2024-06-30",
                point_in_time=True,
                output_contract={"properties": {"commentary": {"type": "string"}}},
            )
        )

        assert str(budget) in rendered
        assert "{" not in rendered.split("output contract")[0], "a placeholder went unformatted"

    def test_a_claim_at_the_stated_budget_validates(self) -> None:
        # The contract as one object: write what was asked for, and it is accepted.
        claim = ProposedClaim(
            statement="s" * CLAIM_STATEMENT_BUDGET,
            kind="opinion",
            basis="b" * CLAIM_BASIS_BUDGET,
        )

        assert len(claim.statement) == CLAIM_STATEMENT_BUDGET

    def test_the_ceiling_still_refuses_a_runaway(self) -> None:
        # The ceiling is a sanity bound, not a style rule — it must still stop a blob.
        with pytest.raises(PydanticValidationError):
            ProposedClaim(statement="s" * (CLAIM_STATEMENT_CEILING + 1), kind="factual")

    def test_a_rejected_reply_is_said_back_field_by_field(self) -> None:
        """What the retry gets, and why both attempts used to fail the same way.

        The message says "22 field(s) broke a constraint", which is true, unactionable, and
        was handed to the retry verbatim. `schema_problems` reads the field-level detail the
        exception carried all along.
        """
        rejected = ValidationError(
            "claude-opus-5's reply could not be read as X: 2 field(s) broke a constraint",
            context={
                "errors": [
                    {
                        "loc": "claims.3.statement",
                        "type": "string_too_long",
                        "msg": "String should have at most 1500 characters",
                    },
                    {"loc": "claims.7.basis", "type": "string_too_long", "msg": "too long"},
                ]
            },
        )

        problems = schema_problems(rejected)

        assert problems == [
            "claims.3.statement: String should have at most 1500 characters",
            "claims.7.basis: too long",
        ]

    def test_every_retry_loop_says_it_back_field_by_field(self) -> None:
        """The function existing is not the property; being *called* is.

        A mutation that reverted either loop to stringifying the exception passed every
        other test in this class — the helper was still correct, still tested, and no
        longer reached. Both drafting loops are scanned, because the defect this closes was
        one of them doing it right (the research worker) while the others did not.
        """
        root = Path(__file__).resolve().parent.parent / "src" / "aer"
        loops = [
            root / "sections" / "writing.py",
            root / "skills" / "execution.py",
            root / "agents" / "worker.py",
        ]

        for path in loops:
            body = path.read_text(encoding="utf-8")
            assert "schema_problems(" in body, (
                f"{path.name} handles a rejected reply without saying the fields back; "
                "a retry given only the count repeats the mistake"
            )
            assert "response schema: {unparsable}" not in body, (
                f"{path.name} feeds the exception's own message to the retry, which names "
                "a count rather than the fields"
            )


class TestTheWriterSpeaksToTheReader:
    """Gap A40: nine live sections narrated evidence budgets, retrieval plans and
    re-run instructions — the platform's internals, addressed to its operator. The rule
    is in both drafting prompts; asserted on the rendered prompt so a template edit
    that drops it fails by name."""

    def test_the_rule_reaches_both_drafting_prompts(self) -> None:
        builtin = SectionWriterAgent().system_prompt(
            SectionWriterInput(
                section_key="business_overview",
                title="Business overview",
                company_name="Microsoft Corporation",
                ticker="MSFT",
                as_of_date="2024-06-30",
                point_in_time=True,
                output_contract={"properties": {"commentary": {"type": "string"}}},
            )
        )
        custom = CustomSectionAgent().system_prompt(
            CustomSectionInput(
                section_key="my_section",
                title="My section",
                company_name="Microsoft Corporation",
                ticker="MSFT",
                as_of_date="2024-06-30",
                output_contract={"properties": {"commentary": {"type": "string"}}},
                skill_body="Analyse the moat.",
            )
        )

        for prompt in (builtin, custom):
            assert "never for the platform's operator" in prompt
            assert "say so in one clause" in prompt
            # Gap R3's half of the rule: the machinery has names, and none of them may
            # reach the reader.
            assert "Never name the plan, the run, the model" in prompt
            assert "follow it without referring to it" in prompt

    def test_the_focus_arrives_as_direction_not_as_a_plan_to_quote(self) -> None:
        """Gap R3. "The approved plan's focus" taught the writer to write "on the point
        the plan asks us to flag" — the model did what the interpolation told it. The
        focus is now handed over as unattributed direction."""
        message = SectionWriterAgent().user_message(
            SectionWriterInput(
                section_key="business_overview",
                title="Business overview",
                company_name="Microsoft Corporation",
                ticker="MSFT",
                as_of_date="2024-06-30",
                point_in_time=True,
                output_contract={"properties": {"commentary": {"type": "string"}}},
                focus="Weigh the cloud segment's growth against its capital intensity.",
            )
        )

        assert "plan" not in message.lower()
        assert "Direction for this section" in message
        assert "never to be quoted" in message
        assert "Weigh the cloud segment's growth" in message
