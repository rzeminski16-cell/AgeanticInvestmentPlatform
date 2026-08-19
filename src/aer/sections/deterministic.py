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

from aer.db.models import Disagreement, Evaluation, Job, ResearchRequest, SectionStatus
from aer.eval import THRESHOLDS, Direction, Metric
from aer.sections.registry import sections_for_job
from aer.sections.valuation_method import commentary_problems, valuation_method_block
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
    if disagreements:
        content["disagreements"] = [_disagreement_row(row) for row in disagreements]
    return content


def _summary(evaluations: list[Evaluation], disagreements: list[Disagreement]) -> str:
    passed = sum(1 for row in evaluations if row.passed is True)
    failed = sum(1 for row in evaluations if row.passed is False)
    unexercised = sum(1 for row in evaluations if row.passed is None)

    parts = [
        f"The run's validators measured {len(evaluations)} metric(s): "
        f"{passed} passed, {failed} failed, {unexercised} not exercised."
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

    return {
        "metric": row.metric,
        "score": str(row.value) if row.value is not None else "—",
        "threshold": threshold,
        "verdict": verdict,
    }


def _disagreement_row(row: Disagreement) -> dict[str, str]:
    """One recorded conflict, in the shape the reader's appendix lays out.

    A red-team challenge — recognised by its structured ``detail`` — renders as topic,
    severity, challenge and basis, with its evidence as ordinary footnotes through the
    renderer's citation keys. The statement appears once, where before it appeared three
    times (truncated in the topic, and twice inside the rationale blob), and no UUID
    reaches the reader (gap R5). A source conflict has no structure beyond the ladder's
    rationale and keeps its original shape.
    """
    resolution = {
        "chose_a": f"resolved by rule '{row.rule.value}': position A selected",
        "chose_b": f"resolved by rule '{row.rule.value}': position B selected",
        "escalated": "escalated for human decision at approval",
        "agreed": "the positions agree",
    }.get(row.resolution.value, row.resolution.value)

    detail = row.detail or {}
    challenge = detail.get("challenge")
    if not challenge:
        return {
            "topic": row.topic,
            "kind": row.kind.value,
            "resolution": resolution,
            "rationale": row.resolution_rationale,
        }

    dimension = str(detail.get("dimension") or "").replace("_", " ").strip()
    shown = {
        "topic": f"Red team \N{EM DASH} {dimension}" if dimension else row.topic,
        "severity": f"{detail['severity']}/5" if detail.get("severity") else "\N{EM DASH}",
        "challenge": str(challenge),
        "basis": str(detail.get("basis") or "\N{EM DASH}"),
        "resolution": resolution,
    }
    evidence = detail.get("evidence") or {}
    for kind, key in (("sources", "source_document_id"), ("calculations", "calculation_id")):
        ids = evidence.get(kind) or []
        if ids:
            shown[key] = str(ids[0])
    return shown


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
    """

    build: Callable[..., Awaitable[dict[str, Any]]]
    check: Callable[[dict[str, Any], dict[str, Any]], list[str]]


AUGMENTERS: dict[str, SectionAugmenter] = {
    "valuation_dcf": SectionAugmenter(build=valuation_method_block, check=commentary_problems),
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
