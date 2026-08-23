"""Jinja2 environment for the server-rendered pages.

One configured environment, built once, with the values every page needs already in
scope. The disclaimer in particular is a global rather than something each handler
remembers to pass: a page that forgets it is a page that presents personal research as
though it were regulated advice, and "remember to include it" is not a control.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined
from starlette.requests import Request

from aer.core.disagreement import position_figure
from aer.version import version

__all__ = ["DISCLAIMER", "STATIC_DIR", "TEMPLATES_DIR", "render", "templates"]

_PACKAGE_DIR: Final = Path(__file__).resolve().parent
TEMPLATES_DIR: Final = _PACKAGE_DIR / "templates"
STATIC_DIR: Final = _PACKAGE_DIR / "static"

DISCLAIMER: Final = (
    "This is a personal research tool, not regulated investment advice. Nothing it "
    "produces is a recommendation to buy, sell or hold any security."
)


def percent(fraction: Any) -> str:
    """Render a stored fraction as a percentage for display.

    Portfolio weights are stored as fractions in JSONB, which means they come back as
    strings. ``Decimal`` throughout: a weight that changes in the third decimal place
    because it passed through a float is a number nobody can reconcile against the
    database, and this project's whole claim is that its numbers reconcile.
    """
    if fraction in (None, ""):
        return ""
    try:
        value = Decimal(str(fraction)) * 100
    except InvalidOperation:
        return str(fraction)
    return f"{value.normalize():f}%"


templates: Final = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["disclaimer"] = DISCLAIMER
templates.env.globals["app_version"] = version()
templates.env.filters["percent"] = percent
# The disagreement rule (gap A68), so four surfaces read one answer rather than four
# copies of a conditional living in Jinja.
templates.env.filters["position_figure"] = position_figure
# Undefined variables raise instead of rendering as empty. A silently blank figure in a
# research report is the exact failure mode this whole project exists to prevent, and a
# template is no place to start making an exception.
templates.env.undefined = StrictUndefined


def render(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> Any:
    """Render ``template_name`` with the request already in context."""
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context or {},
        status_code=status_code,
    )
