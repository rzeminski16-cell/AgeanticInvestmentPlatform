"""One page per tool that is not built yet, at the URL that tool will keep.

Registered in a loop over the registry rather than written out eight times: the point of
`registry.py` being data is that this file has nothing to say about any particular tool, so
a row added there is a page here and a row promoted to `WORKING` is a page gone — replaced
by the real one, at the same URL.

**200, not 404 and not 501.** The page exists and is correct — it says truthfully that the
tool does not. A 404 would be a lie about a URL the launcher links to, and a 501 would put
a server error in a log for a page working exactly as intended.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, Response

from aer.web.templating import render
from aer.web.tools.registry import installed_tools, tools_needing_a_page

if TYPE_CHECKING:
    from aer.web.tools.registry import Tool

__all__ = ["router"]

router = APIRouter(include_in_schema=False)


def _handler(tool: Tool) -> Callable[[Request], Awaitable[Response]]:
    """A page for one row.

    A factory rather than a closure written inside the loop: a closure would capture the
    loop variable by reference and every route would end up describing the last tool, which
    is the oldest bug in Python and reads as eight identical pages.
    """

    async def page(request: Request) -> Response:
        rendered: Response = render(
            request,
            "tools/index.html",
            {"tool": tool, "siblings": [row for row in installed_tools() if row.key != tool.key]},
        )
        return rendered

    return page


for _tool in tools_needing_a_page():
    router.add_api_route(
        _tool.href,
        _handler(_tool),
        methods=["GET"],
        response_class=HTMLResponse,
        name=f"tool_{_tool.key}",
        summary=f"{_tool.label} ({_tool.status.value.lower()})",
    )
