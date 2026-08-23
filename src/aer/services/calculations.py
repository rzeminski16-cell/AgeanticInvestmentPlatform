"""Persisting calculations, and walking their lineage back to evidence.

Two operations.

**Persisting is all-or-nothing.** A :class:`~aer.calc.engine.CalculationContext` holds a
chain of calculations, each citing the ones before it. Writing half of that chain produces
rows whose inputs reference calculations that do not exist — a provenance record that
cannot be resolved, which is worse than no record at all because it looks resolvable until
somebody tries. One savepoint, one outcome.

**Lineage resolves what the schema deliberately does not.** A calculation's inputs are
JSONB, so the database cannot guarantee every source id points at something real; see
``aer.db.models.calculation`` for why that trade was made. :func:`lineage` is the other
half of that decision: it walks the tree, resolves each reference, and **reports the ones
that dangle** rather than pretending they resolved. A broken chain that says so is
recoverable; one that quietly renders as a leaf is not.

The walk is breadth-first with a visited set. Calculations form a directed acyclic graph in
principle — a record can only cite ones created before it — but the guard is present anyway
because "in principle" is doing a lot of work in a system where rows can be written by a
migration, a script, or a future service.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.calc.engine import CalculationContext, CalculationRecord
from aer.calc.units import SourceKind, SourceTable
from aer.db.models import (
    Assumption,
    Attestation,
    Calculation,
    FinancialFact,
    FxRateRow,
    MacroObservationRow,
    Security,
)
from aer.errors import ValidationError
from aer.version import git_sha

__all__ = [
    "LineageNode",
    "lineage",
    "new_context",
    "persist_context",
]

_log = structlog.get_logger("aer.services.calculations")

# How deep a lineage walk goes before giving up. A real valuation chain is perhaps ten
# deep; a hundred means something is wrong, and an unbounded walk over corrupt data is a
# hung request rather than an error message.
MAX_LINEAGE_DEPTH = 100


def new_context() -> CalculationContext:
    """A calculation context stamped with the running code version.

    The one place the git SHA enters the kernel. :mod:`aer.calc` is pure and cannot shell
    out to git, so the impurity lives here, at the edge, where it is visible — and is
    cached by :func:`aer.version.git_sha`, so it costs one subprocess per process rather
    than one per calculation.
    """
    return CalculationContext(code_version=git_sha() or "unknown")


async def persist_context(
    session: AsyncSession,
    context: CalculationContext,
    *,
    job_id: uuid.UUID,
) -> list[Calculation]:
    """Write every calculation in a context, or none of them.

    Args:
        context: The calculations performed. Written in the order they happened, so a row
            citing another is never written before the row it cites.
        job_id: The run these belong to.

    Returns:
        The persisted rows, in the same order.

    Raises:
        ValidationError: If the context is empty. Persisting nothing is almost always a
            caller that forgot to pass the context its functions actually wrote to, and
            silently succeeding would hide that until the report had no numbers in it.
    """
    if not context.records:
        message = (
            "This calculation context is empty. Persisting nothing usually means the "
            "traced functions were given a different context from the one being saved."
        )
        raise ValidationError(message, context={"job_id": str(job_id)})

    # Sequences continue the job's ledger rather than restarting at zero, and the advisory
    # lock is what keeps that true when two workflow nodes persist concurrently (task 34):
    # without it both read the same maximum and write overlapping ranges, and a replay
    # label like ``present_value#0`` would name two different rows of one run. The lock is
    # per job, transaction-scoped, and held only across this read-then-write.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))").bindparams(
            key=f"calculations:{job_id}"
        )
    )
    base = await session.scalar(
        select(func.coalesce(func.max(Calculation.sequence) + 1, 0)).where(
            Calculation.job_id == job_id
        )
    )

    rows = [
        _row_for(record, job_id=job_id, sequence=int(base or 0) + index)
        for index, record in enumerate(context.records)
    ]

    # A savepoint, so a constraint violation part-way through leaves the caller's
    # transaction usable and the table untouched -- rather than half a provenance chain
    # and a session nobody can flush.
    async with session.begin_nested():
        session.add_all(rows)
        await session.flush()

    _log.info(
        "calculations.persisted",
        job_id=str(job_id),
        count=len(rows),
        code_version=context.code_version,
        names=sorted({record.name for record in context.records}),
    )
    return rows


def _row_for(record: CalculationRecord, *, job_id: uuid.UUID, sequence: int) -> Calculation:
    return Calculation(
        id=record.id,
        job_id=job_id,
        sequence=sequence,
        name=record.name,
        formula=record.formula,
        function_ref=record.function_ref,
        code_version=record.code_version,
        inputs=[i.as_dict() for i in record.inputs],
        parameters=dict(record.parameters),
        assumptions=list(record.assumptions),
        output_value=record.output_value,
        output_unit=record.output_unit,
        period_label=record.period.label if record.period else None,
        period_start=record.period.start if record.period else None,
        period_end=record.period.end if record.period else None,
    )


@dataclass(slots=True)
class LineageNode:
    """One node of a provenance tree.

    ``kind`` says what this node is — a calculation, a fact, an assumption, or a reference
    that could not be resolved. The last is not an error state to be hidden: a report whose
    provenance viewer shows "this input points at a fact that is no longer here" is telling
    the reviewer something true and important.
    """

    kind: str
    identifier: str
    label: str = ""
    value: Decimal | None = None
    unit: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    inputs: list[LineageNode] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        """Whether the walk ends here: a fact, an assumption, or a dangling reference."""
        return not self.inputs

    @property
    def is_resolved(self) -> bool:
        return self.kind != "missing"

    def walk(self) -> list[LineageNode]:
        """Every node in the tree, this one first."""
        found = [self]
        for child in self.inputs:
            found.extend(child.walk())
        return found

    @property
    def leaves(self) -> list[LineageNode]:
        """The evidence at the bottom: facts, assumptions, and anything unresolved.

        The answer to "what does this number ultimately rest on?", which is the question a
        reviewer asks about a figure they doubt.
        """
        return [node for node in self.walk() if node.is_leaf]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.identifier,
            "label": self.label,
            "value": str(self.value) if self.value is not None else None,
            "unit": self.unit,
            "detail": self.detail,
            "inputs": [child.as_dict() for child in self.inputs],
        }


async def lineage(
    session: AsyncSession, calculation_id: uuid.UUID, *, max_depth: int = MAX_LINEAGE_DEPTH
) -> LineageNode:
    """Resolve a calculation's inputs recursively, down to facts and assumptions.

    Raises:
        ValidationError: If there is no such calculation.
    """
    root_row = await session.get(Calculation, calculation_id)
    if root_row is None:
        message = f"No calculation {calculation_id}."
        raise ValidationError(message, context={"calculation_id": str(calculation_id)})

    root = _calculation_node(root_row)

    # Breadth-first, so a wide tree resolves in a bounded number of round trips rather
    # than one per node. `visited` guards against a cycle that should be impossible and
    # would otherwise hang the request.
    queue: deque[tuple[LineageNode, Calculation, int]] = deque([(root, root_row, 0)])
    visited: set[str] = {str(calculation_id)}

    while queue:
        node, row, depth = queue.popleft()
        if depth >= max_depth:
            node.inputs.append(
                LineageNode(
                    kind="truncated",
                    identifier=str(row.id),
                    label=f"lineage deeper than {max_depth} levels",
                )
            )
            continue

        for raw in row.inputs:
            child, next_row = await _resolve_input(session, raw, visited=visited)
            node.inputs.append(child)
            if next_row is not None:
                visited.add(str(next_row.id))
                queue.append((child, next_row, depth + 1))

    return root


@dataclass(frozen=True, slots=True)
class _StoredInput:
    """One entry of a calculation's ``inputs`` JSONB, read defensively.

    Every field is coerced rather than trusted. This is application-written JSON, but it
    is JSON in a column that a migration or a script could also write, and a provenance
    viewer that raises on one malformed entry shows nothing instead of showing the rest.
    """

    kind: str
    identifier: str
    table: str
    label: str
    value: Decimal | None
    unit: str
    grade: str = ""

    @classmethod
    def of(cls, raw: dict[str, Any]) -> _StoredInput:
        source = raw.get("source") or {}
        return cls(
            kind=str(source.get("kind", "")),
            identifier=str(source.get("id", "")),
            # Absent on every row written before ADR 0072. An empty string is the signal
            # for the compatibility walk below, not a table nobody registered.
            table=str(source.get("table", "")),
            label=str(source.get("label") or raw.get("name") or ""),
            value=_decimal_or_none(raw.get("value")),
            unit=str(raw.get("unit", "")),
            # Only an attestation writes one. Read from the ledger rather than from the
            # row it points at, because the grade a figure was *computed under* is what a
            # reader needs — a row corrected to documented afterwards does not retroactively
            # evidence arithmetic that ran before it (ADR 0069).
            grade=str(source.get("grade", "")),
        )

    def missing(self, expected: str) -> LineageNode:
        return _missing(self.identifier, self.label, self.value, self.unit, expected)


async def _resolve_input(
    session: AsyncSession, raw: dict[str, Any], *, visited: set[str]
) -> tuple[LineageNode, Calculation | None]:
    """Turn one stored input into a node, and the row to keep walking from.

    Only a calculation returns a second element: it is the one source kind with children,
    and therefore the only one the walk continues through.
    """
    stored = _StoredInput.of(raw)

    if stored.kind == SourceKind.CALCULATION.value:
        return await _resolve_calculation(session, stored, visited=visited)
    return await _resolve_leaf(session, stored), None


async def _resolve_leaf(session: AsyncSession, stored: _StoredInput) -> LineageNode:
    """Resolve a leaf through the relation it names.

    A row written since ADR 0072 says which table holds it and is looked up there once. A
    row written before that says only its kind, and ``_LEGACY_CANDIDATES`` tries the
    relations that kind was ever minted over. That second path is a guess — a defensible
    one, because the candidate tables hold disjoint sets of UUIDs so at most one can match,
    but a guess nonetheless, and it exists to serve old rows rather than to be relied on.

    Either way a leaf that does not resolve is reported as missing against **the relation
    that was searched**, not against the kind. "No row in ``macro_observations``" is a
    fact about the world; "no fact" was a fact about a lookup nobody could see.
    """
    if stored.table:
        table = _KNOWN_TABLES.get(stored.table)
        if table is None:
            # A table name this build does not know: a row from a newer schema, or a
            # corrupt one. Named as found, so the viewer says what it could not resolve.
            return stored.missing(stored.table)
        loader = _LEAF_LOADERS.get(table)
        if loader is None:
            return stored.missing(table.value)
        node = await loader(session, stored)
        return node if node is not None else stored.missing(table.value)

    for candidate in _LEGACY_CANDIDATES.get(stored.kind, ()):
        node = await _LEAF_LOADERS[candidate](session, stored)
        if node is not None:
            return node
    return stored.missing(stored.kind or "unknown")


async def _resolve_calculation(
    session: AsyncSession, stored: _StoredInput, *, visited: set[str]
) -> tuple[LineageNode, Calculation | None]:
    if stored.identifier in visited:
        # Already expanded elsewhere in the tree, or -- if the graph is corrupt -- a
        # cycle. Either way it is shown as a reference rather than expanded twice.
        return (
            LineageNode(
                kind="calculation_ref",
                identifier=stored.identifier,
                label=stored.label,
                value=stored.value,
                unit=stored.unit,
            ),
            None,
        )

    row = await _load_calculation(session, stored.identifier)
    if row is None:
        return stored.missing("calculation"), None
    return _calculation_node(row, value=stored.value, unit=stored.unit), row


async def _financial_fact_node(session: AsyncSession, stored: _StoredInput) -> LineageNode | None:
    fact = await _load_fact(session, stored.identifier)
    if fact is None:
        return None
    return LineageNode(
        kind="fact",
        identifier=stored.identifier,
        label=stored.label or fact.concept,
        value=stored.value if stored.value is not None else fact.value,
        unit=stored.unit or fact.unit,
        detail={
            "table": SourceTable.FINANCIAL_FACTS.value,
            "concept": fact.concept,
            "period_end": fact.period_end.isoformat(),
            "filed_date": fact.filed_date.isoformat(),
            "accession": fact.accession,
            "basis": fact.basis.value,
            "source_document_id": str(fact.source_document_id),
        },
    )


async def _macro_observation_node(
    session: AsyncSession, stored: _StoredInput
) -> LineageNode | None:
    """A published statistic, at the vintage it was read at.

    The kind is still ``fact``: somebody published this and the bytes are archived, which
    is the same guarantee a filing line carries. What differs is the relation, and the two
    dates — a macro figure is identified by the period it describes *and* the vintage it
    was retrieved at, because an archive restates.
    """
    row = await _load_macro_observation(session, stored.identifier)
    if row is None:
        return None
    series = row.series
    return LineageNode(
        kind="fact",
        identifier=stored.identifier,
        label=stored.label or f"{series.key}@{row.observed_on.isoformat()}",
        value=stored.value if stored.value is not None else row.value,
        unit=stored.unit or series.unit,
        detail={
            "table": SourceTable.MACRO_OBSERVATIONS.value,
            "series": series.key,
            "provider": series.provider.value,
            "observed_on": row.observed_on.isoformat(),
            "vintage": row.vintage.isoformat(),
            # False means the current series stamped with a release date rather than a
            # genuinely archived vintage, which is a weaker point-in-time claim.
            "is_archived": row.is_archived,
            "source_document_id": str(row.source_document_id or ""),
        },
    )


async def _fx_rate_node(session: AsyncSession, stored: _StoredInput) -> LineageNode | None:
    """A published exchange rate, on the day it was for.

    The kind is ``fact``: the ECB published this and the response is archived. What the
    detail carries that no other leaf needs is the *pair*, because a rate means nothing
    without its direction — 0.86 is a plausible GBP/USD rate and an impossible USD/GBP one,
    and a node showing only the number could not tell a reader which it was looking at.

    Both dates are shown for the same reason ``macro_observations`` shows both, and with a
    different meaning: ``observed_on`` is when the rate was published, and ``vintage`` is
    when this platform read that publication (ADR 0078).

    The digest is always here and the document id is not. A rate outlives the request that
    fetched it, so a purge nulls the pointer and leaves the hash (ADR 0080) — after which
    this node still answers "what were these numbers taken from?" and no longer answers
    "show me that response".
    """
    row = await _load_fx_rate(session, stored.identifier)
    if row is None:
        return None
    return LineageNode(
        kind="fact",
        identifier=stored.identifier,
        label=stored.label or f"{row.base}/{row.quote}@{row.observed_on.isoformat()}",
        value=stored.value if stored.value is not None else row.rate,
        unit=stored.unit or f"{row.quote}/{row.base}",
        detail={
            "table": SourceTable.FX_RATES.value,
            "base": row.base,
            "quote": row.quote,
            "observed_on": row.observed_on.isoformat(),
            "vintage": row.vintage.isoformat(),
            "artefact_sha256": row.artefact_sha256,
            "source_document_id": str(row.source_document_id or ""),
        },
    )


async def _attestation_node(session: AsyncSession, stored: _StoredInput) -> LineageNode | None:
    """Something the operator asserted, with the grade of evidence beside it.

    The kind is ``attestation`` rather than ``fact``, and the difference is the whole point:
    a filing is a document somebody published and this is a statement somebody signed their
    name to. Which of those a reader is looking at should not require them to notice a
    table name.

    ``grade`` is shown from the *ledger* rather than from the row. A trade entered from
    memory and documented a month later is documented now, and was not when the net asset
    value above it was struck — so the node reports what the arithmetic actually stood on,
    and names the row's current grade beside it when the two differ.
    """
    row = await _load_attestation(session, stored.identifier)
    if row is None:
        return None
    return LineageNode(
        kind="attestation",
        identifier=stored.identifier,
        label=stored.label or f"{row.kind.value} {row.effective_at.date().isoformat()}",
        value=stored.value,
        unit=stored.unit,
        detail={
            "table": SourceTable.ATTESTATIONS.value,
            "attestation_kind": row.kind.value,
            "grade": stored.grade or row.grade.value,
            "grade_now": row.grade.value,
            "effective_at": row.effective_at.isoformat(),
            "recorded_at": row.recorded_at.isoformat(),
            "recorded_by": row.recorded_by,
            "source_document_id": str(row.source_document_id or ""),
            # A superseded row is the interesting one — "I entered 1,000 and meant 100" —
            # and a lineage that hid it would show a figure resting on a correction with no
            # sign that anything was ever different.
            "supersedes_id": str(row.supersedes_id or ""),
        },
    )


async def _security_node(session: AsyncSession, stored: _StoredInput) -> LineageNode | None:
    """A listing, and by extension the bars adjusted from it.

    A price is not a row anybody filed, so the leaf is the listing the price was struck on
    rather than a single bar: which instrument, on which exchange, quoted in what. The bar
    itself is reached from here, and ``quote_currency`` is what says whether the figure
    above needed the pence-to-pounds conversion at all.
    """
    security = await _load_security(session, stored.identifier)
    if security is None:
        return None
    return LineageNode(
        kind="fact",
        identifier=stored.identifier,
        label=stored.label or security.provider_symbol,
        value=stored.value,
        unit=stored.unit,
        detail={
            "table": SourceTable.SECURITIES.value,
            "ticker": security.ticker,
            "exchange": security.exchange,
            "provider_symbol": security.provider_symbol,
            "quote_currency": security.quote_currency,
        },
    )


async def _assumption_node(session: AsyncSession, stored: _StoredInput) -> LineageNode | None:
    assumption = await _load_assumption(session, stored.identifier)
    if assumption is None:
        return None
    return LineageNode(
        kind="assumption",
        identifier=stored.identifier,
        label=stored.label or assumption.name,
        value=stored.value if stored.value is not None else assumption.value,
        unit=stored.unit or assumption.unit,
        detail={
            "table": SourceTable.ASSUMPTIONS.value,
            "justification": assumption.justification,
            "confidence": assumption.confidence,
            "proposed_by": assumption.proposed_by,
            "approved": assumption.approved,
        },
    )


_LeafLoader = Callable[[AsyncSession, "_StoredInput"], Awaitable[LineageNode | None]]

# One entry per relation a leaf can live in. Adding a source table is a line here and a
# constructor on SourceRef; the test that walks both directions is what makes the pair a
# registry rather than two lists (ADR 0072).
#
# SourceTable.CALCULATIONS is deliberately absent. A calculation is the one kind with
# children, so it is resolved by _resolve_calculation, which returns the row to keep
# walking from as well as the node.
_LEAF_LOADERS: Final[Mapping[SourceTable, _LeafLoader]] = {
    SourceTable.FINANCIAL_FACTS: _financial_fact_node,
    SourceTable.MACRO_OBSERVATIONS: _macro_observation_node,
    SourceTable.FX_RATES: _fx_rate_node,
    SourceTable.ATTESTATIONS: _attestation_node,
    SourceTable.SECURITIES: _security_node,
    SourceTable.ASSUMPTIONS: _assumption_node,
}

_KNOWN_TABLES: Final[Mapping[str, SourceTable]] = {table.value: table for table in SourceTable}

# What a kind was ever minted over, for rows written before the table was recorded. The
# order is the frequency the relations were written in, and it is only ever reached by a
# row that predates ADR 0072: nothing minted since carries an empty table.
_LEGACY_CANDIDATES: Final[Mapping[str, Sequence[SourceTable]]] = {
    SourceKind.FACT.value: (
        SourceTable.FINANCIAL_FACTS,
        SourceTable.SECURITIES,
        SourceTable.MACRO_OBSERVATIONS,
    ),
    SourceKind.ASSUMPTION.value: (SourceTable.ASSUMPTIONS,),
}


def _calculation_node(
    row: Calculation, *, value: Decimal | None = None, unit: str = ""
) -> LineageNode:
    return LineageNode(
        kind="calculation",
        identifier=str(row.id),
        label=row.name,
        value=value if value is not None else row.output_value,
        unit=unit or row.output_unit,
        detail={
            "formula": row.formula,
            "function_ref": row.function_ref,
            "code_version": row.code_version,
            "parameters": dict(row.parameters or {}),
            "assumptions": list(row.assumptions or []),
        },
    )


def _missing(
    identifier: str, label: str, value: Decimal | None, unit: str, expected: str
) -> LineageNode:
    """A source reference that does not resolve.

    Surfaced rather than dropped. An input pointing at a deleted fact is a real problem
    with the report that cites it, and a provenance tree that silently omitted it would
    render as though the chain were complete.
    """
    return LineageNode(
        kind="missing",
        identifier=identifier,
        label=label,
        value=value,
        unit=unit,
        detail={"expected": expected},
    )


async def _load_calculation(session: AsyncSession, identifier: str) -> Calculation | None:
    parsed = _uuid_or_none(identifier)
    if parsed is None:
        return None
    return await session.get(Calculation, parsed)


async def _load_fact(session: AsyncSession, identifier: str) -> FinancialFact | None:
    parsed = _uuid_or_none(identifier)
    if parsed is None:
        return None
    return await session.get(FinancialFact, parsed)


async def _load_assumption(session: AsyncSession, identifier: str) -> Assumption | None:
    parsed = _uuid_or_none(identifier)
    if parsed is None:
        return None
    return await session.get(Assumption, parsed)


async def _load_attestation(session: AsyncSession, identifier: str) -> Attestation | None:
    parsed = _uuid_or_none(identifier)
    if parsed is None:
        return None
    return await session.get(Attestation, parsed)


async def _load_fx_rate(session: AsyncSession, identifier: str) -> FxRateRow | None:
    parsed = _uuid_or_none(identifier)
    if parsed is None:
        return None
    return await session.get(FxRateRow, parsed)


async def _load_macro_observation(
    session: AsyncSession, identifier: str
) -> MacroObservationRow | None:
    parsed = _uuid_or_none(identifier)
    if parsed is None:
        return None
    # The series carries the key and the unit the node renders, and a lazy load inside an
    # async session raises rather than emitting a second query.
    return await session.get(
        MacroObservationRow, parsed, options=[selectinload(MacroObservationRow.series)]
    )


async def _load_security(session: AsyncSession, identifier: str) -> Security | None:
    parsed = _uuid_or_none(identifier)
    if parsed is None:
        return None
    return await session.get(Security, parsed)


def _uuid_or_none(value: str) -> uuid.UUID | None:
    """Parse a source id, treating anything unparseable as unresolvable.

    The kernel accepts any stable string as an identifier so it can be tested without a
    database. A non-UUID reaching here is therefore a test fixture or a corrupt row, and
    both should surface as "missing" rather than raising — a provenance viewer that 500s
    on one bad id is less useful than one that shows the rest of the tree.
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


async def calculations_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[Calculation]:
    """Every calculation a run performed, oldest first."""
    result = await session.scalars(
        select(Calculation)
        .where(Calculation.job_id == job_id)
        .order_by(Calculation.created_at, Calculation.name)
    )
    return list(result.all())
