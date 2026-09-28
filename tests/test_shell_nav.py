"""The nav describes routes that exist, and every page is reachable or says why not.

Nav drift is quiet. A link whose route was renamed goes on rendering, and the 404 is found
by whoever clicks it; a page added without a link is simply never visited, which is
indistinguishable from a page nobody finished. Neither shows up in a test of the page
itself, because each page is fine — it is the relationship between them that is wrong.

So the relationship is what is asserted here.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from aer.web import nav as nav_types
from aer.web.nav import NavGroup, NavItem, NavSection, active_key
from aer.web.shell import NAV, OWNERS, UNLISTED, Shell, flat_items, shell_for
from aer.web.shell import registry as nav_registry
from aer.web.templating import templates
from tests.route_fixtures import page_routes_for


@pytest.fixture(scope="module")
def page_routes() -> frozenset[str]:
    """The server-rendered GET routes: what an operator can open in a browser.

    The walk itself lives in `tests/route_fixtures.py` because
    `tests/test_every_page_renders.py` needs the same list, and two answers to "what does
    this application serve" would be the drift this file exists to catch.
    """
    return page_routes_for()


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


class TestNoContributorImportsTheShellBack:
    """The cycle that worked only because of import order.

    `shell/registry.py` imports each contributing tool's module to compose the navigation.
    Those modules need `NavItem` and `NavSection`, and while those types lived under
    `shell/`, importing them ran `aer/web/shell/__init__.py` first — which imports
    `context.py`, which imports `registry.py`, which was half-way through importing the
    contributor. `import aer.web.tools.registry` in a fresh interpreter raised
    `ImportError`; the application never noticed because something always imported
    `aer.web.shell` first.

    The types moved to `aer/web/nav.py`, beside an `__init__` that imports nothing. This is
    the rule that keeps them there: **composition points down.** The shell may import a
    contributor; a contributor may not import the shell.
    """

    def test_no_module_the_registry_imports_reaches_back(self) -> None:
        """Derived from `shell/registry.py`'s own imports rather than from a list here.

        A contributor is exactly a module the registry names — `templating.py` and
        `routes.py` import the shell too and are fine, because nothing imports *them* back.
        Reading the set off the registry means a tool added tomorrow is checked without
        anybody remembering to add it.
        """
        web = Path(__file__).parent.parent / "src" / "aer" / "web"
        registry = ast.parse((web / "shell" / "registry.py").read_text(encoding="utf-8"))
        contributors = sorted(
            node.module
            for node in ast.walk(registry)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("aer.web.")
        )
        assert contributors, "shell/registry.py imports no contributor at all"

        offenders = []
        for module in contributors:
            path = web.parent.parent / Path(module.replace(".", "/") + ".py")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders.extend(
                f"{module} -> {node.module}"
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("aer.web.shell")
            )

        assert not offenders, (
            f"contributors importing the shell back: {offenders}. Composition points down "
            "— the shell imports a tool's module, never the other way, or the package is "
            "half-initialised by the time the tool needs it."
        )

    def test_every_module_under_web_imports_on_its_own(self) -> None:
        """The property the rule above protects, checked directly on the two that broke.

        A subprocess each, because the failure only exists in a *fresh* interpreter: once
        anything has imported `aer.web.shell`, every ordering works.
        """
        for module in ("aer.web.tools.registry", "aer.web.overview.nav", "aer.web.nav"):
            finished = subprocess.run(  # noqa: S603
                [sys.executable, "-c", f"import {module}"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert finished.returncode == 0, f"{module} does not import alone:\n{finished.stderr}"


def _one_group(*items: NavItem) -> NavGroup:
    """One group holding one section holding these items — the smallest whole nav."""
    return NavGroup(key="g", label="G", sections=(NavSection(key="s", tool="t", items=items),))


class TestTheMenuIsGroupedByWhatYouAreDoing:
    """ADR 0112, held as measurements rather than as taste.

    The defect was structural: a nav section is one tool's contribution, and drawing each
    under its own heading made "one heading per tool" a rule nobody chose. Ten headings
    over eighteen destinations, seven of them over a single link, six of those repeating
    the word beneath. Every assertion here is one of those numbers refusing to come back.

    **Amended 28 September 2026**: the menu is the six destinations the operator approved,
    and a destination's pages are its tabs. So the numbers held here are the drawing's.
    """

    def test_the_menu_is_the_six_destinations_as_drawn(self) -> None:
        assert [group.label for group in NAV] == [
            "Today",
            "Portfolio",
            "Companies",
            "Research",
            "Review",
            "Platform",
        ]

    def test_platform_alone_is_set_apart_at_the_foot(self) -> None:
        assert [group.key for group in NAV if group.foot] == ["platform"]
        assert NAV[-1].foot, "the foot is drawn last, below the other five"

    def test_every_destination_opens_on_one_of_its_own_pages(self) -> None:
        # A menu link that opened somewhere its own tabs did not list would light one
        # destination and show another's page.
        strays = {
            group.key: group.destination
            for group in NAV
            if group.destination not in {item.href for item in group.items}
        }

        assert not strays, f"destinations opening outside themselves: {strays}"

    def test_every_destination_is_drawn_with_an_icon_the_menu_has(self) -> None:
        icons = templates.env.get_template("_shell/icons.html").module
        blank = [group.key for group in NAV if "<path" not in str(icons.icon(group.icon))]

        assert not blank, f"destinations whose icon draws nothing: {blank}"

    def test_the_chrome_never_says_one_word_twice(self) -> None:
        # "Watchlist · Watchlist" and "Portfolio · Portfolio" were the smallest version
        # of the whole problem, and the one a reader met twice: in the rail and in the
        # chrome at the width where the rail collapses. A destination's own page is now the
        # destination, once.
        echoes = []
        for item in flat_items():
            words = [word.casefold() for word in shell_for(item.href).location.split(" · ")]
            if len(words) != len(set(words)):
                echoes.append(item.href)

        assert not echoes, f"these pages name one word twice: {echoes}"

    def test_every_registered_section_sits_in_exactly_one_group(self) -> None:
        """A section imported and never placed is a tool nobody can reach.

        Walked from the registry's own module rather than from a list, because a list
        would be the third place to keep in step and the one that quietly disagrees.
        """
        imported = sorted(
            value.key
            for value in vars(nav_registry).values()
            if isinstance(value, nav_types.NavSection)
        )
        placed = sorted(section.key for group in NAV for section in group.sections)

        assert placed == imported, "a section is missing from every group, or is in two"

    def test_the_grouping_is_the_shells_alone(self) -> None:
        """No tool names a group, and none can: the type has no field for it.

        This is what keeps ADR 0071's registration contract one line long. A tool that
        could ask for a heading would be a tool deciding what the product's menu looks
        like, and nine of those is what the menu already looked like.
        """
        assert not hasattr(NavSection, "label")
        assert "group" not in {field.name for field in fields(NavSection)}


class TestWhereYouAre:
    def test_a_leaf_lights_its_own_item(self) -> None:
        assert active_key(NAV, "/settings") == "settings"

    def test_a_child_page_lights_its_parent(self) -> None:
        # The operator is still inside Requests while editing one.
        assert active_key(NAV, "/requests/abc/edit") == "requests"

    def test_the_longest_prefix_wins(self) -> None:
        groups = (
            _one_group(
                NavItem(key="runs", label="Runs", href="/runs"),
                NavItem(key="review", label="Review", href="/runs/review"),
            ),
        )

        assert active_key(groups, "/runs/review/x") == "review"

    def test_a_path_under_nothing_lights_nothing(self) -> None:
        assert active_key(NAV, "/nowhere") == ""

    def test_the_chrome_names_the_destination_and_the_page(self) -> None:
        # What a reader sees at the width where the rail collapses.
        assert shell_for("/risk").location == "Portfolio · Risk"
        assert shell_for("/requests").location == "Research · Requests"

    def test_a_destinations_own_page_is_named_once(self) -> None:
        assert shell_for("/").location == "Today"
        assert shell_for("/portfolio").location == "Portfolio"
        assert shell_for("/companies").location == "Companies"

    def test_an_unlisted_page_names_the_destination_that_owns_it(self) -> None:
        # A run's console is inside Research although no tab names it (ADR 0112, amended).
        assert shell_for("/runs/abc/sources").location == "Research"

    def test_a_page_inside_nothing_names_nothing(self) -> None:
        # The search results belong to no destination, and naming one would say they did.
        assert shell_for("/search").location == ""

    def test_the_root_does_not_light_everything(self) -> None:
        # A `/` item would match every path if its prefix were compared naively.
        groups = (_one_group(NavItem(key="home", label="H", href="/")),)

        assert active_key(groups, "/") == "home"
        assert active_key(groups, "/requests") == ""


class TestTheDestinationYouAreIn:
    """Which of the six the page is inside, and the tabs that follow from it."""

    @pytest.mark.parametrize(
        ("path", "destination"),
        [
            ("/", "today"),
            ("/portfolio/positions/abc", "portfolio"),
            ("/risk", "portfolio"),
            ("/decisions/abc", "portfolio"),
            ("/monitor/findings/abc", "portfolio"),
            ("/companies/abc", "companies"),
            ("/watchlist", "companies"),
            ("/theses/abc", "companies"),
            ("/ask/abc", "companies"),
            ("/reports/abc", "research"),
            ("/requests/new", "research"),
            ("/skills/new", "research"),
            ("/knowledge/graph", "research"),
            ("/analytics", "review"),
            ("/costs", "platform"),
        ],
    )
    def test_a_page_lights_the_destination_its_drawing_does(
        self, path: str, destination: str
    ) -> None:
        assert shell_for(path).active_group == destination

    @pytest.mark.parametrize("path", ["/runs/abc", "/runs/abc/valuation", "/calculations/abc"])
    def test_an_unlisted_page_lights_its_owner(self, path: str) -> None:
        # A run, a calculation and a claim light Research (ADR 0112, amended).
        assert shell_for(path).active_group == "research"

    def test_every_owner_is_a_destination(self) -> None:
        assert set(OWNERS.values()) <= {group.key for group in NAV}

    def test_no_owner_disagrees_with_a_tab_beneath_it(self) -> None:
        # `/runs` covers the *Active run* tab's `/runs/active` as well as every console, and
        # both say Research. A prefix that named another destination than a tab under it
        # would be two answers to one question, decided by which was consulted first.
        disagreements = sorted(
            (prefix, item.href)
            for prefix, owner in OWNERS.items()
            for group in NAV
            for item in group.items
            if (item.prefix == prefix or item.prefix.startswith(f"{prefix}/"))
            and group.key != owner
        )

        assert not disagreements, f"owners contradicting a tab: {disagreements}"

    def test_no_owner_is_stale(self) -> None:
        # Every prefix in the mapping still covers a page nobody's tab names.
        stale = sorted(
            prefix
            for prefix in OWNERS
            if not any(route.startswith(f"{prefix}/") for route in UNLISTED)
        )

        assert not stale, f"owners covering no unlisted page: {stale}"

    def test_a_destination_of_many_pages_shows_them_as_tabs(self) -> None:
        assert [item.label for item in shell_for("/requests").tabs] == [
            "Reports",
            "Requests",
            "Active run",
            "Methods",
            "Knowledge",
        ]

    def test_a_destination_of_one_page_shows_no_tabs(self) -> None:
        # A row of one tab is a heading pretending to be a control.
        assert shell_for("/").tabs == ()

    def test_every_page_the_menu_dropped_is_still_a_tab(self) -> None:
        """The promise the amendment made: nothing left the menu for nowhere.

        Every item is a tab on its own destination's pages, or its destination has only the
        one page and the menu's link is it.
        """
        unreachable = []
        for group in NAV:
            for item in group.items:
                shown = shell_for(item.href)
                in_menu = len(group.items) == 1 and group.destination == item.href
                as_tab = shown.group is group and item in shown.tabs
                if not (in_menu or as_tab):
                    unreachable.append(item.href)

        assert not unreachable, f"pages neither in the menu nor a tab: {unreachable}"


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
