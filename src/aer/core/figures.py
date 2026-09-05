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

**The sign is part of the figure, and the scanner carries it.** The first live run of the
confirmation runbook lost two sections to numerals that were right in every digit and
refused for their sign: the scanner read "-139,500" as ``139500`` and the comparison,
correctly signed, found no stored ``139500000000`` beside a stored ``-139500000000``.
The scanner now reads a minus glued to the digits — the ASCII hyphen, the true minus
sign (U+2212), an en dash (U+2013) — and the words "negative" and "minus" before them, so
"-139,500", a U+2212 before "51.8", and "negative 51.8 days" all carry the sign into the
comparison. What it does not read as a sign: a hyphen between two figures ("2020-2026",
"12%-14%"), and accounting parentheses, because "(67.9 percent)" in prose is a parenthesis
and refusing it would cost more sections than it would catch tables.

**Two questions, one scanner.** The numeral rule asks whether a numeral has *lineage* —
whether it is one of the figures the section's claims name — and for that a numeral
written without a sign reads as the magnitude of a signed figure: "a negative cycle of
51.8 days" over a stored ``-51.79`` is the stored figure, said as people say it. The
agreement metric asks whether a sentence quotes the *right number*, and there the sign
counts: "0.065" over a stored ``-0.0649`` is a dropped sign, reported. :func:`reads_as`
carries the switch, so the two questions differ in exactly one stated way.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

__all__ = ["NUMERAL", "READINGS", "numeral_matches", "numeral_tokens", "reads_as"]

# A numeral as a reader meets one: an optional sign, digits with optional thousands
# separators and decimals, an optional trailing per-cent sign. Word-bounded so "10-K" and
# "FY22Q4" do not shed fragments, but "grew 34%" and "$198,270 million" both surface their
# figures. The trailing guard refuses only a *mid-decimal* stop (".<digit>"), so a numeral
# ending a sentence — "in 2022." — still counts.
#
# The sign, when there is one, is either a word ("negative 51.8", "minus 3") or a mark
# glued to the digits: the hyphen ("-139,500"), the minus sign U+2212, or the en dash
# U+2013. A mark preceded by a word character is
# a range's dash and not a sign ("2020-2026"); the look-behind refuses it. A mark preceded
# by a per-cent sign or a closing bracket is the same dash ("12%-14%"), refused in
# :func:`numeral_matches` rather than here, because a look-behind cannot see past the
# optional group.
#
# A unit glued to the digits is part of how a note writes a figure — "0.09x", "3.5" with
# a U+00D7 after it, "$331,839m", "12bn" — and the closed set is what keeps the word-boundary guard
# meaning what it says: "FY22Q4" still sheds no "22", because "Q" is not a unit.
NUMERAL: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.])"
    r"(?:(?P<word>(?:negative|minus)\s+)|(?P<mark>[-\u2212\u2013]))?"
    r"(?P<digits>\d[\d,]*(?:\.\d+)?)(?:%|bn|mn|m|k|x|\u00d7)?(?!\w)(?!\.\d)",
    re.IGNORECASE,
)

# What a sign mark must not follow to count as one: the character before "-14" in
# "12%-14%" or "(3)-14".
_NOT_A_SIGN_AFTER: Final = frozenset("%)")


def numeral_matches(text: str) -> Iterator[tuple[re.Match[str], str]]:
    """Every numeral in ``text``, with its normalised token: sign kept, separators gone.

    The token is what both scanners compare — ``"-139500"`` for "-139,500", ``"-51.8"``
    for "negative 51.8" — so a caller quoting the neighbourhood of a token and a caller
    collecting the tokens cannot disagree about which span a token came from.
    """
    for match in NUMERAL.finditer(text):
        digits = match.group("digits").replace(",", "")
        signed = bool(match.group("word")) or (
            bool(match.group("mark"))
            and (match.start() == 0 or text[match.start() - 1] not in _NOT_A_SIGN_AFTER)
        )
        yield match, f"-{digits}" if signed else digits


def numeral_tokens(text: str) -> tuple[str, ...]:
    """The normalised numeral tokens of ``text``, in order of appearance."""
    return tuple(token for _, token in numeral_matches(text))


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


def reads_as(quoted: Decimal, stored: Decimal, *, sign_matters: bool = True) -> bool:
    """Whether ``quoted`` is ``stored`` said at the precision ``quoted`` chose.

    With ``sign_matters`` off, a ``quoted`` figure carrying no sign also reads as the
    magnitude of a negative ``stored`` one — the lineage question, where "51.8 days" of a
    cycle the sentence calls negative is the stored ``-51.79``. A ``quoted`` figure that
    *does* carry a sign is always compared as signed: "-51.8" over a stored ``51.79`` is
    wrong under either question.
    """
    if _reads_as_signed(quoted, stored):
        return True
    return not sign_matters and quoted > 0 and _reads_as_signed(quoted, -stored)


def _reads_as_signed(quoted: Decimal, stored: Decimal) -> bool:
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
