"""What a metric is computed from: one row per thing that was actually tried.

Deliberately dumb records. Each carries what the corpus **said should happen** and what the
platform **did**, and nothing else — no verdict, no score, no interpretation. That split is
what stops a metric marking its own homework: the label comes from the fixture, the outcome
comes from running the real code, and the metric only compares them.

Pure: no I/O, no clock, no database. ``mypy --strict``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

__all__ = [
    "CitationObservation",
    "CompletenessObservation",
    "InjectionObservation",
    "ReplayObservation",
    "SourceObservation",
    "UnitObservation",
]


@dataclass(frozen=True, slots=True)
class CitationObservation:
    """One claim/excerpt pair put to the verifier.

    ``genuine`` is the fixture's label: does this excerpt really appear at this locator in
    this document? ``verified`` is what the verifier concluded. Everything the citation
    metrics say is a comparison of those two.
    """

    name: str
    genuine: bool
    verified: bool

    # Kept for the failure report rather than for the arithmetic. "0.94 against a threshold
    # of 0.95" and "0.02" are the same verdict and completely different problems.
    ratio: str | None = None
    error: str | None = None

    @property
    def correct(self) -> bool:
        return self.genuine == self.verified

    @property
    def is_false_positive(self) -> bool:
        """A fabricated excerpt the verifier accepted.

        The hallucinated citation: a claim in a report, pointing at a document, supported by
        words that are not in it, and marked verified. Nothing downstream would catch it.
        """
        return self.verified and not self.genuine

    @property
    def is_false_negative(self) -> bool:
        """A genuine excerpt the verifier refused.

        Costly but not dangerous: it blocks a gate, an operator looks, and the record says
        the check failed. Counted separately for exactly that reason.
        """
        return self.genuine and not self.verified


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One document put through the point-in-time rules.

    ``published`` is the fixture's answer for when it was actually published — ``None``
    where nothing establishes a date. ``admitted`` is whether the platform would let it
    support a claim.
    """

    name: str
    published: date | None
    as_of: date
    admitted: bool

    # What the extractor concluded, kept for the report. A document refused for the wrong
    # reason still counts as refused, and the difference is worth being able to see.
    established: date | None = None

    @property
    def is_after_as_of(self) -> bool:
        """Whether this document postdates the as-of date, per the fixture's label."""
        return self.published is not None and self.published > self.as_of

    @property
    def must_be_refused(self) -> bool:
        """Undatable or post-dated. Both are inadmissible under point-in-time rules."""
        return self.published is None or self.is_after_as_of

    @property
    def is_violation(self) -> bool:
        """A document that should have been refused and was not."""
        return self.must_be_refused and self.admitted


@dataclass(frozen=True, slots=True)
class InjectionObservation:
    """One poisoned document put through acquisition and extraction.

    Resistance is **not** measured by whether the scanner spotted the payload. Detection is
    a warning; containment is the control (ADR 0019). What is measured is whether anything
    the payload asked for actually happened.
    """

    name: str

    # The document reached the model as quoted, delimited data rather than as prose that
    # could read as instruction.
    contained: bool

    # No tool call outside the acting role's allowlist was authorised while this document
    # was in context. This is the §2.10 metric: tool-policy violations ÷ attempts.
    tools_authorised_outside_policy: int = 0

    # Whether the heuristics noticed. Reported, never gating: a payload the scanner misses
    # is still contained, and gating on detection would push the codebase towards a
    # detector that fires on everything.
    detected: bool = False

    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_violation(self) -> bool:
        return not self.contained or self.tools_authorised_outside_policy > 0


@dataclass(frozen=True, slots=True)
class UnitObservation:
    """One arithmetic operation on quantities whose units do not agree.

    ``raised`` is whether the unit algebra refused it. A silent coercion is the failure:
    invariant 5 says a unit mismatch raises, and a result carrying the wrong unit is a
    number that looks right everywhere downstream.
    """

    name: str
    compatible: bool
    raised: bool

    @property
    def is_violation(self) -> bool:
        """A mismatch that produced a value instead of an error."""
        return not self.compatible and not self.raised


@dataclass(frozen=True, slots=True)
class ReplayObservation:
    """One stored calculation re-run from its own record.

    ``expected`` is what the ledger holds; ``replayed`` is what re-executing the named
    function on the stored inputs produced now. ``error`` carries the reason where the
    replay could not run at all — a function that no longer exists, an input that cannot be
    reconstructed — which is a failure of the record, not a case to skip.
    """

    name: str
    expected: Decimal
    expected_unit: str
    replayed: Decimal | None
    replayed_unit: str | None
    error: str | None = None

    @property
    def delta(self) -> Decimal:
        """Relative distance from the stored figure. Infinite where nothing replayed.

        Infinite rather than absent, so a record that cannot be re-run fails a threshold
        instead of vanishing from the maximum.
        """
        from aer.eval.replay import relative_delta  # noqa: PLC0415 -- avoids a module cycle

        if self.replayed is None:
            return Decimal("Infinity")
        return relative_delta(self.expected, self.replayed)

    @property
    def unit_matches(self) -> bool:
        """Whether the replay produced the stored unit.

        A replay that reproduces the number in a different unit has not reproduced the
        calculation — 0.05 pure and 0.05 USD are different claims with the same digits.
        """
        return self.replayed_unit == self.expected_unit


@dataclass(frozen=True, slots=True)
class CompletenessObservation:
    """One calculation's assumption inputs, resolved against the assumptions table as it
    stands now.

    ``unresolved`` are cited ids that no longer match a row; ``unconfirmed`` are rows that
    exist but nobody has (or any longer has) agreed to. Re-proposing an assumption withdraws
    its approval, so the second set is how a report whose basis was pulled out from under it
    gets noticed.
    """

    name: str
    assumption_ids: tuple[str, ...]
    unresolved: tuple[str, ...] = ()
    unconfirmed: tuple[str, ...] = ()

    @property
    def rests_on_assumptions(self) -> bool:
        return bool(self.assumption_ids)

    @property
    def is_complete(self) -> bool:
        return not self.unresolved and not self.unconfirmed
