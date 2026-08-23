"""What kind of business this is, who agreed it, and what that permits.

A classification decides which valuation models may run, so it is a decision about the
*answer* rather than about presentation. `docs/archive/PLAN.md` section 2.9 states the rule: a
blocked model produces a hard gate, not a footnote. This module is the half of that rule
which lives against the database — the proposal, the confirmation, and the mandate that
comes out the other side.

**A proposal is not a classification.** The classifier is a model, and a model classifying
Barclays as early-stage technology would, unchecked, unlock a discounted cash flow on a bank.
So a proposed sector reaches nothing until somebody confirms it at ``SECTOR_SPECIALIST``, and
:func:`mandate_for_job` mints a mandate only from a confirmed one.

**The dangerous direction is permissive, not restrictive.** A wrong classification that
*blocks* a model wastes an operator's afternoon; one that *permits* a model puts a
meaningless number in a report. That asymmetry is why an unconfirmed specialist proposal
stops the run rather than falling through to "unclassified": falling through is the
permissive answer, and it is the one that would be reached by forgetting rather than by
deciding.

**Required metrics are disclosed, never dropped.** A profile names the metrics without which
a report on that sector is not worth reading. :func:`metric_disclosure` says which of them the
run produced and which it did not, and the ones it did not are a paragraph in the report
rather than an absence a reader has to notice.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Decision, GateKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.sectors import (
    SECTOR_PROFILES,
    ModelNotPermittedError,
    SectorProfile,
    ValuationMandate,
    ValuationModel,
    mandate_for,
    profile_for,
    suggested_profiles,
    unclassified_mandate,
)
from aer.db.models import Approval, Job, JobStep, User

__all__ = [
    "CLASSIFY_STEP",
    "ClassificationProposal",
    "MetricDisclosure",
    "classification_payload",
    "confirmed_classification",
    "mandate_for_job",
    "metric_disclosure",
    "propose_from_sic",
    "sector_gate_required",
]

_log = structlog.get_logger("aer.services.sectors")

CLASSIFY_STEP = "classify"
"""The workflow step whose output carries the proposal. One name, used by both halves."""


@dataclass(frozen=True, slots=True)
class ClassificationProposal:
    """What kind of business a classifier thinks this is, and why.

    ``sector_key`` is empty when the classifier found nothing specialist, which is the
    ordinary answer: most listed companies are not banks, insurers, REITs or pre-revenue
    biotechs. An empty proposal does not open a gate.
    """

    sector_key: str
    rationale: str
    proposed_by: str
    confidence: float = 0.0

    # What the SIC code suggested, kept alongside the proposal so a reviewer can see whether
    # the classifier agreed with the filer's own self-description or overrode it. The second
    # is the interesting case and is invisible if only the conclusion is stored.
    sic_code: str = ""
    sic_candidates: tuple[str, ...] = ()

    @property
    def profile(self) -> SectorProfile | None:
        return profile_for(self.sector_key) if self.sector_key else None

    @property
    def is_specialist(self) -> bool:
        return self.profile is not None


def propose_from_sic(sic_code: str, *, proposed_by: str = "sic_lookup") -> ClassificationProposal:
    """The classification a filer's own SIC code suggests. A starting point, not an answer.

    Deterministic and free, so it runs before any model call and gives the classifier
    something to agree or disagree with. Where the code matches more than one profile the
    first is proposed and the rest are recorded as candidates — a reviewer seeing
    ``6512`` matched both REITs and nothing else can tell that the choice was narrow.

    SIC codes are self-reported, decades old in places, and a holding company files under
    whatever its largest subsidiary does. So this proposes; it does not decide.
    """
    candidates = suggested_profiles(sic_code)
    chosen = candidates[0] if candidates else None

    return ClassificationProposal(
        sector_key=chosen.key if chosen is not None else "",
        rationale=(
            f"SIC {sic_code} matches {chosen.label}."
            if chosen is not None
            else f"SIC {sic_code or 'not reported'} matches no specialist sector profile."
        ),
        proposed_by=proposed_by,
        confidence=0.5 if chosen is not None else 0.0,
        sic_code=sic_code,
        sic_candidates=tuple(profile.key for profile in candidates),
    )


def classification_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly what the sector gate approves, as one structure.

    Built from the classify step's own output, so the sector an operator confirms is the
    sector the classifier proposed rather than a re-derivation that might differ. Hashed into
    the approval, so confirming one classification and running another is refused upstream.
    """
    return {
        "sector_key": str(produced.get("sector_key", "")),
        "rationale": str(produced.get("rationale", "")),
        "proposed_by": str(produced.get("proposed_by", "")),
        "blocked_models": list(produced.get("blocked_models", [])),
        "allowed_models": list(produced.get("allowed_models", [])),
        "warnings": list(produced.get("warnings", [])),
    }


def sector_gate_required(produced: Mapping[str, Any]) -> bool:
    """Whether this run needs a person to agree what kind of business it is looking at.

    **On a specialist proposal, not on every run.** An ordinary company does not need a human
    to confirm that it is ordinary; a proposal of "banks" does, because that proposal is what
    stops a discounted cash flow, and a classification nobody reviewed is a model deciding
    which models may run.
    """
    return bool(profile_for(str(produced.get("sector_key", ""))))


async def confirmed_classification(
    session: AsyncSession, job: Job
) -> tuple[SectorProfile | None, str]:
    """The sector this run may act on, and who confirmed it.

    Returns ``(None, "")`` for a run whose classifier proposed nothing specialist — the
    ordinary case, and the one that runs the standard model.

    Raises:
        ModelNotPermittedError: If a specialist sector was proposed and the
            ``SECTOR_SPECIALIST`` gate has not approved it. Refused rather than treated as
            unclassified, because "unclassified" is the permissive answer and reaching it by
            forgetting is exactly the failure this gate exists to prevent.
    """
    step = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job.id, JobStep.step_key == CLASSIFY_STEP)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    produced = (step.output_ref or {}) if step is not None else {}
    proposed = str(produced.get("sector_key", ""))
    profile = profile_for(proposed) if proposed else None

    if profile is None:
        return None, ""

    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.job_id == job.id,
            Approval.gate == GateKind.SECTOR_SPECIALIST,
            Approval.decision == Decision.APPROVED,
        )
        .order_by(Approval.decided_at.desc())
        .limit(1)
    )
    if approval is None:
        message = (
            f"This run's classifier proposed {profile.label}, and nobody has confirmed it. "
            "A specialist classification decides which valuation models may run, so it is "
            "not applied on a model's say-so — and it is not quietly dropped either, "
            "because dropping it would fall through to the standard model, which is the "
            "answer this gate exists to prevent."
        )
        raise ModelNotPermittedError(
            message,
            context={"job_id": str(job.id), "sector": profile.key, "gate": "SECTOR_SPECIALIST"},
        )

    expected = sha256_hex(canonical_json(classification_payload(produced)))
    if approval.payload_hash and approval.payload_hash != expected:
        message = (
            f"The confirmed classification for this run does not match the one on record. "
            f"Somebody approved {profile.label} against a different proposal, so the "
            "approval is not an approval of this."
        )
        raise ModelNotPermittedError(
            message,
            context={
                "job_id": str(job.id),
                "sector": profile.key,
                "approved": approval.payload_hash,
                "current": expected,
            },
        )

    actor = await session.get(User, approval.actor_user_id)
    return profile, actor.email if actor is not None else str(approval.actor_user_id)


async def mandate_for_job(
    session: AsyncSession, job: Job, *, model: ValuationModel, subject: str
) -> ValuationMandate:
    """Permission to run ``model`` on this job's company, or a refusal explaining itself.

    The one place a mandate is minted from stored state. Everything downstream — the DCF, the
    scenario engine, the sensitivity grid — takes the mandate rather than the job, so none of
    them can be reached without passing through here.

    Raises:
        ModelNotPermittedError: If the sector blocks the model, does not implement it, or its
            classification is unconfirmed.
    """
    profile, confirmed_by = await confirmed_classification(session, job)

    if profile is None:
        mandate = unclassified_mandate(model, subject=subject)
    else:
        mandate = mandate_for(model, subject=subject, profile=profile, confirmed_by=confirmed_by)

    _log.info(
        "sector.mandate_granted",
        job_id=str(job.id),
        model=model.value,
        subject=subject,
        sector=mandate.sector_key or "unclassified",
        confirmed_by=confirmed_by or "n/a",
    )
    return mandate


@dataclass(frozen=True, slots=True)
class MetricDisclosure:
    """Which of a sector's required metrics the run produced, and which it did not.

    Both halves, deliberately. A report that lists what it has says nothing about what it
    was supposed to have, and a reader cannot tell an omission from an absence.
    """

    sector_key: str
    sector_label: str
    present: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def as_paragraph(self) -> str:
        """The disclosure as it appears in a report.

        Written here rather than in a template, because *whether* the absence is disclosed
        must not depend on which template rendered it.
        """
        if not self.sector_key:
            return ""
        if self.is_complete:
            return (
                f"Every metric this platform requires for {self.sector_label.lower()} was "
                f"computed: {', '.join(self.present)}."
            )
        return (
            f"This report does not carry {len(self.missing)} of the "
            f"{len(self.present) + len(self.missing)} metrics this platform requires for "
            f"{self.sector_label.lower()}: {', '.join(self.missing)}. They are absent rather "
            "than estimated, and a conclusion drawn without them rests on less than a "
            f"complete picture of this business."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "sector_key": self.sector_key,
            "sector_label": self.sector_label,
            "present": list(self.present),
            "missing": list(self.missing),
            "complete": self.is_complete,
        }


def metric_disclosure(
    profile: SectorProfile | None, *, computed: Iterable[str]
) -> MetricDisclosure:
    """What this run owes its sector, against what it produced.

    An unclassified company owes nothing specific, and gets an empty disclosure rather than a
    made-up one.
    """
    if profile is None:
        return MetricDisclosure(sector_key="", sector_label="", present=(), missing=())

    available = set(computed)
    return MetricDisclosure(
        sector_key=profile.key,
        sector_label=profile.label,
        present=tuple(m for m in profile.required_metrics if m in available),
        missing=tuple(m for m in profile.required_metrics if m not in available),
    )


async def gate_payload_for_job(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    """What the sector review page shows, read back from the classify step.

    Returns an empty payload for a run that has not classified yet, so a page can render
    "nothing to review" rather than an error.
    """
    step = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == CLASSIFY_STEP)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    if step is None or not step.output_ref:
        return {}
    return classification_payload(step.output_ref)


def profile_keys() -> tuple[str, ...]:
    """Every sector key, for a classifier's output schema to be constrained against."""
    return tuple(profile.key for profile in SECTOR_PROFILES)
