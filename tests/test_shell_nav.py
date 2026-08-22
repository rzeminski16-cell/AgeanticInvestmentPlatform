"""The nav describes routes that exist, and every page is reachable or says why not.

Nav drift is quiet. A link whose route was renamed goes on rendering, and the 404 is found
by whoever clicks it; a page added without a link is simply never visited, which is
indistinguishable from a page nobody finished. Neither shows up in a test of the page
itself, because each page is fine — it is the relationship between them that is wrong.

So the relationship is what is asserted here.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.routing import Mount

from aer.api.app import create_app
from aer.config import Settings
from aer.web.shell import NAV, UNLISTED, Shell, flat_items, shell_for
from aer.web.shell.nav import NavItem, NavSection, active_key


def _settings() -> Settings:
    return Settings(http_user_agent="Test test@example.invalid")


def _routes(app: Any) -> list[tuple[str, frozenset[str]]]:
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


@pytest.fixture(scope="module")
def page_routes() -> frozenset[str]:
    """The server-rendered GET routes: what an operator can open in a browser."""
    app = create_app(_settings())
    return frozenset(
        path
        for path, methods in _routes(app)
        if "GET" in methods and not path.startswith(("/api/", "/docs", "/openapi", "/static"))
    )


class TestTheNavPointsAtRealPages:
    def test_every_nav_href_is_a_route(self, page_routes: frozenset[str]) -> None:
        # `/docs` is FastAPI's own and is filtered out of `page_routes`, so it is checked
        # separately rather than excused.
        served = page_routes | {"/docs"}
        missing = [item.href for item in flat_items() if item.href not in served]

        assert not missing, f"nav links no route serves: {missing}"

    def test_no_two_items_claim_the_same_key(self) -> None:
        keys = [item.key for item in flat_items()]

        assert len(keys) == len(set(keys))

    def test_no_two_items_claim_the_same_destination(self) -> None:
        # Two names for one page is two things to keep in step, and the active-state rule
        # would have to break a tie that should not exist.
        prefixes = [item.prefix for item in flat_items()]

        assert len(prefixes) == len(set(prefixes))


class TestEveryPageIsReachableOrDeclaredNot:
    def test_no_page_is_both_navigable_and_unlisted(self) -> None:
        both = {item.href for item in flat_items()} & UNLISTED

        assert not both, f"listed in the nav and named as unlisted: {sorted(both)}"

    def test_every_unlisted_entry_is_a_route(self, page_routes: frozenset[str]) -> None:
        # An entry for a route that no longer exists is a stale excuse, and it would go on
        # excusing whatever later took that path.
        stale = sorted(UNLISTED - page_routes)

        assert not stale, f"UNLISTED names routes that do not exist: {stale}"

    def test_every_page_route_is_navigable_or_named(self, page_routes: frozenset[str]) -> None:
        """The one that catches a page shipped with no way to reach it.

        A new route is either a destination somebody navigates to, or something reached
        from inside another page. Both are fine; neither being decided is not.
        """
        navigable = {item.href for item in flat_items()}
        orphans = sorted(page_routes - navigable - UNLISTED)

        assert not orphans, (
            f"page routes in neither the nav nor UNLISTED: {orphans}. Add a NavItem if an "
            "operator should be able to reach it, or name it in UNLISTED if it is reached "
            "from inside another page."
        )


class TestWhereYouAre:
    def test_a_leaf_lights_its_own_item(self) -> None:
        assert active_key(NAV, "/settings") == "settings"

    def test_a_child_page_lights_its_parent(self) -> None:
        # The operator is still inside Requests while editing one.
        assert active_key(NAV, "/requests/abc/edit") == "requests"

    def test_the_longest_prefix_wins(self) -> None:
        sections = (
            NavSection(
                key="s",
                label="S",
                tool="t",
                items=(
                    NavItem(key="runs", label="Runs", href="/runs"),
                    NavItem(key="review", label="Review", href="/runs/review"),
                ),
            ),
        )

        assert active_key(sections, "/runs/review/x") == "review"

    def test_a_path_under_nothing_lights_nothing(self) -> None:
        assert active_key(NAV, "/nowhere") == ""

    def test_the_root_does_not_light_everything(self) -> None:
        # A `/` item would match every path if its prefix were compared naively.
        sections = (
            NavSection(
                key="s", label="S", tool="t", items=(NavItem(key="home", label="H", href="/"),)
            ),
        )

        assert active_key(sections, "/") == "home"
        assert active_key(sections, "/requests") == ""


class TestTheShellNeedsNoDatabase:
    def test_it_is_built_from_a_path_alone(self) -> None:
        # `web/routes.py`'s landing page renders with Postgres down, and StrictUndefined
        # means base.html naming `shell.nav` would turn that page into a 500 if the shell
        # needed a query. This is that guarantee, asserted rather than assumed.
        shell = shell_for("/requests")

        assert isinstance(shell, Shell)
        assert shell.nav is NAV
        assert shell.active == "requests"

    def test_guidance_is_off_unless_asked_for(self) -> None:
        assert shell_for("/").guidance is False
        assert shell_for("/").guidance_attr == "off"

    def test_guidance_renders_as_an_attribute_value(self) -> None:
        assert shell_for("/", guidance=True).guidance_attr == "on"
