"""Research request endpoints.

Thin by design. Each handler binds input, calls one service function and returns a
schema; the rules live in :mod:`aer.services.requests` so that the HTML form enforces
exactly the same ones through exactly the same code.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response
from starlette.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.core.schemas.request import (
    ResearchRequestCreate,
    ResearchRequestRead,
    ResearchRequestSummary,
)
from aer.db.models import ResearchRequest
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
    archived: Annotated[bool, Query(description="Return the archived ones instead.")] = False,
) -> list[ResearchRequestSummary]:
    rows = await request_service.list_requests(
        session, user_id=user.id, limit=limit, offset=offset, archived=archived
    )
    return [ResearchRequestSummary.model_validate(row) for row in rows]


@router.get("/{request_id}", response_model=ResearchRequestRead, summary="Read one request")
async def read_request(
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> ResearchRequestRead:
    return ResearchRequestRead.model_validate(await _owned(session, request_id, user))


@router.put("/{request_id}", response_model=ResearchRequestRead, summary="Replace a draft request")
async def replace_request(
    request_id: uuid.UUID,
    payload: ResearchRequestCreate,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> ResearchRequestRead:
    """Replace a draft request's contents.

    ``PUT`` rather than ``PATCH``: the body is a whole :class:`ResearchRequestCreate`, and
    every field is validated by the same rules that would have applied at creation. A patch
    would need a rule for what an absent field means, and "leave it" and "clear it" are
    both defensible readings — which is exactly why neither should have to be guessed.

    Raises:
        ConflictError: 409 if a run has already been started for this request. The body was
            never the problem, so resubmitting it would not help; only the resource's state
            could change the answer.
    """
    found = await _owned(session, request_id, user)
    updated = await request_service.update_request(
        session,
        request=found,
        actor=user,
        payload=payload,
        limits=request_service.limits_from(settings),
    )
    await session.commit()
    return ResearchRequestRead.model_validate(updated)


@router.delete("/{request_id}", status_code=HTTP_204_NO_CONTENT, summary="Delete a draft request")
async def delete_request(
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Delete a draft request that has never been run.

    Refused with a 409 once a run exists. Nothing here can delete a request with evidence,
    costs or a report behind it — removing researched material needs an explicit retention
    policy rather than a convenient endpoint.
    """
    found = await _owned(session, request_id, user)
    await request_service.delete_request(session, request=found, actor=user)
    await session.commit()
    return Response(status_code=HTTP_204_NO_CONTENT)


@router.post(
    "/{request_id}/archive", response_model=ResearchRequestRead, summary="Archive a request"
)
async def archive_request(
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> ResearchRequestRead:
    """Hide a request from the list without destroying anything.

    Accepted whatever the request's state — a finished run is the usual thing to archive,
    and nothing an edit or a deletion would damage is touched by hiding a row.

    Raises:
        ConflictError: 409 if it is already archived.
    """
    found = await _owned(session, request_id, user)
    archived = await request_service.archive_request(session, request=found, actor=user)
    await session.commit()
    return ResearchRequestRead.model_validate(archived)


@router.post(
    "/{request_id}/restore", response_model=ResearchRequestRead, summary="Restore a request"
)
async def restore_request(
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> ResearchRequestRead:
    """Put an archived request back on the list.

    Raises:
        ConflictError: 409 if it was not archived.
    """
    found = await _owned(session, request_id, user)
    restored = await request_service.restore_request(session, request=found, actor=user)
    await session.commit()
    return ResearchRequestRead.model_validate(restored)


@router.get(
    "/{request_id}/removal-preview",
    response_model=dict[str, int],
    summary="What purging this request would delete",
)
async def removal_preview(
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> dict[str, int]:
    """Row counts by table. Reads only; nothing is removed."""
    found = await _owned(session, request_id, user)
    return await request_service.removal_preview(session, request=found)


@router.post(
    "/{request_id}/purge",
    response_model=dict[str, int],
    summary="Delete a request and everything derived from it",
)
async def purge_request(
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> dict[str, int]:
    """Irreversibly delete a researched request. Returns what was removed, by table.

    **Its own endpoint rather than a flag on ``DELETE``.** The safe deletion refuses
    anything with evidence behind it, and that refusal is worth keeping reachable — a
    caller that wanted the safe one and got the destructive one because a query parameter
    was set has no way back.

    The audit chain, the spend ledger and the content-addressed artefacts all survive; see
    :func:`aer.services.requests.purge_request` for why each of the three has to.

    Raises:
        ConflictError: 409 if a run is still live. Cancel it first.
    """
    found = await _owned(session, request_id, user)
    removed = await request_service.purge_request(session, request=found, actor=user)
    await session.commit()
    return removed


async def _owned(session: DbSession, request_id: uuid.UUID, user: CurrentUser) -> ResearchRequest:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        message = f"No research request with id {request_id}."
        raise RequestNotFoundError(message, context={"request_id": str(request_id)})
    return found
