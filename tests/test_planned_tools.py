"""The tools that are not built yet: registered, reachable, and honest about it.

The whole point of these pages is that the shape of the product is visible rather than
absent, so the failures worth catching are the ones that would quietly make it invisible
again: a nav item pointing at nothing, eight pages that all describe the same tool, or a
placeholder that answers 404 and reads as a broken link rather than as work not started.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from markupsafe import escape
from starlette.routing import Mount

from aer.api.app import create_app
from aer.config import Settings
from aer.web.planned.nav import OVERSIGHT, PORTFOLIO, planned_tools, resolve_planned
from aer.web.shell import NAV, UNLISTED, flat_items
from tests.api_fixtures import build_app, client_for

ADR_DIR = Path(__file__).parent.parent / "docs" / "adr"


@pytest.fixture
async def client(api_settings, broken_engine, fake_redis):
    # A broken engine on purpose. These pages describe work that does not exist, so they
    # touch no data — and a placeholder that needed the database to say "not built yet"
    # would be the one page in the product that could not explain itself.
    async for made in client_for(build_app(api_settings, engine=broken_engine, redis=fake_redis)):
        yield made


def _page_routes() -> frozenset[str]:
    app = create_app(Settings(http_user_agent="Test test@example.invalid"))
    found: list[str] = []

    def walk(routes: Any) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
                continue
            path = getattr(route, "path", None)
            if path and not isinstance(route, Mount):
                found.append(path)

    walk(app.routes)
    return frozenset(found)


class TestEachOneIsARowWithARecord:
    def test_every_planned_tool_names_an_adr_that_exists(self) -> None:
        missing = [
            tool.key for tool in planned_tools() if not list(ADR_DIR.glob(f"{tool.adr}-*.md"))
        ]

        assert not missing, f"planned tools naming an ADR with no file: {missing}"

    def test_no_two_claim_a_key_or_a_destination(self) -> None:
        keys = [tool.key for tool in planned_tools()]
        hrefs = [tool.href for tool in planned_tools()]

        assert len(keys) == len(set(keys))
        assert len(hrefs) == len(set(hrefs))

    def test_each_says_what_it_will_do_and_what_it_needs(self) -> None:
        """The field that stops this being a "coming soon" page.

        Somebody who opens one of these is asking why it is not here. "The attestation
        tables, and the two clocks" is an answer; a progress bar is not.
        """
        for tool in planned_tools():
            assert len(tool.summary) > 40, tool.key
            assert len(tool.needs) > 40, tool.key

    def test_an_unknown_key_resolves_to_nothing(self) -> None:
        assert resolve_planned("a_tool_nobody_planned") is None


class TestTheyAreInTheNavAndInTheRoutes:
    def test_both_sections_carry_items(self) -> None:
        # Otherwise the two tests below pass by agreeing that the sidebar is empty.
        assert PORTFOLIO.items
        assert OVERSIGHT.items

    def test_every_planned_tool_is_navigable(self) -> None:
        navigable = {item.href for item in flat_items()}
        missing = sorted(tool.href for tool in planned_tools() if tool.href not in navigable)

        assert not missing, f"planned tools with no way to reach them: {missing}"

    def test_every_href_is_a_literal_route(self) -> None:
        """One route per row rather than a parameterised catch-all.

        `/watchlist` is a placeholder today and the watchlist tomorrow, so nothing linking
        to it ever moves — and a literal path is also what lets the nav drift test compare
        hrefs against routes at all, which a `/tools/{key}` would have quietly defeated.
        """
        served = _page_routes()
        missing = sorted(tool.href for tool in planned_tools() if tool.href not in served)

        assert not missing, f"planned tools the application does not serve: {missing}"

    def test_none_is_excused_as_unlisted(self) -> None:
        # `UNLISTED` names pages reached from inside another page. A planned tool is a
        # destination; being in both lists would mean nobody had decided which it was.
        both = {tool.href for tool in planned_tools()} & UNLISTED

        assert not both, f"listed in the nav and named as unlisted: {sorted(both)}"

    def test_the_sections_do_not_claim_to_be_registered_tools(self) -> None:
        """`NavSection.tool` is a registry key, and none of these is registered yet.

        Claiming one would put a tool in the nav that `test_shell_badges` and
        `test_overview` then check their providers against — and a section whose tool has
        no providers is fine, while a provider whose tool has no section is the drift those
        tests exist to catch. Naming them `planned` keeps both readings honest.
        """
        for section in (PORTFOLIO, OVERSIGHT):
            assert section.tool == "planned"

    def test_the_nav_now_describes_the_whole_product(self) -> None:
        # The reason this slice exists: a sidebar listing only what is finished describes a
        # research tool, which is what this codebase is in the middle of not being.
        assert [section.key for section in NAV] == [
            "overview",
            "research",
            "portfolio",
            "oversight",
            "platform",
        ]


class TestThePages:
    async def test_each_one_is_a_page_rather_than_an_error(self, client) -> None:
        """200, not 404 and not 501.

        The page exists and is correct — it says truthfully that the tool does not. A 404
        would be a lie about a URL the sidebar links to, and a 501 would put a server error
        in a log for a page working exactly as intended.
        """
        for tool in planned_tools():
            response = await client.get(tool.href)
            assert response.status_code == 200, tool.key

    async def test_each_one_describes_its_own_tool(self, client) -> None:
        """The bug a loop of closures produces, which reads as eight identical pages.

        Capturing the loop variable by reference gives every route the last row. Nothing
        about the resulting site looks broken: every link works and every page renders.
        """
        seen: dict[str, str] = {}
        for tool in planned_tools():
            body = (await client.get(tool.href)).text
            # Through `escape`, because the rows are prose and prose has apostrophes:
            # "one run's cap" reaches the page as `run&#39;s`, and a raw comparison would
            # fail for the one reason that is not a defect.
            assert str(escape(tool.summary)) in body, tool.key
            assert str(escape(tool.needs)) in body, tool.key
            assert f"ADR {tool.adr}" in body, tool.key
            seen[tool.key] = body

        # And no page carries somebody else's description, which is what the closure bug
        # produces: eight pages that are each internally consistent and all the same tool.
        for tool in planned_tools():
            others = [row for row in planned_tools() if row.key != tool.key]
            leaked = [row.key for row in others if str(escape(row.needs)) in seen[tool.key]]
            assert not leaked, f"{tool.key} describes {leaked}"

    async def test_a_page_says_plainly_that_it_is_not_built(self, client) -> None:
        body = (await client.get("/watchlist")).text

        assert "Not built yet" in body
        assert "Watchlist" in body

    async def test_it_links_to_the_rest_of_the_shape(self, client) -> None:
        # The other seven, so the page is a map rather than a dead end.
        body = (await client.get("/watchlist")).text

        for tool in planned_tools():
            if tool.key != "watchlist":
                assert f'data-planned="{tool.key}"' in body, tool.key
        assert 'data-planned="watchlist"' not in body

    async def test_it_renders_with_the_database_down(self, client) -> None:
        # Asserted rather than assumed: `StrictUndefined` plus a shell that needed a query
        # would turn every one of these into a 500 on the machine of somebody who has not
        # started Postgres — which is exactly who is reading them.
        assert (await client.get("/positions")).status_code == 200
