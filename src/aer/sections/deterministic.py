"""Sections the platform fills itself, from the run's own recorded state.

Two of the eighteen built-in sections are not judgement but record: what the validators
measured, what the sources disagreed about, what earlier approved research concluded.
Handing those to a model would be asking it to paraphrase rows it cannot improve on — and
every paraphrase is a chance to soften a failure. So they are **deterministic**: filled by
the functions here, spending no tokens, declared by ``token_budget = 0`` on their
``section_definitions`` row.

**The draft step routes by the budget, not by the key.** Its rule — no section key appears
in the workflow — survives because the workflow only asks "is this section deterministic?"
(a column) and hands the key to :data:`BUILDERS`, which is this module's registry. A
zero-budget section with no registered builder **fails loudly**: a row that declares
"code fills me" when no code does would otherwise render as an empty section nobody could
explain.

**Each builder runs at a declared stage**, because a section recording the validators
cannot be filled before the validators have run. ``DRAFT`` builders fill with the other
sections; ``VALIDATE`` builders fill at the end of the validate step, after the metric
rows are written and before the red team seals what gate 2 will hash — so the preview the
operator approves already contains them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.disagreement import challenge_heading
from aer.db.models import Disagreement, Evaluation, Job, ResearchRequest, SectionStatus
from aer.eval import BLOCKING, RUN_TIME, THRESHOLDS, Direction, Metric
from aer.sections.registry import sections_for_job
from aer.sections.valuation_method import (
    commentary_problems,
    component_note,
    method_only,
    valuation_method_block,
)
from aer.services.evaluations import NUMERIC_CEILING
from aer.services.history import prior_comparison_content

__all__ = [
    "AUGMENTERS",
    "BUILDERS",
    "SectionAugmenter",
    "SectionStage",
    "fill_deterministic_sections",
    "model_facing_contract",
]

_log = structlog.get_logger("aer.sections.deterministic")

BUILTIN = "builtin"


class SectionStage(StrEnum):
    """When a deterministic section can first be filled truthfully."""

    DRAFT = "draft"
    VALIDATE = "validate"


@dataclass(frozen=True, slots=True)
class DeterministicSection:
    """One platform-filled section: when it runs, and what fills it."""

    stage: SectionStage
    build: Callable[[AsyncSession, Job, ResearchRequest], Awaitable[dict[str, Any]]]


async def fill_deterministic_sections(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    stage: SectionStage,
) -> list[str]:
    """Fill every zero-budget built-in section owned by this stage. Returns their keys.

    A zero-budget section with no builder fails at the DRAFT stage — the earliest moment
    the absence is observable — rather than at whichever later stage might have owned it,
    so the failure names the seed mistake while the seed is still what changed last.
    """
    filled: list[str] = []
    for section in await sections_for_job(session, job.id):
        definition = section.definition
        if definition.origin != BUILTIN or definition.token_budget != 0:
            continue

        builder = BUILDERS.get(section.section_key)
        if builder is None:
            if stage is SectionStage.DRAFT:
                section.status = SectionStatus.FAILED
                section.low_confidence_reason = (
                    "This section's definition declares a token budget of zero — filled "
                    "by code, not by a model — but no deterministic builder is registered "
                    f"for '{section.section_key}'."
                )
            continue
        if builder.stage is not stage:
            continue

        section.content = await builder.build(session, job, request)
        section.status = SectionStatus.GENERATED
        filled.append(section.section_key)

    if filled:
        await session.flush()
        _log.info(
            "sections.deterministic_filled",
            job_id=str(job.id),
            stage=stage.value,
            keys=filled,
        )
    return filled


# -- Prior research comparison -------------------------------------------------------------


async def _prior_research_comparison(
    session: AsyncSession,
    job: Job,
    request: ResearchRequest,
) -> dict[str, Any]:
    """What earlier approved research concluded, against this run — rows, never judgement.

    Delegates to :func:`aer.services.history.prior_comparison_content`: a first run gets
    the honest one-sentence state, and a later run gets the prior view, confidence and
    valuation range plus every prior catalyst (dated against this run's as-of) and key
    risk, each row carrying the prior ``report_id``.
    """
    return await prior_comparison_content(session, job_id=job.id, request=request)


# -- Validation & disagreements ------------------------------------------------------------


async def _validation_disagreements(
    session: AsyncSession,
    job: Job,
    request: ResearchRequest,  # noqa: ARG001 -- the builder signature is uniform
) -> dict[str, Any]:
    """What the validators measured and what the sources disagreed about, as a record.

    Escalated disagreements are described as *escalated for human decision at approval* —
    a statement about what the run did, which stays true after the human decides. The
    decision itself is recorded in ``approvals``, not rewritten into this content.
    """
    evaluations = list(
        await session.scalars(
            select(Evaluation).where(Evaluation.job_id == job.id).order_by(Evaluation.metric)
        )
    )
    disagreements = list(
        await session.scalars(
            select(Disagreement)
            .where(Disagreement.job_id == job.id)
            .order_by(Disagreement.created_at, Disagreement.topic)
        )
    )

    content: dict[str, Any] = {
        "summary": _summary(evaluations, disagreements),
        "validations": [_validation_row(row) for row in evaluations],
    }
    findings = _failed_check_findings(evaluations)
    if findings:
        content["failed_check_findings"] = findings
    if disagreements:
        content["disagreements"] = [
            block for row in disagreements for block in _disagreement_blocks(row)
        ]
    return content


# How many of a failed check's findings the section prints. Enough to act on; a run with
# hundreds of one defect is told the count rather than shown the wall.
_FINDINGS_SHOWN = 10


def _failed_check_findings(evaluations: list[Evaluation]) -> list[dict[str, str]]:
    """What each failed check actually found, one row per finding (gap A60).

    The live run's coverage notice said ``presentation_integrity`` failed — which is
    right — and nothing anywhere said *what it found*, so the operator could not act on
    the failure without opening the approval page and reading a JSONB column. Every
    metric already records its failure strings in the row's details; this is the surface.
    Empty on a clean run, so a report with nothing to confess does not change shape.
    """
    rows: list[dict[str, str]] = []
    for evaluation in evaluations:
        if evaluation.passed is not False:
            continue
        found = [str(item) for item in (evaluation.details or {}).get("failures", [])]
        # Each finding is quoted as a code span, and the backticks are load-bearing (gap
        # R9): a finding that names an unformatted integer *contains* that integer, so
        # printed as prose it would fail the next presentation scan — the check failing on
        # its own output, forever. Code spans are the scan's own carve-out for deliberate
        # literals, and they are also the honest presentation: these are machine strings
        # reproduced verbatim, not sentences of the note.
        rows.extend(
            {"metric": evaluation.metric, "finding": f"`{item}`"}
            for item in found[:_FINDINGS_SHOWN]
        )
        if len(found) > _FINDINGS_SHOWN:
            rows.append(
                {
                    "metric": evaluation.metric,
                    "finding": f"… and {len(found) - _FINDINGS_SHOWN} more of the same kind, "
                    "recorded on the run's evaluation row.",
                }
            )
        if not found:
            # A failed check with no recorded failure strings is still named, because a
            # silent row here would recreate the very gap this table closes.
            rows.append(
                {
                    "metric": evaluation.metric,
                    "finding": "the check failed but recorded no individual findings; "
                    "its score and threshold are in the table above.",
                }
            )
    return rows


def _summary(evaluations: list[Evaluation], disagreements: list[Disagreement]) -> str:
    passed = sum(1 for row in evaluations if row.passed is True)
    failed = sum(1 for row in evaluations if row.passed is False)
    unexercised = sum(1 for row in evaluations if row.passed is None)

    # The guarantees this run did not measure, named rather than left to inference
    # (polish P9): they need corpora of attacks and mismatches a well-behaved run does
    # not contain, and four guarantees a reader cannot account for is worse than four
    # they can see are covered elsewhere. Derived from the metric sets, so a metric that
    # moves between the CI gate and the runtime moves here without an edit.
    ci_only = sorted(metric.value for metric in set(BLOCKING) - set(RUN_TIME))
    parts = [
        f"The run's validators measured {len(evaluations)} metric(s): "
        f"{passed} passed, {failed} failed, {unexercised} not exercised. "
        f"{_spoken_list(ci_only)} are corpus metrics, measured by the CI evaluation "
        "gate against adversarial fixtures rather than against any one run."
    ]
    if disagreements:
        escalated = sum(1 for row in disagreements if row.resolution == "escalated")
        parts.append(
            f"{len(disagreements)} disagreement(s) between sources were recorded"
            + (
                f", of which {escalated} were escalated for human decision at approval."
                if escalated
                else ", all settled by the deterministic resolution ladder."
            )
        )
    else:
        parts.append("No disagreements between sources were recorded.")
    return " ".join(parts)


def _spoken_list(names: list[str]) -> str:
    """``a, b and c`` — a sentence's list, not a repr's."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _validation_row(row: Evaluation) -> dict[str, str]:
    threshold = str(row.threshold)
    try:
        _, direction = THRESHOLDS[Metric(row.metric)]
        bound = "at least" if direction is Direction.AT_LEAST else "at most"
        threshold = f"{bound} {row.threshold}"
    except (ValueError, KeyError):
        # A metric name this code version does not know still renders its stored
        # threshold; the direction is the only thing that cannot be recovered.
        pass

    if row.passed is True:
        verdict = "pass"
    elif row.passed is False:
        verdict = "fail"
    else:
        verdict = "not exercised"

    if row.value is None:
        score = "\N{EM DASH}"
    elif row.value == NUMERIC_CEILING:
        # The column's saturation value, not a measurement: an infinite replay delta is
        # stored clamped (the true value lives in the details). Twelve nines in a printed
        # table read as a crashed validator, so the rendering says what the row means.
        score = "unbounded (clamped at 1e12)"
    else:
        score = str(row.value)

    return {
        "metric": row.metric,
        "score": score,
        "threshold": threshold,
        "verdict": verdict,
    }


def _disagreement_blocks(row: Disagreement) -> list[dict[str, str]]:
    """One recorded conflict as a short run of prose blocks — an argument, not a table row.

    v2 laid these out as columns, and a live document put a two-hundred-word challenge in
    a narrow one: a single row spanned three pages and neither position could be read
    (roadmap §2.4). So each conflict now becomes paragraphs through the renderer's
    prose-block convention — the challenge under its identity, then its basis, then its
    resolution — and a long argument flows down the page the way arguments do.

    Gap R5's guarantees carry over unchanged: the statement appears once, the evidence
    rides the citation keys the renderer turns into footnotes, and no UUID or rationale
    blob reaches a reader. The lead-ins carry no trailing punctuation — both serialisers
    append the colon themselves.
    """
    resolution = {
        "chose_a": f"Resolved by rule '{row.rule.value}': position A selected.",
        "chose_b": f"Resolved by rule '{row.rule.value}': position B selected.",
        "escalated": "Escalated for human decision at approval.",
        "agreed": "The positions agree.",
    }.get(row.resolution.value, f"{row.resolution.value}.")

    detail = row.detail or {}
    challenge = detail.get("challenge")
    if not challenge:
        kind = row.kind.value.replace("_", " ")
        return [
            {
                "lead_in": f"{row.topic} ({kind})",
                "text": str(row.resolution_rationale or "\N{EM DASH}"),
            },
            {"lead_in": "Resolution", "text": resolution},
        ]

    opening = {
        "lead_in": challenge_heading(detail, fallback=row.topic),
        "text": str(challenge),
    }
    evidence = detail.get("evidence") or {}
    for kind, key in (("sources", "source_document_id"), ("calculations", "calculation_id")):
        ids = evidence.get(kind) or []
        if ids:
            opening[key] = str(ids[0])

    blocks = [opening]
    basis = detail.get("basis")
    if basis:
        blocks.append({"lead_in": "Basis", "text": str(basis)})
    blocks.append({"lead_in": "Resolution", "text": resolution})
    return blocks


BUILDERS: dict[str, DeterministicSection] = {
    "prior_research_comparison": DeterministicSection(
        stage=SectionStage.DRAFT, build=_prior_research_comparison
    ),
    "validation_disagreements": DeterministicSection(
        stage=SectionStage.VALIDATE, build=_validation_disagreements
    ),
}


# -- Platform-filled fields inside model-written sections ------------------------------------
#
# ADR 0063. A builder fills a whole zero-budget section; an *augmenter* fills the fields of
# a model-written section whose subject is the platform's own record — a method
# description, a provenance table — where a model's paraphrase can only be equal or wrong.
# The contract marks those fields ``"platform_filled": true``; the writer never sees them
# (:func:`model_facing_contract` strips them from the model's schema, and ``extra="forbid"``
# makes them unrepresentable in its reply), and the executed draft has them merged in from
# ``build`` before the section is stored.


@dataclass(frozen=True, slots=True)
class SectionAugmenter:
    """The code-owned fields of one model-written section.

    ``build`` renders them from the run's records. ``check`` is the deterministic edge of
    the model's remaining field: given the draft's content and the rendered block, it
    returns the problems that refuse the draft — the valuation section uses it to reject a
    commentary describing method inputs the record does not hold.

    ``standalone``, when set, is asked before the model is: given the rendered block, it
    returns the reason the block is this section's *whole* truthful content — in which
    case no writer call is made — or an empty string for the ordinary path (gap A51c).

    ``note``, when set, is what the writer is told about the block it cannot see: the
    other half of ``check``, so a rule the platform enforces is a rule the writer can
    follow rather than guess at.
    """

    build: Callable[..., Awaitable[dict[str, Any]]]
    check: Callable[[dict[str, Any], dict[str, Any]], list[str]]
    standalone: Callable[[dict[str, Any]], str] | None = None
    note: Callable[[dict[str, Any]], str] | None = None


AUGMENTERS: dict[str, SectionAugmenter] = {
    "valuation_dcf": SectionAugmenter(
        build=valuation_method_block,
        check=commentary_problems,
        standalone=method_only,
        note=component_note,
    ),
}


def model_facing_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """The contract with the platform-filled fields removed — what the model is bound by.

    The stored contract keeps every field, because it is also the render order and the
    headings; this narrowing exists so the model's schema — and therefore its reply —
    cannot carry a field the platform owns. Rendering walks the full contract, so the
    merged fields come back at their declared positions.
    """
    properties = contract.get("properties")
    if not isinstance(properties, dict):
        return contract
    kept = {
        name: spec
        for name, spec in properties.items()
        if not (isinstance(spec, dict) and spec.get("platform_filled"))
    }
    if len(kept) == len(properties):
        return contract
    narrowed = {**contract, "properties": kept}
    required = contract.get("required")
    if isinstance(required, list):
        narrowed["required"] = [name for name in required if name in kept]
    return narrowed
