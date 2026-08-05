"""Why a margin moved: the movement decomposed into the lines that caused it.

"Operating margin fell 240 basis points" is an observation. "Operating margin fell 240 basis
points, of which 180 was gross margin and 90 was R&D, partly offset by 30 from SG&A" is
analysis, and it is the difference between a report that describes a company and one that
explains it.

**The residual is a line, not a rounding.** A decomposition over declared expense lines
explains as much of the movement as those lines account for, and no more. Whatever is left
is shown as ``residual`` rather than spread across the components or quietly dropped — a
filer who reports only an aggregate operating-expense line produces a bridge that is almost
all residual, and *that is the honest output*: the movement could not be attributed. A
bridge that always adds up to exactly the movement, however the components were computed, is
a bridge that will one day attribute a change to the wrong line with total confidence.

**Aggregates are excluded from the driver set on purpose.** ``operating_expenses`` is, for
most filers, the total of ``sg_and_a`` and ``research_and_development``. Including all three
would double-count the same cost and produce components that look precise and are wrong by a
factor of two. The overlap goes to the residual instead, where it is visible.

Each component carries its own provenance: it is a traced calculation over the two periods'
figures, so "where did the 180 basis points come from?" resolves to two facts.

Pure and side-effect free. It is given two periods' statements.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.statements import StatementSet
from aer.calc.units import CALC_CONTEXT, CalculationError, Quantity

__all__ = [
    "BRIDGE_SPECS",
    "BridgeComponent",
    "BridgeSpec",
    "MarginBridge",
    "contribution_of",
    "margin_bridge",
    "margin_of",
    "movement_from",
    "residual_of",
]


@dataclass(frozen=True, slots=True)
class BridgeSpec:
    """A margin, and the expense lines a bridge over it will try to attribute to."""

    key: str
    label: str

    # The margin itself: this line over revenue.
    numerator: str

    # The lines whose movement as a share of revenue explains the margin's movement. An
    # expense here reduces the margin, so a component's sign is the negative of the movement
    # in its own revenue share.
    drivers: tuple[str, ...]

    note: str = ""


# The two bridges the statements support.
#
# Gross margin has exactly one driver and is therefore usually fully explained, which makes
# it the check on the machinery: a residual there means the filer's stated gross profit is
# not revenue less cost of revenue, which is itself worth knowing.
BRIDGE_SPECS: Final[tuple[BridgeSpec, ...]] = (
    BridgeSpec(
        key="gross_margin",
        label="Gross margin",
        numerator="gross_profit",
        drivers=("cost_of_revenue",),
        note="One driver. A residual here means stated gross profit is not revenue less "
        "cost of revenue.",
    ),
    BridgeSpec(
        key="operating_margin",
        label="Operating margin",
        numerator="operating_income",
        drivers=(
            "cost_of_revenue",
            "sg_and_a",
            "research_and_development",
            "impairment",
            "restructuring_costs",
        ),
        note="`operating_expenses` is deliberately not a driver: for most filers it is the "
        "total of SG&A and R&D, and including it would double-count them.",
    ),
)


@dataclass(frozen=True, slots=True)
class BridgeComponent:
    """One line's contribution to the movement, in the same units as the margin itself."""

    concept: str
    contribution: Quantity

    # The line's share of revenue in each period. Kept because the contribution alone does
    # not say whether a cost line grew or revenue shrank, and those are different stories.
    opening_share: Quantity
    closing_share: Quantity

    @property
    def value(self) -> Decimal:
        return self.contribution.value


@dataclass(frozen=True, slots=True)
class MarginBridge:
    """A margin's movement between two periods, and what is known to have caused it."""

    key: str
    label: str
    opening: Quantity
    closing: Quantity
    movement: Quantity
    components: tuple[BridgeComponent, ...]

    # The part of the movement the drivers do not account for. See the module docstring: a
    # large residual is a real result, not a defect to be tuned away.
    residual: Quantity

    # Drivers absent from one or both periods, so no contribution could be computed for them.
    # They are part of why the residual is what it is.
    unattributed: tuple[str, ...] = ()

    @property
    def explained(self) -> Decimal:
        """How much of the movement the components account for, as a fraction.

        ``1`` for a fully attributed bridge. Undefined and reported as zero when the margin
        did not move at all, since there is nothing to explain.
        """
        if self.movement.value == 0:
            return Decimal(0)
        # In the calculation context, not Python's default 28 digits. A sum of 34-digit
        # figures rounded to 28 and then divided is a different number from the one the
        # residual was computed against, and the two would disagree by the rounding.
        with localcontext(CALC_CONTEXT):
            attributed = sum((c.value for c in self.components), Decimal(0))
            return attributed / self.movement.value

    def describe(self) -> str:
        parts = ", ".join(f"{c.concept} {c.value:+}" for c in self.components)
        return (
            f"{self.label} moved {self.movement.value:+} "
            f"({parts}; residual {self.residual.value:+})"
        )


@traced(
    name="margin_of",
    formula="margin = line / revenue",
    assumptions=("Both figures are from the same period and the same statement.",),
)
def margin_of(_context: CalculationContext, *, line: Quantity, revenue: Quantity) -> Quantity:
    """A line as a fraction of the period's revenue.

    Raises:
        CalculationError: If revenue is not positive, which makes every share of it
            meaningless rather than merely large.
    """
    if revenue.value <= 0:
        message = (
            f"Revenue is {revenue.value}, so no line's share of it is defined. A bridge "
            "needs both periods to have revenue to be a share of."
        )
        raise CalculationError(message, context={"revenue": str(revenue.value)})
    return line / revenue


@traced(
    name="margin_movement",
    formula="movement = closing share - opening share",
    assumptions=(
        "The two periods are of the same length. A bridge between a half-year and a full "
        "year attributes to lines a difference that is mostly the calendar.",
    ),
)
def movement_from(
    _context: CalculationContext, *, opening: Quantity, closing: Quantity
) -> Quantity:
    """The change in a share between two periods."""
    return closing - opening


@traced(
    name="bridge_contribution",
    formula="contribution = opening share - closing share",
    assumptions=(
        "The line is a cost: a larger share of revenue reduces the margin, so a "
        "contribution is the *fall* in the line's share and not the rise.",
    ),
)
def contribution_of(
    _context: CalculationContext, *, opening_share: Quantity, closing_share: Quantity
) -> Quantity:
    """One line's contribution to the margin's movement.

    Subtracted in this order rather than by negating a movement. Negating drops the
    provenance — plain quantity arithmetic has no calculation to point at — and the sign is
    the single easiest thing to get wrong here: a bridge with every sign flipped still sums
    to the movement and tells a completely different story.
    """
    return opening_share - closing_share


@traced(
    name="bridge_residual",
    formula="residual = movement - sum(contributions)",
    assumptions=(
        "Whatever the declared drivers do not account for. A large residual means the "
        "decomposition is incomplete, not that the movement is inexplicable.",
    ),
)
def residual_of(
    _context: CalculationContext, *, movement: Quantity, contributions: Sequence[Quantity]
) -> Quantity:
    """The part of the movement the components leave over.

    Every contribution is a recorded input, so a reader can see what the residual is a
    residual *of* rather than being handed a number with no denominator.
    """
    remaining = movement
    for contribution in contributions:
        remaining = remaining - contribution
    return remaining


def margin_bridge(
    context: CalculationContext,
    spec: BridgeSpec,
    *,
    opening: StatementSet,
    closing: StatementSet,
) -> MarginBridge | None:
    """Decompose one margin's movement between two periods.

    ``None`` when the margin itself cannot be computed in both periods — there is no
    movement to explain, and inventing one from whichever period is available would be worse
    than saying nothing.

    Raises:
        CalculationError: If either period's revenue is not positive.
    """
    opening_margin = _share(context, opening, spec.numerator)
    closing_margin = _share(context, closing, spec.numerator)
    if opening_margin is None or closing_margin is None:
        return None

    movement = movement_from(context, opening=opening_margin, closing=closing_margin)

    components: list[BridgeComponent] = []
    unattributed: list[str] = []
    for driver in spec.drivers:
        opening_share = _share(context, opening, driver)
        closing_share = _share(context, closing, driver)
        if opening_share is None or closing_share is None:
            unattributed.append(driver)
            continue

        components.append(
            BridgeComponent(
                concept=driver,
                contribution=contribution_of(
                    context, opening_share=opening_share, closing_share=closing_share
                ),
                opening_share=opening_share,
                closing_share=closing_share,
            )
        )

    residual = residual_of(
        context,
        movement=movement,
        contributions=[component.contribution for component in components],
    )

    return MarginBridge(
        key=spec.key,
        label=spec.label,
        opening=opening_margin,
        closing=closing_margin,
        movement=movement,
        components=tuple(components),
        residual=residual,
        unattributed=tuple(unattributed),
    )


def _share(context: CalculationContext, statements: StatementSet, concept: str) -> Quantity | None:
    """A concept's share of the period's revenue, or ``None`` if the line is absent."""
    line = statements.get(concept)
    revenue = statements.get("revenue")
    if line is None or revenue is None:
        return None
    return margin_of(context, line=line, revenue=revenue)
