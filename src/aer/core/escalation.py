"""The §2.4 escalation triggers: ten conditions, any one of which raises the gate-2 banner.

``docs/archive/PLAN.md`` §2.4 lists ten trigger conditions and says "any one pauses the run and
raises a banner at Gate 2". The run already pauses at gate 2 unconditionally — the final
gate requires a person — so what a fired trigger changes is what that pause *says*: the
banner names the condition, and the operator approves knowing it, or does not approve.
A trigger that fired and surfaced nothing would be the platform noticing a problem and
keeping it to itself, which is worse than not noticing.

**Everything here is arithmetic over already-recorded rows.** The evaluations table, the
disagreements ladder, the section coverage floors, the skill pins' clamps, the cost rows
and the source flags are all written by earlier deterministic steps; this module reads
their shapes and compares against thresholds. No model is consulted — a model deciding
whether to raise the banner about model uncertainty would be the fox auditing the henhouse
— and no I/O happens, so the same rows always produce the same triggers. That determinism
is load-bearing: the fired triggers join the gate-2 payload *inside the approval hash*,
so the hash sealed by the red-team step and the hash the review page computes live must
agree, and they can only agree if this function is a pure one of the rows.

The thresholds are §2.4's stated defaults, held as named constants. §2.4 marks them
"configurable"; when configuration arrives it replaces the constants, not the shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Final

from aer.core.disagreement import DisagreementKind

__all__ = [
    "CONFIDENCE_FLOOR",
    "COST_ALERT_RATIO",
    "ConflictScene",
    "CostScene",
    "FiredTrigger",
    "MetricScore",
    "PolicyClamp",
    "SectionScene",
    "SourceScene",
    "TriggerKind",
    "fire_triggers",
]


class TriggerKind(StrEnum):
    """The §2.4 escalation vocabulary, one member per row of the table, in its order."""

    LOW_SOURCE_COVERAGE = "low_source_coverage"
    CREDIBLE_SOURCE_CONFLICT = "credible_source_conflict"
    POTENTIAL_LOOK_AHEAD = "potential_look_ahead"
    HIGH_MODEL_UNCERTAINTY = "high_model_uncertainty"
    MATERIAL_MISSING_SECTION = "material_missing_section"
    SKILL_POLICY_CLAMP = "skill_policy_clamp"
    COST_ABOVE_THRESHOLD = "cost_above_threshold"
    VALIDATION_FAILURE = "validation_failure"
    SUSPICIOUS_SOURCE = "suspicious_source"

    # `THESIS_DISAGREEMENT` was here and is deliberately gone (2026-08-25). It was never one
    # of §2.4's rows; it was appended, and it fired on the red team materially contradicting
    # the draft — which is the red team doing exactly what it is paid to do. A banner that
    # said "three faults" over two faults and one adversary working correctly taught an
    # operator to read the red one as noise, which is the only way a trigger can fail.
    #
    # The challenges are not lost: they are `disagreements` rows and they reach both the gate
    # page and the report's appendix on their own section, which is where a reader can weigh
    # them instead of being alarmed by their existence.


# §2.4: "any section self-confidence < 0.5". A float because it compares against the
# sections' own confidence column, which is a float — this is a self-assessment, not money.
CONFIDENCE_FLOOR: Final = 0.5

# §2.4: "estimated or actual > 80% of per-run cap".
COST_ALERT_RATIO: Final = Decimal("0.8")

# The §2.10 metric names this module reads, restated as strings so the correctness core
# does not import the evaluation package. A test outside this module holds them equal to
# `aer.eval.metrics.Metric`'s values, so a renamed metric fails a build rather than
# quietly un-wiring a trigger.
_CITATION_ACCURACY: Final = "citation_accuracy"
_HALLUCINATED_CITATION_RATE: Final = "hallucinated_citation_rate"
_TEMPORAL_COMPLIANCE: Final = "temporal_compliance"
_LOOK_AHEAD_RECALL: Final = "look_ahead_recall"
_PRIMARY_SOURCE_RATIO: Final = "primary_source_ratio"
_NUMERICAL_CONSISTENCY: Final = "numerical_consistency"
_FIGURE_PLAUSIBILITY: Final = "figure_plausibility"
_CITED_FIGURE_AGREEMENT: Final = "cited_figure_agreement"

# `SectionStatus` values, restated for the same reason the metric names are. The check
# constraint on `report_sections.status` and the bridging test keep these honest.
_GENERATED: Final = "generated"
_SKIPPED: Final = "skipped_not_applicable"

# How many pieces of evidence a trigger names before summarising. The banner is a summons
# to the detail tables below it, not a reproduction of them.
_EVIDENCE_CAP: Final = 6


@dataclass(frozen=True, slots=True)
class MetricScore:
    """One evaluations row, as the triggers read it.

    ``passed`` is tri-state exactly as the table stores it: ``None`` is the not-exercised
    row, which no trigger fires on — a metric with nothing to measure is not a failure.
    ``disputes`` carries the validator advisories that contradict the deterministic
    verdict — the "validator disagreement" half of §2.4's uncertainty trigger.
    """

    metric: str
    passed: bool | None
    value: Decimal | None = None
    threshold: Decimal | None = None
    failures: tuple[str, ...] = ()
    disputes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SectionScene:
    """One report section with its coverage verdict and its own self-assessment.

    ``covered`` and ``shortfall`` come from the section's evidence floor — the built-in
    floor for a built-in, the pinned composed policy for a custom section. ``status`` is
    the ``SectionStatus`` value as a string, so this module needs no ORM import.
    """

    key: str
    status: str
    required: bool = False
    custom: bool = False
    has_primary: bool = False
    covered: bool = True
    shortfall: str = ""
    confidence: float | None = None

    # From the section's own evidence policy. The deterministic sections (task 44) declare
    # ``requires_primary: false`` because their evidence is the run's own rows; a trigger
    # that demanded a primary source of them would fire on every clean run.
    requires_primary: bool = True

    @property
    def generated(self) -> bool:
        return self.status == _GENERATED

    @property
    def enabled(self) -> bool:
        """Whether this run meant to produce the section at all.

        A section skipped as not-applicable was excluded on purpose, with its reason on
        the record; §2.4's missing-section trigger is about sections the run *owed*.
        """
        return self.status != _SKIPPED


@dataclass(frozen=True, slots=True)
class ConflictScene:
    """One disagreements row, reduced to what the triggers ask of it."""

    topic: str
    kind: DisagreementKind
    material: bool = False
    settled_by_human: bool = False


@dataclass(frozen=True, slots=True)
class PolicyClamp:
    """One clamp the additive-only composer applied to a skill's requested policy."""

    skill_key: str
    field: str
    requested: str
    effective: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SourceScene:
    """One source document, reduced to the two flags the triggers read."""

    name: str
    post_dated: bool = False
    admissible: bool = True
    injection_flagged: bool = False


@dataclass(frozen=True, slots=True)
class CostScene:
    """What the run may spend, what was estimated, and what it has actually spent."""

    cap_gbp: Decimal
    estimated_gbp: Decimal | None = None
    actual_gbp: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class FiredTrigger:
    """One §2.4 condition that held, with the evidence that made it hold."""

    kind: TriggerKind
    message: str
    evidence: tuple[str, ...] = field(default=())

    def as_record(self) -> dict[str, object]:
        """The form carried in the gate-2 payload, inside the approval hash."""
        return {
            "kind": self.kind.value,
            "message": self.message,
            "evidence": list(self.evidence),
        }


def fire_triggers(
    *,
    point_in_time: bool,
    metrics: tuple[MetricScore, ...],
    sections: tuple[SectionScene, ...],
    conflicts: tuple[ConflictScene, ...],
    clamps: tuple[PolicyClamp, ...],
    sources: tuple[SourceScene, ...],
    cost: CostScene,
) -> tuple[FiredTrigger, ...]:
    """Evaluate all nine §2.4 conditions over one run's recorded rows.

    Returns the fired triggers in the table's own order, each carrying the evidence that
    made it fire. An empty tuple is the clean run — no banner.
    """
    candidates = (
        _low_source_coverage(sections, metrics),
        _credible_source_conflict(conflicts),
        _potential_look_ahead(sources, metrics, point_in_time=point_in_time),
        _high_model_uncertainty(sections, metrics),
        _material_missing_section(sections),
        _skill_policy_clamp(clamps),
        _cost_above_threshold(cost),
        _validation_failure(metrics),
        _suspicious_source(sources),
    )
    return tuple(trigger for trigger in candidates if trigger is not None)


# ==========================================================================================
# The nine conditions, in §2.4's order
# ==========================================================================================


def _low_source_coverage(
    sections: tuple[SectionScene, ...], metrics: tuple[MetricScore, ...]
) -> FiredTrigger | None:
    """Any required section owing a primary source that cites none, or ratio < 60%.

    "Owing" is the section's own evidence floor: a section whose policy sets
    ``requires_primary: false`` never claimed primary sourcing, so its absence is not
    thinner sourcing than the report stands on — it is the declared floor being met.
    """
    evidence = [
        f"required section '{row.key}' cites no primary source"
        for row in sections
        if row.required and row.generated and row.requires_primary and not row.has_primary
    ]
    ratio = _score(metrics, _PRIMARY_SOURCE_RATIO)
    if ratio is not None and ratio.passed is False:
        evidence.append(
            f"primary-source ratio {_shown(ratio.value)} is below "
            f"the {_shown(ratio.threshold)} floor"
        )
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.LOW_SOURCE_COVERAGE,
        message=(
            "Parts of this draft rest on thinner sourcing than the report claims to "
            "stand on. The coverage table below shows each section against its floor."
        ),
        evidence=_capped(evidence),
    )


def _credible_source_conflict(conflicts: tuple[ConflictScene, ...]) -> FiredTrigger | None:
    """Two Tier ≤4 sources disagree by more than 2% on a material figure.

    The ``material`` flag *is* that test — :func:`aer.core.disagreement.resolve` sets it
    from the tier limit and the 2% threshold — so this reads the recorded verdict rather
    than re-deriving it. A conflict a person has already settled does not re-raise the
    banner: the point of the trigger is that somebody looks, and somebody did.
    """
    evidence = [
        row.topic
        for row in conflicts
        if row.kind is not DisagreementKind.THESIS_CONFLICT
        and row.material
        and not row.settled_by_human
    ]
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.CREDIBLE_SOURCE_CONFLICT,
        message=(
            "Credible sources disagree materially about a figure. Both positions are "
            "recorded side by side below; approving publishes the conflict on the record."
        ),
        evidence=_capped(evidence),
    )


def _potential_look_ahead(
    sources: tuple[SourceScene, ...],
    metrics: tuple[MetricScore, ...],
    *,
    point_in_time: bool,
) -> FiredTrigger | None:
    """A source published after the as-of date is usable while point-in-time is on.

    Two ways to hold: a post-dated source that is admissible anyway (an override, or an
    enforcement gap — either way a person should see it), or the temporal metrics
    recording that inadmissible evidence was in fact used.
    """
    evidence = (
        [
            f"'{row.name}' postdates the as-of date and is admissible"
            for row in sources
            if row.post_dated and row.admissible
        ]
        if point_in_time
        else []
    )
    for name in (_TEMPORAL_COMPLIANCE, _LOOK_AHEAD_RECALL):
        score = _score(metrics, name)
        if score is not None and score.passed is False:
            evidence.extend(score.failures or (f"{name} failed",))
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.POTENTIAL_LOOK_AHEAD,
        message=(
            "Evidence dated after the as-of date can reach this report. Under "
            "point-in-time rules that is look-ahead, and the affected sources are named."
        ),
        evidence=_capped(evidence),
    )


def _high_model_uncertainty(
    sections: tuple[SectionScene, ...], metrics: tuple[MetricScore, ...]
) -> FiredTrigger | None:
    """Any section self-confidence below 0.5, or a validator disputing a verdict."""
    evidence = [
        f"section '{row.key}' reports confidence {row.confidence:.2f}"
        for row in sections
        if row.confidence is not None and row.confidence < CONFIDENCE_FLOOR
    ]
    evidence.extend(f"{score.metric}: {dispute}" for score in metrics for dispute in score.disputes)
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.HIGH_MODEL_UNCERTAINTY,
        message=(
            "The run is unsure of its own work: a section rates itself below the "
            "confidence floor, or an advisory validator disputes a recorded verdict."
        ),
        evidence=_capped(evidence),
    )


def _material_missing_section(sections: tuple[SectionScene, ...]) -> FiredTrigger | None:
    """A required built-in or enabled custom section is empty or below its evidence floor."""
    evidence: list[str] = []
    for row in sections:
        if not (row.required or row.custom) or not row.enabled:
            continue
        if not row.generated:
            evidence.append(f"'{row.key}' was not generated (status: {row.status})")
        elif not row.covered:
            evidence.append(f"'{row.key}' is below its evidence floor ({row.shortfall})")
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.MATERIAL_MISSING_SECTION,
        message=(
            "A section this run owed is missing or under-evidenced. The report would "
            "publish with a hole a reader may not notice; the operator must."
        ),
        evidence=_capped(evidence),
    )


def _skill_policy_clamp(clamps: tuple[PolicyClamp, ...]) -> FiredTrigger | None:
    """The additive-only composer tightened what a skill file asked for."""
    evidence = [
        f"{clamp.skill_key}: {clamp.field} requested {clamp.requested}, "
        f"effective {clamp.effective}" + (f" ({clamp.reason})" if clamp.reason else "")
        for clamp in clamps
    ]
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.SKILL_POLICY_CLAMP,
        message=(
            "A custom section ran under a stricter policy than its skill file requested. "
            "The effective policy differs from what was written; the clamps are listed."
        ),
        evidence=_capped(evidence),
    )


def _cost_above_threshold(cost: CostScene) -> FiredTrigger | None:
    """Estimated or actual spend above 80% of the per-run cap."""
    alert_at = cost.cap_gbp * COST_ALERT_RATIO
    evidence: list[str] = []
    if cost.estimated_gbp is not None and cost.estimated_gbp > alert_at:
        evidence.append(f"estimated £{cost.estimated_gbp} exceeds £{alert_at} (80% of cap)")
    if cost.actual_gbp > alert_at:
        evidence.append(f"actual spend £{cost.actual_gbp} exceeds £{alert_at} (80% of cap)")
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.COST_ABOVE_THRESHOLD,
        message=(
            f"This run is close to its £{cost.cap_gbp} cost cap. The hard cap still "
            "stops the run in code; this banner is the warning before it does."
        ),
        evidence=_capped(evidence),
    )


def _validation_failure(metrics: tuple[MetricScore, ...]) -> FiredTrigger | None:
    """A citation, a number or a figure the run cannot stand behind.

    The hallucinated-citation rate joins the citation half: a fabricated excerpt is the
    exact failure the 98% figure exists to catch, and a run with one fabrication and
    ninety-nine clean citations would otherwise pass the letter of the wording while
    failing its whole point. Figure plausibility joins for the same reason from the
    other side (gap A61): a front page carrying a margin above one is the failure the
    numeric checks exist to prevent, reached by a route none of them measures.

    Cited-figure agreement joins from a third (gap R19). Every other member of this list
    asks whether a figure is *recorded* correctly; that one asks whether the sentence used
    the figure it cited, which is the only route by which the MSFT note could draft a quick
    ratio of 0.93 over a recorded 1.567 with the whole gate green.
    """
    evidence: list[str] = []
    for name in (
        _CITATION_ACCURACY,
        _HALLUCINATED_CITATION_RATE,
        _NUMERICAL_CONSISTENCY,
        _FIGURE_PLAUSIBILITY,
        _CITED_FIGURE_AGREEMENT,
    ):
        score = _score(metrics, name)
        if score is None or score.passed is not False:
            continue
        evidence.append(
            f"{name} scored {_shown(score.value)} against a threshold of {_shown(score.threshold)}"
        )
        evidence.extend(score.failures[: _EVIDENCE_CAP - len(evidence)])
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.VALIDATION_FAILURE,
        message=(
            "A validator failed this run. The scores and the named failures are in the "
            "validation table; approving over a failed validation is a recorded decision."
        ),
        evidence=_capped(evidence),
    )


def _suspicious_source(sources: tuple[SourceScene, ...]) -> FiredTrigger | None:
    """The injection heuristics flagged a document this run acquired.

    §2.4's other half — "domain not on allowlist" — is enforced at fetch time, in code:
    only :mod:`aer.fetch` makes outbound requests and it refuses off-policy hosts, so an
    off-list domain cannot become a source row for this trigger to read. The flag half is
    the one that reaches the recorded rows.
    """
    evidence = [
        f"'{row.name}' tripped the injection heuristics" for row in sources if row.injection_flagged
    ]
    if not evidence:
        return None
    return FiredTrigger(
        kind=TriggerKind.SUSPICIOUS_SOURCE,
        message=(
            "A source document contains patterns consistent with a prompt-injection "
            "attempt. Its content was handled as data, never as instruction; the "
            "flagged passages are recorded on the source for review."
        ),
        evidence=_capped(evidence),
    )


# ==========================================================================================
# Helpers
# ==========================================================================================


def _score(metrics: tuple[MetricScore, ...], name: str) -> MetricScore | None:
    return next((score for score in metrics if score.metric == name), None)


def _shown(value: Decimal | None) -> str:
    """A score for a banner line. Normalised so 0.980000 reads as 0.98."""
    if value is None:
        return "?"
    text = str(value.normalize())
    # `Decimal.normalize` turns 100 into 1E+2; a banner should never say that.
    return str(value) if "E" in text else text


def _capped(evidence: list[str]) -> tuple[str, ...]:
    if len(evidence) <= _EVIDENCE_CAP:
        return tuple(evidence)
    kept = evidence[: _EVIDENCE_CAP - 1]
    kept.append(f"and {len(evidence) - len(kept)} more")
    return tuple(kept)
