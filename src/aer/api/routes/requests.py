"""Research request endpoints.

Thin by design. Each handler binds input, calls one service function and returns a
schema; the rules live in :mod:`aer.services.requests` so that the HTML form enforces
exactly the same ones through exactly the same code.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response
from starlette.status import HTTP_201_CREATED, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.core.schemas.request import (
    ResearchRequestCreate,
    ResearchRequestRead,
    ResearchRequestSummary,
)
from aer.errors import AerError
from aer.services import requests as request_service

__all__ = ["router"]

router = APIRouter(prefix="/api/requests", tags=["requests"])


class RequestNotFoundError(AerError):
    """No such request, or it belongs to someone else.

    Deliberately one error for both. Distinguishing them would let a caller enumerate
    which ids exist by watching for a 403 among the 404s, which is a small leak now and a
    real one the moment there is more than one user.
    """

    code = "request_not_found"
    http_status = HTTP_404_NOT_FOUND


@router.post(
    "",
    status_code=HTTP_201_CREATED,
    response_model=ResearchRequestRead,
    summary="Create a research request",
)
async def create_request(
    payload: ResearchRequestCreate,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> ResearchRequestRead:
    """Create a request in ``DRAFT``. No planning job is started."""
    created = await request_service.create_request(
        session,
        user=user,
        payload=payload,
        limits=request_service.limits_from(settings),
    )
    await session.commit()

    response.headers["Location"] = f"{router.prefix}/{created.id}"
    return ResearchRequestRead.model_validate(created)


@router.get("", response_model=list[ResearchRequestSummary], summary="List research requests")
async def list_requests(
    session: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResearchRequestSummary]:
    rows = await request_service.list_requests(session, user_id=user.id, limit=limit, offset=offset)
    return [ResearchRequestSummary.model_validate(row) for row in rows]


@router.get("/{request_id}", response_model=ResearchRequestRead, summary="Read one request")
async def read_request(
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> ResearchRequestRead:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        message = f"No research request with id {request_id}."
        raise RequestNotFoundError(message, context={"request_id": str(request_id)})
    return ResearchRequestRead.model_validate(found)
