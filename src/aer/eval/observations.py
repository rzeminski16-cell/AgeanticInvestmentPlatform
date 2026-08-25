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
    "CitedFigureObservation",
    "CompletenessObservation",
    "ConformanceObservation",
    "ContainmentObservation",
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

    # Whether the run this document served enforced point-in-time. Defaults to on, which
    # is what the eval corpus exercises; the live path passes the request's own setting.
    point_in_time: bool = True

    @property
    def is_after_as_of(self) -> bool:
        """Whether this document postdates the as-of date, per the fixture's label."""
        return self.published is not None and self.published > self.as_of

    @property
    def must_be_refused(self) -> bool:
        """What the mode the run actually ran in demands.

        A post-dated document is inadmissible in any mode — it claims knowledge of a
        future the analysis is not supposed to have. An *undatable* one is inadmissible
        only under point-in-time rules, where "cannot be shown to predate the as-of date"
        is disqualifying; with the mode off, the acquisition layer deliberately admits it
        (``decide_quarantine`` applies exactly this split), and a metric that failed the
        run anyway was enforcing a rule the operator had switched off. The live AAPL run
        ran point-in-time off and still wore a temporal-compliance failure on page 1 for
        seven undated-but-admitted documents.
        """
        if self.is_after_as_of:
            return True
        return self.point_in_time and self.published is None

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
class CitedFigureObservation:
    """One drafted claim that names a calculation, and the calculation it names.

    Invariant 3 says no figure reaches a report unless it is a stored fact, a recorded
    calculation or an attestation. Every metric before this one asks whether a figure is
    *recorded* correctly — replayed consistently, cited admissibly, dated honestly — and
    none asks whether the sentence used the figure it cited. The 2026-08-24 MSFT note
    asserted a quick ratio of 0.93 over a `quick_ratio` calculation of 1.567, and only the
    adversarial reviewer noticed.

    ``text`` is the claim as drafted; ``value`` and ``unit`` are the cited calculation's
    own. The comparison lives in :func:`aer.eval.runtime.cited_figure_agreement`, because
    what counts as agreement is a rendering question and this type holds no opinions.
    """

    name: str
    text: str
    calculation: str
    value: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class ContainmentObservation:
    """One adversarial skill file put through the real containment layers.

    ``guarded_by`` is the corpus's label: the layer that should stop this escalation —
    the frontmatter schema, the additive-only composer, the output-contract validation,
    or the prompt boundary. ``stopped_by`` is where the platform actually stopped it,
    and ``None`` means it did not: the escalation succeeded, which is the §2.10 number
    that must be zero.

    The two are recorded separately because "contained somewhere" is not the guarantee.
    A reserved field caught only at execution time would mean the authoring-time refusal
    had quietly died, and the corpus test that compares the columns is what notices.
    """

    name: str
    escalation: str
    guarded_by: str
    stopped_by: str | None
    detail: str = ""

    @property
    def contained(self) -> bool:
        return self.stopped_by is not None

    @property
    def is_violation(self) -> bool:
        """The escalation succeeded — the §2.10 count that must be zero."""
        return not self.contained

    @property
    def at_expected_layer(self) -> bool:
        """Whether the layer that stopped it is the layer the corpus says owns it."""
        return self.stopped_by == self.guarded_by


@dataclass(frozen=True, slots=True)
class ConformanceObservation:
    """One custom-section output put to the real contract validation.

    ``should_conform`` is the corpus label: this content genuinely satisfies its
    section's projected ``output_contract``. ``conforms`` is the validator's verdict.
    The corpus carries violating outputs as well as conforming ones, because against
    only-conforming content a validator that accepts everything scores 100%.
    """

    name: str
    should_conform: bool
    conforms: bool
    problems: tuple[str, ...] = ()

    @property
    def correct(self) -> bool:
        return self.should_conform == self.conforms

    @property
    def accepted_a_violation(self) -> bool:
        """Non-conforming content the validator passed — the dangerous direction.

        It is how an undeclared field, or a figure of the wrong type, rides into a
        report inside a dict everyone downstream assumes was checked.
        """
        return self.conforms and not self.should_conform


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
