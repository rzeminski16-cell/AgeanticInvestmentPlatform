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
        body = (await web_client.get("/")).text
        for remote in ("https://cdn", "http://cdn", "unpkg.com", "jsdelivr", "googleapis"):
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

    def test_the_stylesheet_contains_classes_the_templates_use(self):
        # The drift guard. Tailwind only emits classes it found while scanning, so a
        # class present in a template but absent from the output means the stylesheet was
        # not rebuilt after that template changed.
        compiled = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
        # The last two are the destructive-action styling on the cancel and delete buttons.
        # They are the newest classes in the templates, so they are the ones a stale
        # stylesheet would be missing.
        for expected in ("antialiased", "max-w-5xl", "tracking-tight", "border-red-300"):
            assert expected in compiled, f"{expected} missing; run `just css`"

    def test_every_template_is_present(self):
        for name in ("base.html", "_nav.html", "index.html"):
            assert (TEMPLATES_DIR / name).is_file()

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

    async def test_a_page_under_nothing_marks_nothing_current(self, web_client):
        body = (await web_client.get("/")).text

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
