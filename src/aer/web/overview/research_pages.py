"""The research tool's drawer contents.

One fragment: enough of a stopped or failed run to decide whether it needs you now, so an
operator can triage a feed of eight without eight trips out to a console and back.

Its own module rather than `pages.py`, for the reason `research.py` is its own module: the
Overview screen composes, and what a research run looks like up close is the research
tool's to decide. A second tool's drawer is a second file, and neither knows about the
other.

**A fragment, so it extends nothing.** It is swapped into the shell's drawer, which is
already on the page; a template extending `base.html` would put a whole second page inside
the first. The route stays a real URL, and the link that opens the drawer keeps an `href`
to the run console, so with scripting off the same click is a page.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, Response
from starlette.status import HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession
from aer.db.models import Job, ResearchRequest
from aer.services.approvals import pending_gate
from aer.web.overview.research import GATE_ASKS
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)


@router.get("/overview/runs/{job_id}/preview", response_class=HTMLResponse)
async def run_preview(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """A run, close enough to triage.

    Returns the same 404 for "no such run" and "not yours", which is the rule the JSON API
    and every page here already follow: two answers would let a caller enumerate which ids
    exist by watching which ones answer differently.
    """
    found = (
        await session.execute(
            select(Job, ResearchRequest)
            .join(ResearchRequest, ResearchRequest.id == Job.work_order_id)
            .where(Job.id == job_id, ResearchRequest.user_id == user.id)
        )
    ).first()
    if found is None:
        missing: Response = render(
            request,
            "overview/_missing.html",
            {"message": f"No run {job_id}."},
            status_code=HTTP_404_NOT_FOUND,
        )
        return missing

    job, mandate = found
    gate = await pending_gate(session, job)
    fragment: Response = render(
        request,
        "overview/_run_preview.html",
        {
            "job": job,
            "mandate": mandate,
            # The same phrase the feed used, from the same map, so the row and the panel
            # cannot describe the gate differently.
            "asked": GATE_ASKS.get(gate) if gate else "",
            # `job.total_cost_gbp` is a column the engine maintains, so this costs no query
            # and — more to the point — is the same number the budget guard compares against.
            "spent": f"£{job.total_cost_gbp:,.2f}",
        },
    )
    return fragment
