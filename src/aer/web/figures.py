"""Figures the interface shows, rendered before they reach a template.

ADR 0077's rule is that JavaScript may own chrome and never a figure. The quieter half of
the same rule is that a **template** may not own one either: a Jinja file that writes
``£{{ spend_gbp }}`` is deciding a currency symbol, and the next surface that shows the same
number decides it again, differently. The run console does exactly that today.

So a handler assembles the string and the template prints it. The convention the design
handoff sets is followed here: **a field ending ``_display`` is a complete server-rendered
string** and a template neither formats it nor adds to it.

**Three money renderings exist, deliberately, and this is only one of them.**

* ``pounds`` — what the *operator* spent, in pounds. Cost ceilings, run spend, the month's
  total. Here.
* ``portfolio.pages._pounds`` — a book, exact to the penny in its own currency, because that
  screen is reconciled line by line against a broker statement.
* ``render.display.money`` — a company's figures in a report, in millions, resolved against a
  ``HouseStyle`` (ADR 0056).

They are not interchangeable and merging them would be a fourth. A report that printed a
£1.2m book as "£1m" is right; a portfolio that did would be unreconcilable against anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from aer.services.overview import TypicalCost
from aer.web.shell.provenance import ProvenanceRef
from aer.web.vocabulary import Tone

__all__ = [
    "NOT_AVAILABLE",
    "CostContext",
    "RenderedFigure",
    "cost_context",
    "cost_guidance",
    "pounds",
    "waited_for",
]

# What a figure the platform cannot state looks like. Never a blank cell and never a zero: a
# blank reads as nil and a zero reads as a measurement, and both are claims the record does
# not support.
NOT_AVAILABLE: Final = "—"

# When spend stops being background information and starts being a decision. Below it, the
# figure is a fact; at or above it, the operator is close enough to the ceiling that the next
# step may not fit.
NEAR_CEILING: Final = Decimal("0.8")


def pounds(amount: Decimal) -> str:
    """Operator spend, in pounds, decided in Python rather than in a template.

    **A total that rounds to nothing says so.** "We have spent nothing this month" and "we
    have spent a third of a penny" are different answers, and ``£0.00`` is only one of them —
    the wrong one, on a screen whose whole subject is what a run has cost.
    """
    if amount and abs(amount) < Decimal("0.01"):
        return "under £0.01"
    return f"£{amount:,.2f}"


@dataclass(frozen=True, slots=True)
class CostContext:
    """What a run has spent, against what it is allowed, as one object.

    Assembled once and shown identically on the console and on all seven gates. Today those
    surfaces disagree: the console renders a bare total with a currency symbol typed into the
    template, gate 1 shows an estimate, gate 3 shows a cost block with an alert threshold, and
    the other five show nothing. **The number that decides whether to keep going is the same
    number every time**, and the operator should not have to learn three presentations of it.

    Every field is a finished string except ``fraction``, which is a proportion for a bar or a
    class name and never a figure a reader is shown.
    """

    spent_display: str
    ceiling_display: str
    remaining_display: str
    fraction: Decimal
    """Spent over ceiling, clamped to 1. Zero when there is no ceiling to divide by."""

    is_near_ceiling: bool
    scope: str = "run"
    """``run`` or ``month``. The two have different remedies and must not be conflated.

    Raising a request's own cap releases a run stopped on its own ceiling and does nothing at
    all for one stopped on the month's — that operator has to change the monthly budget or
    wait for the month to turn.
    """

    @property
    def summary(self) -> str:
        """The one line every surface shows: what is spent, out of what."""
        return f"{self.spent_display} of {self.ceiling_display}"


def cost_context(*, spent: Decimal, ceiling: Decimal | None, scope: str = "run") -> CostContext:
    """Build the context from the two figures a run already records.

    ``ceiling`` is optional because one genuinely may not be known — a monthly budget that was
    never configured, a run recorded before caps existed. In that case the ceiling and the
    remainder are stated as unavailable rather than guessed at, and nothing is ever presented
    as "0% of budget used" on the strength of a missing denominator.
    """
    if ceiling is None or ceiling <= 0:
        return CostContext(
            spent_display=pounds(spent),
            ceiling_display=NOT_AVAILABLE,
            remaining_display=NOT_AVAILABLE,
            fraction=Decimal(0),
            is_near_ceiling=False,
            scope=scope,
        )

    remaining = ceiling - spent
    fraction = min(spent / ceiling, Decimal(1)) if spent > 0 else Decimal(0)
    return CostContext(
        spent_display=pounds(spent),
        ceiling_display=pounds(ceiling),
        # A run past its ceiling has nothing left rather than a negative allowance. The
        # overspend itself is not hidden — `spent_display` is the larger number, plainly.
        remaining_display=pounds(max(remaining, Decimal(0))),
        fraction=fraction,
        is_near_ceiling=fraction >= NEAR_CEILING,
        scope=scope,
    )


def waited_for(since: datetime, *, now: datetime) -> str:
    """How long something has been sitting there, in the coarsest honest unit.

    "2 days" rather than "2 days, 4 hours, 11 minutes". The reader is triaging four rows, not
    billing against them, and a duration precise to the minute makes the *ordering* harder to
    see rather than easier.

    **The clock is a parameter.** A renderer that read `datetime.now()` would be a function no
    test could pin and a figure that changed between two calls in one render — the same reason
    `core/` is free of clock reads. The handler reads the clock once and passes it down.

    Rounds down, always. Something that started 47 hours ago has been waiting "1 day", not two:
    on a page about what needs attention, rounding a duration up is inventing urgency.
    """
    elapsed = now - since
    if elapsed < timedelta(0):
        # A start time in the future is a clock that moved, not a negative wait.
        return "just now"
    days = elapsed.days
    if days >= 1:
        return "1 day" if days == 1 else f"{days} days"
    hours = elapsed.seconds // 3600
    if hours >= 1:
        return "1 hour" if hours == 1 else f"{hours} hours"
    minutes = elapsed.seconds // 60
    if minutes >= 1:
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    return "just now"


@dataclass(frozen=True, slots=True)
class RenderedFigure:
    """A figure and its lineage, as one object, so a surface cannot show one without the other.

    ADR 0077 made a provenance badge a link with a required ref, which stops a badge being
    rendered without a drill-down. This closes the other half of the same gap: a **figure**
    rendered without a badge at all. A template holding a bare string has nothing to hang a
    lineage off and nothing to stop it printing the number anyway, and invariant 3 — no figure
    reaches a surface unless it is a stored fact, a recorded calculation or an attestation — is
    a rule about exactly that moment.

    So there are two shapes and no third. A figure the platform can state carries where it came
    from; a figure it cannot state carries why not. Neither is constructible without its half,
    which makes a bare number and an unexplained dash both unrepresentable rather than
    discouraged.
    """

    value_display: str
    provenance: ProvenanceRef | None = None
    unavailable_because: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        if not self.value_display.strip():
            message = (
                f"The figure {self.label or 'with no label'} renders as nothing. An empty cell "
                f"reads as nil; use `unavailable` and say why, which renders {NOT_AVAILABLE!r}."
            )
            raise ValueError(message)
        if self.is_available and self.provenance is None:
            message = (
                f"The figure {self.value_display!r} has no provenance. A number on a screen "
                "with no way to read where it came from is the confidently-wrong surface this "
                "platform exists to prevent — the same fault ADR 0077 refuses in a badge that "
                "links nowhere, one step earlier."
            )
            raise ValueError(message)
        if not self.is_available and self.provenance is not None:
            message = (
                "An unavailable figure carries a provenance reference. There is no lineage "
                "under a number that does not exist, and a badge beside a dash claims one."
            )
            raise ValueError(message)
        if not self.is_available and not self.unavailable_because.strip():
            message = (
                f"The figure {self.label or 'with no label'} is unavailable and does not say "
                "why. A bare dash is read as a zero or as a bug, and it is usually neither."
            )
            raise ValueError(message)

    @property
    def is_available(self) -> bool:
        """Whether this is a figure at all, or the platform saying it has none."""
        return self.value_display != NOT_AVAILABLE

    @classmethod
    def traced(
        cls, value_display: str, provenance: ProvenanceRef, *, label: str = ""
    ) -> RenderedFigure:
        """A figure the platform can state, with where to go and read it."""
        return cls(value_display=value_display, provenance=provenance, label=label)

    @classmethod
    def unavailable(cls, because: str, *, label: str = "") -> RenderedFigure:
        """No figure, and the reason there is none.

        The reason is required. "Not filed for this period" and "the extraction failed" send
        the reader to two different places, and a dash sends them to neither.
        """
        return cls(value_display=NOT_AVAILABLE, unavailable_because=because, label=label)


def cost_guidance(typical: TypicalCost) -> str:
    """What to tell an operator who is about to choose a spending ceiling.

    **The unknown case is the one that matters**, and it is the one a fresh install is always
    in. A range averaged from no runs would be a figure with the confidence of a measurement
    and nothing behind it, so there is no range — and the sentence still says the thing the
    operator actually needs, which is that the number they type is enforced rather than
    reported. That half is true whether or not there is any history.
    """
    enforced = (
        "Enforced in code: a run projected to cross this stops for your decision rather "
        "than quietly spending past it."
    )
    # The two bounds are checked rather than `is_known`, which reads better and narrows
    # nothing: a property cannot tell the type checker that two other fields are not None,
    # so the convenient spelling is the one that needs a cast to compile.
    if typical.low is None or typical.high is None:
        if typical.sample:
            runs = "one finished run" if typical.sample == 1 else f"{typical.sample} finished runs"
            return (
                f"Only {runs} at this depth so far, which is too few to quote a range. {enforced}"
            )
        return (
            f"No finished runs at this depth yet, so there is no typical range to show. {enforced}"
        )
    return (
        f"Runs at this depth have cost {pounds(typical.low)} to {pounds(typical.high)} "
        f"across {typical.sample} finished runs. {enforced}"
    )


def tone_for(cost: CostContext) -> Tone:
    """How a cost reads: ordinary, worth noticing, or the reason a run stopped.

    **Never `FAILURE`.** A run at its ceiling has not gone wrong; a guardrail has held. The
    state vocabulary makes the same distinction for `BUDGET_EXCEEDED`, and a cost block that
    contradicted it would put the two halves of one event in two different colours.
    """
    if cost.fraction >= Decimal(1):
        return Tone.REFUSAL
    return Tone.WARNING if cost.is_near_ceiling else Tone.INFO
