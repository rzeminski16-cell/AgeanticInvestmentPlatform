"""The tool registry and the launcher it drives.

Three states, and where a tool appears follows from which one it is in. The failures worth
catching are the ones that would quietly make the shape of the product invisible again: a
launcher entry pointing at nothing, eight pages that all describe the same tool, a
placeholder that answers 404 and reads as a broken link, or a working tool whose real pages
get overwritten by a placeholder claiming its URL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from markupsafe import escape
from starlette.routing import Mount

from aer.api.app import create_app
from aer.config import Settings
from aer.web.shell import NAV, UNLISTED, flat_items
from aer.web.tools.registry import (
    INSTALLED_TOOLS,
    PORTFOLIO,
    Tool,
    ToolStatus,
    installed_tools,
    resolve_tool,
    tools_needing_a_page,
)
from tests.api_fixtures import build_app, client_for

ADR_DIR = Path(__file__).parent.parent / "docs" / "adr"


@pytest.fixture
async def client(api_settings, broken_engine, fake_redis):
    # A broken engine on purpose. The launcher is a list from a registry and a placeholder
    # describes work that does not exist, so neither touches data — and the front page of a
    # local tool is the page you open *because* something is not working.
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


class TestEachToolIsARowWithARecord:
    def test_every_tool_names_an_adr_that_exists(self) -> None:
        missing = [
            tool.key for tool in installed_tools() if not list(ADR_DIR.glob(f"{tool.adr}-*.md"))
        ]

        assert not missing, f"tools naming an ADR with no file: {missing}"

    def test_no_two_claim_a_key_or_a_destination(self) -> None:
        keys = [tool.key for tool in installed_tools()]
        hrefs = [tool.href for tool in installed_tools()]

        assert len(keys) == len(set(keys))
        assert len(hrefs) == len(set(hrefs))

    def test_a_tool_that_is_not_built_says_what_it_needs(self) -> None:
        """The field that stops a placeholder being a progress bar.

        Somebody opening one is asking why it is not here. "Attestations and their two
        grades, because a fill price is not filed, not chosen and not calculated" is an
        answer; a percentage is not.
        """
        for tool in tools_needing_a_page():
            assert len(tool.summary) > 40, tool.key
            assert len(tool.needs) > 40, tool.key

    def test_a_working_tool_needs_nothing(self) -> None:
        # `needs` is what is missing. A built tool with a list of prerequisites is a row
        # somebody forgot to clear when it shipped.
        for tool in installed_tools():
            if tool.is_built:
                assert tool.needs == "", tool.key

    def test_the_platform_has_seven_working_tools_and_nothing_half_built(self) -> None:
        """Stated rather than counted loosely: this is the claim the launcher makes.

        Portfolio was ``UNDER_CONSTRUCTION`` while its tables and arithmetic were being
        built and is working now — it has a screen, an entry form and figures that resolve
        to their trades. Theses arrived whole (§3.5): a judgement table, a list, a detail
        and four forms, in one change. The monitor followed (§3.6): a role, a pass, a
        findings table and the one gate no research run opens. Then decisions (§3.7): the
        journal entry written before the outcome, and the trade that points back at it.
        Post-trade review and decision analytics came together (§3.8): the reviewer
        proposes, the operator confirms, and the analytics count the confirmed reviews with
        the ``n`` beside every statistic. Nothing occupies the middle state today, which is
        a fact about this moment rather than a reason to remove it.
        """
        by_status = {tool.key: tool.status for tool in installed_tools()}

        assert by_status["research"] is ToolStatus.WORKING
        assert by_status["portfolio"] is ToolStatus.WORKING
        assert by_status["theses"] is ToolStatus.WORKING
        assert by_status["monitor"] is ToolStatus.WORKING
        assert by_status["decisions"] is ToolStatus.WORKING
        assert by_status["review"] is ToolStatus.WORKING
        assert by_status["analytics"] is ToolStatus.WORKING
        assert sum(1 for status in by_status.values() if status is ToolStatus.WORKING) == 7
        assert not [
            key for key, status in by_status.items() if status is ToolStatus.UNDER_CONSTRUCTION
        ]

    def test_an_unknown_key_resolves_to_nothing(self) -> None:
        assert resolve_tool("a_tool_nobody_planned") is None


class TestWhereEachStatePutsATool:
    def test_a_working_tool_is_never_served_a_placeholder(self) -> None:
        """The failure that would be hardest to see: a placeholder claiming a live URL.

        `/requests` is the research tool's own page. A loop over the whole registry rather
        than over the unbuilt rows would register a second route for it, and which one
        answered would depend on router order.
        """
        served = {tool.key for tool in tools_needing_a_page()}

        assert "research" not in served
        assert served == {tool.key for tool in INSTALLED_TOOLS if not tool.is_built}

    def test_the_portfolio_is_in_the_navigation(self) -> None:
        # It was there while it was being built, so that watching it arrive was possible,
        # and it is there now because it works. The section did not have to move.
        assert [item.key for item in PORTFOLIO.items] == ["portfolio"]
        assert "/portfolio" in {item.href for item in flat_items()}

    def test_a_planned_tool_is_on_the_launcher_and_not_in_the_navigation(self) -> None:
        """The operator's decision, as a test.

        A navigation listing seven things nobody can use is worse than a launcher that
        shows the shape once. `UNLISTED` is where that decision is written down, so it is
        arguable rather than accidental.
        """
        navigable = {item.href for item in flat_items()}
        planned = [tool for tool in installed_tools() if tool.status is ToolStatus.PLANNED]

        assert planned, "no planned tools at all — the two checks below assert nothing"
        for tool in planned:
            assert tool.href not in navigable, tool.key
            assert tool.href in UNLISTED, tool.key

    def test_every_href_is_a_literal_route(self) -> None:
        """One route per row rather than a parameterised catch-all.

        `/portfolio` is under construction today and the portfolio tomorrow, so nothing
        linking to it ever moves — and a literal path is also what lets the nav drift test
        compare hrefs against routes at all, which a `/tools/{key}` would have defeated.
        """
        served = _page_routes()
        missing = sorted(tool.href for tool in installed_tools() if tool.href not in served)

        assert not missing, f"tools the application does not serve: {missing}"

    def test_the_navigation_is_eight_sections_now(self) -> None:
        assert [section.key for section in NAV] == [
            "overview",
            "research",
            "portfolio",
            "theses",
            "decisions",
            "monitor",
            "review",
            "platform",
        ]


class TestTheLauncher:
    async def test_it_lists_every_tool_with_its_state(self, client) -> None:
        body = (await client.get("/")).text

        for tool in installed_tools():
            assert f'data-tool="{tool.key}"' in body, tool.key
            assert f'data-status="{tool.status.value}"' in body, tool.key
            assert str(escape(tool.summary)) in body, tool.key

    async def test_the_working_tool_carries_its_common_action(self, client) -> None:
        # The old landing page's "Start a research request" button, back as a field on the
        # row rather than a line in the template — so the second tool's action appears when
        # its row grows one.
        body = (await client.get("/")).text

        assert 'href="/requests/new"' in body
        assert "Start a research request" in body

    async def test_a_tool_that_cannot_be_used_carries_no_action(self) -> None:
        # Refused at construction rather than hidden at render: a button on a tool that
        # does not exist is a button that goes nowhere.
        with pytest.raises(ValueError, match="goes nowhere"):
            Tool(
                key="probe",
                label="Probe",
                status=ToolStatus.PLANNED,
                href="/probe",
                summary="x",
                adr="0071",
                needs="y",
                action_label="Do the thing",
                action_href="/probe/new",
            )

    async def test_an_action_with_nowhere_to_go_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nowhere to go"):
            Tool(
                key="probe",
                label="Probe",
                status=ToolStatus.WORKING,
                href="/probe",
                summary="x",
                adr="0071",
                action_label="Do the thing",
            )

    async def test_it_renders_with_the_database_down(self, client) -> None:
        """The whole reason the launcher and the work list are separated.

        The front page of a local tool is the page you open when something is not working,
        and the most likely reason you are looking at it is that Postgres is not running.
        """
        response = await client.get("/")

        assert response.status_code == 200
        assert 'data-tool="research"' in response.text
        assert "not reachable" in response.text

    async def test_the_work_list_is_not_shown_as_empty_when_it_is_unknown(self, client) -> None:
        # The one thing the degraded page must not do. "Nothing is waiting" is a claim, and
        # with the database down it is a claim nobody checked.
        body = (await client.get("/")).text

        assert "Nothing is waiting" not in body
        assert "Your attention" not in body

    async def test_the_former_address_still_leads_somewhere(self, client) -> None:
        response = await client.get("/overview", follow_redirects=False)

        assert response.status_code == 308
        assert response.headers["location"] == "/"


class TestThePlaceholderPages:
    async def test_each_one_is_a_page_rather_than_an_error(self, client) -> None:
        """200, not 404 and not 501.

        The page exists and is correct — it says truthfully that the tool does not. A 404
        would be a lie about a URL the launcher links to, and a 501 would put a server
        error in a log for a page working exactly as intended.
        """
        for tool in tools_needing_a_page():
            response = await client.get(tool.href)
            assert response.status_code == 200, tool.key

    async def test_each_one_describes_its_own_tool(self, client) -> None:
        """The bug a loop of closures produces, which reads as eight identical pages.

        Capturing the loop variable by reference gives every route the last row. Nothing
        about the resulting site looks broken: every link works and every page renders.
        """
        seen: dict[str, str] = {}
        for tool in tools_needing_a_page():
            body = (await client.get(tool.href)).text
            # Through `escape`, because the rows are prose and prose has apostrophes:
            # "one run's cap" reaches the page as `run&#39;s`.
            assert str(escape(tool.summary)) in body, tool.key
            assert str(escape(tool.needs)) in body, tool.key
            assert f"ADR {tool.adr}" in body, tool.key
            seen[tool.key] = body

        for tool in tools_needing_a_page():
            others = [row for row in tools_needing_a_page() if row.key != tool.key]
            leaked = [row.key for row in others if str(escape(row.needs)) in seen[tool.key]]
            assert not leaked, f"{tool.key} describes {leaked}"

    async def test_it_says_which_state_it_is_in(self, client) -> None:
        """ "Under construction" and "Planned" are different promises.

        Nothing is under construction today — portfolio was and now works — so this asserts
        the state that has an occupant, and asserts against the registry rather than
        against a hard-coded URL so it follows whichever tool is in which state.
        """
        for tool in tools_needing_a_page():
            body = (await client.get(tool.href)).text
            assert tool.status.value in body, tool.key

        assert "Planned" in (await client.get("/watchlist")).text

    async def test_it_links_to_the_rest_of_the_shape(self, client) -> None:
        body = (await client.get("/watchlist")).text

        for tool in installed_tools():
            if tool.key != "watchlist":
                assert f'data-tool="{tool.key}"' in body, tool.key
