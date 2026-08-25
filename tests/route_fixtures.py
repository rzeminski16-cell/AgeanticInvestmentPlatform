"""What the application serves, as a plain builder two test modules can share.

A builder rather than a fixture, for the reason `tests/scene_fixtures.py` gives: pytest
permits importing a fixture out of another test module and then reports it as a redefinition
at every call site, and calling one directly is an error. Each module defines its own
one-line fixture over these.

Extracted when `tests/test_every_page_renders.py` needed the same route list
`tests/test_shell_nav.py` was already computing. Two copies of "what does this application
serve" is exactly the drift that file exists to catch, one storey up.
"""

from __future__ import annotations

from typing import Any

from starlette.routing import Mount

from aer.api.app import create_app
from aer.config import Settings

__all__ = ["page_routes_for", "routes_of", "test_settings"]


def test_settings() -> Settings:
    """The minimum a `Settings` needs to construct. Nothing here reads a real environment."""
    return Settings(http_user_agent="Test test@example.invalid")


def routes_of(app: Any) -> list[tuple[str, frozenset[str]]]:
    """Every route the application serves, walking the routers it includes.

    FastAPI keeps an included router wrapped rather than flattened, so the obvious
    `app.routes` walk finds three routes and misses forty.
    """
    found: list[tuple[str, frozenset[str]]] = []

    def walk(routes: Any) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
                continue
            path = getattr(route, "path", None)
            if path and not isinstance(route, Mount):
                found.append((path, frozenset(getattr(route, "methods", None) or ())))

    walk(app.routes)
    return found


def page_routes_for(app: Any | None = None) -> frozenset[str]:
    """The server-rendered GET routes: what an operator can open in a browser."""
    built = app if app is not None else create_app(test_settings())
    return frozenset(
        path
        for path, methods in routes_of(built)
        if "GET" in methods and not path.startswith(("/api/", "/docs", "/openapi", "/static"))
    )
