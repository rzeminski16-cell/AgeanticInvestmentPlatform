"""The evaluation harness: the platform's guarantees, expressed as numbers with thresholds.

Every claim this project makes has been proved once, by a test written the day the feature
landed. That is not the same as being true tomorrow. §2.10 of ``docs/PLAN.md`` names the
measurements that must keep holding, and this package computes six of them as a **blocking
gate**: a regression in any one fails the build rather than appearing in a report nobody
reads.

The metrics are library code rather than test code because they outlive Phase 2. In Phase 3
the same functions run against a live report and write ``evaluations`` rows; the corpora
change, the arithmetic does not.

**Nothing here fetches or infers.** A metric takes observations somebody else gathered by
running the real code, and reduces them to a number and a verdict. Keeping the gathering
out means the metric can be unit-tested against handwritten observations, and means the
gate cannot accidentally become "the metric agrees with itself".
"""

from __future__ import annotations

from aer.eval.metrics import (
    THRESHOLDS,
    Direction,
    Metric,
    MetricResult,
    citation_accuracy,
    evaluate_all,
    hallucinated_citation_rate,
    injection_resistance,
    look_ahead_recall,
    temporal_compliance,
    unit_integrity,
)
from aer.eval.observations import (
    CitationObservation,
    InjectionObservation,
    SourceObservation,
    UnitObservation,
)

__all__ = [
    "THRESHOLDS",
    "CitationObservation",
    "Direction",
    "InjectionObservation",
    "Metric",
    "MetricResult",
    "SourceObservation",
    "UnitObservation",
    "citation_accuracy",
    "evaluate_all",
    "hallucinated_citation_rate",
    "injection_resistance",
    "look_ahead_recall",
    "temporal_compliance",
    "unit_integrity",
]
