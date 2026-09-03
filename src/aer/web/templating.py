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

from aer.config import get_settings
from aer.core.disagreement import challenge_heading, position_figure
from aer.version import version
from aer.web.csrf import (
    CSRF_FIELD_NAME,
    new_csrf_token,
    set_csrf_cookie,
    usable_csrf_token,
)
from aer.web.shell import GUIDANCE_COOKIE, THEME_COOKIE, shell_for
from aer.web.vocabulary import ROLE_WORDS

__all__ = ["DISCLAIMER", "STATIC_DIR", "STYLES_DIR", "TEMPLATES_DIR", "render", "templates"]

_PACKAGE_DIR: Final = Path(__file__).resolve().parent
TEMPLATES_DIR: Final = _PACKAGE_DIR / "templates"
STATIC_DIR: Final = _PACKAGE_DIR / "static"
# The Tailwind *source*, which is not served. Named here so a test can read the palette
# it declares rather than re-deriving it from the minified output.
STYLES_DIR: Final = _PACKAGE_DIR / "styles"

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
# The roles a skill composes into, in a reader's words (ADR 0108): the gate page and the
# skills editor read one mapping rather than each carrying a copy.
templates.env.globals["role_words"] = ROLE_WORDS
templates.env.filters["percent"] = percent
# The disagreement rule (gap A68), so four surfaces read one answer rather than four
# copies of a conditional living in Jinja.
templates.env.filters["position_figure"] = position_figure
templates.env.filters["challenge_heading"] = challenge_heading
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
    """Render ``template_name`` with the request and the shell already in context.

    The shell is injected here rather than passed by each handler, for the reason the
    disclaimer is a global: a page that forgot it would render with no navigation, and the
    failure would read as a styling bug rather than as a page nobody can leave. Handlers
    cannot omit what they never supply.

    A handler may still pass its own ``shell`` — the skills pages do nothing of the kind
    today, and nothing should — but an explicit one wins, so a test can render a page under
    a nav it controls without reaching into this module.

    **A CSRF token is part of the shell too**, for the same reason the nav is. The menu
    carries preference controls, those controls are forms, and a form on every page needs a
    token on every page — so a handler that never thought about CSRF cannot ship a menu
    whose controls silently do nothing. A handler that mints its own still wins: it is
    passed in the context, and this leaves it exactly alone.
    """
    supplied = context or {}
    settings = get_settings()
    # The request's own token first. A fragment rendered through this door — the badge
    # counts, a form's error list — would otherwise mint a new one and set it, replacing the
    # value every form already on the page is carrying. See `usable_csrf_token`.
    token = (
        supplied.get("csrf_token")
        or usable_csrf_token(request, settings)
        or new_csrf_token(settings)
    )
    merged: dict[str, Any] = {
        "shell": shell_for(
            request.url.path,
            guidance=request.cookies.get(GUIDANCE_COOKIE) == "on",
            # Unknown values fall back to `system` inside `shell_for`, so a hand-edited
            # cookie cannot put an arbitrary string into an attribute on `<html>`.
            theme=request.cookies.get(THEME_COOKIE, "system"),
        ),
        "csrf_field": CSRF_FIELD_NAME,
        "csrf_token": token,
        **supplied,
    }
    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        context=merged,
        status_code=status_code,
    )
    # Only for the token this function minted or adopted. A handler that supplied one owns
    # setting its own cookie, and two `Set-Cookie` headers for one name is a race over which
    # token the browser keeps — the form would then carry one and the cookie the other.
    #
    # Re-setting an adopted token is deliberate rather than wasteful: it refreshes the
    # cookie's lifetime on a session that is plainly still in use, and writes the same value,
    # so no form anywhere on the page is invalidated by it.
    if "csrf_token" not in supplied:
        set_csrf_cookie(response, token)
    return response
