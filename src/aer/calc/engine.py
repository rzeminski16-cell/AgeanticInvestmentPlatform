"""Recording what was computed, from what, by which code.

**The rule this module enforces.** A traced function will not compute anything from a
number that cannot say where it came from. Every :class:`~aer.calc.units.Quantity` argument
must carry a :class:`~aer.calc.units.SourceRef` — a fact, an assumption, or another
calculation — and a bare ``Decimal`` argument raises rather than being accepted as a
convenience.

That is the whole point. A platform whose figures are auditable is one where "where did
this number come from?" has an answer at every step, and the only way to guarantee that is
to make the unanswerable case impossible to express rather than merely discouraged. A
check performed at the end, over the finished report, catches the numbers somebody
remembered to check.

**The formula is written by a human, not derived.** ``@traced`` takes the formula as a
string declared next to the function::

    @traced(name="cagr", formula="cagr = (end / start) ^ (1 / years) - 1")

Deriving it from the AST was considered and rejected. An AST rendering of a real financial
function is unreadable — it exposes guard clauses, intermediate names and Decimal
plumbing — and the audience for a formula string is a person checking whether the
arithmetic matches what they expected. A declared string can be wrong, but it is wrong
visibly, next to the code it describes, where a reviewer sees both.

**Inputs and parameters are different things.** An *input* is a measured quantity that
came from evidence and must be sourced. A *parameter* is a structural choice — how many
periods, which currency to present in — recorded verbatim so the calculation is
reproducible, but not claiming to be evidence. Both are persisted. Conflating them would
either force fake sources onto structural choices or let real measurements through
unsourced.

**Calculations chain.** The quantity a traced function returns carries a source pointing at
its own record, so feeding it into another traced function is exactly as sourced as feeding
in a fact. That is what makes lineage a tree rather than a list.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Concatenate, Final, ParamSpec

from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    Unit,
    UnsourcedValueError,
)

__all__ = [
    "UNKNOWN_CODE_VERSION",
    "CalculationContext",
    "CalculationInput",
    "CalculationRecord",
    "PeriodStamp",
    "traced",
]

UNKNOWN_CODE_VERSION: Final = "unknown"
"""Used when the git SHA cannot be determined.

A reproducibility warning rather than an error: a container shipped without a ``.git``
directory should still be able to compute, and a sentinel in the record makes the gap
visible to anyone later trying to reproduce the figure.
"""

P = ParamSpec("P")


@dataclass(frozen=True, slots=True)
class CalculationInput:
    """One sourced value that went into a calculation.

    The unit is stored as its rendered symbol rather than as a :class:`Unit`, because this
    record is destined for a JSONB column and a database row that needs the application to
    interpret it is a database row that stops being readable the moment the application
    changes.
    """

    name: str
    value: Decimal
    unit: str
    source_kind: SourceKind
    source_id: str
    source_label: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": str(self.value),
            "unit": self.unit,
            "source": {
                "kind": self.source_kind.value,
                "id": self.source_id,
                "label": self.source_label,
            },
        }


@dataclass(frozen=True, slots=True)
class PeriodStamp:
    """The reporting period a calculation was struck on.

    The live AAPL report printed an EBITDA above its own revenue because the ratio was an
    annual figure and the fact beside it a quarterly one, and nothing anywhere said so. A
    figure's period is part of what the figure *is*: a record without one can only be
    compared to another by hoping both meant the same span.

    ``label`` is the human form a reader compares by — "FY2025", "Q3 2026". The dates
    bound the span exactly for code that needs more than a name; either may be ``None``
    when the source facts did not state it.
    """

    label: str
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True, slots=True)
class CalculationRecord:
    """Everything needed to re-check one computation.

    ``id`` is generated here rather than by the database, because the output quantity has
    to reference it *before* anything is persisted — a calculation feeding another one
    must be identifiable while both are still in memory.
    """

    id: uuid.UUID
    name: str
    formula: str
    function_ref: str
    code_version: str
    inputs: tuple[CalculationInput, ...]
    output_value: Decimal
    output_unit: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()

    # The period scope in force when the calculation was struck, or None for one whose
    # result is not a statement-period figure at all (a discount rate, a price multiple
    # as at a date). Stamped by the context, not passed by callers — see
    # CalculationContext.period.
    period: PeriodStamp | None = None

    @property
    def source_ref(self) -> SourceRef:
        """This calculation, as something another calculation can cite."""
        return SourceRef.calculation(self.id, label=self.name)

    @property
    def input_sources(self) -> tuple[SourceRef, ...]:
        return tuple(
            SourceRef(kind=i.source_kind, identifier=i.source_id, label=i.source_label)
            for i in self.inputs
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "formula": self.formula,
            "function_ref": self.function_ref,
            "code_version": self.code_version,
            "inputs": [i.as_dict() for i in self.inputs],
            "parameters": dict(self.parameters),
            "output_value": str(self.output_value),
            "output_unit": self.output_unit,
            "assumptions": list(self.assumptions),
            "period": (
                {
                    "label": self.period.label,
                    "start": self.period.start.isoformat() if self.period.start else None,
                    "end": self.period.end.isoformat() if self.period.end else None,
                }
                if self.period
                else None
            ),
        }

    def __str__(self) -> str:
        return f"{self.name} = {self.output_value} {self.output_unit}  [{self.formula}]"


class CalculationContext:
    """Collects the record of every calculation performed during one piece of work.

    Not a global and not a context variable: passed explicitly as the first argument to
    every traced function. Ambient state would make a traced function's behaviour depend on
    something invisible at the call site, and would make two concurrent research runs share
    a ledger.

    ``code_version`` is supplied rather than resolved here, because resolving it means
    running ``git`` — I/O, which :mod:`aer.calc` is not permitted to do. The service layer
    passes :func:`aer.version.git_sha`'s answer in.
    """

    __slots__ = ("_period", "_records", "_struck", "code_version")

    def __init__(self, *, code_version: str = UNKNOWN_CODE_VERSION) -> None:
        self.code_version = code_version or UNKNOWN_CODE_VERSION
        self._records: list[CalculationRecord] = []
        self._period: PeriodStamp | None = None
        # Identity -> the record already struck for it, so a second identical call reuses
        # the first rather than adding a row (gap R14; see `add`).
        self._struck: dict[tuple[Any, ...], CalculationRecord] = {}

    @property
    def current_period(self) -> PeriodStamp | None:
        """The period scope in force, stamped onto every record struck while it holds."""
        return self._period

    @contextmanager
    def period(
        self, label: str, *, start: date | None = None, end: date | None = None
    ) -> Iterator[None]:
        """Stamp every calculation struck inside the block with this reporting period.

        A scope on the ledger rather than a parameter on every call, because the period
        is a property of the *pass* — "now computing FY2025" — and threading it through
        forty traced signatures would make every calculation's interface about something
        none of their arithmetic uses. Scopes restore what they replaced, so nesting is
        safe and leaving the block always returns to the enclosing state.
        """
        previous = self._period
        self._period = PeriodStamp(label=label, start=start, end=end)
        try:
            yield
        finally:
            self._period = previous

    @contextmanager
    def stamped(self, stamp: PeriodStamp | None) -> Iterator[None]:
        """Scope to a period already held as a stamp, or leave the scope untouched.

        For the caller striking a figure *of another period* inside this one's pass — a
        paired quality signal computing its base on the prior year's statements. Without
        it that row carries the current period's label over the previous period's inputs,
        which is a mislabelled figure rather than a duplicate one (gap R14).
        """
        if stamp is None:
            yield
            return
        with self.period(stamp.label, start=stamp.start, end=stamp.end):
            yield

    @property
    def records(self) -> tuple[CalculationRecord, ...]:
        """Every calculation performed, in the order it happened.

        Order is significant: a calculation can only cite ones that came before it, so
        persisting in this order never writes a row referencing one that does not exist.
        """
        return tuple(self._records)

    def add(self, record: CalculationRecord) -> CalculationRecord:
        """Record this calculation, or return the identical one already struck.

        **The same arithmetic on the same inputs is one derivation, however many callers
        ask for it** (gap R14). Several do: the ratio suite strikes EBITDA once for its
        margin and again inside net debt to EBITDA; days outstanding is struck three times
        as the receivable, inventory and payable ratios and all three again inside the cash
        conversion cycle; a paired quality signal recomputes its own base for the closing
        period. A row per call is what put 118 calculations on the CHRW note's approval
        page, with ``ebitda`` appearing twice at the same value in the same year and
        nothing to say which of the two rows a citation meant.

        Reusing rather than appending keeps lineage a tree: the second caller's result is
        attributed to the record the first one struck, so one figure has one id.

        **Identity is every field except the id** — name, formula, function, code version,
        inputs, output, parameters, assumptions and period. A different period, a different
        input or a different result is a different derivation and gets its own row;
        collapsing those would be a claim about the run that is not true.
        """
        identity = _identity_of(record)
        struck = self._struck.get(identity)
        if struck is not None:
            return struck

        self._records.append(record)
        self._struck[identity] = record
        return record

    def find(self, calculation_id: uuid.UUID | str) -> CalculationRecord | None:
        wanted = str(calculation_id)
        return next((r for r in self._records if str(r.id) == wanted), None)

    def named(self, name: str) -> tuple[CalculationRecord, ...]:
        return tuple(r for r in self._records if r.name == name)

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"<CalculationContext {len(self._records)} records at {self.code_version[:7]}>"


TracedFunction = Callable[Concatenate[CalculationContext, P], Quantity]


def _identity_of(record: CalculationRecord) -> tuple[Any, ...]:
    """What makes two records the same derivation: everything but the id.

    ``parameters`` is a mapping and mappings do not hash, so it is flattened to sorted
    pairs with each value rendered — a parameter's identity is what it *is*, and two calls
    whose parameters render differently are not the same call.
    """
    return (
        record.name,
        record.formula,
        record.function_ref,
        record.code_version,
        record.inputs,
        record.output_value,
        record.output_unit,
        tuple(sorted((key, repr(value)) for key, value in record.parameters.items())),
        record.assumptions,
        record.period,
    )


def traced(
    *, name: str, formula: str, assumptions: Sequence[str] = ()
) -> Callable[[TracedFunction[P]], TracedFunction[P]]:
    """Register a function as a recorded calculation.

    Args:
        name: What the calculation is called, e.g. ``"revenue_cagr"``. Appears in the
            provenance viewer and is what a reader looks for.
        formula: The arithmetic in a form a person can check, e.g.
            ``"cagr = (end / start) ^ (1 / years) - 1"``. Declared, not derived — see the
            module docstring.
        assumptions: Free-text notes about what the formula takes for granted, e.g. that
            periods are of equal length. Recorded on every invocation, because an
            assumption nobody wrote down is one a reviewer has to reverse-engineer.

    The wrapped function takes a :class:`CalculationContext` first, then its own
    arguments, and returns a :class:`~aer.calc.units.Quantity`. The wrapper validates every
    argument, records the computation, and returns the result attributed to that record.

    Raises:
        UnsourcedValueError: If any quantity argument has no source, or if a bare numeric
            value is passed where a sourced quantity belongs.
    """

    def decorate(function: TracedFunction[P]) -> TracedFunction[P]:
        function_ref = f"{function.__module__}:{function.__qualname__}"
        signature = inspect.signature(function)

        def wrapper(context: CalculationContext, /, *args: P.args, **kwargs: P.kwargs) -> Quantity:
            bound = _bind(signature, context, args, kwargs)
            inputs, parameters = _classify(bound, name=name)

            result = function(context, *args, **kwargs)
            _require_quantity(result, function_ref=function_ref, calculation=name)

            record = context.add(
                CalculationRecord(
                    id=uuid.uuid4(),
                    name=name,
                    formula=formula,
                    function_ref=function_ref,
                    code_version=context.code_version,
                    inputs=inputs,
                    output_value=result.value,
                    output_unit=result.unit.symbol,
                    parameters=parameters,
                    assumptions=tuple(assumptions),
                    period=context.current_period,
                )
            )
            # Attributed to its own record, so passing this result into another traced
            # function is exactly as sourced as passing in a fact. This is the line that
            # makes lineage a tree.
            return result.with_source(record.source_ref)

        wrapper.__name__ = function.__name__
        wrapper.__qualname__ = function.__qualname__
        wrapper.__doc__ = function.__doc__
        wrapper.__module__ = function.__module__
        # `inspect.signature` follows this, so a traced function still reports the parameters
        # it actually takes rather than the wrapper's `(*args, **kwargs)`. Without it the
        # decorator hides every signature it touches, and a test asserting that no calculation
        # has a default argument would pass vacuously by inspecting the wrapper instead.
        wrapper.__wrapped__ = function  # type: ignore[attr-defined]
        # Attached so a caller can display the formula without invoking anything, and so a
        # test can assert the declared formula matches the implementation.
        wrapper.calculation_name = name  # type: ignore[attr-defined]
        wrapper.formula = formula  # type: ignore[attr-defined]
        wrapper.function_ref = function_ref  # type: ignore[attr-defined]

        return wrapper

    return decorate


def _bind(
    signature: inspect.Signature,
    context: CalculationContext,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Match the call's arguments to their parameter names.

    Named rather than positional, so a recorded input says ``"start"`` rather than
    ``"argument 1"`` — which is the difference between a provenance record a person can
    read and one they have to decode against the source.
    """
    bound = signature.bind(context, *args, **kwargs)
    bound.apply_defaults()
    arguments = dict(bound.arguments)
    # The context is machinery, not an input to the arithmetic.
    arguments.pop(next(iter(signature.parameters)), None)
    return arguments


def _classify(
    arguments: Mapping[str, Any], *, name: str
) -> tuple[tuple[CalculationInput, ...], dict[str, Any]]:
    """Split a call's arguments into sourced inputs and structural parameters.

    Raises:
        UnsourcedValueError: On any quantity with no source, or any bare numeric value.
    """
    inputs: list[CalculationInput] = []
    parameters: dict[str, Any] = {}

    for argument_name, value in arguments.items():
        if isinstance(value, Quantity):
            inputs.append(_as_input(argument_name, value, calculation=name))
        elif isinstance(value, Decimal | float):
            # The rule the whole task turns on. A bare number is refused even though
            # accepting it would be convenient, because convenience here means a figure in
            # a report that nothing can account for.
            _refuse_bare_number(argument_name, value, calculation=name)
        elif isinstance(value, Sequence | frozenset | set) and not isinstance(value, str | bytes):
            inputs.extend(_expand_sequence(argument_name, value, calculation=name))
        else:
            # An int, a string, an enum, a bool, None. Structural, recorded verbatim.
            parameters[argument_name] = value

    return tuple(inputs), parameters


def _expand_sequence(
    argument_name: str, values: Iterable[Any], *, calculation: str
) -> list[CalculationInput]:
    """Record each element of a series argument as its own input.

    A year-on-year series takes a sequence of quantities. Recording the sequence as one
    input would lose which element came from which filing, so each is recorded separately
    with an index in its name.
    """
    expanded: list[CalculationInput] = []
    for index, value in enumerate(values):
        if isinstance(value, Quantity):
            expanded.append(_as_input(f"{argument_name}[{index}]", value, calculation=calculation))
        elif isinstance(value, Decimal | float):
            _refuse_bare_number(f"{argument_name}[{index}]", value, calculation=calculation)
        else:
            message = (
                f"{calculation}: {argument_name}[{index}] is a "
                f"{type(value).__name__}. A series input must contain quantities."
            )
            raise CalculationError(message, context={"calculation": calculation})
    return expanded


def _as_input(argument_name: str, value: Quantity, *, calculation: str) -> CalculationInput:
    if value.source is None:
        message = (
            f"{calculation}: the input {argument_name!r} ({value}) has no source. Every "
            "number entering a calculation must trace to a fact, an assumption or another "
            "calculation — otherwise the figure it produces cannot be defended, and a "
            "report is only as good as the worst-sourced number in it."
        )
        raise UnsourcedValueError(
            message,
            context={
                "calculation": calculation,
                "input": argument_name,
                "unit": value.unit.symbol,
            },
        )

    return CalculationInput(
        name=argument_name,
        value=value.value,
        unit=value.unit.symbol,
        source_kind=value.source.kind,
        source_id=value.source.identifier,
        source_label=value.source.label,
    )


def _require_quantity(result: object, *, function_ref: str, calculation: str) -> None:
    """Confirm a traced function returned a Quantity.

    Takes ``object`` so mypy cannot narrow the check away, for the same reason as
    :func:`aer.calc.units._require_exact_decimal`: the annotation is a promise to typed
    callers, and this is what catches the promise being broken.
    """
    if isinstance(result, Quantity):
        return
    message = (
        f"{function_ref} returned {type(result).__name__}, not a Quantity. A traced "
        "calculation must return a value carrying its unit."
    )
    raise CalculationError(message, context={"calculation": calculation})


def _refuse_bare_number(argument_name: str, value: object, *, calculation: str) -> None:
    message = (
        f"{calculation}: {argument_name!r} was passed as a bare "
        f"{type(value).__name__}. A traced calculation takes sourced quantities, not raw "
        "numbers — a raw number has no unit and no provenance, which are the two things "
        "that make a figure checkable. Wrap it: Quantity.of(value, unit, source=...)."
    )
    raise UnsourcedValueError(
        message,
        context={
            "calculation": calculation,
            "input": argument_name,
            "type": type(value).__name__,
        },
    )


def unit_of(record: CalculationRecord) -> Unit:
    """The record's output unit, parsed back from its stored symbol.

    Exists so that a persisted record can be fed back into further arithmetic without the
    caller re-deriving the unit from the calculation's name.
    """
    return Unit.parse(record.output_unit)
