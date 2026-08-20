"""Relations between headline figures that cannot hold together.

**Traceability is not sanity, and the MTB run is the proof** (gap A61). Its front page
published a 172.1% net margin: the $442m "revenue" was a stored fact with a hashed
source, the margin was a recorded calculation with its formula, inputs and code version,
every citation verified — and the number is impossible, because the revenue concept had
resolved to a partial caption. Every guard held. No guard asked whether the number could
be true.

This module is that question, asked in code. It holds a closed set of relations between
the figures a front page shows — relations that cannot hold on a consolidated statement,
or that no going concern exhibits — and reports every violation with the values that
made it fire. It decides nothing about *which* figure is wrong: income exceeding revenue
means one of the two is mislabelled, and saying which would be a guess. The callers
withhold the implicated surface and raise the finding for a person to judge (ADR 0066).

Pure by the rules of this package: no I/O, no clock, no session. The caller assembles
the figures; this module only compares them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

__all__ = [
    "LARGE_BALANCE_SHEET",
    "TURNOVER_FLOOR",
    "FigureScene",
    "Impossibility",
    "impossible_relations",
]

# Below this asset turnover, a company with a large balance sheet is not slow — the
# revenue figure is not revenue. Chosen under any real operating company: a bank, the
# slowest-turning franchise there is, runs 0.03 to 0.10 on genuine total revenue; the MTB
# run's mislabelled caption produced 0.0076.
TURNOVER_FLOOR: Final = Decimal("0.01")

# The balance-sheet size past which the turnover floor applies, in the statement's own
# currency unit. A small company's turnover can be legitimately strange; a nine-figure
# balance sheet turning below the floor means the numerator is mislabelled.
LARGE_BALANCE_SHEET: Final = Decimal(1_000_000_000)


@dataclass(frozen=True, slots=True)
class FigureScene:
    """One period's headline figures, as far as the run holds them.

    ``revenue`` and ``net_income`` must be in the same currency to be comparable; the
    caller passes both only when their recorded units agree, and leaves a field ``None``
    when the run holds no figure for it. A scene with nothing to compare produces no
    findings — absence is not implausibility.
    """

    period: str
    revenue: Decimal | None = None
    net_income: Decimal | None = None
    net_margin: Decimal | None = None
    asset_turnover: Decimal | None = None
    total_assets: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Impossibility:
    """One relation that cannot hold, stated with the values that made it fire."""

    period: str
    statement: str


def impossible_relations(scenes: tuple[FigureScene, ...]) -> tuple[Impossibility, ...]:
    """Every impossible relation among the scenes, in scene order.

    Three relations, each checked only where both sides exist:

    - **Income above revenue.** Net income cannot exceed the revenue it came from on a
      consolidated statement. It is representable — a one-off gain below the revenue
      line can produce it — which is exactly why this reports rather than raises: the
      ordinary explanation is a mislabelled revenue caption, and the extraordinary one
      deserves a person's eyes either way.
    - **A margin above one.** The ratio form of the same statement, checked separately
      because a run can record the ratio for periods whose underlying facts it no longer
      shows side by side — the MTB run's history carried four such years.
    - **Turnover below the floor on a large balance sheet.** Revenue below one percent
      of assets on a billion-plus balance sheet is not a slow business, it is a
      numerator that is not total revenue.
    """
    found: list[Impossibility] = []
    for scene in scenes:
        if (
            scene.revenue is not None
            and scene.net_income is not None
            and scene.revenue > 0
            and scene.net_income > scene.revenue
        ):
            found.append(
                Impossibility(
                    period=scene.period,
                    statement=(
                        f"net income {scene.net_income} exceeds revenue {scene.revenue} "
                        f"for {scene.period} — impossible on a consolidated statement; "
                        "the ordinary cause is a revenue concept resolved to a partial "
                        "caption"
                    ),
                )
            )
        if scene.net_margin is not None and scene.net_margin > 1:
            found.append(
                Impossibility(
                    period=scene.period,
                    statement=(
                        f"net margin {scene.net_margin} for {scene.period} is above 1 — "
                        "income exceeding the revenue it is measured against"
                    ),
                )
            )
        if (
            scene.asset_turnover is not None
            and scene.total_assets is not None
            and scene.total_assets >= LARGE_BALANCE_SHEET
            and 0 <= scene.asset_turnover < TURNOVER_FLOOR
        ):
            found.append(
                Impossibility(
                    period=scene.period,
                    statement=(
                        f"asset turnover {scene.asset_turnover} for {scene.period} is "
                        f"below {TURNOVER_FLOOR} on total assets of {scene.total_assets} "
                        "— revenue under one percent of assets is a mislabelled "
                        "numerator, not a slow business"
                    ),
                )
            )
    return tuple(found)
