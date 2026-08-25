"""The per-run validators: §2.10's judgement applied to a live run, written as rows.

Task 39. Four validators, eight rows, one rule about authority:

* **citation** — the deterministic verifier is authoritative. It runs first, its verdicts
  become the accuracy and hallucination rows, and the LLM only *locates* candidate
  excerpts for claims it could not resolve — recorded as advice beside the verdict, never
  as a change to it.
* **temporal** — deterministic date checks over the run's sources, reusing the CI gate's
  own observation shape and metric functions, because the fixture semantics (undated and
  post-dated sources are inadmissible) are the platform's own quarantine rules. The LLM
  adjudicates ambiguous dates, advisory.
* **numerical** — the task 32 replay harness over the run's stored calculations.
* **coverage** — per-section evidence floors, with custom sections held to their pinned
  composed policy, and the primary-source ratio over the run's numeric claims.

**A metric with nothing to measure is recorded as not exercised** — a NULL value and a
NULL verdict — because "every completed run carries all eight rows" must be a checkable
property, and a fabricated pass would be the one kind of row worse than a missing one.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.validator import DOCUMENT_WINDOW_CHARS, AssistInput, ValidatorAssist
from aer.calc.plausibility import FigureScene
from aer.core.enums import ClaimKind
from aer.db.models import (
    Artefact,
    Calculation,
    Citation,
    Claim,
    Evaluation,
    Extraction,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
)
from aer.errors import AerError
from aer.eval.metrics import (
    RUN_TIME,
    THRESHOLDS,
    EmptyCorpusError,
    Metric,
    MetricResult,
    assumption_completeness,
    look_ahead_recall,
    numerical_consistency,
    temporal_compliance,
)
from aer.eval.observations import CitedFigureObservation, SourceObservation
from aer.eval.replay import completeness_observations_for_job, replay_observations_for_job
from aer.eval.runtime import (
    RunCitation,
    SectionCoverage,
    SourcedClaim,
    cited_figure_agreement,
    figure_plausibility,
    presentation_integrity,
    primary_source_ratio,
    run_citation_accuracy,
    run_hallucinated_citation_rate,
    source_coverage,
)
from aer.extract import extract_text
from aer.render.document import assemble_document
from aer.render.html import render_html
from aer.render.markdown import serialise_markdown
from aer.sections.registry import sections_for_job
from aer.services.facts import visible_facts
from aer.services.scope import scope_for_request
from aer.verify.citations import verify_job_citations

__all__ = [
    "MAX_ASSISTS",
    "NUMERIC_CEILING",
    "evaluate_run",
    "evaluations_for_job",
    "section_coverage_for_job",
]

_log = structlog.get_logger("aer.services.evaluations")

# Advisory calls per validator per run. A cap in code, not a hope: assists are the only
# model spend in the validate step, and an uncapped helper on a run with forty failed
# citations would be a cost nobody estimated.
MAX_ASSISTS: Final = 4

# How much of a claim an advisory question quotes.
_QUESTION_CHARS: Final = 600

# Tiers 1 and 2 are primary for coverage purposes — the same bar `SourceTier.is_primary`
# applies, restated here as a rank so the check works over bare integers.
_PRIMARY_RANK: Final = 2

# The largest value NUMERIC(20, 8) can hold — what an infinite replay delta is stored
# as, with the true value kept in the details. Public because the renderer needs to
# recognise a clamped score: twelve nines in a validation table read as a crashed
# validator, not as "unbounded" (polish P9).
NUMERIC_CEILING: Final = Decimal("999999999999.99999999")


@dataclass(slots=True)
class _RunRows:
    """Everything the validators read, loaded once."""

    citations: list[tuple[Citation, Claim]] = field(default_factory=list)
    sources: list[SourceDocument] = field(default_factory=list)
    sections: list[ReportSection] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    source_tiers: dict[uuid.UUID, int] = field(default_factory=dict)
    fact_sources: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    citation_sources: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)


async def evaluate_run(
    context: AgentContext,
    *,
    job: Job,
    request: ResearchRequest,
    use_batch: bool = True,
) -> list[Evaluation]:
    """Verify, measure, advise, and write the run's evaluation rows — the §2.10
    run-time set plus the presentation gate (gap O3).

    Replaces any rows a previous validation of this run wrote: an evaluation is derived
    data, and "the run's citation score" must be one answer, not a history.

    Args:
        use_batch: Route multiple advisory questions through the provider's batch path.
            The sync path asks them one at a time; both produce identical rows, which is
            tested rather than assumed.
    """
    # The deterministic verifier first, and authoritatively: every verdict the citation
    # rows are built from is written before any model is consulted.
    await verify_job_citations(
        context.session, context.store, job_id=job.id, settings=context.settings
    )

    rows = await _load(context.session, job=job, request=request)

    results: dict[Metric, MetricResult | None] = {}
    citation_rows = _citation_rows(rows, request=request)
    results[Metric.CITATION_ACCURACY] = _measure(lambda: run_citation_accuracy(citation_rows))
    results[Metric.HALLUCINATED_CITATION_RATE] = _measure(
        lambda: run_hallucinated_citation_rate(citation_rows)
    )

    source_rows = _source_rows(rows, request=request)
    results[Metric.TEMPORAL_COMPLIANCE] = _measure(lambda: temporal_compliance(source_rows))
    results[Metric.LOOK_AHEAD_RECALL] = _measure(lambda: look_ahead_recall(source_rows))

    coverage_rows = _coverage_rows(rows)
    results[Metric.SOURCE_COVERAGE] = _measure(lambda: source_coverage(coverage_rows))
    sourcing_rows = _sourcing_rows(rows)
    results[Metric.PRIMARY_SOURCE_RATIO] = _measure(lambda: primary_source_ratio(sourcing_rows))

    replays = await replay_observations_for_job(context.session, job.id)
    results[Metric.NUMERICAL_CONSISTENCY] = _measure(lambda: numerical_consistency(replays))
    completeness = await completeness_observations_for_job(context.session, job.id)
    results[Metric.ASSUMPTION_COMPLETENESS] = _measure(
        lambda: assumption_completeness(completeness)
    )

    # The presentation gate (gap O3): the draft rendered exactly as the preview renders
    # it, scanned for the defect classes a live note once shipped. Assembly is
    # deterministic and spends nothing; a failure here lands on the coverage notice and
    # the gate-2 banner like any other failed check.
    document = await assemble_document(context.session, job=job, request=request)
    results[Metric.PRESENTATION_INTEGRITY] = _measure(
        lambda: presentation_integrity(
            serialise_markdown(document),
            render_html(document),
            sections=len(document.sections),
        )
    )

    # Gap A61: are the headline figures possible? Deterministic, over the run's own
    # recorded facts and calculations; a failure names the impossible relation with its
    # values. This is the check the MTB run proved missing — every metric above passed
    # while the front page carried a 172.1% net margin.
    scenes = await _figure_scenes(context.session, job=job, request=request)
    results[Metric.FIGURE_PLAUSIBILITY] = _measure(lambda: figure_plausibility(scenes))

    # Invariant 3's missing half (gap R19): a claim that names a calculation must quote
    # that calculation's figure. Every check above asks whether a figure is *recorded*
    # correctly and none reads the sentence, so the MSFT note could draft a quick ratio of
    # 0.93 over a recorded 1.567 with the whole gate green.
    cited = await _cited_figures(context.session, job=job)
    results[Metric.CITED_FIGURE_AGREEMENT] = _measure(lambda: cited_figure_agreement(cited))

    advisories = await _advise(context, rows, use_batch=use_batch, request=request)

    written = await _write(context.session, job_id=job.id, results=results, advisories=advisories)
    _log.info(
        "evaluations.written",
        job_id=str(job.id),
        rows=len(written),
        failed=[row.metric for row in written if row.passed is False],
        not_exercised=[row.metric for row in written if row.passed is None],
        advisories=sum(len(items) for items in advisories.values()),
    )
    # A failed check names what it found, in the log as well as the report (gap A60).
    # The live run's operator was told `presentation_integrity` failed and had to open
    # the approval page to learn which check — the findings were on the row all along.
    for row in written:
        if row.passed is False:
            found = [str(item) for item in (row.details or {}).get("failures", [])]
            _log.warning(
                "evaluations.check_failed",
                job_id=str(job.id),
                metric=row.metric,
                findings=[_shorten(item, limit=120) for item in found[:5]],
                total_findings=len(found),
            )
    return written


async def evaluations_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[Evaluation]:
    """A run's rows in the §2.10 run-time order — the order the dashboard renders."""
    found = {
        row.metric: row
        for row in await session.scalars(select(Evaluation).where(Evaluation.job_id == job_id))
    }
    return [found[metric.value] for metric in RUN_TIME if metric.value in found]


async def section_coverage_for_job(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> list[SectionCoverage]:
    """Each of a run's sections held against its own evidence floor, freshly computed.

    The same rows the coverage metric is measured over, exposed because the escalation
    engine and the gate-2 coverage matrix both need the per-section verdicts, not just
    the ratio — and a second derivation of "what stands behind this section" would be a
    place for the two to disagree.
    """
    rows = await _load(session, job=job, request=request)
    return _coverage_rows(rows)


# ==========================================================================================
# Loading
# ==========================================================================================


async def _load(session: AsyncSession, *, job: Job, request: ResearchRequest) -> _RunRows:
    rows = _RunRows()

    rows.sections = await sections_for_job(session, job.id)
    section_ids = [section.id for section in rows.sections]

    if section_ids:
        rows.claims = list(
            await session.scalars(
                select(Claim)
                .where(Claim.report_section_id.in_(section_ids))
                .order_by(Claim.created_at, Claim.id)
            )
        )
    claim_ids = [claim.id for claim in rows.claims]
    claims_by_id = {claim.id: claim for claim in rows.claims}

    if claim_ids:
        citations = await session.scalars(
            select(Citation).where(Citation.claim_id.in_(claim_ids)).order_by(Citation.created_at)
        )
        rows.citations = [(c, claims_by_id[c.claim_id]) for c in citations]

    rows.sources = list(
        await session.scalars(
            select(SourceDocument)
            .where(SourceDocument.work_order_id == request.id)
            .order_by(SourceDocument.retrieved_at)
        )
    )
    rows.source_tiers = {row.id: row.source_tier.rank for row in rows.sources}

    cited_fact_ids = {c.financial_fact_id for c in rows.claims if c.financial_fact_id is not None}
    if cited_fact_ids:
        facts = await session.scalars(
            select(FinancialFact).where(FinancialFact.id.in_(cited_fact_ids))
        )
        rows.fact_sources = {fact.id: fact.source_document_id for fact in facts}

    for citation, _ in rows.citations:
        rows.citation_sources.setdefault(citation.claim_id, set()).add(citation.source_document_id)
    return rows


# ==========================================================================================
# The deterministic measurements
# ==========================================================================================


def _citation_rows(rows: _RunRows, *, request: ResearchRequest) -> list[RunCitation]:
    built: list[RunCitation] = []
    for citation, claim in rows.citations:
        # The comparison's own outcome, separate from the overall verdict. Decided by
        # re-asking the platform's own admissibility question of the source row — not by
        # parsing the error text — so a citation the verifier refused before comparing
        # (quarantine, look-ahead) is not mistaken for a fabrication.
        if citation.excerpt_verified:
            excerpt_found: bool | None = True
        elif _source_comparable(rows, citation.source_document_id, request=request):
            excerpt_found = False
        else:
            excerpt_found = None
        built.append(
            RunCitation(
                name=f"claim {_shorten(claim.text)!r}",
                verified=citation.excerpt_verified,
                excerpt_found=excerpt_found,
                ratio=str(citation.match_ratio) if citation.match_ratio is not None else None,
                error=citation.verification_error,
            )
        )
    return built


def _source_comparable(rows: _RunRows, source_id: uuid.UUID, *, request: ResearchRequest) -> bool:
    """Whether the verifier would have reached the excerpt comparison for this source.

    The same two refusals `aer.verify.citations` applies before re-reading a document:
    an inadmissible source, and — in point-in-time mode — one whose latest supportable
    date postdates the request. A citation refused on either is the temporal family's
    failure, and the hallucination row must not claim an excerpt nobody checked.
    """
    source = next((row for row in rows.sources if row.id == source_id), None)
    if source is None or not source.is_admissible:
        return False
    if not request.point_in_time:
        return True
    latest = source.publication_date_latest or source.publication_date
    return latest is None or latest <= request.as_of_date


def _source_rows(rows: _RunRows, *, request: ResearchRequest) -> list[SourceObservation]:
    # The run's own mode travels with each observation: the hallucination metric already
    # respects request.point_in_time, and the temporal metric judging the same run by a
    # stricter rule than it ran under is how a point-in-time-off report came to wear a
    # temporal-compliance failure on its front page.
    return [
        SourceObservation(
            name=row.title or row.url,
            published=row.publication_date_latest or row.publication_date,
            as_of=request.as_of_date,
            admitted=row.is_admissible,
            established=row.publication_date,
            point_in_time=request.point_in_time,
        )
        for row in rows.sources
    ]


def _coverage_rows(rows: _RunRows) -> list[SectionCoverage]:
    """One coverage row per generated section, held to its own floor.

    A section's evidence is everything actually standing behind it: its claims'
    citations, the source documents of the facts those claims name, and the source
    references its structured content carries — the same references the renderer turns
    into footnotes.
    """
    claims_by_section: dict[uuid.UUID, list[Claim]] = {}
    for claim in rows.claims:
        claims_by_section.setdefault(claim.report_section_id, []).append(claim)

    built: list[SectionCoverage] = []
    for section in rows.sections:
        sources: set[uuid.UUID] = set()
        for claim in claims_by_section.get(section.id, []):
            sources |= rows.citation_sources.get(claim.id, set())
            if claim.financial_fact_id in rows.fact_sources:
                sources.add(rows.fact_sources[claim.financial_fact_id])
        sources |= _content_source_ids(section.content)

        policy = (section.definition.evidence_policy or {}) if section.definition else {}
        record_only = _entirely_platform_filled(
            section.content,
            section.definition.output_contract if section.definition else None,
        )
        built.append(
            SectionCoverage(
                name=section.section_key,
                generated=section.status is SectionStatus.GENERATED,
                distinct_sources=len(sources),
                has_primary=any(
                    rank is not None and rank <= _PRIMARY_RANK
                    for rank in (rows.source_tiers.get(s) for s in sources)
                ),
                min_sources=0 if record_only else int(policy.get("min_sources", 1)),
                requires_primary=(
                    False if record_only else bool(policy.get("requires_primary", True))
                ),
            )
        )
    return built


def _entirely_platform_filled(
    content: dict[str, Any] | None, contract: dict[str, Any] | None
) -> bool:
    """Whether a section's stored content is wholly the platform's rendered record.

    A section the augmenter filled standalone — the valuation with no valuation to
    interpret (gap A51c) — carries only fields its contract marks ``platform_filled``:
    figures rendered from recorded rows, citing calculations rather than source
    documents. Holding that to a primary-source floor would fire a coverage trigger on
    every honest no-valuation run — a warning nobody can act on, which is the warning
    nobody reads on the day one matters — so it is assessed the way the zero-budget
    sections' own declared policies already assess them.
    """
    properties = (contract or {}).get("properties")
    if not isinstance(properties, dict) or not content:
        return False
    declared = [key for key in content if key in properties]
    return bool(declared) and all(
        isinstance(properties[key], dict) and properties[key].get("platform_filled")
        for key in declared
    )


def _content_source_ids(content: dict[str, Any] | None) -> set[uuid.UUID]:
    found: set[uuid.UUID] = set()
    _walk_for_sources(content, found)
    return found


def _walk_for_sources(value: Any, found: set[uuid.UUID]) -> None:
    if isinstance(value, dict):
        reference = value.get("source_document_id")
        if isinstance(reference, str):
            with contextlib.suppress(ValueError):
                found.add(uuid.UUID(reference))
        for item in value.values():
            _walk_for_sources(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_for_sources(item, found)


def _sourcing_rows(rows: _RunRows) -> list[SourcedClaim]:
    built: list[SourcedClaim] = []
    for claim in rows.claims:
        if claim.kind is not ClaimKind.NUMERIC:
            continue
        sources = set(rows.citation_sources.get(claim.id, set()))
        if claim.financial_fact_id in rows.fact_sources:
            sources.add(rows.fact_sources[claim.financial_fact_id])
        ranks = [rows.source_tiers[s] for s in sources if s in rows.source_tiers]
        built.append(
            SourcedClaim(
                name=f"claim {_shorten(claim.text)!r}",
                best_tier_rank=min(ranks) if ranks else None,
            )
        )
    return built


_PLAUSIBILITY_CALCULATIONS: Final = ("net_margin", "asset_turnover")

_SCENE_ANNUAL: Final = "FY"


def _scene_period(fact: FinancialFact) -> str:
    if fact.fiscal_period == _SCENE_ANNUAL:
        return f"FY{fact.fiscal_year}"
    return f"{fact.fiscal_period} FY{fact.fiscal_year}"


async def _cited_figures(session: AsyncSession, *, job: Job) -> tuple[CitedFigureObservation, ...]:
    """Every drafted claim that names a calculation, beside the calculation it names.

    Joined on `claims.calculation_id`, which the section writer sets when a sentence rests
    on a recorded figure. That column is what makes this check structural rather than
    textual: the alternative is to hunt "quick ratio of 0.93" in prose and look up a
    `quick_ratio` row, which needs a ratio vocabulary somebody maintains for ever and is
    wrong the first time a writer phrases one differently.
    """
    rows = (
        await session.execute(
            select(Claim, Calculation)
            .join(Calculation, Calculation.id == Claim.calculation_id)
            .join(ReportSection, ReportSection.id == Claim.report_section_id)
            .where(ReportSection.job_id == job.id)
            .order_by(ReportSection.section_key, Claim.created_at, Claim.id)
        )
    ).all()
    return tuple(
        CitedFigureObservation(
            name=f"{claim.section.section_key}/{calculation.name}#{calculation.sequence}",
            text=claim.text,
            calculation=calculation.name,
            value=calculation.output_value,
            unit=calculation.output_unit,
        )
        for claim, calculation in rows
    )


async def _figure_scenes(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> tuple[FigureScene, ...]:
    """One scene per period, from the run's recorded facts and calculations.

    The same rows the front page reads: the subject's visible facts for revenue, net
    income and total assets, and the run's recorded margin and turnover calculations by
    their period labels. Revenue and net income join a scene only when their recorded
    units agree — a comparison across currencies would be a new error, not a check.
    """
    facts = list(
        await session.scalars(
            visible_facts(await scope_for_request(session, request))
            .where(FinancialFact.concept.in_(("revenue", "net_income", "total_assets")))
            .order_by(FinancialFact.period_end.desc(), FinancialFact.concept)
        )
    )
    by_period: dict[str, dict[str, Any]] = {}
    for fact in facts:
        if fact.fiscal_year is None or fact.fiscal_period is None:
            continue
        scene = by_period.setdefault(_scene_period(fact), {})
        scene.setdefault(fact.concept, (Decimal(str(fact.value)), fact.unit))

    calculations = await session.scalars(
        select(Calculation)
        .where(
            Calculation.job_id == job.id,
            Calculation.name.in_(_PLAUSIBILITY_CALCULATIONS),
            Calculation.period_label.is_not(None),
        )
        .order_by(Calculation.sequence)
    )
    ratios: dict[str, dict[str, Decimal]] = {}
    for calc in calculations:
        case = (calc.parameters or {}).get("case")
        if case not in (None, "base"):
            continue
        label = str(calc.period_label)
        ratios.setdefault(label, {}).setdefault(calc.name, Decimal(str(calc.output_value)))

    scenes: list[FigureScene] = []
    for period in sorted(set(by_period) | set(ratios)):
        held = by_period.get(period, {})
        revenue = held.get("revenue")
        income = held.get("net_income")
        comparable = revenue is not None and income is not None and revenue[1] == income[1]
        assets = held.get("total_assets")
        pair = ratios.get(period, {})
        scenes.append(
            FigureScene(
                period=period,
                revenue=revenue[0] if comparable and revenue else None,
                net_income=income[0] if comparable and income else None,
                net_margin=pair.get("net_margin"),
                asset_turnover=pair.get("asset_turnover"),
                total_assets=assets[0] if assets else None,
            )
        )
    return tuple(scenes)


def _measure(compute: Any) -> MetricResult | None:
    """Run one metric, with an empty population becoming ``None`` — not exercised.

    The gate's metrics refuse an empty corpus because for CI that is a broken fixture.
    For a live run it is a fact about the run — no post-dated source to catch, no
    assumption to check — and the honest record is a row that says so.
    """
    try:
        result: MetricResult = compute()
    except EmptyCorpusError:
        return None
    return result


# ==========================================================================================
# The advisory assists
# ==========================================================================================


async def _advise(
    context: AgentContext, rows: _RunRows, *, use_batch: bool, request: ResearchRequest
) -> dict[Metric, list[dict[str, Any]]]:
    """Ask the validator role about what the deterministic checks could not settle.

    Everything returned is advice: it lands in the evaluation rows' details, and no code
    path exists from here to a verdict. An assist that fails is recorded as failed advice
    rather than failing the validation — the deterministic rows are already complete.
    """
    inputs: list[tuple[Metric, AssistInput]] = []
    inputs.extend(
        (Metric.CITATION_ACCURACY, item)
        for item in await _citation_questions(
            context.session, rows, context=context, request=request
        )
    )
    inputs.extend(
        (Metric.TEMPORAL_COMPLIANCE, item)
        for item in await _temporal_questions(context.session, rows, context=context)
    )
    if not inputs:
        return {}

    agent = ValidatorAssist()
    advisories: dict[Metric, list[dict[str, Any]]] = {}
    try:
        if use_batch and len(inputs) > 1:
            answers = await agent.run_batch(context, [payload for _, payload in inputs])
        else:
            answers = [await agent.run(context, payload) for _, payload in inputs]
    except AerError as failed:
        _log.warning("evaluations.assists_failed", error=str(failed))
        return {}

    for (metric, payload), advisory in zip(inputs, answers, strict=True):
        advisories.setdefault(metric, []).append(
            {
                "kind": payload.kind,
                "source_document_id": payload.source_document_id,
                "question": payload.question,
                "found": advisory.found,
                "candidate_excerpt": advisory.candidate_excerpt,
                "proposed_date": advisory.proposed_date,
                "rationale": advisory.rationale,
                "confidence": advisory.confidence,
                "advisory": True,
            }
        )
    return advisories


async def _citation_questions(
    session: AsyncSession, rows: _RunRows, *, context: AgentContext, request: ResearchRequest
) -> list[AssistInput]:
    """One excerpt-location question per unresolved citation, up to the cap.

    Unresolved means the comparison ran and failed — the claim may be supported by text
    *elsewhere* in the document, and finding a candidate is the one thing a model can
    add. Citations refused for admissibility are not questions; their document may not
    be used at all.
    """
    questions: list[AssistInput] = []
    for citation, claim in rows.citations:
        if len(questions) >= MAX_ASSISTS:
            break
        if citation.excerpt_verified or citation.override_reason is not None:
            continue
        if not _source_comparable(rows, citation.source_document_id, request=request):
            # The document may not be used at all; locating a better excerpt in it
            # would be advice about evidence the run is forbidden to cite.
            continue
        window = await _document_window(session, context, extraction_id=citation.extraction_id)
        if window is None:
            continue
        text, source_id, tier = window
        questions.append(
            AssistInput(
                kind="excerpt_location",
                question=(
                    "The claim below cites this document, but the recorded excerpt did "
                    "not verify. Find a passage that supports the claim, if one exists.\n"
                    f"Claim: {claim.text[:_QUESTION_CHARS]}"
                ),
                source_document_id=source_id,
                source_tier=tier,
                document_text=text,
            )
        )
    return questions


async def _temporal_questions(
    session: AsyncSession, rows: _RunRows, *, context: AgentContext
) -> list[AssistInput]:
    """One date-adjudication question per undated source with readable text, capped."""
    questions: list[AssistInput] = []
    for source in rows.sources:
        if len(questions) >= MAX_ASSISTS:
            break
        if (source.publication_date_latest or source.publication_date) is not None:
            continue
        extraction = await session.scalar(
            select(Extraction)
            .where(Extraction.source_document_id == source.id)
            .order_by(Extraction.created_at)
            .limit(1)
        )
        if extraction is None:
            # Nothing has extracted readable text from this source, so there is nothing
            # for an adjudicator to read. Not a gap to paper over with a guess.
            continue
        window = await _document_window(session, context, extraction_id=extraction.id)
        if window is None:
            continue
        text, source_id, tier = window
        questions.append(
            AssistInput(
                kind="date_adjudication",
                question=(
                    "This document has no established publication date, so under "
                    "point-in-time rules it is quarantined. Does its own text establish "
                    "when it was published?"
                ),
                source_document_id=source_id,
                source_tier=tier,
                document_text=text,
            )
        )
    return questions


async def _document_window(
    session: AsyncSession, context: AgentContext, *, extraction_id: uuid.UUID
) -> tuple[str, str, str] | None:
    """A bounded window of a document's extracted text, with its id and tier.

    Re-extracted from the archived artefact by hash — the same read the verifier does —
    so the assist reads what the platform can prove it holds, not a copy from memory.
    ``None`` where the document cannot be re-read; failed advice is skipped advice.
    """
    extraction = await session.get(Extraction, extraction_id)
    if extraction is None:  # pragma: no cover -- RESTRICT keeps extractions alive
        return None
    source = await session.get(SourceDocument, extraction.source_document_id)
    if source is None:  # pragma: no cover -- RESTRICT again
        return None
    artefact = await session.get(Artefact, source.artefact_id)
    if artefact is None:  # pragma: no cover -- artefact_id is NOT NULL
        return None
    try:
        document = await extract_text(
            context.store,
            sha256=artefact.sha256,
            extractor=extraction.extractor,
            settings=context.settings,
        )
    except AerError as unreadable:
        _log.warning(
            "evaluations.document_unreadable",
            source_document_id=str(source.id),
            error=str(unreadable),
        )
        return None
    return (
        document.text.text[:DOCUMENT_WINDOW_CHARS],
        str(source.id),
        source.source_tier.value,
    )


# ==========================================================================================
# Writing
# ==========================================================================================


async def _write(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    results: dict[Metric, MetricResult | None],
    advisories: dict[Metric, list[dict[str, Any]]],
) -> list[Evaluation]:
    await session.execute(delete(Evaluation).where(Evaluation.job_id == job_id))

    written: list[Evaluation] = []
    for metric in RUN_TIME:
        result = results[metric]
        threshold, direction = THRESHOLDS[metric]
        details: dict[str, Any] = {"direction": direction.value}
        if result is None:
            details["note"] = "not exercised: this run had nothing for the metric to measure"
            details["population"] = 0
            row = Evaluation(
                job_id=job_id,
                metric=metric.value,
                value=None,
                threshold=threshold,
                passed=None,
                details=details,
            )
        else:
            details["population"] = result.population
            details["failures"] = list(result.failures)
            value = result.value
            # An infinite delta — a calculation that would not replay at all — cannot be
            # a NUMERIC value. The column takes the ceiling; the details keep the truth;
            # the failing verdict stands either way.
            if not value.is_finite():
                details["value"] = str(value)
                value = NUMERIC_CEILING
            row = Evaluation(
                job_id=job_id,
                metric=metric.value,
                value=value,
                threshold=threshold,
                passed=result.passed,
                details=details,
            )
        if advisories.get(metric):
            details["advisories"] = advisories[metric]
        session.add(row)
        written.append(row)

    await session.flush()
    return written


def _shorten(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
