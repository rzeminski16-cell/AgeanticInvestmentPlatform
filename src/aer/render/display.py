"""The display formatter: every published value through one door, at render time.

Gap R1. The live report printed ``11729000000 USD`` mid-sentence, ``109417000000 USD`` in
a table cell and ``0.462`` where a reader needed ``46.2%`` — while the identical figures
rendered correctly as ``$11,729 million`` in the footnotes, because A40 gave the notes a
formatter and nothing gave one to the content. This module is the one formatter, and ADR
0056 states its contract: formatting is a projection of the stored value applied at
render, never a rewrite of what was stored — the artefact trail keeps exactly what the
model produced and the calculations keep their full precision.

**Deterministic, and honest about ambiguity.** A value formats only when its meaning is
knowable from what travels with it: a currency unit makes it money, a ``/shares`` unit
makes it per-share, a label naming a margin makes it a percentage. A bare number whose
meaning nothing states is passed through with thousands separators and nothing more —
guessing a unit at render time would be the unit-coercion this platform refuses
everywhere else.

Pure functions over :class:`~aer.config.HouseStyle`. No I/O, no settings lookup — the
caller resolves the effective style once and passes it down.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from aer.config import HouseStyle
from aer.core.dates import format_date

__all__ = ["cell", "date_text", "money", "prose", "scalar"]

_SYMBOLS: Final[dict[str, str]] = {"USD": "$", "GBP": "£", "EUR": "€"}

_MILLION: Final = Decimal(1_000_000)
_BILLION: Final = Decimal(1_000_000_000)

# Labels that make a pure number a percentage. Keywords rather than a model call: the
# question is how the label's author would read the figure aloud, and these are the words
# that make the answer "as a percent".
_PERCENT_WORDS: Final[tuple[str, ...]] = (
    "margin",
    "rate",
    "growth",
    "yield",
    "return",
    "share of",
    "value share",
    "percentage",
    "wacc",
    "cagr",
)

# Labels that make a pure number a multiple, read "times".
_TIMES_WORDS: Final[tuple[str, ...]] = (
    "ratio",
    "coverage",
    "turnover",
    "multiple",
    "leverage",
    " to ",
    "times",
    "ev/",
    "p/e",
)


def money(value: Decimal, currency: str, *, style: HouseStyle, in_table: bool = False) -> str:
    """A whole-company money amount, in the house style.

    Tables always render in millions — a column only lines up in one scale — and prose
    follows ``style.prose_money``: ``auto`` switches to billions at the configured
    threshold, ``millions`` never scales. Amounts below a million render plainly; a
    threshold or a scale is presentation, and pretending $250,000 is "$0m" is not.
    """
    symbol = _SYMBOLS.get(currency.upper(), f"{currency.upper()} ")
    magnitude = abs(value)

    if magnitude < _MILLION:
        # To the cent at most. The ledger stores twelve decimal places, and the valuation
        # page showed them — "$1,234.500000000000" (first live run of the runbook) — which
        # is precision the figure does not have and a reader cannot use.
        cents = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{symbol}{_grouped(cents)}"

    if not in_table and style.prose_money == "auto" and magnitude >= style.billions_from:
        scaled = (value / _BILLION).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        # One decimal always — "$391.0bn", never "$391bn" — so a column of prose figures
        # reads at one precision rather than jittering with the arithmetic.
        return f"{symbol}{scaled:,.1f}bn"

    scaled = (value / _MILLION).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{symbol}{_grouped(scaled)}m"


def date_text(value: date, *, style: HouseStyle) -> str:
    """A date as the house style prints one.

    Through :func:`aer.core.dates.format_date` rather than ``strftime`` directly, because
    the default pattern uses ``%-d`` and that directive does not exist outside glibc. This
    line raised ``ValueError: Invalid format string`` on Windows for every report, at the
    end of the run.
    """
    return format_date(value, style.date_format)


def scalar(
    value: Any,
    *,
    style: HouseStyle,
    unit: str | None = None,
    label: str = "",
    in_table: bool = False,
) -> str:
    """One value as the page shows it, formatted as far as its meaning is stated.

    The dispatch is on the unit first and the label second, and the fallback is the value
    with separators — never a guessed scale or an invented percent sign.
    """
    # An absent value is an em dash, never "None" and never a blank that leaves a
    # footnote marker standing alone in the cell — the MTB report printed a retained
    # earnings row as its own marker twice (gap A66).
    if value is None or (isinstance(value, str) and not value.strip()):
        return "\N{EM DASH}"

    number = _decimal_of(value)
    if number is None:
        return prose(str(value), style=style) if isinstance(value, str) else f"{value}"

    read = _unit_reading(
        number, unit=(unit or "").strip(), label=label, style=style, in_table=in_table
    )
    if read is not None:
        return read
    plain = _grouped(number) if number == number.to_integral_value() else _trimmed(number)
    # A stated unit naming a currency or a scale is shown, not dropped. The MTB report's
    # balance-sheet table read "Total assets 219.3" beside a cash-flow table reading
    # "2,280" — both rows carried units like "USD billions", both vanished here, and the
    # two scales sat a page apart with nothing telling the reader which was which (gap
    # A66). Only money-and-scale words earn the suffix: the platform's own type words
    # ("pure", "x") are for the formatter, and printing them would trade one leak for
    # another.
    if unit and _MONEY_SCALE_UNIT.search(unit):
        return f"{plain} {unit.strip()}"
    return plain


_ISO_CODE_LENGTH: Final = 3

# A unit worth printing when the formatter cannot restate it: one naming a currency or a
# scale. "USD billions" tells the reader what "219.3" is; "pure" tells them nothing.
_MONEY_SCALE_UNIT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:USD|GBP|EUR)\b|[$\u00a3\u20ac]|\b(?:million|billion|trillion|thousand)s?\b",
    re.IGNORECASE,
)


def _unit_reading(
    number: Decimal, *, unit: str, label: str, style: HouseStyle, in_table: bool
) -> str | None:
    """The reading a stated unit supports, or ``None`` for a number nobody explained."""
    code = unit.upper()

    if "/" in unit:
        top, bottom = unit.split("/", 1)
        if top.upper() in _SYMBOLS and bottom.strip().lower() in {"share", "shares"}:
            symbol = _SYMBOLS[top.upper()]
            return f"{symbol}{_grouped(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))}"
        return None

    if code in _SYMBOLS or (len(code) == _ISO_CODE_LENGTH and code.isalpha() and code != "GBX"):
        return money(number, code, style=style, in_table=in_table)

    reading: str | None = None
    if unit.lower() == "shares":
        reading = _grouped(number)
    elif unit in {"%", "percent"}:
        reading = f"{_trimmed(number)}%"
    elif unit.lower() in {"pure", "ratio", ""}:
        reading = _pure_reading(number, label=label)
    return reading


def cell(item: dict[str, Any], column: str, *, style: HouseStyle) -> str:
    """One table cell: the row's ``value`` formatted by its own ``unit`` and ``label``.

    Every other column passes through :func:`scalar` with no unit, which formats dates
    and separates thousands and otherwise leaves the author's text alone.
    """
    value = item.get(column, "")
    if column == "value":
        return scalar(
            value,
            style=style,
            unit=str(item.get("unit", "") or ""),
            label=str(item.get("label", "") or ""),
            in_table=True,
        )
    if isinstance(value, str):
        return prose(value, style=style)
    return scalar(value, style=style, in_table=True)


# A money amount the model wrote out longhand: five or more digits against an ISO code,
# which is the shape the live report printed mid-sentence. Shorter amounts — "45 USD per
# share" — are left as written, because rescaling them buys nothing.
_PROSE_MONEY: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.,])(\d{5,})(?:\.\d+)?\s?(USD|GBP|EUR)\b"
)

# The same longhand amount wearing a symbol and thousands separators: "$442,000,000",
# which the MTB report printed mid-sentence two pages from the front page's "$442m"
# (gap A66). Two comma groups minimum, so "$45,000" — a figure a writer may well want
# at that precision — is left as written.
_PROSE_SYMBOL_MONEY: Final[re.Pattern[str]] = re.compile(
    r"([$\u00a3\u20ac])(\d{1,3}(?:,\d{3}){2,})(?:\.\d+)?\b"
)

_SYMBOL_CURRENCIES: Final[dict[str, str]] = {"$": "USD", "\u00a3": "GBP", "\u20ac": "EUR"}

_PROSE_DATE: Final[re.Pattern[str]] = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def prose(text: str, *, style: HouseStyle) -> str:
    """Prose with its embedded machine-notation values re-said in the house style.

    Two rewrites only, both unambiguous: a longhand amount against an ISO currency code,
    and an ISO date. Anything else in the sentence is the writer's, and a formatter that
    edited more of a sentence than it could prove the meaning of would be an editor.
    """

    def _money(match: re.Match[str]) -> str:
        return money(Decimal(match.group(1)), match.group(2), style=style)

    def _symbol_money(match: re.Match[str]) -> str:
        amount = Decimal(match.group(2).replace(",", ""))
        return money(amount, _SYMBOL_CURRENCIES[match.group(1)], style=style)

    def _date(match: re.Match[str]) -> str:
        try:
            when = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return match.group(0)
        return date_text(when, style=style)

    rewritten = _PROSE_SYMBOL_MONEY.sub(_symbol_money, _PROSE_MONEY.sub(_money, text))
    return _PROSE_DATE.sub(_date, rewritten)


# -- Internals -----------------------------------------------------------------------------


def _pure_reading(value: Decimal, *, label: str) -> str | None:
    """How a label's author would say a dimensionless number aloud, or ``None``.

    ``0.462`` labelled "operating margin" is "46.2%"; labelled "current ratio" it reads
    "times" and takes the multiplication sign; labelled nothing recognisable it is
    nobody's to reinterpret.
    """
    lowered = f" {label.lower()} "
    if any(word in lowered for word in _PERCENT_WORDS):
        scaled = (value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"{_trimmed(scaled)}%"
    if any(word in lowered for word in _TIMES_WORDS):
        times = _trimmed(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return f"{times}\N{MULTIPLICATION SIGN}"
    return None


def _decimal_of(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal | int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return Decimal(text)
    return None


def _grouped(value: Decimal) -> str:
    """Thousands separators, decimals kept exactly as the value carries them."""
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,f}"


def _trimmed(value: Decimal) -> str:
    """The number with trailing zeros dropped: ``46.20`` prints ``46.2``, ``2.00`` ``2``."""
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text
