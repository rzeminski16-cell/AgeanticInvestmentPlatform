"""The server-rendered shell.

The disclaimer assertions are the point of this file. "This is not investment advice" is
the one piece of text on every page that has to be there for a reason that is not
aesthetic, and the only thing keeping it there is a test that fails when it goes.
"""

from __future__ import annotations

import re

import pytest

from aer.config import load_settings
from aer.version import version
from aer.web.shell import flat_items, shell_for
from aer.web.templating import DISCLAIMER, STATIC_DIR, TEMPLATES_DIR, templates
from tests.api_fixtures import build_app, client_for


@pytest.fixture
async def web_client(api_settings, broken_engine, fake_redis):
    # Built on dependencies that are down, deliberately. The landing page is the first
    # thing an operator opens when something is not working, so it has to render and say
    # what is wrong rather than return a blank 500.
    async for client in client_for(build_app(api_settings, engine=broken_engine, redis=fake_redis)):
        yield client


class TestLandingPage:
    async def test_renders(self, web_client):
        response = await web_client.get("/")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    async def test_carries_the_disclaimer(self, web_client):
        assert DISCLAIMER in (await web_client.get("/")).text

    async def test_says_it_is_not_investment_advice(self, web_client):
        # Asserted independently of the exact disclaimer wording, so rephrasing the
        # sentence cannot accidentally remove the claim it is there to make.
        body = (await web_client.get("/")).text
        assert "not investment advice" in body.lower()

    async def test_shows_the_build_identity(self, web_client):
        assert version() in (await web_client.get("/")).text

    async def test_loads_no_third_party_asset(self, web_client):
        # Local-first means the GUI works with no internet connection, and a remote
        # script tag on a page that can reach the database is a supply-chain risk taken
        # for convenience.
        #
        # The font hosts are here because `googleapis` alone was not the whole rule. A
        # Google Fonts stylesheet is served from `fonts.googleapis.com`, which that word
        # catches — but the woff2 it then asks for comes from `fonts.gstatic.com`, which it
        # does not, and neither does any of the other three ways to reach for a typeface.
        # The typeface is vendored (`tests/test_fonts.py`); this is what stops it quietly
        # stopping being.
        body = (await web_client.get("/")).text
        for remote in (
            "https://cdn",
            "http://cdn",
            "unpkg.com",
            "jsdelivr",
            "googleapis",
            "gstatic",
            "typekit",
            "fonts.bunny.net",
        ):
            assert remote not in body

    async def test_the_page_is_not_indexable(self, web_client):
        assert 'name="robots"' in (await web_client.get("/")).text

    async def test_it_says_what_is_wrong_when_the_database_is_down(self, web_client):
        # Degraded, not broken. "The database is not reachable, run just up" is actionable;
        # a 500 with a request id is a puzzle.
        body = (await web_client.get("/")).text

        assert "not reachable" in body
        assert "just up" in body

    async def test_pages_that_show_data_still_fail_loudly(self, web_client):
        # Only the landing page degrades. A list page that rendered "no requests" while
        # the database was unreachable would be stating something false.
        assert (await web_client.get("/requests")).status_code == 500


class TestStaticAssets:
    async def test_the_stylesheet_is_served(self, web_client):
        response = await web_client.get("/static/css/app.css")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")

    async def test_htmx_is_served_locally(self, web_client):
        response = await web_client.get("/static/vendor/htmx.min.js")

        assert response.status_code == 200
        assert len(response.content) > 1000

    async def test_the_typeface_is_served_locally(self, web_client):
        response = await web_client.get("/static/fonts/inter-latin-wght-normal.woff2")

        assert response.status_code == 200
        assert len(response.content) > 10_000
        # The type matters: a woff2 served as `application/octet-stream` still loads, but a
        # preload declared `as="font" type="font/woff2"` against a mismatched type is
        # discarded and fetched again — the head start spent twice.
        assert response.headers["content-type"] == "font/woff2"

    async def test_a_missing_asset_is_a_404(self, web_client):
        assert (await web_client.get("/static/nope.css")).status_code == 404

    async def test_the_static_path_cannot_escape_its_root(self, web_client):
        # httpx normalises `..` out of a URL before it is sent, so the traversal is sent
        # pre-encoded -- which is what an attacker would do anyway.
        response = await web_client.get("/static/..%2f..%2f..%2fetc%2fpasswd")

        assert response.status_code in {307, 404}
        assert "root:" not in response.text


class TestProductionHardening:
    @pytest.fixture
    def production_settings(self, settings_env, tmp_path):
        settings_env.setenv("AER_APP_ENV", "production")
        settings_env.setenv("AER_SECRET_KEY", "a-real-key-would-come-from-the-environment")
        settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
        return load_settings()

    @pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
    async def test_the_api_documentation_is_off(
        self, production_settings, broken_engine, fake_redis, path
    ):
        # The docs are a development affordance. Served in production they advertise the
        # entire surface to anyone who reaches the port, which for an application with no
        # authentication is a map handed to whoever asks.
        app = build_app(production_settings, engine=broken_engine, redis=fake_redis)
        async for client in client_for(app):
            assert (await client.get(path)).status_code == 404

    @pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
    async def test_the_api_documentation_is_on_in_development(self, web_client, path):
        assert (await web_client.get(path)).status_code == 200


class TestCommittedBuildOutput:
    """CI runs ruff, mypy and pytest, and needs Node for none of them. These assert the
    committed artefacts that keep it that way actually exist."""

    def test_the_compiled_stylesheet_is_present(self):
        stylesheet = STATIC_DIR / "css" / "app.css"
        assert stylesheet.is_file()
        # Tailwind's own reset alone is larger than this; anything smaller means the
        # build produced an empty or failed output.
        assert stylesheet.stat().st_size > 2000

    def test_the_stylesheet_covers_every_class_the_templates_use(self):
        """The drift guard, over the whole set rather than a sample of it.

        Tailwind emits only what it found while scanning, so a class in a template and not
        in the output means the stylesheet was not rebuilt after that template changed —
        and the page renders that rule as nothing at all.

        This replaced four hand-picked class names whose own comment called them "the
        newest classes in the templates, so they are the ones a stale stylesheet would be
        missing". That was true when it was written and stopped being true the next time
        somebody edited a template: `pt-3` and `border-amber-200` went into
        `runs/assumptions.html` and the sample never moved, so the amber divider on that
        page had been rendering unstyled. A sample that has to be kept newest is a sample
        nobody will keep newest.
        """
        compiled = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
        missing = sorted(
            token
            for token in _template_classes()
            if token not in _NOT_UTILITIES and _selector(token) not in compiled
        )

        assert not missing, (
            f"classes missing from the compiled stylesheet: {missing}. Run `just css`."
        )

    def test_every_template_is_present(self):
        for name in ("base.html", "_nav.html", "index.html"):
            assert (TEMPLATES_DIR / name).is_file()

    def test_the_ui_aggregator_exports_every_macro(self):
        """`_ui/index.html` is the one import a page makes, so it has to actually re-export.

        Jinja does not export a name brought in with `{% from %}`, so the aggregator as
        first written exported nothing and `ui.card` raised `UndefinedError` — invisibly,
        because it shipped before any page used it. The Overview screen was the first page
        to import it and it failed on its first render. Each name is now assigned at the
        top level, and this is the check that a macro added to a component file and
        forgotten in the aggregator is a red build rather than a page that breaks when
        somebody first reaches for it.
        """
        defined = {
            name
            for source in ("provenance.html", "surfaces.html")
            for name in re.findall(
                r"{%-?\s*macro\s+(\w+)\s*\(",
                (TEMPLATES_DIR / "_ui" / source).read_text(encoding="utf-8"),
            )
        }
        exported = {
            name
            for name in dir(templates.env.get_template("_ui/index.html").module)
            if not name.startswith("_")
        }

        assert defined, "no macros found in _ui/ — the pattern above stopped matching"
        assert defined <= exported, (
            f"macros defined in _ui/ and not exported by _ui/index.html: "
            f"{sorted(defined - exported)}. Add a `set` beside its import."
        )

    def test_the_shared_request_form_is_present(self):
        # `new.html` and `edit.html` both include it and neither renders without it, so a
        # missing partial is two broken pages rather than one.
        for name in ("_form.html", "new.html", "edit.html", "immutable.html"):
            assert (TEMPLATES_DIR / "requests" / name).is_file()


class TestTheShellRendersFromData:
    """The nav is a loop over `shell.nav`, and the shell is injected rather than passed.

    Built on a broken engine like the rest of this file, which is the assertion that
    matters most here: `StrictUndefined` means `base.html` naming `shell.nav` would turn
    the one page an operator opens when Postgres is down into a 500 if the shell needed a
    query. It does not, and this is where that stays true.
    """

    async def test_every_nav_label_reaches_the_page(self, web_client):
        body = (await web_client.get("/")).text

        for item in flat_items():
            assert f'href="{item.href}"' in body, f"{item.key} is missing from the rendered nav"
            assert item.label in body

    def test_the_current_item_says_so(self):
        """Asserted against the template rather than a route, and deliberately.

        Every page that would light a nav item needs the database, and this file's client
        is built on one that is down on purpose. Rendering the partial directly tests the
        one thing at issue — that the template marks the active item — without pretending
        to test a page it cannot reach.
        """
        markup = templates.env.get_template("_nav.html").render(
            shell=shell_for("/requests"), disclaimer=DISCLAIMER
        )

        assert markup.count('aria-current="page"') == 1
        assert 'href="/requests"' in markup

    async def test_the_main_menu_marks_itself_current(self, web_client):
        # `/` used to be a landing page that was in no section. It is the Overview item's
        # destination now, so marking nothing would be the bug.
        body = (await web_client.get("/")).text

        assert body.count('aria-current="page"') == 1

    async def test_a_page_under_nothing_marks_nothing_current(self, web_client):
        # A planned tool: reachable from the launcher, in no section, and needing no
        # database — which is why it can be asked of this client at all.
        body = (await web_client.get("/watchlist")).text

        assert 'aria-current="page"' not in body

    async def test_guidance_is_off_by_default(self, web_client):
        assert 'data-guidance="off"' in (await web_client.get("/")).text


class TestGuidanceMode:
    """A form POST that redirects, so it works with scripting off (ADR 0006)."""

    async def _token(self, web_client) -> str:
        page = await web_client.get("/requests/new")
        found = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert found, "no CSRF token on the form page"
        return found.group(1)

    async def test_turning_it_on_is_remembered(self, web_client):
        token = await self._token(web_client)

        response = await web_client.post(
            "/_shell/guidance",
            data={"guidance": "on", "next": "/requests", "csrf_token": token},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/requests"
        assert 'data-guidance="on"' in (await web_client.get("/")).text

    async def test_turning_it_off_again_is_remembered(self, web_client):
        token = await self._token(web_client)
        data = {"next": "/", "csrf_token": token}
        await web_client.post("/_shell/guidance", data={**data, "guidance": "on"})

        await web_client.post("/_shell/guidance", data=data, follow_redirects=False)

        assert 'data-guidance="off"' in (await web_client.get("/")).text

    async def test_it_refuses_to_forward_off_site(self, web_client):
        # A redirect that followed a form field anywhere would be an open redirect: a page
        # on this origin that hands the operator to somebody else's.
        token = await self._token(web_client)

        for hostile in ("https://example.invalid/", "//example.invalid/", "javascript:alert(1)"):
            response = await web_client.post(
                "/_shell/guidance",
                data={"guidance": "on", "next": hostile, "csrf_token": token},
                follow_redirects=False,
            )

            assert response.headers["location"] == "/", f"{hostile} was followed"

    async def test_a_forged_token_changes_nothing(self, web_client):
        response = await web_client.post(
            "/_shell/guidance",
            data={"guidance": "on", "next": "/requests", "csrf_token": "forged.1.deadbeef"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert 'data-guidance="off"' in (await web_client.get("/")).text


# Class names in the templates that Tailwind does not generate and never will: form and
# behaviour hooks, and the node and edge classes the knowledge graph's SVG carries. They are
# listed rather than pattern-matched, so a genuine utility cannot hide among them by
# accident.
_NOT_UTILITIES: frozenset[str] = frozenset(
    {
        "archive-request",
        "remove-request",
        "restore-request",
        "edge-comparable",
        "edge-membership",
        "node-company",
        "node-theme",
    }
)


def _selector(token: str) -> str:
    """A class name as it appears in compiled CSS, with the characters Tailwind escapes."""
    return "." + "".join("\\" + ch if ch in ":/.[]%()#," else ch for ch in token)


def _template_classes() -> set[str]:
    """Every statically written class name in every template.

    Attributes containing Jinja are skipped: a class composed at render time is not a string
    this can check, and guessing at what it might become would make the test fail on
    something no page ever asks for.
    """
    found: set[str] = set()
    for template in TEMPLATES_DIR.rglob("*.html"):
        body = template.read_text(encoding="utf-8")
        for match in re.finditer(r'class="([^"]*)"', body):
            raw = match.group(1)
            if "{" in raw or "}" in raw:
                continue
            found.update(token for token in raw.split() if token)
    return found
