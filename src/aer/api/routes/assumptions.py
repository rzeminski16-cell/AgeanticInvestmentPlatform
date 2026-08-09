"""The assumptions a request rests on: list, amend, confirm.

Every route here checks the request belongs to the caller, because an assumption is a
judgement somebody made about somebody else's analysis and neither half of that is public.

**Confirming is a ``POST`` with a payload hash, exactly like a gate.** An operator confirms
what a page showed them, and a page that changed between the render and the click was showing
something else. Without the hash, confirming would mean "somebody clicked at some point",
which is the same emptiness the run gates exist to avoid.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession
from aer.db.models import Assumption, ResearchRequest, User
from aer.errors import AerError, ValidationError
from aer.services import assumptions as assumption_service
from aer.services import scenarios as scenario_service
from aer.services.approvals import payload_hash_for
from aer.services.assumption_gate import PROPOSABLE_NAMES

__all__ = ["assumptions_payload", "router"]

router = APIRouter(prefix="/api/requests", tags=["assumptions"])


class AssumptionNotFoundError(AerError):
    """No such assumption, or it belongs to somebody else's request."""

    code = "assumption_not_found"
    http_status = HTTP_404_NOT_FOUND


class AmendRequest(BaseModel):
    """A person replacing a value with one of their own."""

    model_config = ConfigDict(extra="forbid")

    value: Decimal
    justification: str = Field(min_length=1, max_length=4000)
    unit: str | None = Field(default=None, max_length=32)


class ProposeRequest(BaseModel):
    """A value a person is putting forward for an assumption that has none.

    ``name`` is bounded to the vocabulary a valuation actually reads. An arbitrary name
    would be accepted, stored, shown on the page and then silently ignored by
    :func:`aer.services.valuation.inputs_from`, which looks assumptions up by name — and an
    operator who typed `terminal_growth_rate` would spend a long time wondering why their
    forecast still would not run.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    value: Decimal
    unit: str = Field(min_length=1, max_length=32)
    justification: str = Field(min_length=1, max_length=4000)

    @field_validator("name")
    @classmethod
    def _known_name(cls, value: str) -> str:
        if value in PROPOSABLE_NAMES:
            return value
        message = (
            f"{value!r} is not an assumption this platform reads. Known names: "
            f"{', '.join(PROPOSABLE_NAMES)}. A driver may also be given per year as "
            "`<driver>_y1` through `<driver>_y5` for a path that changes."
        )
        raise ValueError(message)


class ConfirmRequest(BaseModel):
    """Agreement that this value may be used, against what was displayed."""

    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(min_length=64, max_length=64)


class AssumptionsRead(BaseModel):
    """Every assumption on a request, and the hash confirming one requires."""

    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    assumptions: list[dict[str, Any]]

    # How many are still waiting on a person. The number that blocks a valuation, surfaced
    # rather than left to be counted from the list — a caller that forgot to count would
    # treat a page of unconfirmed assumptions as a page of settled ones.
    unconfirmed: int

    payload_hash: str


def assumptions_payload(rows: list[Assumption]) -> dict[str, Any]:
    """Exactly what the assumptions surface shows, as one structure.

    Built here and used by the page, the JSON API and the confirm hash alike, so "what the
    operator saw" and "what they confirmed" are the same object by construction rather than
    by three functions agreeing. Values are strings because they are ``Decimal``; a JSON
    number would round them, and a hash over a rounded figure is a hash over something
    nobody displayed.
    """
    return {
        "assumptions": [
            {
                "id": str(row.id),
                "name": row.name,
                "value": str(row.value),
                "unit": row.unit,
                "justification": row.justification,
                "confidence": row.confidence,
                "proposed_by": row.proposed_by,
                "approved": row.approved,
                "approved_by": row.approved_by,
            }
            for row in rows
        ]
    }


@router.get(
    "/{request_id}/assumptions",
    response_model=AssumptionsRead,
    summary="The assumptions a request rests on",
)
async def read_assumptions(
    request_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> AssumptionsRead:
    await _owned_request(session, request_id=request_id, user=user)
    rows = await assumption_service.assumptions_for_request(session, request_id)
    payload = assumptions_payload(rows)

    return AssumptionsRead(
        request_id=request_id,
        assumptions=list(payload["assumptions"]),
        unconfirmed=sum(1 for row in rows if not row.approved),
        payload_hash=payload_hash_for(payload),
    )


@router.post(
    "/{request_id}/assumptions",
    response_model=AssumptionsRead,
    summary="Put forward an assumption the run could not propose",
)
async def create_assumption(
    request_id: uuid.UUID,
    *,
    payload: Annotated[ProposeRequest, Body()],
    session: DbSession,
    user: CurrentUser,
) -> AssumptionsRead:
    """A person supplies a value nothing in this run could source.

    **Without this the assumptions gate could never be cleared.** A discounted cash flow
    needs a risk-free rate, a beta and an equity risk premium; this workflow acquires no
    macroeconomic series and no price history, and the premium is a judgement with no
    series behind it at all. Amending and confirming operate on rows that exist, so a run
    that proposed eight of eleven had no route to the other three and the valuation stayed
    permanently out of reach.

    Recorded as a human proposal — ``by_human`` and the operator's own address — and
    **still unconfirmed**, because :func:`aer.services.assumptions.propose` makes every
    proposal unconfirmed whatever its caller says. Typing a number and agreeing to it are
    two acts here, exactly as they are for a value a model put forward.

    Proposing a name that already exists supersedes it, which makes this idempotent in the
    way that matters: a repeated submission leaves one assumption and a visible history,
    not two rows disagreeing about the same thing.
    """
    await _owned_request(session, request_id=request_id, user=user)

    await assumption_service.propose(
        session,
        request_id=request_id,
        name=payload.name,
        value=payload.value,
        unit=payload.unit,
        justification=payload.justification,
        proposed_by=user.email,
        by_human=True,
    )
    await session.commit()
    return await read_assumptions(request_id, session, user)


@router.post(
    "/{request_id}/assumptions/{assumption_id}/amend",
    response_model=AssumptionsRead,
    summary="Replace an assumption's value",
)
async def amend_assumption(
    request_id: uuid.UUID,
    assumption_id: uuid.UUID,
    *,
    payload: Annotated[AmendRequest, Body()],
    session: DbSession,
    user: CurrentUser,
) -> AssumptionsRead:
    """Amend, keeping the previous proposal on the record.

    The amendment un-confirms the assumption, so a value changed after confirmation has to
    be confirmed again. See :mod:`aer.services.assumptions`.
    """
    await _owned_request(session, request_id=request_id, user=user)
    assumption = await _assumption(session, request_id=request_id, assumption_id=assumption_id)

    await assumption_service.amend(
        session,
        assumption=assumption,
        value=payload.value,
        justification=payload.justification,
        actor=user,
        unit=payload.unit,
    )
    await session.commit()
    return await read_assumptions(request_id, session, user)


@router.post(
    "/{request_id}/assumptions/{assumption_id}/confirm",
    response_model=AssumptionsRead,
    summary="Agree that an assumption may be used",
)
async def confirm_assumption(
    request_id: uuid.UUID,
    assumption_id: uuid.UUID,
    *,
    payload: Annotated[ConfirmRequest, Body()],
    session: DbSession,
    user: CurrentUser,
) -> AssumptionsRead:
    """Confirm against the hash of what was displayed.

    Raises:
        ValidationError: If the assumptions changed since the page was rendered, or if this
            one was already confirmed.
    """
    await _owned_request(session, request_id=request_id, user=user)
    assumption = await _assumption(session, request_id=request_id, assumption_id=assumption_id)

    rows = await assumption_service.assumptions_for_request(session, request_id)
    current = payload_hash_for(assumptions_payload(rows))
    if current != payload.payload_hash:
        message = (
            "The assumptions changed after this page was rendered, so confirming would "
            "agree to something other than what was shown. Reload and look again."
        )
        raise ValidationError(message, context={"shown": payload.payload_hash, "current": current})

    await assumption_service.confirm(session, assumption=assumption, actor=user)
    await session.commit()
    return await read_assumptions(request_id, session, user)


@router.get(
    "/{request_id}/scenarios",
    summary="The cases a request carries, resolved against the base",
)
async def read_scenarios(
    request_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> dict[str, Any]:
    """Each scenario with the assumptions it actually runs on.

    Resolved rather than stored, so a corrected base case shows through here immediately —
    the property `docs/phase-3-plan.md` task 24 asks for, visible at the surface.
    """
    await _owned_request(session, request_id=request_id, user=user)
    scenarios = await scenario_service.scenarios_for_request(session, request_id)

    resolved = []
    for scenario in scenarios:
        state = await scenario_service.resolve(session, scenario=scenario)
        resolved.append(
            {
                "key": scenario.key,
                "label": scenario.label,
                "description": scenario.description,
                "overridden": list(state.overridden),
                "values": {
                    name: {"value": str(q.value), "unit": q.unit.symbol}
                    for name, q in sorted(state.values.items())
                },
            }
        )

    return {"request_id": str(request_id), "scenarios": resolved}


# -- Internals ---------------------------------------------------------------------------


async def _owned_request(
    session: AsyncSession, *, request_id: uuid.UUID, user: User
) -> ResearchRequest:
    request = await session.get(ResearchRequest, request_id)
    if request is None or request.user_id != user.id:
        message = f"No research request {request_id}."
        raise AssumptionNotFoundError(message, context={"request_id": str(request_id)})
    return request


async def _assumption(
    session: AsyncSession, *, request_id: uuid.UUID, assumption_id: uuid.UUID
) -> Assumption:
    assumption = await session.scalar(
        select(Assumption).where(
            Assumption.id == assumption_id, Assumption.request_id == request_id
        )
    )
    if assumption is None:
        message = f"No assumption {assumption_id} on request {request_id}."
        raise AssumptionNotFoundError(message, context={"assumption_id": str(assumption_id)})
    return assumption
