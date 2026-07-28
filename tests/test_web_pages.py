"""The server-rendered shell.

The disclaimer assertions are the point of this file. "This is not investment advice" is
the one piece of text on every page that has to be there for a reason that is not
aesthetic, and the only thing keeping it there is a test that fails when it goes.
"""

from __future__ import annotations

import pytest

from aer.config import load_settings
from aer.version import version
from aer.web.templating import DISCLAIMER, STATIC_DIR, TEMPLATES_DIR
from tests.api_fixtures import build_app, client_for


@pytest.fixture
async def web_client(api_settings, broken_engine, fake_redis):
    # The landing page touches no dependency, so it must render with everything down.
    # If that ever stops being true, this fixture fails and says so.
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
        for expected in ("antialiased", "max-w-5xl", "tracking-tight"):
            assert expected in compiled, f"{expected} missing; run `just css`"

    def test_every_template_is_present(self):
        for name in ("base.html", "_nav.html", "index.html"):
            assert (TEMPLATES_DIR / name).is_file()
