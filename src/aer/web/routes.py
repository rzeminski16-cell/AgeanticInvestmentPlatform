"""Server-rendered pages.

Only the landing page for now. The research request form arrives in the next task; it is
deliberately absent here rather than stubbed, because a half-built form invites people to
use it.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import HTMLResponse

from aer.version import build_identity
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse, summary="Landing page")
async def index(request: Request) -> HTMLResponse:
    response: HTMLResponse = render(
        request,
        "index.html",
        {"build": build_identity()},
    )
    return response
