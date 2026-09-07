"""Units that travel with the number, and refuse to be ignored.

**The failure this prevents.** ``143015000000`` is not a fact. It is dollars, or shares,
or dollars per share, and the distance between the first and the third is eleven orders of
magnitude. A codebase that passes bare ``Decimal`` values around gets this wrong
eventually — not through a dramatic bug, but through one function returning millions where
its caller expected units, and every figure downstream being off by a factor of a million
while looking entirely plausible.

**How it is prevented.** A :class:`Quantity` is a value *and* a unit. Arithmetic between
quantities operates on both. Adding dollars to pounds raises; there is no code path that
coerces, defaults, or picks one. The check is not a convention anybody has to remember —
it is what ``+`` does.

**Units are dimensional vectors, not strings.** A unit is a mapping from base symbol to
integer exponent: dollars is ``{USD: 1}``, dollars per share is ``{USD: 1, shares: -1}``,
and a ratio is ``{}``. Multiplication adds exponents and division subtracts them, so every
rule the specification asks for falls out of the arithmetic rather than being written
down separately:

* ``USD / USD`` → ``{}``, dimensionless. A margin is a pure number.
* ``USD / shares`` → ``{USD: 1, shares: -1}``.
* ``{} * USD`` → ``USD``. A growth rate times a revenue is a revenue.

String units would require a table of every legal combination, and the first combination
missing from the table would be a silent wrong answer.

**Currency conversion is never implicit.** ``USD`` and ``GBP`` are different base symbols,
so they simply do not add. Converting requires :meth:`Quantity.convert`, an explicit rate,
and — because a rate is itself a number that came from somewhere — a **source** on that
rate. An FX rate nobody can point at is an assumption pretending to be a fact.

**Every quantity carries where it came from.** ``source`` is what makes a figure defensible
and what :mod:`aer.calc.engine` requires before it will compute anything. A quantity with
no source is a number somebody typed.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    localcontext,
)
from enum import StrEnum
from typing import Final

from aer.core.enums import Grade
from aer.errors import AerError

__all__ = [
    "CALC_CONTEXT",
    "DIMENSIONLESS",
    "CalculationError",
    "Quantity",
    "SourceKind",
    "SourceRef",
    "SourceTable",
    "Unit",
    "UnitMismatchError",
    "UnsourcedValueError",
    "money",
    "ratio",
    "shares",
]


# Precision 34 is IEEE 754 decimal128. Chosen over Python's default 28 because a
# discounted cash flow chains dozens of multiplications and divisions, and the error in
# the last place compounds; 34 leaves enough headroom that intermediate rounding never
# reaches a figure anybody reads.
#
# The traps matter as much as the precision. By default Decimal returns Infinity for a
# division by zero and NaN for an invalid operation, and both propagate silently through
# every subsequent step to produce a report full of "NaN" that nobody can explain. Trapped,
# they raise at the operation that caused them.
#
# This is a *value*, not a mutation of the global context. Arithmetic enters it explicitly
# via `localcontext`, so importing this module changes nothing about how Decimal behaves
# anywhere else -- which is what keeps `aer.calc` free of side effects.
CALC_CONTEXT: Final = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero],
)

# ISO 4217 is three uppercase letters. Anything else in a currency position is a mistake,
# and catching it here beats discovering it when an FX lookup returns nothing.
_CURRENCY_PATTERN: Final = re.compile(r"\A[A-Z]{3}\Z")

# Non-currency base symbols this platform understands. Kept small deliberately: a unit
# system that accepts any string is a unit system in which a typo is a new dimension.
#
# `ratio` is a share reorganisation's multiplier (ADR 0094), and it is a *base* symbol
# rather than `DIMENSIONLESS` on purpose: the book's walk multiplies by a `ratio` movement
# and adds a `shares` one, so the two must not compare equal. A dimensionless split would
# be silently summed into the share count — a two-for-one reading as two extra shares.
_KNOWN_SYMBOLS: Final[frozenset[str]] = frozenset({"shares", "year", "day", "ratio"})

# EDGAR writes a dimensionless ratio as "pure". Mapped rather than treated as a base
# symbol, because it is not a dimension -- it is the absence of one. "segment" joins the
# aliases as a dimensionless count (polish P9): filers tag NumberOfOperatingSegments and
# NumberOfReportableSegments with it, and a count of segments is a bare number, not a
# dimension anything divides by -- the alternative warned six times on every clean run.
_DIMENSIONLESS_ALIASES: Final[frozenset[str]] = frozenset({"pure", "ratio", "", "1", "segment"})


class CalculationError(AerError):
    """A calculation could not be performed correctly.

    Never a wrong answer, always a refusal. Every subclass represents a situation where
    continuing would produce a number that looks fine and is not.
    """

    code = "calculation_error"
    http_status = 422


class UnitMismatchError(CalculationError):
    """An operation was attempted between incompatible units.

    The error this whole module exists to raise. It is never caught and recovered from by
    substituting a default: if the units do not match, the operation was a mistake, and
    the only correct response is to stop.
    """

    code = "unit_mismatch"


class UnsourcedValueError(CalculationError):
    """A quantity with no provenance was used where a sourced one is required."""

    code = "unsourced_value"


class SourceKind(StrEnum):
    """What kind of thing a quantity's value came from.

    Four kinds, and the list is closed. Every number resolves, eventually, to a fact
    somebody filed, an assumption somebody made and justified, an attestation somebody
    signed their name to, or a calculation over those three.

    **The fourth arrived by meeting the standard the first three set, not by relaxing it.**
    This docstring said "three, deliberately closed — a fourth kind would be a way in for a
    number with no story", and that sentence was the bar rather than the wall. A fill price
    is none of the first three: nobody filed it, nobody chose it, no code computed it. What
    ADR 0073 had to show was that an attestation arrives with a story of its own — two
    times, a named assertor, and a grade of evidence that propagates — rather than as an
    exemption from having one. Anything that cannot show as much still does not get in.
    """

    FACT = "fact"
    """A reported figure, traced to a filing and a hashed artefact.

    Published by somebody else, whichever relation holds it: a filing line, a macro
    observation, a closing price and an exchange rate all carry this guarantee. Which
    table to read is :class:`SourceTable`'s answer, not this one's."""

    CALCULATION = "calculation"
    """The output of another traced calculation. This is what makes lineage a tree."""

    ASSUMPTION = "assumption"
    """A value chosen rather than observed — a discount rate, a growth fade, an FX rate.
    Recorded with its justification, so a reviewer can disagree with it specifically."""

    ATTESTATION = "attestation"
    """What the operator's own book says, as at one time and as known at another.

    A fill price, a holding, a cash balance. Not filed, not chosen, not computed —
    asserted, by somebody whose name is on the assertion. Every such reference carries a
    :class:`~aer.core.enums.Grade`, and :class:`SourceRef` refuses one without it (ADR
    0073)."""


class SourceTable(StrEnum):
    """Which relation holds the row a source reference names.

    ``SourceKind`` and this answer different questions, and conflating them is what let a
    ``macro_observations`` id be stored as a bare ``fact`` that only ``financial_facts``
    would ever be searched for. A yield in ``macro_observations`` and a revenue line in
    ``financial_facts`` carry the same *guarantee* and live in different *relations*; the
    kind says the first and this says the second (ADR 0076).

    The kernel names tables and never touches one. These are identifiers a reader resolves,
    not a dependency on the database — ``calc`` stays pure and importable without it.
    """

    FINANCIAL_FACTS = "financial_facts"
    """A figure extracted from a filing."""

    MACRO_OBSERVATIONS = "macro_observations"
    """A published statistic at a vintage — a yield, an index level."""

    FX_RATES = "fx_rates"
    """One day's published exchange rate for one pair (ADR 0082).

    Its own relation rather than a macro observation, though the two tables are shaped
    alike: a rate is identified by a currency *pair* and there is no series key to look it
    up by. Reading one out of ``macro_observations`` would mean inventing a series for
    every pair the ECB publishes and one for every cross it does not."""

    SECURITIES = "securities"
    """A listing, and by extension the price bars adjusted from it."""

    ATTESTATIONS = "attestations"
    """Something the operator asserted about their own affairs — a trade, a balance.

    The leaf is the attestation, not one of its numbers, for the reason ``SECURITIES`` is
    the leaf for a price: a transaction carries a quantity, a price and a fee, and which of
    them a figure used is the calculation input's business rather than the reference's."""

    ASSUMPTIONS = "assumptions"
    CALCULATIONS = "calculations"

    RISK_SCENARIO_SHOCKS = "risk_scenario_shocks"
    """A fraction the operator stated a scenario moves a target by (ADR 0106).

    An assumption's guarantee — a number somebody chose, neither published nor a fact
    about the book — in its own relation, because a scenario shock is not a valuation
    input and has no run to be confirmed against."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a number came from.

    ``identifier`` is a database id in production and any stable string in a test. Typed
    loosely on purpose: the kernel is pure and must not depend on the database's notion of
    an id to be testable.

    There is deliberately no general ``fact()`` constructor. Several relations hold figures
    somebody published, and a single constructor covering them all is precisely the
    unstated default this class used to carry: it read as "a fact" and resolved as "a row
    in ``financial_facts``", and nothing complained when a caller meant neither. One
    constructor per relation makes the choice visible at the site that makes it.
    """

    kind: SourceKind
    identifier: str
    table: SourceTable
    label: str = ""

    # How strong the evidence is, and only an attestation has one. A filing is documented
    # by definition (invariant 1), an assumption is neither documented nor attested, and a
    # calculation's grade is whatever its inputs make it — which is
    # :func:`aer.calc.attestation.grade_of`'s answer rather than a field's.
    #
    # **On the reference rather than looked up**, because the pure kernel has to be able to
    # state the property. A NAV computed from an attested holding is an attested NAV, and a
    # module that had to ask a database which grade a leaf carried could not say so without
    # a session — so the containment would live in the service layer, one caller away from
    # being forgotten.
    grade: Grade | None = None

    def __post_init__(self) -> None:
        if self.kind is SourceKind.ATTESTATION and self.grade is None:
            message = (
                f"The attestation {self.identifier!r} has no grade. A figure the operator "
                "asserted is either documented or attested, and code above this has to know "
                "which — an ungraded one would propagate as though it were evidenced."
            )
            raise UnsourcedValueError(message, context={"identifier": self.identifier})
        if self.kind is not SourceKind.ATTESTATION and self.grade is not None:
            message = (
                f"A {self.kind.value} reference carries a grade ({self.grade.value}). The "
                "grade is what distinguishes a documented attestation from a typed one; on "
                "anything else it is a claim about evidence that nothing checks."
            )
            raise CalculationError(
                message, context={"kind": self.kind.value, "grade": self.grade.value}
            )

    @classmethod
    def financial_fact(cls, identifier: str | uuid.UUID, *, label: str = "") -> SourceRef:
        return cls(
            kind=SourceKind.FACT,
            identifier=str(identifier),
            table=SourceTable.FINANCIAL_FACTS,
            label=label,
        )

    @classmethod
    def macro_observation(cls, identifier: str | uuid.UUID, *, label: str = "") -> SourceRef:
        return cls(
            kind=SourceKind.FACT,
            identifier=str(identifier),
            table=SourceTable.MACRO_OBSERVATIONS,
            label=label,
        )

    @classmethod
    def fx_rate(cls, identifier: str | uuid.UUID, *, label: str = "") -> SourceRef:
        """A published exchange-rate observation.

        A ``FACT`` like the other two: somebody published this rate for this day and the
        response it was parsed from is archived. An FX rate a person *typed* is not this —
        it has no publication behind it, and ADR 0082 sends it to the attestation record
        rather than letting it in here wearing a fact's guarantee.
        """
        return cls(
            kind=SourceKind.FACT,
            identifier=str(identifier),
            table=SourceTable.FX_RATES,
            label=label,
        )

    @classmethod
    def security(cls, identifier: str | uuid.UUID, *, label: str = "") -> SourceRef:
        return cls(
            kind=SourceKind.FACT,
            identifier=str(identifier),
            table=SourceTable.SECURITIES,
            label=label,
        )

    @classmethod
    def attestation(
        cls, identifier: str | uuid.UUID, *, grade: Grade, label: str = ""
    ) -> SourceRef:
        """Something the operator asserted, with the grade of evidence behind it.

        ``grade`` is keyword-only and has no default. A default would be a decision made
        once, invisibly, for every call site that did not think about it — and whichever
        way it fell it would be wrong: defaulting to documented lets a typed figure travel
        as evidence, defaulting to attested marks a hashed custodian statement as hearsay.
        """
        return cls(
            kind=SourceKind.ATTESTATION,
            identifier=str(identifier),
            table=SourceTable.ATTESTATIONS,
            label=label,
            grade=grade,
        )

    @classmethod
    def calculation(cls, identifier: str | uuid.UUID, *, label: str = "") -> SourceRef:
        return cls(
            kind=SourceKind.CALCULATION,
            identifier=str(identifier),
            table=SourceTable.CALCULATIONS,
            label=label,
        )

    @classmethod
    def assumption(cls, identifier: str | uuid.UUID, *, label: str = "") -> SourceRef:
        return cls(
            kind=SourceKind.ASSUMPTION,
            identifier=str(identifier),
            table=SourceTable.ASSUMPTIONS,
            label=label,
        )

    @classmethod
    def scenario_shock(cls, identifier: str | uuid.UUID, *, label: str = "") -> SourceRef:
        """A shock the operator stated: an assumption's guarantee in the scenario's relation."""
        return cls(
            kind=SourceKind.ASSUMPTION,
            identifier=str(identifier),
            table=SourceTable.RISK_SCENARIO_SHOCKS,
            label=label,
        )

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.identifier}"


@dataclass(frozen=True, slots=True)
class Unit:
    """A dimensional vector: base symbols to integer exponents.

    Stored as a sorted tuple of pairs rather than a dict so the value is hashable, is
    comparable by equality, and renders the same way every time. Exponents of zero are
    removed at construction, so ``USD * shares / shares`` compares equal to ``USD`` rather
    than merely behaving like it.
    """

    dimensions: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        cleaned = tuple(sorted((symbol, power) for symbol, power in self.dimensions if power != 0))
        for symbol, _ in cleaned:
            _validate_symbol(symbol)
        object.__setattr__(self, "dimensions", cleaned)

    # -- Construction --------------------------------------------------------------------

    @classmethod
    def base(cls, symbol: str, power: int = 1) -> Unit:
        """A unit of one base symbol, e.g. ``Unit.base("USD")``."""
        return cls(((symbol, power),))

    @classmethod
    def currency(cls, code: str) -> Unit:
        """A currency unit from an ISO 4217 code.

        Raises:
            UnitMismatchError: If the code is not three uppercase letters.
        """
        normalised = code.strip().upper()
        if not _CURRENCY_PATTERN.match(normalised):
            message = (
                f"{code!r} is not an ISO 4217 currency code. Three uppercase letters, "
                "e.g. USD or GBP."
            )
            raise UnitMismatchError(message, context={"code": code})
        return cls.base(normalised)

    @classmethod
    def parse(cls, text: str) -> Unit:
        """Parse a unit string, including the forms EDGAR uses and the ones this module
        renders.

        Understands ``USD``, ``shares``, ``USD/shares``, ``USD^2``, ``USD·shares``,
        ``pure`` and the empty string.

        **This is the exact inverse of :attr:`symbol`, and it has to be.** A calculation's
        output unit is stored in the database as text and read back by
        :func:`aer.calc.engine.unit_of`; a unit that renders but does not parse would make
        a persisted calculation unusable as an input to the next one — and would do so only
        for the squared and compound units, which are precisely the ones nobody tests by
        hand.

        Deliberately not a general expression parser. There are no brackets and no implicit
        multiplication: a grammar rich enough to need them is one in which a typo parses
        successfully into the wrong dimension.

        Raises:
            UnitMismatchError: If the string is not a unit this platform knows.
        """
        cleaned = text.strip()
        if cleaned.lower() in _DIMENSIONLESS_ALIASES:
            return DIMENSIONLESS

        numerator, separator, denominator = cleaned.partition("/")
        unit = cls._product(numerator)
        if separator:
            unit = unit / cls._product(denominator)
        return unit

    @classmethod
    def _product(cls, text: str) -> Unit:
        """A run of atoms joined by the product separator, e.g. ``USD·shares``."""
        unit = DIMENSIONLESS
        for part in text.split("·"):
            unit = unit * cls._atom(part)
        return unit

    @classmethod
    def _atom(cls, text: str) -> Unit:
        """One symbol, optionally with an integer exponent: ``shares`` or ``USD^2``."""
        symbol = text.strip()
        if symbol.lower() in _DIMENSIONLESS_ALIASES:
            return DIMENSIONLESS

        base, caret, exponent_text = symbol.partition("^")
        power = 1
        if caret:
            try:
                power = int(exponent_text)
            except ValueError as exc:
                message = f"{symbol!r} has a non-integer exponent. Units take whole powers."
                raise UnitMismatchError(message, context={"symbol": symbol}) from exc

        base = base.strip()
        if base in _KNOWN_SYMBOLS:
            return cls.base(base, power)
        return cls.currency(base) ** power

    # -- Algebra -------------------------------------------------------------------------

    def __mul__(self, other: Unit) -> Unit:
        combined: dict[str, int] = dict(self.dimensions)
        for symbol, power in other.dimensions:
            combined[symbol] = combined.get(symbol, 0) + power
        return Unit(tuple(combined.items()))

    def __truediv__(self, other: Unit) -> Unit:
        return self * other**-1

    def __pow__(self, exponent: int) -> Unit:
        return Unit(tuple((symbol, power * exponent) for symbol, power in self.dimensions))

    # -- Inspection ----------------------------------------------------------------------

    @property
    def is_dimensionless(self) -> bool:
        """Whether this is a pure number — a ratio, a margin, a growth rate."""
        return not self.dimensions

    @property
    def currencies(self) -> tuple[str, ...]:
        """The currency symbols appearing in this unit, in any position."""
        return tuple(symbol for symbol, _ in self.dimensions if _CURRENCY_PATTERN.match(symbol))

    @property
    def symbol(self) -> str:
        """The unit rendered for a human, e.g. ``USD/shares``.

        Round-trips through :meth:`parse` for every unit this platform constructs, which
        is what lets a unit be stored as text in the database and read back exactly.
        """
        if not self.dimensions:
            return "pure"

        positive = [(s, p) for s, p in self.dimensions if p > 0]
        negative = [(s, -p) for s, p in self.dimensions if p < 0]

        top = "·".join(_render(s, p) for s, p in positive) if positive else "1"
        if not negative:
            return top
        bottom = "·".join(_render(s, p) for s, p in negative)
        return f"{top}/{bottom}"

    def __str__(self) -> str:
        return self.symbol


def _render(symbol: str, power: int) -> str:
    return symbol if power == 1 else f"{symbol}^{power}"


def _validate_symbol(symbol: str) -> None:
    if symbol in _KNOWN_SYMBOLS or _CURRENCY_PATTERN.match(symbol):
        return
    message = (
        f"{symbol!r} is not a unit this platform knows. Known: "
        f"{', '.join(sorted(_KNOWN_SYMBOLS))}, or an ISO 4217 currency code. A unit "
        "system that accepts any string is one in which a typo becomes a new dimension."
    )
    raise UnitMismatchError(message, context={"symbol": symbol})


DIMENSIONLESS: Final = Unit()
"""A pure number: a ratio, a margin, a growth rate, a multiple."""


def _require_exact_decimal(value: object) -> None:
    """Refuse anything that is not a ``Decimal``, at construction.

    Takes ``object`` rather than ``Decimal`` deliberately. Annotated as ``Decimal``, mypy
    would narrow the check away as unreachable — correctly, for typed callers. But the
    annotation is not what runs, and a float arriving from an untyped boundary (a JSON
    body, a notebook, a test) is the start of every rounding complaint anyone will ever
    make about this platform. Refusing at construction, where the fix is one word, beats
    explaining at presentation, where it is not.
    """
    if isinstance(value, Decimal):
        return
    message = (
        f"A Quantity value must be a Decimal, not {type(value).__name__}. Binary "
        "floating point cannot represent 0.1 exactly, and a research platform that "
        "silently rounds cash flows is worthless."
    )
    raise CalculationError(message, context={"type": type(value).__name__})


@dataclass(frozen=True, slots=True)
class Quantity:
    """A number, its unit, and where it came from.

    Frozen. Every operation returns a new quantity, so an intermediate value cannot be
    mutated underneath a calculation that already used it — which matters because a
    calculation's recorded inputs must still be the values it actually used when the
    record is written.
    """

    value: Decimal
    unit: Unit = DIMENSIONLESS
    source: SourceRef | None = None

    def __post_init__(self) -> None:
        _require_exact_decimal(self.value)
        if self.value.is_nan() or self.value.is_infinite():
            message = f"{self.value} is not a usable quantity."
            raise CalculationError(message, context={"value": str(self.value)})

    # -- Construction --------------------------------------------------------------------

    @classmethod
    def of(
        cls,
        value: Decimal | int | str,
        unit: Unit | str = DIMENSIONLESS,
        *,
        source: SourceRef | None = None,
    ) -> Quantity:
        """Build a quantity, accepting a unit string and an exact numeric literal.

        ``float`` is deliberately absent from the accepted types. A string or an int
        converts exactly; a float has already lost whatever precision it was going to
        lose before this function sees it.
        """
        return cls(
            value=value if isinstance(value, Decimal) else Decimal(str(value)),
            unit=Unit.parse(unit) if isinstance(unit, str) else unit,
            source=source,
        )

    def with_source(self, source: SourceRef) -> Quantity:
        """The same value and unit, attributed to a source."""
        return replace(self, source=source)

    # -- Arithmetic ----------------------------------------------------------------------

    def __add__(self, other: Quantity) -> Quantity:
        self._require_same_unit(other, "add")
        return Quantity(value=_arith(lambda: self.value + other.value), unit=self.unit)

    def __sub__(self, other: Quantity) -> Quantity:
        self._require_same_unit(other, "subtract")
        return Quantity(value=_arith(lambda: self.value - other.value), unit=self.unit)

    def __mul__(self, other: Quantity) -> Quantity:
        return Quantity(value=_arith(lambda: self.value * other.value), unit=self.unit * other.unit)

    def __truediv__(self, other: Quantity) -> Quantity:
        if other.value == 0:
            message = (
                "Division by zero. A ratio with a zero denominator is undefined, not "
                "infinite, and substituting a large number here would put a fabricated "
                "figure into a report."
            )
            raise CalculationError(
                message, context={"numerator": str(self.value), "unit": self.unit.symbol}
            )
        return Quantity(value=_arith(lambda: self.value / other.value), unit=self.unit / other.unit)

    def __neg__(self) -> Quantity:
        return Quantity(value=-self.value, unit=self.unit)

    def __abs__(self) -> Quantity:
        return Quantity(value=abs(self.value), unit=self.unit)

    def power(self, exponent: Decimal | int) -> Quantity:
        """Raise to a power.

        A dimensioned quantity may only be raised to an integer power — ``USD^1.5`` is not
        a unit anything has. A dimensionless one may take any exponent, which is what a
        CAGR's ``^(1/n)`` needs.

        Raises:
            UnitMismatchError: If a dimensioned quantity gets a fractional exponent.
        """
        if isinstance(exponent, int) or exponent == exponent.to_integral_value():
            whole = int(exponent)
            return Quantity(value=_arith(lambda: self.value**whole), unit=self.unit**whole)

        if not self.unit.is_dimensionless:
            message = (
                f"{self.unit.symbol} cannot be raised to the fractional power {exponent}. "
                "There is no such unit. Only a dimensionless quantity has fractional "
                "powers."
            )
            raise UnitMismatchError(
                message, context={"unit": self.unit.symbol, "exponent": str(exponent)}
            )

        if self.value < 0:
            message = (
                f"A fractional power of the negative number {self.value} is not real. "
                "This usually means a growth calculation was handed a sign change, which "
                "has no meaningful compound rate."
            )
            raise CalculationError(message, context={"value": str(self.value)})

        return Quantity(value=_arith(lambda: self.value**exponent), unit=DIMENSIONLESS)

    # -- Currency ------------------------------------------------------------------------

    def convert(self, target: Unit | str, *, rate: Quantity) -> Quantity:
        """Convert to another currency using an explicit, sourced rate.

        Args:
            target: The unit to convert into.
            rate: The exchange rate, as a quantity whose unit is ``target/source`` — so
                converting USD to GBP needs a rate in ``GBP/USD``. Stating the rate's own
                unit is what stops it being applied upside down, which is a mistake that
                produces a number roughly right in magnitude and wrong by the square of
                the rate.

        Raises:
            UnsourcedValueError: If the rate has no source. An FX rate nobody can point at
                is an assumption pretending to be a fact, and a valuation resting on one
                is not defensible.
            UnitMismatchError: If applying the rate does not produce the target unit.
        """
        wanted = Unit.parse(target) if isinstance(target, str) else target

        if rate.source is None:
            message = (
                "An exchange rate must carry a source. A rate nobody can point at is an "
                "assumption pretending to be a fact, and every figure converted with it "
                "inherits that."
            )
            raise UnsourcedValueError(
                message, context={"from": self.unit.symbol, "to": wanted.symbol}
            )

        produced = self.unit * rate.unit
        if produced != wanted:
            message = (
                f"Converting {self.unit.symbol} with a rate in {rate.unit.symbol} produces "
                f"{produced.symbol}, not {wanted.symbol}. The rate is the wrong way up, or "
                "for the wrong pair."
            )
            raise UnitMismatchError(
                message,
                context={
                    "from": self.unit.symbol,
                    "rate_unit": rate.unit.symbol,
                    "produced": produced.symbol,
                    "wanted": wanted.symbol,
                },
            )

        return Quantity(value=_arith(lambda: self.value * rate.value), unit=wanted)

    # -- Presentation --------------------------------------------------------------------

    def round_to(self, places: int) -> Quantity:
        """Round for display.

        The **only** place rounding happens deliberately. Rounding intermediate values is
        how a chain of calculations drifts away from its own inputs; the context carries
        34 digits precisely so that nothing has to.
        """
        exponent = Decimal(1).scaleb(-places)
        return Quantity(
            value=self.value.quantize(exponent, rounding=ROUND_HALF_EVEN, context=CALC_CONTEXT),
            unit=self.unit,
            source=self.source,
        )

    # -- Comparison ----------------------------------------------------------------------

    def __lt__(self, other: Quantity) -> bool:
        self._require_same_unit(other, "compare")
        return self.value < other.value

    def __le__(self, other: Quantity) -> bool:
        self._require_same_unit(other, "compare")
        return self.value <= other.value

    def __gt__(self, other: Quantity) -> bool:
        self._require_same_unit(other, "compare")
        return self.value > other.value

    def __ge__(self, other: Quantity) -> bool:
        self._require_same_unit(other, "compare")
        return self.value >= other.value

    def __eq__(self, other: object) -> bool:
        """Equal when the value and the unit are equal. Provenance is not identity.

        Two quantities of $5 are the same quantity whether one came from a filing and the
        other from an assumption. Where they came from matters enormously for whether a
        claim is defensible, and not at all for whether the numbers are the same.
        """
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.value == other.value and self.unit == other.unit

    def __hash__(self) -> int:
        return hash((self.value, self.unit))

    # -- Internals -----------------------------------------------------------------------

    def _require_same_unit(self, other: Quantity, operation: str) -> None:
        if self.unit == other.unit:
            return
        hint = ""
        if self.unit.currencies and other.unit.currencies:
            hint = " Currencies never convert implicitly — use convert() with a sourced rate."
        message = f"Cannot {operation} {other.unit.symbol} and {self.unit.symbol}.{hint}"
        raise UnitMismatchError(
            message,
            context={
                "operation": operation,
                "left": self.unit.symbol,
                "right": other.unit.symbol,
            },
        )

    def __str__(self) -> str:
        return f"{self.value} {self.unit.symbol}"


def _arith(operation: Callable[[], Decimal]) -> Decimal:
    """Run one Decimal operation inside the calculation context.

    Wrapped so that precision and the traps are applied uniformly, rather than depending
    on whatever context the caller's thread happened to have. A trapped overflow or
    invalid operation surfaces as a :class:`CalculationError` rather than as a bare
    ``decimal`` exception nobody upstream is catching.
    """
    try:
        with localcontext(CALC_CONTEXT):
            result: Decimal = operation()
            return +result
    except (InvalidOperation, DivisionByZero, OverflowError) as exc:
        message = f"The arithmetic could not be performed exactly ({type(exc).__name__})."
        raise CalculationError(message, context={"detail": str(exc)}) from exc


# -- Convenience constructors ------------------------------------------------------------
#
# Short names for the three units that appear in almost every calculation. Worth having
# because `money("143015000000", "USD", source=...)` reads like the thing it is, and
# `Quantity(Decimal("143015000000"), Unit.currency("USD"), source)` does not.


def money(
    value: Decimal | int | str, currency: str, *, source: SourceRef | None = None
) -> Quantity:
    """An amount in a currency."""
    return Quantity.of(value, Unit.currency(currency), source=source)


def shares(value: Decimal | int | str, *, source: SourceRef | None = None) -> Quantity:
    """A share count."""
    return Quantity.of(value, Unit.base("shares"), source=source)


def ratio(value: Decimal | int | str, *, source: SourceRef | None = None) -> Quantity:
    """A dimensionless number: a margin, a growth rate, a multiple."""
    return Quantity.of(value, DIMENSIONLESS, source=source)
