"""The run-time metric arithmetic: §2.10's definitions applied to a live run's own rows.

The CI gate (:mod:`aer.eval.metrics`) scores the *machinery* against labelled corpora —
"did the verifier reach the verdict the fixture knows is right?". A live run has no
labels; what it has is its own recorded verdicts, sources, sections and claims, and §2.10
words each run-time metric directly over those: verified ÷ total, admissible ÷ used,
covered ÷ generated. This module is that arithmetic — the same
:class:`~aer.eval.metrics.MetricResult`, the same thresholds, the same quantisation —
kept pure so a validator can be tested against handwritten rows exactly as the gate's
metrics are.

Temporal compliance and look-ahead recall need nothing new: the fixture semantics of
:class:`~aer.eval.observations.SourceObservation` are the platform's own quarantine rules
(undated and post-dated sources are inadmissible under point-in-time), so a live run's
sources build the same observations and reuse the same functions.

**An empty population raises**, exactly as the gate's metrics do — and the caller decides
what that means. For CI it is a broken fixture and a red build; for a live run it is a
metric with nothing to measure, recorded as *not exercised* rather than as a pass, which
is the evaluations table's NULL verdict.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from aer.calc.plausibility import FigureScene, impossible_relations
from aer.core.section_output import gap_sentences
from aer.eval.metrics import (
    THRESHOLDS,
    EmptyCorpusError,
    Metric,
    MetricResult,
    ratio,
)

__all__ = [
    "PRIMARY_TIER_RANK",
    "RunCitation",
    "SectionCoverage",
    "SourcedClaim",
    "figure_plausibility",
    "presentation_integrity",
    "primary_source_ratio",
    "run_citation_accuracy",
    "run_hallucinated_citation_rate",
    "source_coverage",
]

# §2.10's primary-source ratio counts "numeric claims from Tier ≤ 4". Distinct from the
# tier-1-or-2 bar the coverage metric uses for "primary source" — the ratio's question is
# whether a figure has an authoritative record anywhere behind it, and licensed market
# data (tier 4) is authoritative for prices.
PRIMARY_TIER_RANK: Final = 4


@dataclass(frozen=True, slots=True)
class RunCitation:
    """One citation the run recorded, with the verifier's verdict on it.

    ``excerpt_found`` is the comparison's own outcome, separate from ``verified``: a
    citation can fail verification for admissibility reasons (a quarantined source, a
    post-dated document) with the excerpt itself perfectly real. ``None`` means the
    comparison never ran.
    """

    name: str
    verified: bool
    excerpt_found: bool | None = None
    ratio: str | None = None
    error: str | None = None


def run_citation_accuracy(rows: list[RunCitation]) -> MetricResult:
    """§2.10's run-time wording exactly: verified citations ÷ total citations.

    Against a live run this is the right number, where against a corpus it was not: the
    corpus check needs fabrications to catch a verifier that says yes to everything, and
    a live run's protection against that verifier is the corpus check itself, running in
    CI on the same code.
    """
    _require(Metric.CITATION_ACCURACY, rows)

    verified = sum(1 for row in rows if row.verified)
    threshold, direction = THRESHOLDS[Metric.CITATION_ACCURACY]
    return MetricResult(
        metric=Metric.CITATION_ACCURACY,
        value=ratio(verified, len(rows)),
        threshold=threshold,
        direction=direction,
        population=len(rows),
        failures=tuple(_describe(row) for row in rows if not row.verified),
    )


def run_hallucinated_citation_rate(rows: list[RunCitation]) -> MetricResult:
    """Citations whose excerpt could not be found in the artefact ÷ total. Must be zero.

    The §2.10 definition — "citations whose excerpt does not exist in the artefact" —
    decided by the deterministic comparison alone. A citation refused for admissibility
    is not counted here: its excerpt may be real, and the temporal metrics own that
    failure. One that failed the *match* is the hallucination shape, whatever an LLM
    might say in its defence.
    """
    _require(Metric.HALLUCINATED_CITATION_RATE, rows)

    fabricated = [row for row in rows if row.excerpt_found is False]
    threshold, direction = THRESHOLDS[Metric.HALLUCINATED_CITATION_RATE]
    return MetricResult(
        metric=Metric.HALLUCINATED_CITATION_RATE,
        value=ratio(len(fabricated), len(rows)),
        threshold=threshold,
        direction=direction,
        population=len(rows),
        failures=tuple(_describe(row) for row in fabricated),
    )


def _describe(row: RunCitation) -> str:
    margin = f", ratio {row.ratio}" if row.ratio else ""
    reason = row.error or "did not verify"
    return f"{row.name} ({reason}{margin})"


@dataclass(frozen=True, slots=True)
class SectionCoverage:
    """One generated section, and the evidence actually standing behind it.

    ``min_sources`` and ``requires_primary`` are the section's own floor: the built-in
    floor for a built-in section, the pinned composed policy for a custom one — which is
    how §2.10's "custom sections held to their own evidence_policy" is applied without
    this module knowing what a skill is.
    """

    name: str
    generated: bool
    distinct_sources: int
    has_primary: bool
    min_sources: int = 1
    requires_primary: bool = True

    @property
    def covered(self) -> bool:
        if self.distinct_sources < self.min_sources:
            return False
        return self.has_primary if self.requires_primary else True

    @property
    def shortfall(self) -> str:
        parts: list[str] = []
        if self.distinct_sources < self.min_sources:
            parts.append(f"cites {self.distinct_sources} of {self.min_sources} source(s)")
        if self.requires_primary and not self.has_primary:
            parts.append("no primary source")
        return "; ".join(parts)


def source_coverage(rows: list[SectionCoverage]) -> MetricResult:
    """Sections meeting their evidence floor ÷ all generated sections. Must be ≥ 90%.

    Only generated sections count in either direction: a failed or skipped section is a
    different visible state with its own accounting, and letting it into this denominator
    would double-punish a failure the run already surfaced.
    """
    generated = [row for row in rows if row.generated]
    if not generated:
        message = (
            "Source coverage was measured over a run with no generated sections. "
            "There was nothing to hold to an evidence floor."
        )
        raise EmptyCorpusError(message, context={"metric": Metric.SOURCE_COVERAGE.value})

    covered = [row for row in generated if row.covered]
    threshold, direction = THRESHOLDS[Metric.SOURCE_COVERAGE]
    return MetricResult(
        metric=Metric.SOURCE_COVERAGE,
        value=ratio(len(covered), len(generated)),
        threshold=threshold,
        direction=direction,
        population=len(generated),
        failures=tuple(f"{row.name} ({row.shortfall})" for row in generated if not row.covered),
    )


@dataclass(frozen=True, slots=True)
class SourcedClaim:
    """One numeric claim, and the most authoritative tier found behind it.

    ``best_tier_rank`` is the lowest tier number among the sources supporting the claim —
    through its citations and through the source document of the fact it names — or
    ``None`` where nothing tiered stands behind it at all.
    """

    name: str
    best_tier_rank: int | None

    @property
    def primary_sourced(self) -> bool:
        return self.best_tier_rank is not None and self.best_tier_rank <= PRIMARY_TIER_RANK


def primary_source_ratio(rows: list[SourcedClaim]) -> MetricResult:
    """Numeric claims resting on tier ≤ 4 evidence ÷ all numeric claims. Must be ≥ 60%."""
    _require(Metric.PRIMARY_SOURCE_RATIO, rows)

    sourced = [row for row in rows if row.primary_sourced]
    threshold, direction = THRESHOLDS[Metric.PRIMARY_SOURCE_RATIO]
    return MetricResult(
        metric=Metric.PRIMARY_SOURCE_RATIO,
        value=ratio(len(sourced), len(rows)),
        threshold=threshold,
        direction=direction,
        population=len(rows),
        failures=tuple(
            f"{row.name} ("
            + (
                "no tiered source behind it"
                if row.best_tier_rank is None
                else f"best tier {row.best_tier_rank}"
            )
            + ")"
            for row in rows
            if not row.primary_sourced
        ),
    )


def _require(metric: Metric, rows: Sequence[object]) -> None:
    if rows:
        return
    message = (
        f"{metric.value} was measured over a run with nothing in its population. "
        "The caller records that as not exercised, never as a pass."
    )
    raise EmptyCorpusError(message, context={"metric": metric.value})


# ==========================================================================================
# Presentation integrity (gap O3)
# ==========================================================================================
#
# The defects below each shipped in a live note once — an eleven-digit integer mid-table,
# literal asterisks where emphasis was meant, "Evidence: ef2bd367-…" in the appendix, a
# section spending its length on what the evidence lacks. Each was fixed at its source;
# this metric is what keeps every fix permanent, the way citation_accuracy holds
# provenance. Zero, not "low".

# A reader-visible run of five or more bare digits: no separators, no symbol, not part of
# a word ("FY2026"), not a decimal's fraction. Four digits stay legal — years.
_BARE_INTEGER: Final[re.Pattern[str]] = re.compile(r"(?<![\w.,$£€/-])\d{5,}(?![\w,.%-])")

_UUID: Final[re.Pattern[str]] = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)

# What the defect scan must not read: link targets and autolinks (a SEC path carries long
# digit runs), and code spans (an artefact digest or code version is deliberately literal).
_INVISIBLE: Final[re.Pattern[str]] = re.compile(r"<https?://[^>]+>|\]\([^)]+\)|`[^`]*`")


def presentation_integrity(markdown: str, html: str, *, sections: int) -> MetricResult:
    """The rendered document's presentation defects, counted — threshold zero.

    Args:
        markdown: The document's Markdown notation. Integers, UUIDs and gap sentences
            are counted here, after link targets and code spans are removed.
        html: The HTML notation. Literal ``**`` is counted here and only here — in
            Markdown a paired asterisk run is notation, in HTML it is a leak.
        sections: How many sections the document renders. One gap sentence per section
            is the drafting budget (gap R4); anything past that is a defect.
    """
    if not markdown.strip():
        message = "presentation_integrity was asked to score an empty document."
        raise EmptyCorpusError(message, context={"metric": Metric.PRESENTATION_INTEGRITY.value})

    visible = _INVISIBLE.sub(" ", markdown)
    failures: list[str] = []

    failures.extend(f"unformatted integer {found!r}" for found in _BARE_INTEGER.findall(visible))
    failures.extend(f"raw UUID {found!r}" for found in _UUID.findall(visible))

    asterisks = html.count("**")
    if asterisks:
        failures.append(f"literal '**' appears {asterisks} time(s) in the rendered HTML")

    gaps = len(gap_sentences({"text": visible}))
    if gaps > sections:
        failures.append(f"{gaps} gap sentences against {sections} sections (one each allowed)")

    threshold, direction = THRESHOLDS[Metric.PRESENTATION_INTEGRITY]
    return MetricResult(
        metric=Metric.PRESENTATION_INTEGRITY,
        value=Decimal(len(failures)),
        threshold=threshold,
        direction=direction,
        population=max(sections, 1),
        failures=tuple(failures),
    )


def figure_plausibility(scenes: tuple[FigureScene, ...]) -> MetricResult:
    """The count of impossible relations among the run's headline figures — threshold zero.

    Gap A61's metric. The MTB run published a 172.1% net margin from a revenue concept
    resolved to a partial caption, and every other metric passed, because every other
    metric asks whether a figure is *recorded* correctly — consistently replayed,
    correctly cited, admissibly dated — and none asks whether it is *possible*. This one
    does, over :func:`aer.calc.plausibility.impossible_relations`' closed set: income
    above revenue, a margin above one, turnover below the floor on a large balance
    sheet. Each finding names the period and the values, so the failure row reads as an
    argument rather than a count.

    A run with no headline figures at all raises, and the caller records the metric as
    not exercised — nothing to measure is a fact about the run, not a pass.
    """
    if not scenes:
        message = "figure_plausibility was asked to score a run holding no headline figures."
        raise EmptyCorpusError(message, context={"metric": Metric.FIGURE_PLAUSIBILITY.value})

    found = impossible_relations(scenes)
    threshold, direction = THRESHOLDS[Metric.FIGURE_PLAUSIBILITY]
    return MetricResult(
        metric=Metric.FIGURE_PLAUSIBILITY,
        value=Decimal(len(found)),
        threshold=threshold,
        direction=direction,
        population=len(scenes),
        failures=tuple(item.statement for item in found),
    )
