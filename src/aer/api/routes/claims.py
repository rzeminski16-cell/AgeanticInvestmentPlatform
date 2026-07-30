"""The provenance drill-down: one claim, and everything it rests on.

``GET /api/claims/{id}`` is the JSON twin of the claim page. It returns the claim, the
figure it asserts, and every citation with the **exact excerpt** and the verifier's verdict
on it — including the failures. An endpoint that returned only the citations that verified
would make a report look better than it is, which is the one thing a provenance endpoint
must never do.

Ownership is checked here rather than in the service: the route is where the caller's
identity lives. A claim belonging to another user's run answers 404 rather than 403, for
the same reason every other route in this API does — distinguishing "does not exist" from
"not yours" lets a caller enumerate ids by watching which ones answer differently.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from starlette.status import HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession
from aer.db.models import Claim, Job, ReportSection, ResearchRequest
from aer.errors import AerError
from aer.services import provenance

__all__ = ["router"]

router = APIRouter(prefix="/api/claims", tags=["claims"])


class ClaimNotFoundError(AerError):
    """No such claim, or it belongs to somebody else's run."""

    code = "claim_not_found"
    http_status = HTTP_404_NOT_FOUND


class ClaimRead(BaseModel):
    """A claim with its evidence resolved."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    kind: str
    text: str
    section_key: str
    section_title: str
    job_id: uuid.UUID

    # Whether this claim would pass the gate's evidence check. False is not an error state
    # to be hidden — it is the finding.
    supported: bool

    # The stored fact or recorded calculation the claim asserts, or null for a claim that
    # asserts no figure.
    figure: dict[str, Any] | None

    citations: list[dict[str, Any]]


@router.get("/{claim_id}", response_model=ClaimRead, summary="A claim and its evidence")
async def read_claim(claim_id: uuid.UUID, session: DbSession, user: CurrentUser) -> ClaimRead:
    """Everything behind one sentence of a report."""
    if not await _is_visible(session, claim_id=claim_id, user_id=user.id):
        message = f"No claim {claim_id}."
        raise ClaimNotFoundError(message, context={"claim_id": str(claim_id)})

    view = await provenance.claim_view(session, claim_id)
    if view is None:  # pragma: no cover -- visibility already proved it exists
        message = f"No claim {claim_id}."
        raise ClaimNotFoundError(message, context={"claim_id": str(claim_id)})

    return ClaimRead(**view.as_dict())


async def _is_visible(session: DbSession, *, claim_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Whether this claim belongs to a run of the asking user's own request."""
    owner = await session.scalar(
        select(ResearchRequest.user_id)
        .join(Job, Job.request_id == ResearchRequest.id)
        .join(ReportSection, ReportSection.job_id == Job.id)
        .join(Claim, Claim.report_section_id == ReportSection.id)
        .where(Claim.id == claim_id)
    )
    return owner == user_id
