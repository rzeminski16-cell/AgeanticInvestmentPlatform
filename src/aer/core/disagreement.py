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

It is **not** filing selection. :func:`aer.sources.sec.selection.select_latest` decides
which filing's figure stands for a period; this decides between figures from different
sources that already stand. Feeding it a restatement is possible — hence rung 4 — but that
is a safety net, not the intended input.

**The ladder decides between sources, and two of this run's own outputs are not two
sources.** A red team challenging the draft, two published values for one calculated figure,
and a sentence denying a figure the document prints are all comparisons in which no
publisher differs from another, so none of them descends the rungs: each has its own
constructor here — :func:`thesis_conflict`, :func:`figure_contradiction`,
:func:`denied_figure` — which escalates by construction and states its own real reason. A
rung's rationale is the only part of a stored disagreement a reader of the report meets, and
it may not describe a tier contest that never happened. See ADR 0125.

It is **not** a judgement about whether a figure matters. See :attr:`Resolution.material`.

Pure and side-effect free: no I/O, no clock, no database. The service layer in
:mod:`aer.services.disagreements` turns a :class:`Resolution` into a row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final

from aer.core.enums import FactBasis, SourceTier
from aer.errors import AerError

__all__ = [
    "AGREEMENT_TOLERANCE",
    "MATERIALITY_THRESHOLD",
    "THESIS_UNIT",
    "DisagreementKind",
    "Position",
    "Resolution",
    "ResolutionOutcome",
    "ResolutionRule",
    "ResolvedBy",
    "UnresolvableDisagreementError",
    "canonical_unit",
    "challenge_heading",
    "denied_figure",
    "figure_contradiction",
    "position_figure",
    "relative_difference",
    "resolve",
    "spoken_rule",
    "spoken_tier",
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

    SELF_CONTRADICTION = "self_contradiction"
    """The assembled document disagrees with itself (ADR 0125).

    Two published values for one figure at one period, or a sentence denying a figure the
    document prints. Distinct from every kind above because **both sides are this run's own
    output**: there is no second source, so there is nothing to prefer one by, and the
    remedy is a redraft rather than a choice."""


class ResolvedBy(StrEnum):
    """Who or what settled it.

    Separate from :class:`ResolutionRule`, which names *which* rule fired.
    ``docs/archive/PLAN.md`` puts both in one ``resolved_by TEXT`` column; splitting them makes
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
    DOCUMENT_CONTRADICTS_ITSELF = "document_contradicts_itself"

    @property
    def spoken(self) -> str:
        """The rung as the report's own appendix says it, never ``later_filing_wins``.

        The published document printed *"Resolved by rule 'later_filing_wins': position A
        selected"* — an enum value and a positional letter, in the section whose whole job
        is to make the rest of the report trustworthy. A reader meets these words; the
        value is for counting.
        """
        return _RULE_WORDS.get(self, self.value.replace("_", " "))


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

# The credible-source conflict threshold from `docs/archive/PLAN.md` section 2.4.
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


# The unit a thesis-level position carries. A thesis conflict has no quantity — the two
# sides are arguments, not numbers — so the ladder stores a placeholder value of zero
# under this unit and never compares it. Named because four surfaces have to recognise it,
# and a literal repeated four times is a rule nobody owns (gap A68).
THESIS_UNIT: Final = "thesis"


# Each rung as the appendix says it, phrased to sit after a colon and before the position
# that won. Only the rungs that *decide* need a winner's clause; the ones that escalate are
# rendered by their own branch, which says nothing won.
_RULE_WORDS: Final[dict[ResolutionRule, str]] = {
    ResolutionRule.UNIT_MISMATCH: "the two are measured in different units",
    ResolutionRule.VALUES_AGREE: "the two agree",
    ResolutionRule.SUSPECTED_SCALE_ERROR: "the two are a clean power of ten apart",
    ResolutionRule.LOWER_TIER_WINS: "the more authoritative source was preferred",
    ResolutionRule.BASIS_MISMATCH: "one is as reported and the other a restatement",
    ResolutionRule.LATER_FILING_WINS: "the same publisher's later filing was preferred",
    ResolutionRule.SAME_TIER_SAME_DATE: "the two are equally authoritative and equally dated",
    ResolutionRule.THESIS_CONFLICT: "a challenge to the draft's own conclusion",
    ResolutionRule.DOCUMENT_CONTRADICTS_ITSELF: "this report disagrees with itself",
}


def spoken_rule(name: str) -> str:
    """A stored rung name in words, for a rule this code version may not know.

    The same shape as :func:`aer.eval.metrics.spoken_metric`, for the same reason: a
    disagreement recorded under a build whose ladder had a rung this one does not must
    still render, so an unknown name is spelled out from its own words rather than
    refused. The gate payload stores the value, so a page reading one back has a string
    rather than a member.
    """
    try:
        return ResolutionRule(name).spoken
    except ValueError:
        return name.replace("_", " ")


def spoken_tier(name: str) -> str:
    """A stored tier name in words, for a tier this code version may not know.

    The same shape as :func:`spoken_rule`, and for the same reason: a position was stored
    as JSONB, so a page reading one back has a string rather than a member.
    """
    try:
        return SourceTier(name).spoken
    except ValueError:
        return name.replace("_", " ").lower()


def position_figure(position: Mapping[str, Any]) -> str:
    """How one stored position's quantity reads on a page.

    Gap A68. Every disagreement on the approval page carried "0 thesis (T1_REGULATORY)"
    under both sides — the placeholder rendered as though it informed the reader. It could
    not: a thesis conflict has no quantity, so the zero is definitionally true of both
    positions and says nothing about either. Noise, on the page doing the platform's most
    important work.

    A thesis position reads as its tier alone, which is the only thing about it that
    genuinely differs from the other side. Anything with a real quantity keeps it.

    Takes the stored record rather than a :class:`Position` because that is what the
    surfaces hold: the JSONB written by :meth:`Position.as_record`.
    """
    unit = str(position.get("unit", ""))
    tier = str(position.get("tier", ""))
    computed = bool(position.get("computed"))
    if unit == THESIS_UNIT:
        # The run's own draft denying a figure has neither a quantity nor a publisher, so
        # there is nothing here but the absence itself (ADR 0125). The red team's side of a
        # thesis conflict does have a publisher behind its evidence, and keeps its tier.
        return "no figure" if computed else spoken_tier(tier)
    if computed:
        # A calculation has no publisher, so there is no tier to print (ADR 0125). Printing
        # one would attribute this platform's own arithmetic to a regulator, on the page
        # where the operator decides whether to publish it.
        return f"{position.get('value', '')} {unit} (this run's own arithmetic)".strip()
    return f"{position.get('value', '')} {unit} ({spoken_tier(tier)})".strip()


def challenge_heading(detail: Mapping[str, Any] | None, *, fallback: str) -> str:
    """How one recorded conflict announces itself above its own argument.

    A red-team row's ``topic`` is a shortened copy of its statement — enough to fingerprint
    the row and to name it in a log line, and no more. Printed as the heading *above* that
    statement, which is what the review page did, it read as the same sentence twice: once
    cut short, then whole. So a challenge names what it is — the dimension it attacks and
    how hard — and leaves the argument below it to be the argument.

    ``fallback`` is the topic, for a conflict that is not a red-team challenge: a source
    conflict's topic is a real short label ("revenue FY2024"), not a shortening of anything.

    Takes the stored record rather than a row, for the reason :func:`position_figure` does:
    the surfaces hold JSONB, and this module knows nothing about tables.
    """
    detail = detail or {}
    dimension = str(detail.get("dimension") or "").replace("_", " ").strip()
    if not dimension:
        return fallback
    heading = f"Red team \N{EM DASH} {dimension}"
    severity = detail.get("severity")
    return f"{heading}, severity {severity}/5" if severity else heading


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

    # This platform's own arithmetic rather than something a publisher said (ADR 0125). The
    # ladder never reads it — a calculation conflict does not reach the ladder — but the
    # *page* does: see `position_figure`, which must not print a tier beside a figure no
    # source ever published. `tier` and `filed_date` stay required because the type is
    # shared, and a computed position fills them with the run's own scene; nothing reads
    # them once this is set.
    computed: bool = False

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
            "computed": self.computed,
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

    # A credible-source conflict as `docs/archive/PLAN.md` section 2.4 defines it: both positions
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
                f"{position_a.label} is {position_a.tier.spoken} and {position_b.label} "
                f"is {position_b.tier.spoken}. The more authoritative source carries more "
                f"weight, so "
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
                f"{position_a.label} is {position_a.basis.spoken} and {position_b.label} "
                f"is {position_b.basis.spoken}. Both can be true of the same period, because "
                "they answer different questions, and preferring one by date would answer "
                "a question nobody asked."
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
                f"Both are {position_a.tier.spoken}, {position_a.basis.spoken}. "
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
                f"{position_a.tier.spoken}, both {position_a.basis.spoken}, both filed "
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
    """Rung 6 of ``docs/archive/PLAN.md`` section 2.9: the red team against the base thesis.

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


def figure_contradiction(*, first: Position, second: Position, topic: str) -> Resolution:
    """One figure, one period, two values the same document published (ADR 0125).

    **The ladder is deliberately not run.** It decides between two *sources* by tier, basis
    and filing date, and neither side here is a source: both are this run's own output,
    struck by the same code from the same evidence on the same day. Rung 6 would escalate,
    which is the right outcome reached by an argument that is false at every step, and the
    rationale it wrote would tell a reader that two numbers no regulator ever saw were
    filed by one. A rationale is the only part of this row a reader of the report meets.

    Agreement within :data:`AGREEMENT_TOLERANCE` records nothing, exactly as rung 1 does,
    so a caller can run this over every group without doing the arithmetic itself.

    The two are put into the same canonical order :func:`resolve` uses, so the same pair
    compared twice names the same side A.
    """
    position_a, position_b = _canonical_order(first, second)
    comparable = canonical_unit(position_a.unit) == canonical_unit(position_b.unit)

    if not comparable:
        return Resolution(
            outcome=ResolutionOutcome.ESCALATED,
            rule=ResolutionRule.DOCUMENT_CONTRADICTS_ITSELF,
            rationale=(
                f"This report publishes {topic} twice, in {position_a.unit} and in "
                f"{position_b.unit}. One figure measured two ways is a defect in the run "
                "rather than a difference of opinion, and nothing here converts between "
                "them."
            ),
            position_a=position_a,
            position_b=position_b,
            relative_difference=None,
            material=True,
        )

    difference = relative_difference(position_a.value, position_b.value)
    if difference <= AGREEMENT_TOLERANCE:
        return _resolution(
            ResolutionOutcome.AGREED,
            ResolutionRule.VALUES_AGREE,
            (
                f"Every published figure for {topic} agrees on {position_a.value} "
                f"{position_a.unit} to within {AGREEMENT_TOLERANCE:.2%}."
            ),
            position_a,
            position_b,
            difference=difference,
            material=False,
        )

    return _resolution(
        ResolutionOutcome.ESCALATED,
        ResolutionRule.DOCUMENT_CONTRADICTS_ITSELF,
        (
            f"This report publishes {topic} as {position_a.value} in "
            f"{position_a.label} and as {position_b.value} in {position_b.label}. Both "
            "are this run's own arithmetic, so there is no more authoritative source to "
            "prefer and no later filing to take: one of the two is wrong, and a reader "
            "cannot tell which."
        ),
        position_a,
        position_b,
        difference=difference,
        material=True,
    )


def denied_figure(
    *, denial: Position, published: Position, topic: str, sentence: str
) -> Resolution:
    """A sentence saying a figure is unavailable, in a document that prints it (ADR 0125).

    The commonest self-contradiction the readiness audit found, and the one every judge
    named: an executive summary denying a discounted cash flow thirty lines above the
    valuation table, a risks section calling operating cash flow unavailable while six
    sections cite it.

    Asymmetric by construction, as :func:`thesis_conflict` is: the denial is always A and
    the figure it denies is always B. There is no numeric distance between a sentence and a
    number, so there is no relative difference to report — a zero would read as agreement.
    """
    return Resolution(
        outcome=ResolutionOutcome.ESCALATED,
        rule=ResolutionRule.DOCUMENT_CONTRADICTS_ITSELF,
        rationale=(
            f'{denial.label} states: "{sentence.strip()}" — while this report publishes '
            f"{topic} as {published.value} {published.unit} in {published.label}. A "
            "document that denies a figure it prints tells its reader not to believe "
            "either place."
        ),
        position_a=denial,
        position_b=published,
        relative_difference=None,
        material=True,
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


def _ordering_key(position: Position) -> tuple[int, date, str, str, str, str, int, str, bool]:
    return (
        position.tier.rank,
        position.filed_date,
        position.reference,
        str(position.value),
        position.unit,
        position.basis.value,
        position.scale,
        position.label,
        position.computed,
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
