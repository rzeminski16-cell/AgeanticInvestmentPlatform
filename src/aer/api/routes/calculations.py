"""Calculation provenance endpoints.

``GET /api/calculations/{id}`` is the endpoint that makes the platform's central claim
checkable from outside. It returns the formula, every input with its unit and its source,
the code version that produced the figure, and — on request — the full lineage tree down
to the facts and assumptions the number ultimately rests on.

The lineage tree is opt-in via a query parameter rather than always returned. A deep
valuation chain resolves dozens of rows, and a viewer listing a run's calculations wants
the summaries, not fifty trees.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.status import HTTP_404_NOT_FOUND

from aer.api.deps import DbSession
from aer.db.models import Calculation
from aer.errors import AerError
from aer.services import calculations as calculation_service

__all__ = ["router"]

router = APIRouter(prefix="/api/calculations", tags=["calculations"])


class CalculationNotFoundError(AerError):
    """No such calculation."""

    code = "calculation_not_found"
    http_status = HTTP_404_NOT_FOUND


class CalculationRead(BaseModel):
    """A calculation, as an auditor needs to see it.

    Values are strings, not numbers. JSON numbers are IEEE doubles in every parser that
    will ever consume this, and serialising an exact ``Decimal`` as one would corrupt the
    figure at the boundary — the last place anybody would look for a rounding error.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    name: str
    formula: str
    function_ref: str
    code_version: str
    inputs: list[dict[str, Any]]
    parameters: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[Any] = Field(default_factory=list)
    output_value: str
    output_unit: str

    @classmethod
    def of(cls, row: Calculation) -> CalculationRead:
        return cls(
            id=row.id,
            job_id=row.job_id,
            name=row.name,
            formula=row.formula,
            function_ref=row.function_ref,
            code_version=row.code_version,
            inputs=list(row.inputs or []),
            parameters=dict(row.parameters or {}),
            assumptions=list(row.assumptions or []),
            output_value=str(row.output_value),
            output_unit=row.output_unit,
        )


class CalculationDetail(BaseModel):
    """A calculation and, optionally, everything underneath it."""

    calculation: CalculationRead
    lineage: dict[str, Any] | None = None

    # Source references that did not resolve. Reported at the top level rather than only
    # buried in the tree, because "this figure rests on a fact that is no longer here" is
    # a headline about the report, not a detail of one branch.
    unresolved: list[dict[str, Any]] = Field(default_factory=list)


@router.get(
    "/{calculation_id}",
    response_model=CalculationDetail,
    summary="Retrieve a calculation with its provenance",
)
async def read_calculation(
    calculation_id: uuid.UUID,
    session: DbSession,
    include_lineage: Annotated[
        bool, Query(description="Resolve the full provenance tree down to facts.")
    ] = True,
) -> CalculationDetail:
    """Return a calculation's formula, inputs, sources and code version."""
    row = await session.get(Calculation, calculation_id)
    if row is None:
        message = f"No calculation {calculation_id}."
        raise CalculationNotFoundError(message, context={"calculation_id": str(calculation_id)})

    if not include_lineage:
        return CalculationDetail(calculation=CalculationRead.of(row))

    tree = await calculation_service.lineage(session, calculation_id)
    unresolved = [
        {"id": node.identifier, "label": node.label, "expected": node.detail.get("expected")}
        for node in tree.walk()
        if not node.is_resolved
    ]

    return CalculationDetail(
        calculation=CalculationRead.of(row),
        lineage=tree.as_dict(),
        unresolved=unresolved,
    )
