"""The server-rendered shell.

The disclaimer assertions are the point of this file. "This is not investment advice" is
the one piece of text on every page that has to be there for a reason that is not
aesthetic, and the only thing keeping it there is a test that fails when it goes.
"""

from __future__ import annotations

import mimetypes
import re

import pytest

from aer.api.app import _LOCAL_MEDIA_TYPES
from aer.api.security import CSRF_COOKIE_NAME
from aer.config import load_settings
from aer.version import version
from aer.web.shell import flat_items, shell_for
from aer.web.templating import DISCLAIMER, STATIC_DIR, STYLES_DIR, TEMPLATES_DIR, templates
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

    def test_every_served_suffix_python_does_not_know_is_pinned(self):
        """The drift guard, and the reason the test below cannot be the only one.

        `mimetypes` seeds itself from the host — `/etc/mime.types` on Linux, the registry on
        Windows — and only its small hardcoded table is identical everywhere. So the
        assertion below passes on Linux whether or not this application pins anything, and
        the same code served `application/octet-stream` on Windows for as long as nobody ran
        the suite there.

        A fresh ``MimeTypes()`` is that hardcoded table and nothing else, which makes it the
        one baseline that means the same thing on every machine. Anything in the served tree
        it cannot name has to be pinned here instead.
        """
        builtin = mimetypes.MimeTypes()
        unknown = {
            path.suffix
            for path in STATIC_DIR.rglob("*")
            if path.is_file() and builtin.guess_type(path.name)[0] is None
        }

        unpinned = unknown - set(_LOCAL_MEDIA_TYPES)
        assert not unpinned, (
            f"{sorted(unpinned)} is served from static/ and Python does not know it on its "
            "own, so its type depends on the operating system. Add it to "
            "_LOCAL_MEDIA_TYPES in aer.api.app."
        )

    async def test_the_pinned_types_are_actually_registered(self, web_client):
        """Creating the application must apply the pins, not merely declare them."""
        for suffix, media_type in _LOCAL_MEDIA_TYPES.items():
            assert mimetypes.guess_type(f"x{suffix}")[0] == media_type, (
                f"{suffix} is in _LOCAL_MEDIA_TYPES but did not reach mimetypes; "
                "_register_local_media_types is not being called."
            )

    async def test_the_typeface_is_served_locally(self, web_client):
        # The preloaded face, which is the one whose absence shows on every page.
        response = await web_client.get("/static/fonts/source-sans-3-latin-wght-normal.woff2")

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
            shell=shell_for("/requests"),
            disclaimer=DISCLAIMER,
            # The menu carries the preference forms, so it names a token. Supplied rather
            # than made optional: a form that rendered without one under `StrictUndefined`
            # would be a control that silently does nothing, which is the failure the strict
            # undefined exists to catch.
            csrf_field="csrf_token",
            csrf_token="test-token",
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


class TestTheDarkPaletteIsOneThing:
    """The source has two dark blocks and the compiled sheet has two arms per `dark:` rule.

    Two of everything is the price of a tri-state in plain CSS: a custom property cannot be
    aliased across two selectors without an indirection nobody would thank us for. What can
    be had for free is the guarantee that the two never drift, which is what this is.
    """

    def _declarations(self, block: str) -> dict[str, str]:
        found = {}
        for line in block.splitlines():
            if ":" in line and line.strip().startswith("--"):
                name, _, value = line.strip().rstrip(";").partition(":")
                found[name.strip()] = value.strip()
        return found

    def _block(self, source: str, selector: str) -> str:
        """The declarations under a selector, found by the selector rather than by an offset.

        Matched by scanning forward from the selector to its opening brace and then to the
        first line that closes it, because the selector *list* grows: the explicit-dark block
        gained `[data-scheme="dark"]` when the navigation rail started sharing it, and a
        `split` on the exact old text found nothing and raised rather than reporting drift.
        """
        at = -1
        while True:
            at = source.index(selector, at + 1)
            body = source[source.index("{", at) + 1 :]
            # The custom-property blocks contain no nested braces, so the first one closes it.
            body = body[: body.index("}")]
            # Both selectors also appear in the `@custom-variant dark` rule at the top of the
            # file, which declares no tokens. Keep looking until the block that does.
            if "--aer-" in body:
                return body

    def test_the_two_dark_blocks_declare_the_same_values(self):
        source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")

        media = self._declarations(self._block(source, ':root:not([data-theme="light"])'))
        explicit = self._declarations(self._block(source, ':root[data-theme="dark"]'))

        assert media == explicit
        assert media, "no custom properties found; the parse has drifted"

    def test_the_fixed_dark_region_shares_that_block_rather_than_copying_it(self):
        """ADR 0088: a region that keeps one scheme's colours takes that scheme's accents
        entire, and does so **without duplicating tokens**.

        The navigation rail is `#102b35` on a light page and on a dark one. Before this,
        a focus ring inside it took the *light* accent and landed at 2.04:1 against the rail
        — a WCAG 2.2 SC 1.4.11 failure, very nearly invisible, and unmeasured because the
        rail's colours were in no token table.

        A third copy of the dark values is the obvious way to fix that and the wrong one:
        three copies drift where two only might. So the rail is a second selector on the
        block that already exists, and this is what stops somebody splitting it back out.
        """
        source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")

        at = source.index(':root[data-theme="dark"]', source.index("@theme"))
        selectors = source[at : source.index("{", at)]

        assert '[data-scheme="dark"]' in selectors, (
            "the rail no longer shares the explicit dark block. If it has its own copy of "
            "the dark values, there are now three places for them to drift apart."
        )

    def test_the_compiled_dark_variant_answers_an_explicit_choice(self):
        """Without the custom variant, `dark:` compiles to `prefers-color-scheme` alone.

        Half these templates predate the semantic tokens and still say `dark:bg-slate-900`,
        so a `dark:` that only watched the machine would leave those panels light when the
        operator chose dark — a control that works on some pages and not others.
        """
        compiled = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")

        assert ":root[data-theme=dark]" in compiled
        assert ":root:not([data-theme=light])" in compiled


class TestTheColourScheme:
    """Light, dark or the machine's own, remembered per operator.

    Dark mode existed from the first day of the design tokens and followed
    `prefers-color-scheme` only, so the sole way to change it was to change the operating
    system — which is not a control, and nobody found one because there was not one.
    """

    async def _token(self, web_client) -> str:
        page = await web_client.get("/")
        found = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert found, "no CSRF token in the shell"
        return found.group(1)

    async def test_every_page_carries_the_control(self, web_client):
        body = (await web_client.get("/")).text

        assert 'id="aer-theme-light"' in body
        assert 'id="aer-theme-dark"' in body
        assert 'id="aer-theme-system"' in body

    async def test_the_machines_choice_is_the_default(self, web_client):
        """No attribute at all, which is what leaves `prefers-color-scheme` in charge."""
        body = (await web_client.get("/")).text

        assert "data-theme=" not in body

    async def test_choosing_dark_is_remembered(self, web_client):
        token = await self._token(web_client)

        response = await web_client.post(
            "/_shell/theme",
            data={"theme": "dark", "next": "/requests", "csrf_token": token},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/requests"
        assert 'data-theme="dark"' in (await web_client.get("/")).text

    async def test_going_back_to_the_machines_choice_removes_the_attribute(self, web_client):
        """`system` is a choice somebody can make and go back to, not a missing cookie."""
        token = await self._token(web_client)
        await web_client.post(
            "/_shell/theme", data={"theme": "dark", "next": "/", "csrf_token": token}
        )

        await web_client.post(
            "/_shell/theme", data={"theme": "system", "next": "/", "csrf_token": token}
        )

        assert "data-theme=" not in (await web_client.get("/")).text

    async def test_a_value_that_is_not_a_theme_does_not_reach_the_attribute(self, web_client):
        """A hand-typed POST must not put an arbitrary string into `<html>`."""
        token = await self._token(web_client)

        await web_client.post(
            "/_shell/theme",
            data={"theme": '"><script>', "next": "/", "csrf_token": token},
            follow_redirects=False,
        )

        assert "<script>" not in (await web_client.get("/")).text

    async def test_it_refuses_to_forward_off_site(self, web_client):
        token = await self._token(web_client)

        response = await web_client.post(
            "/_shell/theme",
            data={"theme": "dark", "next": "https://example.invalid/", "csrf_token": token},
            follow_redirects=False,
        )

        assert response.headers["location"] == "/"


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


class TestATokenSurvivesAFragment:
    """A render must not replace the token every form on the page is already carrying.

    The bug this exists to prevent shipped and was invisible for a day. `render()` minted a
    token for any handler that did not supply one and set the cookie from it — correct for a
    page, wrong for a fragment. `GET /_shell/badges` is fetched by htmx on **every** page
    load, renders through the same door, and landed a beat after the page: the cookie became
    the fragment's, every form on the page was still carrying the page's, and the next
    submission was refused with "the anti-forgery token was missing or stale".

    **With scripting off nothing fetched the fragment and every form worked**, which is the
    wrong half of the product to have working, and is why the in-process suite could not see
    it — an HTTP client does not run htmx. Forty browser tests did.

    The fix is that a double-submit cookie is a secret for the *session*: a render adopts the
    token the request already carries and mints only when there is none.
    """

    async def test_a_fragment_render_keeps_the_token_the_page_issued(self, web_client):
        page = await web_client.get("/requests/new")
        issued = page.cookies.get(CSRF_COOKIE_NAME)
        assert issued, "the page issued no CSRF cookie"

        # Exactly what htmx does on load. The client's own jar carries the cookie forward,
        # which is what a browser does and what makes this the real sequence rather than a
        # reconstruction of it.
        await web_client.get("/_shell/badges")

        after = web_client.cookies.get(CSRF_COOKIE_NAME)
        assert after == issued, (
            "the badge fragment replaced the CSRF cookie. Every form already rendered on the "
            "page is carrying the previous value, so the operator's next submission is "
            "refused for a reason that has nothing to do with their submission."
        )

    async def test_the_form_and_the_cookie_agree_on_a_rendered_page(self, web_client):
        page = await web_client.get("/requests/new")
        in_cookie = page.cookies.get(CSRF_COOKIE_NAME)
        in_form = re.findall(r'name="csrf_token"\s+value="([^"]+)"', page.text)

        assert in_form, "the page rendered no CSRF field"
        assert set(in_form) == {in_cookie}, (
            "a form on the page carries a token the cookie does not hold. The double submit "
            "compares the two for equality, so this is a form that cannot be submitted."
        )
