"""Re-running the ledger, and checking what it rests on.

Two of the gate's eight metrics come from here, and both work by walking stored
``calculations`` rows rather than by trusting anything in memory.

**Numerical consistency** re-executes every calculation from exactly what the row recorded —
the named traced function, the sourced inputs, the structural parameters — and compares the
answer with the one stored. The row is the whole input: a calculation that cannot be re-run
from its own record is a calculation whose provenance is decorative, and it fails the metric
rather than being skipped. This is also what makes the thirty golden calculations one fixture
rather than thirty test functions — a golden case is a hand-written row replayed through the
same harness, so the harness itself is exercised against answers a person computed.

**Assumption completeness** checks the other direction: not whether the arithmetic reproduces,
but whether what it rests on is still standing. Every input whose source is an assumption must
resolve to an assumption row somebody confirmed. An assumption amended after the run
un-confirms itself (`aer.services.assumptions`), so this is the metric that notices a report
whose stated basis has been pulled out from under it.

**The registry is derived, not maintained.** Every traced function in ``aer.calc`` announces
its own calculation name; the registry walks the modules and collects them, refusing
duplicates. A hand-kept mapping would be one function behind the codebase within a month, and
the miss would be silent — a calculation absent from the registry simply would not be
replayed, which is why :func:`replay` treats "no such function" as a failure rather than a
skip.
"""

from __future__ import annotations

import importlib
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Any, Final, get_type_hints

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.calc.units import CALC_CONTEXT, Quantity, SourceKind, SourceRef, SourceTable
from aer.core.enums import Grade
from aer.db.models import Assumption, Calculation
from aer.errors import AerError
from aer.eval.observations import CompletenessObservation, ReplayObservation

__all__ = [
    "CALC_MODULES",
    "RegistryError",
    "completeness_observations",
    "completeness_observations_for_job",
    "registry",
    "replay",
    "replay_observations_for_job",
]

# Every module that defines traced calculations. Listed rather than discovered by walking the
# package, so importing the registry cannot import something with side effects by accident —
# and a new calc module that is not added here fails the registry-coverage test in
# `tests/test_calc_golden.py` rather than silently not being replayable.
CALC_MODULES: Final[tuple[str, ...]] = (
    "aer.calc.basic",
    "aer.calc.bridge",
    "aer.calc.comps",
    "aer.calc.dcf",
    "aer.calc.fx",
    "aer.calc.outcomes",
    "aer.calc.portfolio",
    "aer.calc.prices",
    "aer.calc.quality",
    "aer.calc.ratios",
    "aer.calc.statements",
    "aer.calc.wacc",
)

# `revenue[0]`, `observations[3]` — the engine's expansion of a sequence argument into one
# recorded input per element. Collapsed back into sequences on replay.
_INDEXED: Final = re.compile(r"\A(?P<argument>\w+)\[(?P<index>\d+)\]\Z")


class RegistryError(AerError):
    """The calculation registry cannot be built or cannot answer.

    Always a code defect rather than bad data: two traced functions sharing a name, or a
    stored calculation naming a function that no longer exists. Both mean the ledger and the
    code have drifted apart, which is precisely what the replay harness exists to notice.
    """

    code = "eval_registry"


_REGISTRY: dict[str, Any] | None = None


def registry() -> Mapping[str, Any]:
    """Every traced calculation, by the name its records carry.

    Raises:
        RegistryError: If two functions claim one name. The ledger stores the name, so a
            duplicate would make every replay of it ambiguous.
    """
    global _REGISTRY  # noqa: PLW0603 -- built once; the modules it walks are immutable
    if _REGISTRY is not None:
        return _REGISTRY

    found: dict[str, Any] = {}
    for module_name in CALC_MODULES:
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            name = getattr(value, "calculation_name", None)
            if name is None or getattr(value, "__module__", "") != module.__name__:
                continue
            if name in found:
                message = (
                    f"Two traced functions claim the calculation name {name!r}. The ledger "
                    "stores the name, so every stored record of it is now ambiguous."
                )
                raise RegistryError(message, context={"name": name})
            found[name] = value

    _REGISTRY = found
    return found


def replay(
    *,
    name: str,
    label: str,
    inputs: Sequence[Mapping[str, Any]],
    parameters: Mapping[str, Any],
    expected_value: Decimal,
    expected_unit: str,
) -> ReplayObservation:
    """Re-run one stored calculation and compare it with what was stored.

    Never raises for a broken record: an input that cannot be reconstructed, a function that
    no longer exists, arithmetic that now refuses — each becomes an observation carrying the
    error, because "this row cannot be re-run" is exactly the finding the metric exists to
    surface, and an exception here would stop the harness at the first of them.
    """
    try:
        function = registry().get(name)
        if function is None:
            message = f"no traced function is named {name!r}"
            raise RegistryError(message, context={"name": name})

        kwargs = _reconstruct_inputs(inputs)
        kwargs |= _coerce_parameters(function, parameters)

        throwaway = CalculationContext(code_version="replay")
        result = function(throwaway, **kwargs)
    except Exception as exc:
        return ReplayObservation(
            name=label,
            expected=expected_value,
            expected_unit=expected_unit,
            replayed=None,
            replayed_unit=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    return ReplayObservation(
        name=label,
        expected=expected_value,
        expected_unit=expected_unit,
        replayed=result.value,
        replayed_unit=result.unit.symbol,
    )


def _reconstruct_inputs(inputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Stored inputs back into keyword arguments, sequences reassembled.

    The source on each rebuilt quantity is the stored one, not a synthetic marker: the
    traced wrapper refuses unsourced values, and a replay that invented sources would be
    exercising a slightly different function from the one that ran.
    """
    scalars: dict[str, Quantity] = {}
    sequences: dict[str, list[tuple[int, Quantity]]] = {}

    for stored in inputs:
        quantity = _quantity_from(stored)
        name = str(stored["name"])
        match = _INDEXED.match(name)
        if match is None:
            scalars[name] = quantity
        else:
            sequences.setdefault(match["argument"], []).append((int(match["index"]), quantity))

    rebuilt: dict[str, Any] = dict(scalars)
    for argument, indexed in sequences.items():
        indexed.sort(key=lambda pair: pair[0])
        positions = [index for index, _ in indexed]
        if positions != list(range(len(positions))):
            message = (
                f"The stored elements of {argument!r} are not a contiguous sequence "
                f"(indices {positions}). An element is missing from the record."
            )
            raise RegistryError(message, context={"argument": argument})
        rebuilt[argument] = [quantity for _, quantity in indexed]
    return rebuilt


# What a kind meant before ADR 0072 recorded the relation alongside it. Replay re-runs a
# stored calculation from its own record, so a row written under the old shape has to come
# back as something valid — and the table it comes back as is the one the resolver would
# have reached for anyway. The arithmetic does not depend on it; faithfulness does.
_TABLE_BY_KIND: Final[Mapping[SourceKind, SourceTable]] = {
    SourceKind.FACT: SourceTable.FINANCIAL_FACTS,
    SourceKind.ASSUMPTION: SourceTable.ASSUMPTIONS,
    SourceKind.CALCULATION: SourceTable.CALCULATIONS,
    # `ATTESTATION` postdates ADR 0072 by an entire record, so no row that omits its table
    # can be one — this entry exists so the lookup is total rather than because it is ever
    # reached, and a `KeyError` here would fail a replay for a shape that cannot occur.
    SourceKind.ATTESTATION: SourceTable.ATTESTATIONS,
}


def _quantity_from(stored: Mapping[str, Any]) -> Quantity:
    source = stored.get("source") or {}
    kind = SourceKind(str(source.get("kind", "fact")))
    stored_table = str(source.get("table", ""))
    table = SourceTable(stored_table) if stored_table else _TABLE_BY_KIND[kind]
    stored_grade = str(source.get("grade", ""))
    return Quantity.of(
        Decimal(str(stored["value"])),
        str(stored["unit"]),
        source=SourceRef(
            kind=kind,
            identifier=str(source.get("id", "replay")),
            table=table,
            label=str(source.get("label", "")),
            # Replayed rather than looked up, like everything else here: a replay re-runs a
            # calculation from its own record, and reading the attestation's *current*
            # grade would let a row documented since change what the original arithmetic is
            # replayed as having stood on.
            grade=Grade(stored_grade) if stored_grade else None,
        ),
    )


def _coerce_parameters(function: Any, parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Structural parameters back into the types the function's guards demand.

    JSONB flattens an enum to its string value and a date to ISO text; the guards on the
    other side (`_require_method`, `_require_frequency`, ...) refuse exactly those strings,
    and they are right to — a replay that loosened the guards would be replaying different
    code. So the coercion happens here, driven by the function's own annotations rather than
    by a table somebody keeps.
    """
    hints = get_type_hints(function.__wrapped__)
    coerced: dict[str, Any] = {}
    for name, value in parameters.items():
        annotation = hints.get(name)
        if value is None:
            coerced[name] = None
        elif isinstance(annotation, type) and issubclass(annotation, Enum):
            coerced[name] = annotation(value)
        elif annotation is date and isinstance(value, str):
            coerced[name] = date.fromisoformat(value)
        else:
            coerced[name] = value
    return coerced


# -- The harnesses ---------------------------------------------------------------------------


async def replay_observations_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> list[ReplayObservation]:
    """Every calculation a run stored, re-run from its own record."""
    rows = await _calculations_for(session, job_id)
    return [
        replay(
            name=row.name,
            label=f"{row.name}#{row.sequence}",
            inputs=row.inputs,
            parameters=row.parameters,
            expected_value=row.output_value,
            expected_unit=row.output_unit,
        )
        for row in rows
    ]


def completeness_observations(
    rows: Sequence[Calculation], *, confirmed: Mapping[str, bool]
) -> list[CompletenessObservation]:
    """Each calculation's assumption inputs, checked against what is confirmed *now*.

    ``confirmed`` maps assumption ids to their current approval state; an id absent from the
    mapping is an assumption that no longer resolves at all. Both are violations — invariant
    3's third leg is that an assumption reaching a report is one somebody agreed to, and an
    agreement that was later withdrawn or a row that vanished both break it.
    """
    observations: list[CompletenessObservation] = []
    for row in rows:
        cited = tuple(
            str(item.get("source", {}).get("id", ""))
            for item in row.inputs
            if item.get("source", {}).get("kind") == SourceKind.ASSUMPTION.value
        )
        unresolved = tuple(identifier for identifier in cited if identifier not in confirmed)
        unconfirmed = tuple(
            identifier
            for identifier in cited
            if identifier in confirmed and not confirmed[identifier]
        )
        observations.append(
            CompletenessObservation(
                name=f"{row.name}#{row.sequence}",
                assumption_ids=cited,
                unresolved=unresolved,
                unconfirmed=unconfirmed,
            )
        )
    return observations


async def completeness_observations_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> list[CompletenessObservation]:
    """The completeness corpus for one run, resolved against the assumptions table."""
    rows = await _calculations_for(session, job_id)

    cited: set[str] = set()
    for row in rows:
        for item in row.inputs:
            source = item.get("source", {})
            if source.get("kind") == SourceKind.ASSUMPTION.value:
                cited.add(str(source.get("id", "")))

    resolvable = [identifier for identifier in cited if _is_uuid(identifier)]
    confirmed: dict[str, bool] = {}
    if resolvable:
        found = await session.scalars(
            select(Assumption).where(Assumption.id.in_([uuid.UUID(i) for i in resolvable]))
        )
        confirmed = {str(row.id): bool(row.approved) for row in found}

    return completeness_observations(rows, confirmed=confirmed)


async def _calculations_for(session: AsyncSession, job_id: uuid.UUID) -> list[Calculation]:
    return list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job_id)
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def relative_delta(expected: Decimal, replayed: Decimal) -> Decimal:
    """How far a replayed figure sits from the stored one, as a fraction of the stored one.

    Against a stored figure of zero the delta is the absolute difference instead, because a
    fraction of nothing is undefined and "it was zero and now it is 3" is exactly as wrong as
    a large relative move.
    """
    with localcontext(CALC_CONTEXT):
        try:
            if expected == 0:
                return abs(replayed)
            return abs(replayed - expected) / abs(expected)
        except InvalidOperation:  # pragma: no cover -- both operands are finite Decimals
            return Decimal("Infinity")
