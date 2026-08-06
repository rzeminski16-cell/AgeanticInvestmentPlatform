"""Two sources, one number, and no silent winner.

Sooner or later two admissible sources report different values for the same thing. The
failure mode this module exists to prevent is not "the wrong one was chosen" — it is that
one was chosen and **nobody was told**. A report whose revenue figure came from the second
of two disagreeing filings, with no record that the first existed, is a report whose
reader cannot audit the one decision that mattered.

So every comparison produces a :class:`Resolution` naming the rung that fired, and the
losing position is retained rather than discarded. Where the ladder cannot decide, it says
so, and the decision goes to a human at gate 2.

## The ladder

Evaluated in order. The order is the design; the rungs are almost obvious once it is
fixed.

===  ===================================================  ==========================
 #   Condition                                             Outcome
===  ===================================================  ==========================
 0   Units differ and cannot be reconciled                 escalate
 1   Values agree within tolerance                         agree, nothing recorded
 2   Values differ by a clean power of ten                 escalate
 3   Tiers differ                                          lower tier number wins
 4   Same tier, different basis                            escalate
 5   Same tier, same basis, different filed date           later filing wins
 6   Same tier, same basis, same filed date                escalate
===  ===================================================  ==========================

**Rung 0 comes first because everything after it compares numbers.** A unit mismatch is
not a disagreement about a quantity; it is a question about two different quantities, and
nothing further down the ladder is meaningful until it is answered. Nothing is coerced —
invariant 5 says a unit mismatch raises rather than converting, and a resolver that quietly
turned GBP into USD would be that failure wearing a different hat.

**Rung 2 comes before the tier rung, and that is the one placement worth arguing about.**
Suppose a tier-1 filing yields 245,122 and a tier-2 report yields 245,122,000,000, both
labelled USD. The tier rung would pick the tier-1 figure and be *wrong*: the two are a
factor of a million apart, so one of them was mis-parsed, and a million-fold parsing bug is
not evidence about which publisher is more reliable. Resolving it by tier would take a
defect and give it a provenance record saying the regulator said so. It goes to a human,
who can look at both documents and see which parser lost a scale factor.

**Rung 4 before rung 5** for a smaller version of the same reason: an as-reported figure
and a restatement of the same period are both true, of different questions. Choosing the
later one silently is how a backtest starts flattering itself.

## What this module is not

It is **not** point-in-time selection. :func:`aer.sources.sec.pit.select_point_in_time`
decides which facts are admissible as at a date; this decides between the ones that
already are. Feeding it a restatement is possible — hence rung 4 — but that is a safety
net, not the intended input.

It is **not** a judgement about whether a figure matters. See :attr:`Resolution.material`.

Pure and side-effect free: no I/O, no clock, no database. The service layer in
:mod:`aer.services.disagreements` turns a :class:`Resolution` into a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from aer.core.enums import FactBasis, SourceTier
from aer.errors import AerError

__all__ = [
    "AGREEMENT_TOLERANCE",
    "MATERIALITY_THRESHOLD",
    "DisagreementKind",
    "Position",
    "Resolution",
    "ResolutionOutcome",
    "ResolutionRule",
    "ResolvedBy",
    "UnresolvableDisagreementError",
    "canonical_unit",
    "relative_difference",
    "resolve",
    "thesis_conflict",
]


class DisagreementKind(StrEnum):
    """What sort of thing the two positions disagree about."""

    SOURCE_CONFLICT = "source_conflict"
    """Two sources report different values for the same measured thing."""

    CALCULATION_CONFLICT = "calculation_conflict"
    """Two routes to the same figure — a reported total against the sum of its parts,
    say — do not agree."""

    THESIS_CONFLICT = "thesis_conflict"
    """The red team's conclusion against the base thesis. Never auto-resolved."""


class ResolvedBy(StrEnum):
    """Who or what settled it.

    Separate from :class:`ResolutionRule`, which names *which* rule fired.
    ``docs/PLAN.md`` puts both in one ``resolved_by TEXT`` column; splitting them makes
    each queryable, and "how many of our conflicts did a human have to settle?" is the
    question worth being able to ask.
    """

    RULE = "rule"
    """The deterministic ladder in this module."""

    HUMAN = "human"
    """An operator, at a gate."""

    AGENT = "agent"
    """A model. Only ever after the ladder declined to decide, and never for a figure."""


class ResolutionOutcome(StrEnum):
    """What happened to a disagreement."""

    AGREED = "agreed"
    """The two positions say the same thing. Nothing is recorded."""

    CHOSE_A = "chose_a"
    """Position A won under a rule. B is retained as losing evidence."""

    CHOSE_B = "chose_b"
    """Position B won under a rule. A is retained as losing evidence."""

    ESCALATED = "escalated"
    """No rule decides this. It goes to a human at gate 2, with both positions."""


class ResolutionRule(StrEnum):
    """Which rung of the ladder fired.

    Stored rather than the prose rationale alone, so "how often does the tier rule decide
    our numbers?" is a query. A rationale is for reading; this is for counting.
    """

    UNIT_MISMATCH = "unit_mismatch"
    VALUES_AGREE = "values_agree"
    SUSPECTED_SCALE_ERROR = "suspected_scale_error"
    LOWER_TIER_WINS = "lower_tier_wins"
    BASIS_MISMATCH = "basis_mismatch"
    LATER_FILING_WINS = "later_filing_wins"
    SAME_TIER_SAME_DATE = "same_tier_same_date"
    THESIS_CONFLICT = "thesis_conflict"


class UnresolvableDisagreementError(AerError):
    """The ladder reached the end without a rung firing.

    Not a data condition — a defect. Every pair of positions matches exactly one rung, and
    a test exhausts the input space to prove it. Raising rather than returning a default is
    the whole point: a "no rule applied, so we picked one" outcome is precisely the silent
    winner this module exists to prevent, and it would be indistinguishable from a rule
    having fired.
    """

    code = "disagreement_unresolvable"


# Two sources reporting the same figure rarely produce identical decimals: one rounds to
# the nearest million, the other carries units. Five basis points absorbs presentation
# rounding on any figure a filing states and is far below anything a reader would call a
# difference.
AGREEMENT_TOLERANCE: Final = Decimal("0.0005")

# The credible-source conflict threshold from `docs/PLAN.md` section 2.4.
MATERIALITY_THRESHOLD: Final = Decimal("0.02")

# How close a ratio must sit to a power of ten before it is called a scale error rather
# than a difference. Tight on purpose: a genuine 10.3x disagreement is a disagreement, and
# reporting it as a parsing bug would send a reviewer looking for the wrong thing.
_SCALE_TOLERANCE: Final = Decimal("0.005")

# The range of the `scale` check constraint on `financial_facts`. Outside it a ratio is not
# a rescaled number, it is coincidence.
_SCALE_POWERS: Final = tuple(range(1, 13))

# Below this the conflict is not a credible-source conflict in the sense section 2.4 means:
# a newspaper contradicting a filing is expected, and banners for it would be ignored.
_CREDIBLE_TIER_LIMIT: Final = 4


@dataclass(frozen=True, slots=True)
class Position:
    """One source's answer, and everything the ladder needs to weigh it.

    ``reference`` is the stable identity of whatever asserted this — a financial-fact id, a
    calculation id, a source-document id. It is what a reader follows to get back to the
    document, and what makes the same disagreement recognisable when the run is repeated.
    """

    reference: str
    label: str
    value: Decimal
    unit: str
    tier: SourceTier
    filed_date: date
    basis: FactBasis = FactBasis.AS_REPORTED

    # The power of ten the *source* presented the figure in. Provenance only: `value` is
    # absolute, per `financial_facts.scale`. Carried so a scale-error escalation can show a
    # reviewer what each document actually said.
    scale: int = 0

    def as_record(self) -> dict[str, object]:
        """The form stored in ``disagreements.position_a`` / ``position_b``.

        Both positions are stored whatever the outcome, including the losing one. A record
        of a decision that does not include what was rejected is a record of nothing.
        """
        return {
            "reference": self.reference,
            "label": self.label,
            "value": str(self.value),
            "unit": self.unit,
            "tier": self.tier.value,
            "filed_date": self.filed_date.isoformat(),
            "basis": self.basis.value,
            "scale": self.scale,
        }


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of one comparison, and the argument for it."""

    outcome: ResolutionOutcome
    rule: ResolutionRule
    rationale: str

    # Canonically ordered; see `resolve`. `position_a` is not "the one passed first".
    position_a: Position
    position_b: Position

    # ``None`` where the values are not comparable at all — a unit mismatch, or a thesis
    # conflict, neither of which has a numeric distance.
    relative_difference: Decimal | None

    # A credible-source conflict as `docs/PLAN.md` section 2.4 defines it: both positions
    # at tier 4 or better, differing by more than 2%.
    #
    # Section 2.4 says "on a material figure", and that half is deliberately **not**
    # decided here. Whether a figure matters depends on what the report leans on, which the
    # ladder cannot see. This flag raises the banner; a person decides what it means.
    material: bool

    @property
    def escalates(self) -> bool:
        return self.outcome is ResolutionOutcome.ESCALATED

    @property
    def is_recordable(self) -> bool:
        """Whether this produces a ``disagreements`` row.

        Agreement does not. Rung 1 is the ordinary case — two sources saying the same
        thing — and a row per agreeing pair would bury the rows that mean something.
        """
        return self.outcome is not ResolutionOutcome.AGREED

    @property
    def winner(self) -> Position | None:
        """The position that won, if one did."""
        if self.outcome is ResolutionOutcome.CHOSE_A:
            return self.position_a
        if self.outcome is ResolutionOutcome.CHOSE_B:
            return self.position_b
        return None

    @property
    def loser(self) -> Position | None:
        """The position that lost, retained for the report's disagreement appendix."""
        if self.outcome is ResolutionOutcome.CHOSE_A:
            return self.position_b
        if self.outcome is ResolutionOutcome.CHOSE_B:
            return self.position_a
        return None


def canonical_unit(unit: str) -> str:
    """A unit string reduced to what actually distinguishes it.

    Case and surrounding space only. Deliberately not a unit *parser*: ``aer.calc.units``
    owns unit algebra, and a second, looser notion of "same unit" living here would be a
    place for the two to disagree about whether ``USD`` and ``usd`` are the same thing
    while each was individually correct.
    """
    return unit.strip().casefold()


def relative_difference(first: Decimal, second: Decimal) -> Decimal:
    """How far apart two values are, as a fraction of the larger magnitude.

    Symmetric, so the answer does not depend on which position was passed first — the
    property that lets :func:`resolve` reorder its arguments freely. Zero against zero is
    zero; zero against anything else is 1, which reads as "completely different" and is
    the only honest answer when there is no scale to measure against.
    """
    largest = max(abs(first), abs(second))
    if largest == 0:
        return Decimal(0)
    return abs(first - second) / largest


def resolve(first: Position, second: Position) -> Resolution:  # noqa: PLR0911
    """Apply the ladder to two positions.

    Seven exits, one per rung, and deliberately not fewer. Splitting the ladder across
    helpers to satisfy a return count would put the one thing in this module worth reading
    in two places, and the order of the rungs *is* the design.

    **The result does not depend on argument order.** The two are put into a canonical
    order first — most authoritative tier, then earliest filing, then reference — so that
    the same pair compared twice produces the same row with the same winner, whichever way
    round the caller happened to hold them. Without this, running the same conflict through
    twice would write two rows disagreeing about which position was "A".

    Raises:
        UnresolvableDisagreementError: If no rung fired. A defect, never a data condition;
            see the exception's docstring.
    """
    position_a, position_b = _canonical_order(first, second)

    unit_a = canonical_unit(position_a.unit)
    unit_b = canonical_unit(position_b.unit)

    # Rung 0. Before any arithmetic, because there is no arithmetic to do.
    if unit_a != unit_b:
        return Resolution(
            outcome=ResolutionOutcome.ESCALATED,
            rule=ResolutionRule.UNIT_MISMATCH,
            rationale=(
                f"{position_a.label} reports {position_a.unit} and {position_b.label} "
                f"reports {position_b.unit}. These are different quantities, not different "
                "answers, and nothing here converts between them."
            ),
            position_a=position_a,
            position_b=position_b,
            relative_difference=None,
            material=True,
        )

    difference = relative_difference(position_a.value, position_b.value)
    material = _is_credible_source_conflict(position_a, position_b, difference)

    # Rung 1. The ordinary case, and the only one that records nothing.
    if difference <= AGREEMENT_TOLERANCE:
        return _resolution(
            ResolutionOutcome.AGREED,
            ResolutionRule.VALUES_AGREE,
            (
                f"{position_a.label} and {position_b.label} agree on "
                f"{position_a.value} {position_a.unit} to within "
                f"{AGREEMENT_TOLERANCE:.2%}."
            ),
            position_a,
            position_b,
            difference=difference,
            material=material,
        )

    # Rung 2. Ahead of the tier rung; see the module docstring.
    power = _power_of_ten_apart(position_a.value, position_b.value)
    if power is not None:
        return _resolution(
            ResolutionOutcome.ESCALATED,
            ResolutionRule.SUSPECTED_SCALE_ERROR,
            (
                f"{position_a.label} reports {position_a.value} and {position_b.label} "
                f"reports {position_b.value}, a factor of 10^{power} apart in the same "
                "unit. That is a scale error in one of them rather than a disagreement "
                "about the figure, so neither is preferred until a person says which."
            ),
            position_a,
            position_b,
            difference=difference,
            material=material,
        )

    # Rung 3. The rule the tier numbers exist for.
    if position_a.tier is not position_b.tier:
        # Canonical order puts the better tier first, so A is always the winner here.
        return _resolution(
            ResolutionOutcome.CHOSE_A,
            ResolutionRule.LOWER_TIER_WINS,
            (
                f"{position_a.label} is {position_a.tier.value} and {position_b.label} is "
                f"{position_b.tier.value}. The lower tier number carries more weight, so "
                f"{position_a.value} stands and {position_b.value} is retained as the "
                "rejected position."
            ),
            position_a,
            position_b,
            difference=difference,
            material=material,
        )

    # Rung 4. Same tier from here on.
    if position_a.basis is not position_b.basis:
        return _resolution(
            ResolutionOutcome.ESCALATED,
            ResolutionRule.BASIS_MISMATCH,
            (
                f"{position_a.label} is {position_a.basis.value} and {position_b.label} is "
                f"{position_b.basis.value}. Both can be true of the same period, because "
                "they answer different questions, and preferring one by date would be a "
                "look-ahead decision made by accident."
            ),
            position_a,
            position_b,
            difference=difference,
            material=material,
        )

    # Rung 5. Same tier, same basis: the later filing is the publisher's own correction.
    if position_a.filed_date != position_b.filed_date:
        # Canonical order puts the earlier filing first, so B is the later one.
        return _resolution(
            ResolutionOutcome.CHOSE_B,
            ResolutionRule.LATER_FILING_WINS,
            (
                f"Both are {position_a.tier.value} and {position_a.basis.value}. "
                f"{position_b.label} was filed on {position_b.filed_date.isoformat()}, "
                f"after {position_a.label} on {position_a.filed_date.isoformat()}, so it "
                "is the same publisher's later word on the same basis."
            ),
            position_a,
            position_b,
            difference=difference,
            material=material,
        )

    # Rung 6. Same tier, same basis, same day, different number. Nothing left to prefer by.
    if position_a.value != position_b.value:
        return _resolution(
            ResolutionOutcome.ESCALATED,
            ResolutionRule.SAME_TIER_SAME_DATE,
            (
                f"{position_a.label} and {position_b.label} are both "
                f"{position_a.tier.value}, both {position_a.basis.value}, both filed "
                f"{position_a.filed_date.isoformat()}, and report {position_a.value} "
                f"against {position_b.value}. There is nothing left to prefer one by."
            ),
            position_a,
            position_b,
            difference=difference,
            material=material,
        )

    # Unreachable: rung 1 caught equal values, and rung 6 catches unequal ones. Present so
    # that an edit which opens a hole in the ladder fails loudly rather than inventing a
    # winner. See `UnresolvableDisagreementError`.
    message = (  # pragma: no cover
        "No rung of the disagreement ladder applied. This is a defect in the ladder, not a "
        "property of the data."
    )
    raise UnresolvableDisagreementError(  # pragma: no cover
        message,
        context={"a": position_a.reference, "b": position_b.reference},
    )


def thesis_conflict(
    *, first: Position, second: Position, topic: str, material: bool = True
) -> Resolution:
    """Rung 6 of ``docs/PLAN.md`` section 2.9: the red team against the base thesis.

    Never auto-resolved, by design rather than by omission. A challenge that the system
    itself could dismiss would be a challenge worth nothing; both positions are published
    side by side in the report's disagreement appendix and the reader decides.

    No numeric comparison happens, so there is no relative difference to report.
    ``material`` defaults to true — the point of running a red team is that its findings
    are read — but the caller that knows a challenge's severity may say a low-severity
    quibble does not raise the section 2.4 banner. It is still escalated and still
    published; materiality decides the banner, never the record.
    """
    return Resolution(
        outcome=ResolutionOutcome.ESCALATED,
        rule=ResolutionRule.THESIS_CONFLICT,
        rationale=(
            f"{first.label} and {second.label} reach opposing conclusions on {topic}. A "
            "thesis-level disagreement is never resolved automatically; both are published."
        ),
        position_a=first,
        position_b=second,
        relative_difference=None,
        material=material,
    )


def _canonical_order(first: Position, second: Position) -> tuple[Position, Position]:
    """Order two positions so the comparison is a function of the pair, not the call.

    Better tier first, then earlier filing. The tier and date rungs read this ordering
    directly — "A is the better tier", "B is the later filing" — which is why they can name
    a winner without re-comparing.

    **The key covers every field, not just those three.** Two positions agreeing on tier,
    date and reference but differing in value would otherwise tie, and a tie is settled by
    argument order — which is exactly the property this function exists to remove. The
    rationale names the positions in order, so anything that can appear in a rationale has
    to appear in the key — the **raw** unit rather than the canonical one, because that is
    the string the rationale prints, and ordering ``USD`` and ``usd`` as equal would leave
    the wording decided by argument order.
    """
    key = _ordering_key
    return (first, second) if key(first) <= key(second) else (second, first)


def _ordering_key(position: Position) -> tuple[int, date, str, str, str, str, int, str]:
    return (
        position.tier.rank,
        position.filed_date,
        position.reference,
        str(position.value),
        position.unit,
        position.basis.value,
        position.scale,
        position.label,
    )


def _resolution(
    outcome: ResolutionOutcome,
    rule: ResolutionRule,
    rationale: str,
    position_a: Position,
    position_b: Position,
    *,
    difference: Decimal,
    material: bool,
) -> Resolution:
    return Resolution(
        outcome=outcome,
        rule=rule,
        rationale=rationale,
        position_a=position_a,
        position_b=position_b,
        relative_difference=difference,
        material=material,
    )


def _is_credible_source_conflict(
    position_a: Position, position_b: Position, difference: Decimal
) -> bool:
    both_credible = (
        position_a.tier.rank <= _CREDIBLE_TIER_LIMIT
        and position_b.tier.rank <= _CREDIBLE_TIER_LIMIT
    )
    return both_credible and difference > MATERIALITY_THRESHOLD


def _power_of_ten_apart(first: Decimal, second: Decimal) -> int | None:
    """The exponent by which two values differ, if they differ by a clean power of ten.

    ``None`` when they do not, when either is zero — nothing is a power of ten away from
    zero — or when they have opposite signs, because a sign flip is a different mistake
    and naming it a scale error would misdirect whoever reads the escalation.
    """
    if first == 0 or second == 0 or (first > 0) != (second > 0):
        return None

    larger, smaller = max(abs(first), abs(second)), min(abs(first), abs(second))
    try:
        ratio = larger / smaller
    except (InvalidOperation, ZeroDivisionError):  # pragma: no cover -- smaller is non-zero
        return None

    for power in _SCALE_POWERS:
        expected = Decimal(10) ** power
        if abs(ratio - expected) / expected <= _SCALE_TOLERANCE:
            return power
    return None
