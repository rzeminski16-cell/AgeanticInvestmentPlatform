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
from decimal import Decimal
from typing import Final

from aer.web.vocabulary import Tone

__all__ = ["NOT_AVAILABLE", "CostContext", "cost_context", "pounds"]

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


def tone_for(cost: CostContext) -> Tone:
    """How a cost reads: ordinary, worth noticing, or the reason a run stopped.

    **Never `FAILURE`.** A run at its ceiling has not gone wrong; a guardrail has held. The
    state vocabulary makes the same distinction for `BUDGET_EXCEEDED`, and a cost block that
    contradicted it would put the two halves of one event in two different colours.
    """
    if cost.fraction >= Decimal(1):
        return Tone.REFUSAL
    return Tone.WARNING if cost.is_near_ceiling else Tone.INFO
