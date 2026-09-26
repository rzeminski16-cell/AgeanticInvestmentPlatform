"""What a report's valuation was, by method, read back from the run that produced it.

ROADMAP §3.19 item 76. ``reports`` carried a low, a base, a high and a currency, with a check
that the low did not exceed the high, and no code path ever wrote them. Every surface that
showed a report's valuation read those columns and printed nothing: the reports list, the
company page's chart and card, the digest the planner is given, the vault note, the API, and
the comparison section's *prior* column, which read *"not recorded"* on every report that had
a prior. And the shape was the one ADR 0132 took off the masthead: a range with no method
attached, where the two figures are two answers.

**Read back, never copied.** The figures are the base case's own recorded rows, one per
terminal method (a discounted cash flow's perpetuity growth and exit multiple) or treatment
(a bank's residual income, competed away or in perpetuity). A copy on the report row would be
a second source for one number, and the first surface to read the copy while another read the
row would print two. Reading the row is also what lets every figure keep the id of the
calculation behind it (invariant 3), and it serves every report ever approved without a
backfill.

**Nothing here does arithmetic.** It selects stored rows and says them in the house style.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import TerminalMethod
from aer.calc.residual_income import TerminalTreatment
from aer.config import HouseStyle
from aer.db.models import Calculation
from aer.render import display

__all__ = [
    "NOT_RECORDED",
    "MethodFigure",
    "ReportValuation",
    "valuation_of",
    "valuations_for",
]

# What a surface says for a report whose run recorded no per-share answer: a run that stopped
# before valuing, or one whose record predates the method tag.
NOT_RECORDED: Final = "not recorded"

# The base case's label on every answer row a valuation strikes.
_BASE: Final = "base"

# Each model's per-share row, the parameter that tells its two answers apart, and those
# answers in the order a reader meets them, in the words the report itself uses: the
# masthead's for a discounted cash flow, the valuation section's for a bank.
_ANSWERS: Final[tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]] = (
    (
        "value_per_share",
        "method",
        (
            (TerminalMethod.GORDON_GROWTH.value, "perpetuity growth"),
            (TerminalMethod.EXIT_MULTIPLE.value, "exit multiple"),
        ),
    ),
    (
        "residual_income_per_share",
        "treatment",
        (
            (TerminalTreatment.FADE_TO_NOTHING.value, "excess return competed away"),
            (TerminalTreatment.PERPETUAL_GROWTH.value, "excess return in perpetuity"),
        ),
    ),
)

_NAMES: Final[tuple[str, ...]] = tuple(name for name, _, _ in _ANSWERS)


@dataclass(frozen=True, slots=True)
class MethodFigure:
    """One method's answer: what it is called, what it gave, and the row that says so."""

    key: str
    label: str
    value: Decimal
    currency: str
    calculation_id: uuid.UUID

    def shown(self, *, style: HouseStyle) -> str:
        return display.money(self.value, self.currency, style=style)


@dataclass(frozen=True, slots=True)
class ReportValuation:
    """A report's per-share answers, one per method, in the report's own order."""

    figures: tuple[MethodFigure, ...] = ()

    @property
    def currency(self) -> str:
        return self.figures[0].currency if self.figures else ""

    def spoken(self, *, style: HouseStyle | None = None) -> str:
        """The answers as one line, each named by its method, or :data:`NOT_RECORDED`.

        Joined by "and", never "to": two terminal methods give two answers, and a range is a
        claim neither makes (ADR 0132). The same line the masthead prints.
        """
        if not self.figures:
            return NOT_RECORDED
        active = style if style is not None else HouseStyle()
        shown = " and ".join(
            f"{figure.shown(style=active)} ({figure.label})" for figure in self.figures
        )
        return f"{shown} a share"


async def valuations_for(
    session: AsyncSession, job_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ReportValuation]:
    """Each run's per-share answers, in one query, keyed by job. A run with none is absent."""
    wanted = list(dict.fromkeys(job_ids))
    if not wanted:
        return {}
    rows = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id.in_(wanted), Calculation.name.in_(_NAMES))
            .order_by(Calculation.sequence)
        )
    )
    by_job: dict[uuid.UUID, list[Calculation]] = {}
    for row in rows:
        by_job.setdefault(row.job_id, []).append(row)
    found: dict[uuid.UUID, ReportValuation] = {}
    for job_id, calculations in by_job.items():
        figures = _answers(calculations)
        if figures:
            found[job_id] = ReportValuation(figures=figures)
    return found


async def valuation_of(session: AsyncSession, job_id: uuid.UUID) -> ReportValuation:
    """One run's per-share answers, or an empty valuation that says it recorded none."""
    return (await valuations_for(session, [job_id])).get(job_id, ReportValuation())


def _answers(calculations: Sequence[Calculation]) -> tuple[MethodFigure, ...]:
    """The base case's newest row for each method, in the model's reading order.

    The case must be stated as the base case: a sensitivity cell, a scenario and an argued
    lever each strike the same names, and none of them is the report's answer.
    """
    figures: list[MethodFigure] = []
    for name, discriminator, methods in _ANSWERS:
        base = [
            row
            for row in calculations
            if row.name == name and (row.parameters or {}).get("case") == _BASE
        ]
        for key, label in methods:
            row = next(
                (item for item in reversed(base) if item.parameters.get(discriminator) == key),
                None,
            )
            if row is not None:
                figures.append(
                    MethodFigure(
                        key=key,
                        label=label,
                        value=row.output_value,
                        currency=_currency_of(row.output_unit),
                        calculation_id=row.id,
                    )
                )
    return tuple(figures)


def _currency_of(unit: str) -> str:
    """``USD/shares`` is dollars a share: the currency is what stands above the line."""
    return unit.split("/", 1)[0].strip().upper()
