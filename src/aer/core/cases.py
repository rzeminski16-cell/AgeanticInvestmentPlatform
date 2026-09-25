"""The two cases a report argues, and the levers a point may turn on (ADR 0135).

The report takes no side. It sets out the case for owning the shares and the case against,
each point argued from the evidence, and a point may turn on one **lever**: one input of the
report's discounted cash flow moved to a value the record already holds. The model names a
lever by its key and writes no figure for it; code strikes the valuation and prints what it
gives.

This module is the vocabulary and the rules, and nothing that reads a database: which
levers exist, how a key is spelled, and what a drafted pair of cases must satisfy before it
is accepted. The list a run actually offers — which observations its filings hold, and what
each one is — is :mod:`aer.services.cases`'s, because it reads the record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from aer.errors import ValidationError

__all__ = [
    "DRIVERS",
    "LEVER_FIELD",
    "MAX_IMBALANCE",
    "PRICED_FOR_FIELD",
    "Anchor",
    "Direction",
    "LeverKey",
    "Side",
    "case_problems",
    "levers_named",
]


class Direction(StrEnum):
    """Which way a lever moves the value per share, the two terminal methods read together.

    Known before any case is drafted, because code strikes every lever on the list: the model
    cannot see which way an input moves a valuation, and guessing is how a point came to name
    depreciation at its heaviest as a case against the shares when the model the report
    prints holds the operating margin and adds depreciation back, so the strike raised both
    values.

    A method the lever leaves where it was counts for neither side: a terminal lever moves
    one method only, and moves it one way.
    """

    RAISES = "raises"
    LOWERS = "lowers"
    # The two methods move opposite ways, or neither moves.
    MIXED = "mixed"

    @classmethod
    def between(cls, base: Sequence[Decimal], struck: Sequence[Decimal]) -> Direction:
        """How ``struck`` sits against ``base``, method by method, in the same order."""
        moves = [after - before for before, after in zip(base, struck, strict=True)]
        moved = [move for move in moves if move != 0]
        if moved and all(move > 0 for move in moved):
            return cls.RAISES
        if moved and all(move < 0 for move in moved):
            return cls.LOWERS
        return cls.MIXED

    @property
    def spoken(self) -> str:
        return _DIRECTION_WORDS[self]


_DIRECTION_WORDS: Final[dict[Direction, str]] = {
    Direction.RAISES: "raises the value per share by every terminal method it moves",
    Direction.LOWERS: "lowers the value per share by every terminal method it moves",
    Direction.MIXED: "raises one terminal method's value per share and lowers the other's",
}


class Side(StrEnum):
    """The two cases, by the field each is drafted in."""

    FOR = "case_for"
    AGAINST = "case_against"

    @property
    def priced_field(self) -> str:
        """The platform-filled table beside the case: what its levers give."""
        return f"{self.value}_priced"

    @property
    def spoken(self) -> str:
        return _SIDE_WORDS[self]

    def contradicted_by(self, direction: Direction) -> bool:
        """Whether a lever moving the value this way argues the other case.

        A lever whose two methods disagree argues neither, and either case may name it: the
        table beside the case prints both figures.
        """
        return direction is _CONTRADICTS[self]


_SIDE_WORDS: Final[dict[Side, str]] = {
    Side.FOR: "the case for",
    Side.AGAINST: "the case against",
}

_CONTRADICTS: Final[dict[Side, Direction]] = {
    Side.FOR: Direction.LOWERS,
    Side.AGAINST: Direction.RAISES,
}

# Where a point names its lever. Never rendered: the priced table is what a reader sees.
LEVER_FIELD: Final = "lever"

# Which levers the priced tables were struck for, so a second pass over an unchanged draft
# strikes nothing and a redrafted one strikes again. Not in the contract, so never rendered.
PRICED_FOR_FIELD: Final = "priced_for"

# Two cases a point apart in number are still weighed alike; further apart, the count itself
# has taken a side.
MAX_IMBALANCE: Final = 1


class Anchor(StrEnum):
    """Where on the record a lever's value comes from."""

    LOWEST = "lowest"
    HIGHEST = "highest"
    LATEST = "latest"
    # Perpetual growth at the rate the base case's exit multiple implies.
    EXIT_MULTIPLE_IMPLIES = "exit_multiple_implies"
    # Perpetual growth at the risk-free rate the assumptions gate confirmed.
    RISK_FREE_RATE = "risk_free_rate"
    # The exit multiple the base case's perpetuity growth implies.
    GROWTH_IMPLIES = "growth_implies"


# The six drivers the proposals derive from the filings, in the proposals' own order. Each
# has a history, so each can be moved to its lowest, highest or latest observation.
DRIVERS: Final[tuple[str, ...]] = (
    "revenue_growth",
    "ebit_margin",
    "capex_intensity",
    "depreciation_intensity",
    "working_capital_intensity",
    "tax_rate",
)

_HISTORY_ANCHORS: Final = (Anchor.LOWEST, Anchor.HIGHEST, Anchor.LATEST)

# Every lever that can exist on any run. A run offers the subset its record holds.
_VOCABULARY: Final[frozenset[tuple[str, Anchor]]] = frozenset(
    {(driver, anchor) for driver in DRIVERS for anchor in _HISTORY_ANCHORS}
    | {
        ("terminal_growth", Anchor.EXIT_MULTIPLE_IMPLIES),
        ("terminal_growth", Anchor.RISK_FREE_RATE),
        ("exit_multiple", Anchor.GROWTH_IMPLIES),
    }
)

_SEPARATOR: Final = ":"


@dataclass(frozen=True, slots=True)
class LeverKey:
    """One lever: which input it moves, and where on the record the value comes from."""

    input: str
    anchor: Anchor

    def __post_init__(self) -> None:
        if (self.input, self.anchor) not in _VOCABULARY:
            message = f"{self.input} has no lever at {self.anchor.value}."
            raise ValidationError(message, context={"input": self.input})

    @property
    def key(self) -> str:
        """How a point names it: ``ebit_margin:lowest``."""
        return f"{self.input}{_SEPARATOR}{self.anchor.value}"

    @classmethod
    def parse(cls, text: str) -> LeverKey | None:
        """The lever a key names, or ``None`` for a key no run could offer."""
        name, separator, anchor = text.strip().partition(_SEPARATOR)
        if not separator:
            return None
        try:
            return cls(input=name, anchor=Anchor(anchor))
        except (ValueError, ValidationError):
            # An anchor the enum does not know, or a pair the vocabulary does not hold.
            return None

    @classmethod
    def every(cls) -> tuple[LeverKey, ...]:
        """The whole vocabulary, drivers first in the proposals' order."""
        order = {name: index for index, name in enumerate((*DRIVERS, "terminal_growth"))}
        return tuple(
            cls(input=name, anchor=anchor)
            for name, anchor in sorted(
                _VOCABULARY,
                key=lambda pair: (order.get(pair[0], len(order)), list(Anchor).index(pair[1])),
            )
        )


def levers_named(content: Mapping[str, Any]) -> tuple[tuple[Side, int, str, str], ...]:
    """Every lever a drafted pair of cases names: side, point number, lead-in and key.

    Point numbers count from one, as a reader and a refusal count them. A point naming no
    lever is not listed.
    """
    named: list[tuple[Side, int, str, str]] = []
    for side in Side:
        for number, point in enumerate(_points(content, side), start=1):
            key = str(point.get(LEVER_FIELD) or "").strip()
            if key:
                named.append((side, number, str(point.get("lead_in") or "").strip(), key))
    return tuple(named)


def case_problems(
    content: Mapping[str, Any],
    *,
    offered: Mapping[str, str],
    none_because: str,
    directions: Mapping[str, Direction] | None = None,
) -> list[str]:
    """What stops a drafted pair of cases being accepted. Empty means sound.

    Args:
        offered: The levers this run's record supplies, key to words. Empty when there are
            none, in which case ``none_because`` says why and every lever named is refused
            with that reason.
        directions: Which way each offered lever moves the value, as code struck it. A lever
            that moves the value against its case is refused: printed beside that case, the
            platform's own figures would argue the other one.
    """
    moves = directions or {}
    problems: list[str] = []

    counts = {side: len(_points(content, side)) for side in Side}
    if abs(counts[Side.FOR] - counts[Side.AGAINST]) > MAX_IMBALANCE:
        problems.append(
            f"The case for has {_points_word(counts[Side.FOR])} and the case against has "
            f"{_points_word(counts[Side.AGAINST])}. Weigh the two alike: they may differ by "
            f"at most {MAX_IMBALANCE} point, or the count itself takes a side."
        )

    seen: dict[Side, set[str]] = {side: set() for side in Side}
    for side, number, _, key in levers_named(content):
        where = f"Point {number} of {side.spoken}"
        if not offered:
            problems.append(
                f"{where} names a lever, but this run has none to strike: {none_because} "
                "Leave the lever out and make the point in words."
            )
            continue
        if key not in offered:
            problems.append(
                f"{where} names the lever {key!r}, which is not on this run's list. Name one "
                "exactly as listed, or none."
            )
            continue
        direction = moves.get(key)
        if direction is not None and side.contradicted_by(direction):
            problems.append(
                f"{where} names the lever {key!r}, which {direction.spoken}, so beside "
                f"{side.spoken} the platform's own figures would argue the other case. Name a "
                "lever that moves the value the way this case argues, or none."
            )
            continue
        if key in seen[side]:
            problems.append(
                f"{where} names a lever another point of {side.spoken} already turns on. "
                "Each point turns on its own lever, or on none."
            )
        seen[side].add(key)

    return problems


def _points(content: Mapping[str, Any], side: Side) -> list[Mapping[str, Any]]:
    points = content.get(side.value)
    if not isinstance(points, list):
        return []
    return [point for point in points if isinstance(point, Mapping)]


def _points_word(count: int) -> str:
    return f"{count} point" if count == 1 else f"{count} points"
