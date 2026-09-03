"""How one stored figure can legitimately reach prose, and whether a quoted one did.

A drafter writing about a company does not write `331839000000`. It writes "$331.8
billion", or "331,839" in a table of millions, or "46.8%" over a stored `0.4676`. Each is
the same figure said differently, and none of them is the figure being wrong.

**One definition, because two would disagree.** This began inside the evaluation layer,
where `cited_figure_agreement` asks whether a sentence quoting a calculation quotes the
right number. The §2.12 numeral rule asks a neighbouring question — whether a numeral in
the content is one the claims account for — and answered it by comparing digit strings, so
a figure the platform's own renderer would print was refused as unsourced. Two of the
sections a live run lost died there (roadmap §2.1). The readings are the same readings and
the arithmetic is the same arithmetic; they live here so there is only one of each.

**Precision is the draft's own, not a tolerance.** "46.8" claims one decimal place and
"0.09" claims two, and each is judged against what it actually asserts. A relative
tolerance cannot do this: loose enough to accept a two-decimal rounding of a small ratio,
it would accept half the errors worth catching.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

__all__ = ["READINGS", "reads_as"]

# `render.display` scales a dimensionless figure by a hundred for a percentage and renders
# money in millions; a drafter writing longhand says billions. Nothing else is admitted: a
# reading is a presentation this platform actually produces, not any factor that happens to
# make two numbers meet.
READINGS: Final[tuple[Decimal, ...]] = (
    Decimal(1),
    Decimal(100),
    Decimal("0.001"),
    Decimal("0.000001"),
    Decimal("0.000000001"),
)


def reads_as(quoted: Decimal, stored: Decimal) -> bool:
    """Whether ``quoted`` is ``stored`` said at the precision ``quoted`` chose."""
    # `as_tuple().exponent` is `'n'`, `'N'` or `'F'` for a NaN or an infinity, which no
    # figure scraped out of prose can be — but the type says otherwise, so it is narrowed
    # rather than asserted away.
    exponent = quoted.as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    step = Decimal(1).scaleb(-places)
    for scale in READINGS:
        scaled = stored * scale
        try:
            if scaled.quantize(step, rounding=ROUND_HALF_UP) == quoted:
                return True
        except InvalidOperation:  # pragma: no cover -- a figure too large to quantise
            continue
    return False


def plain_decimal(value: Decimal | None) -> str:
    """A figure as a reader would write it: no exponent, and none of the trailing zeros a
    NUMERIC round-trip adds to a threshold somebody typed as 25. Empty for none."""
    if value is None:
        return ""
    trimmed = value.normalize()
    return f"{trimmed:f}" if trimmed != 0 else "0"
