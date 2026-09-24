"""What moved between two runs' ledgers, and whether it is material (F4, ADR 0131 §5).

The refresh's diff, pure. Two sets of figures in — the prior run's and the new run's, each
keyed by what it measures, when, and under which case — and the changes out, each judged
by the mechanism's table (`08-mechanisms.md` §1.4) rather than by anybody's reading:

| Test | Threshold |
|---|---|
| Relative change | ``|Δ| ≥ 2%``, ``Δ = (new - prior) / |prior|`` |
| An anchor figure | any change: the figures a report rests on |
| A sign change | any |
| A premise threshold crossed | any, however small |
| A figure a held premise reads | half the relative threshold (ADR 0122 §2) |
| Appearance or disappearance | a figure that now exists, or no longer does |
| ``prior = 0`` and ``new ≠ 0`` | relative change is undefined; material |

Nothing here reads a row or renders a sentence a reader keeps: the narrative on each change
is the record's own plain statement, and the change summary says it in the house style. The
model writes prose *from* these rows, never instead of them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

__all__ = [
    "ANCHORS",
    "MATERIAL_RELATIVE_CHANGE",
    "WATCHED_RELATIVE_CHANGE",
    "Change",
    "Figure",
    "Movement",
    "diff_figures",
    "relative_change",
]

MATERIAL_RELATIVE_CHANGE: Final = Decimal("0.02")
"""The audit's own matcher treats more than two per cent as a contradiction rather than a
rounding difference, so the refresh treats it as a move."""

WATCHED_RELATIVE_CHANGE: Final = MATERIAL_RELATIVE_CHANGE / 2
"""Half the ordinary threshold, for a figure a premise the operator holds reads (ADR 0122
§2). Materiality is a property of the figure and of *this operator's* position: a one per
cent move in a figure a premise rests on is a move its holder would want to see, and the
same move in a figure nothing rests on is within the noise."""

# The figures a report rests on, by the name the ledger or the fact store records them
# under. Any change at all is material: a share count that moved by a tenth of a per cent
# changes every per-share figure, and a reader who was not told would find out from the
# arithmetic.
ANCHORS: Final[frozenset[str]] = frozenset(
    {
        "revenue",
        "net_income",
        "operating_cash_flow",
        "long_term_debt",
        "total_debt",
        "shares_outstanding",
        "diluted_shares_outstanding",
        "value_per_share",
        "enterprise_value",
        "equity_value",
    }
)


class Movement(StrEnum):
    """Why a change is material, or that it is not."""

    RELATIVE = "relative"
    ANCHOR = "anchor"
    SIGN = "sign"
    APPEARED = "appeared"
    DISAPPEARED = "disappeared"
    FROM_ZERO = "from_zero"
    PREMISE = "premise"
    WATCHED = "watched"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class Figure:
    """One figure as a run held it: what it measures, when, and under which case."""

    kind: str
    """``fact`` or ``calculation``: which relation the reference names."""

    name: str
    value: Decimal
    unit: str
    period: str = ""
    case: str = ""
    # What tells two rows of one name apart beyond the period — the canonical parameters
    # of a calculation, or a fact's dimension. Part of the key, never shown.
    distinguisher: str = ""
    reference: str = ""
    """The row's id, for the change row to point at."""

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.kind, self.name, self.period, self.case, self.distinguisher)


@dataclass(frozen=True, slots=True)
class Change:
    """One figure's movement between the runs, judged."""

    kind: str
    name: str
    period: str
    case: str
    prior: Figure | None
    new: Figure | None
    change_pct: Decimal | None
    material: bool
    movement: Movement
    narrative: str

    @property
    def magnitude(self) -> Decimal:
        """How far it moved, for ordering the summary: the largest relative change first,
        and the changes with no ratio — an appearance, a sign change from nought — ahead
        of every ratio, because a figure that was not there is the bigger news."""
        if self.change_pct is None:
            return Decimal("Infinity")
        return abs(self.change_pct)


def relative_change(prior: Decimal, new: Decimal) -> Decimal | None:
    """``(new - prior) / |prior|``, or ``None`` where the prior is nought."""
    if prior == 0:
        return None
    return (new - prior) / abs(prior)


Crossing = Callable[[Figure, Figure], str | None]
"""Whether a premise's threshold lies between the prior and the new figure, said as the
premise's own sentence, or ``None``. Supplied by the caller that holds the premises; the
diff stays pure."""


def diff_figures(
    prior: Sequence[Figure],
    new: Sequence[Figure],
    *,
    anchors: frozenset[str] = ANCHORS,
    threshold: Decimal = MATERIAL_RELATIVE_CHANGE,
    crossed: Crossing | None = None,
    watched: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[Change, ...]:
    """Every figure present in either run, judged by the table, in the runs' own order.

    A key present in both is one change; a key in one only is an appearance or a
    disappearance. Two rows sharing a key within one run — a ledger written twice — keep
    the last, which is the row a reader of that run's page is shown.

    ``watched`` names the ``(kind, name)`` pairs a premise the operator holds reads; each
    is material at half the relative threshold (ADR 0122 §2). Supplied by the caller that
    holds the premises, as ``crossed`` is; the diff stays pure.
    """
    before = {figure.key: figure for figure in prior}
    after = {figure.key: figure for figure in new}
    keys = list(dict.fromkeys([*before, *after]))
    return tuple(
        _judge(
            before.get(key),
            after.get(key),
            anchors=anchors,
            threshold=threshold,
            crossed=crossed,
            watched=watched,
        )
        for key in keys
    )


def _judge(
    prior: Figure | None,
    new: Figure | None,
    *,
    anchors: frozenset[str],
    threshold: Decimal,
    crossed: Crossing | None,
    watched: frozenset[tuple[str, str]],
) -> Change:
    if prior is None or new is None:
        return _one_sided(prior, new)
    ratio = relative_change(prior.value, new.value)
    movement, narrative = _movement(
        prior, new, ratio, anchors=anchors, threshold=threshold, crossed=crossed, watched=watched
    )
    return _change(
        prior, new, ratio, movement, narrative, material=movement is not Movement.UNCHANGED
    )


def _one_sided(prior: Figure | None, new: Figure | None) -> Change:
    """A figure one run holds and the other does not: material either way."""
    if new is not None:
        return _change(
            prior,
            new,
            None,
            Movement.APPEARED,
            f"{_words(new)} appears for the first time, at {_plain(new)}.",
        )
    if prior is None:  # pragma: no cover -- `diff_figures` never asks about no figure at all
        message = "A change needs at least one side."
        raise ValueError(message)
    return _change(
        prior,
        new,
        None,
        Movement.DISAPPEARED,
        f"{_words(prior)} is no longer computed; the prior run held {_plain(prior)}.",
    )


def _movement(
    prior: Figure,
    new: Figure,
    ratio: Decimal | None,
    *,
    anchors: frozenset[str],
    threshold: Decimal,
    crossed: Crossing | None,
    watched: frozenset[tuple[str, str]],
) -> tuple[Movement, str]:
    """The table, top to bottom: the first row that holds is the movement."""
    premise = crossed(prior, new) if crossed is not None else None
    moved = _moved(prior, new, ratio)
    unchanged = prior.value == new.value
    # Above the ordinary relative row, so a watched figure's row says what it feeds however
    # far it moved; the move itself is in the sentence either way.
    feeds_a_premise = (
        (new.kind, new.name) in watched and ratio is not None and abs(ratio) >= threshold / 2
    )
    rows: tuple[tuple[bool, Movement, str], ...] = (
        (premise is not None, Movement.PREMISE, f"{moved} It crosses {premise}."),
        (
            prior.value == 0 and new.value != 0,
            Movement.FROM_ZERO,
            f"{_words(new)} moved from nought to {_plain(new)}.",
        ),
        (
            not unchanged and (prior.value < 0) != (new.value < 0),
            Movement.SIGN,
            f"{moved} The sign changed.",
        ),
        (
            prior.name in anchors and not unchanged,
            Movement.ANCHOR,
            f"{moved} It anchors the report.",
        ),
        (
            feeds_a_premise,
            Movement.WATCHED,
            f"{moved} It feeds a premise you hold, and is material at half the ordinary threshold.",
        ),
        (ratio is not None and abs(ratio) >= threshold, Movement.RELATIVE, moved),
        (unchanged, Movement.UNCHANGED, f"{_words(new)} is unchanged at {_plain(new)}."),
    )
    for holds, movement, narrative in rows:
        if holds:
            return movement, narrative
    return Movement.UNCHANGED, f"{moved} Within the threshold."


def _change(
    prior: Figure | None,
    new: Figure | None,
    ratio: Decimal | None,
    movement: Movement,
    narrative: str,
    *,
    material: bool = True,
) -> Change:
    shown = new if new is not None else prior
    if shown is None:  # pragma: no cover -- `_one_sided` and `_judge` never pass two nones
        message = "A change needs at least one side."
        raise ValueError(message)
    return Change(
        kind=shown.kind,
        name=shown.name,
        period=shown.period,
        case=shown.case,
        prior=prior,
        new=new,
        change_pct=ratio,
        material=material,
        movement=movement,
        narrative=narrative,
    )


def _moved(prior: Figure, new: Figure, ratio: Decimal | None) -> str:
    if ratio is None:
        return f"{_words(new)} moved from {_plain(prior)} to {_plain(new)}."
    return f"{_words(new)} moved from {_plain(prior)} to {_plain(new)} ({_percent(ratio)})."


def _words(figure: Figure) -> str:
    name = figure.name.replace("_", " ")
    when = f" for {figure.period}" if figure.period else ""
    case = f" in the {figure.case} case" if figure.case and figure.case != "base" else ""
    return f"{name[:1].upper()}{name[1:]}{when}{case}"


def _plain(figure: Figure) -> str:
    value = f"{figure.value.normalize():f}"
    unit = figure.unit.strip()
    return f"{value} {unit}" if unit and unit.lower() != "pure" else value


def _percent(ratio: Decimal) -> str:
    scaled = (ratio * 100).quantize(Decimal("0.1"))
    sign = "+" if scaled > 0 else ""
    return f"{sign}{scaled}%"
