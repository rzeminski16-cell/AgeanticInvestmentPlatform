"""The shell's own behaviour, driven by a real browser.

The badge count is the first thing in this codebase that a page renders empty and fills
afterwards, and the two ways it can be wrong are both invisible to an in-process test. A
swap that lands nowhere leaves a slot blank for ever and htmx says nothing about it; a swap
that replaces the live region rather than its contents fills the slot correctly and stops
announcing anything, to exactly the readers who need it announced. Both need htmx to have
actually run, so both are here.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.core.enums import JobStatus
from aer.db.models import Job, ResearchRequest, User
from aer.services.runs import awaiting_approval_count
from aer.web.shell import GUIDANCE_COOKIE
from tests.db_fixtures import run_async
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# The fragment is fetched on `load`, so the number arrives a round trip after the page.
BADGE_TIMEOUT_MS = 10_000


class StoppedRuns:
    """Runs parked at a gate in the live server's database.

    Built directly rather than by driving the workflow: what is under test is the sidebar,
    and a run's status is the only thing about it the count reads.
    """

    def __init__(self, database_url: str, *, count: int) -> None:
        self._database_url = database_url
        self.count = count
        run_async(self._create())

    async def _create(self) -> None:
        # A throwaway engine per operation, pooling nothing; see `RunFixture` in
        # `test_run_console` for why an asyncpg connection must not outlive its loop.
        engine = create_async_engine(self._database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                user = await session.scalar(select(User))
                assert user is not None, "the live_server fixture seeds one"

                for index in range(self.count):
                    request = ResearchRequest(
                        user_id=user.id,
                        company_name=f"Contoso {index}",
                        ticker=f"CTS{index}",
                        exchange="NASDAQ",
                        as_of_date=AS_OF_DATE,
                        point_in_time=True,
                        base_currency="USD",
                        investment_horizon_months=12,
                        max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
                    )
                    session.add(request)
                    await session.flush()
                    session.add(
                        Job(
                            work_order_id=request.id,
                            request_id=request.id,
                            workflow_version="test",
                            code_version="abc",
                            status=JobStatus.AWAITING_APPROVAL,
                            started_at=datetime.now(UTC),
                        )
                    )
                await session.commit()
        finally:
            await engine.dispose()


@pytest.fixture
def stopped_runs(live_server: str, database_url: str) -> StoppedRuns:
    return StoppedRuns(database_url, count=2)


class FinishedRun:
    """One commissioned request whose run succeeded.

    The caught-up state, as distinct from first-run: ``has_ever_commissioned`` reads the
    request, and a succeeded job leaves nothing waiting, broken or unstarted — an empty
    work list for a reader who has been here before. On a fresh database the same empty
    list is the *first-run* page, which says something else entirely.
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        run_async(self._create())

    async def _create(self) -> None:
        engine = create_async_engine(self._database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                user = await session.scalar(select(User))
                assert user is not None, "the live_server fixture seeds one"

                request = ResearchRequest(
                    user_id=user.id,
                    company_name="Contoso Finished",
                    ticker="CTSF",
                    exchange="NASDAQ",
                    as_of_date=AS_OF_DATE,
                    point_in_time=True,
                    base_currency="USD",
                    investment_horizon_months=12,
                    max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
                )
                session.add(request)
                await session.flush()
                finished = datetime.now(UTC)
                session.add(
                    Job(
                        work_order_id=request.id,
                        request_id=request.id,
                        workflow_version="test",
                        code_version="abc",
                        status=JobStatus.SUCCEEDED,
                        started_at=finished,
                        finished_at=finished,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()


@pytest.fixture
def finished_run(live_server: str, database_url: str) -> FinishedRun:
    return FinishedRun(database_url)


def _number(page: Page) -> Any:
    # The visible half. The slot also holds an `sr-only` sentence, so asking the slot
    # itself for its text would get the digit twice.
    return page.locator('#aer-badge-approvals > span[aria-hidden="true"]')


class TestTheCountArrivesAfterThePage:
    def test_the_slot_fills_with_the_number_of_stopped_runs(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/reports")

        expect(_number(page)).to_have_text(str(stopped_runs.count), timeout=BADGE_TIMEOUT_MS)

    def test_it_says_what_it_counted(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        # A bare numeral beside a word is read as "Requests 2" and means nothing.
        page.goto(f"{live_server}/reports")

        expect(page.locator("#aer-badge-approvals .sr-only")).to_have_text(
            f"{stopped_runs.count} runs waiting for your approval", timeout=BADGE_TIMEOUT_MS
        )

    def test_the_live_region_survives_the_swap(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        """ADR 0077's second gap, proved rather than asserted about a template.

        `hx-swap-oob="true"` — the default — would replace the whole element with the one
        the server sent, and the server's copy carries no `aria-live`. The slot would still
        show the right number and would never announce another one. This is the difference
        between the two swap modes, read off the live DOM after htmx has run.
        """
        page.goto(f"{live_server}/reports")
        expect(_number(page)).to_have_text(str(stopped_runs.count), timeout=BADGE_TIMEOUT_MS)

        expect(page.locator("#aer-badge-approvals")).to_have_attribute("aria-live", "polite")

    def test_a_run_nobody_stopped_leaves_the_slot_empty(self, page: Page, live_server: str) -> None:
        """No runs, no pill — not a zero.

        `empty:hidden` keys on the slot having no content at all, so this is also the check
        that the first paint renders nothing rather than whitespace.
        """
        page.goto(f"{live_server}/reports")
        page.wait_for_load_state("networkidle")

        expect(page.locator("#aer-badge-approvals")).to_be_hidden()
        assert page.locator("#aer-badge-approvals").inner_html() == ""


class TestTheOverviewScreen:
    """The second surface, and the first proof that guidance mode reaches a browser.

    Everything else about guidance has been asserted about a template or a cookie. What
    could not be asserted anywhere else is that `.aer-guide` is actually in the compiled
    stylesheet and that `body[data-guidance]` actually reveals it — a rule that failed to
    compile would leave the callouts hidden for ever, and every server-side test would go
    on passing.
    """

    def test_a_stopped_run_is_listed_with_somewhere_to_go(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/overview")

        rows = page.locator("[data-attention]")
        expect(rows).to_have_count(stopped_runs.count)
        expect(rows.first.get_by_role("link", name="Open the run")).to_be_visible()

    def test_a_new_operator_is_told_where_to_start(self, page: Page, live_server: str) -> None:
        # An empty database is the first-run state, deliberately distinct from caught-up
        # (tranche 5): the same empty work list means "nothing here works yet" to a new
        # operator and "caught up" to a returning one, and only the first needs telling
        # what to do.
        page.goto(f"{live_server}/overview")

        expect(page.get_by_text("Start with two things")).to_be_visible()
        expect(page.get_by_role("link", name="Commission research")).to_be_visible()

    def test_nothing_waiting_offers_the_next_thing_to_do(
        self, page: Page, live_server: str, finished_run: FinishedRun
    ) -> None:
        page.goto(f"{live_server}/overview")

        # The heading specifically: the verdict above it also says "Nothing is waiting
        # for you", and a bare text match resolves to both.
        expect(page.get_by_role("heading", name="Nothing is waiting", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="Commission research")).to_be_visible()

    def test_the_callouts_are_hidden_until_guidance_is_on(
        self, page: Page, live_server: str
    ) -> None:
        page.goto(f"{live_server}/overview")

        expect(page.locator(".aer-guide").first).to_be_hidden()

    def test_guidance_mode_reveals_them(self, browser: Browser, live_server: str) -> None:
        context = browser.new_context()
        try:
            # The cookie the toggle route sets. Set directly because no control renders it
            # yet — a form in the shell needs a CSRF token in the shell, which is its own
            # slice. What is under test is the CSS, and the CSS does not care who set it.
            context.add_cookies([{"name": GUIDANCE_COOKIE, "value": "on", "url": live_server}])
            page = context.new_page()
            page.goto(f"{live_server}/overview")

            expect(page.locator("body")).to_have_attribute("data-guidance", "on")
            expect(page.locator(".aer-guide").first).to_be_visible()
        finally:
            context.close()


class TestTheDrawer:
    """The chrome layer, which only a browser can say anything about.

    Focus, the Escape key and the background scroll lock are browser behaviour a server
    cannot send, so every assertion here needs a real one. The trap in particular has no
    server-side shadow: a page can look perfectly correct and still let Tab wander into a
    background the reader cannot see.
    """

    def test_previewing_opens_the_panel_with_the_row_it_came_from(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/overview")
        row = page.locator("[data-attention]").first
        title = row.locator("a").first.inner_text()

        row.get_by_role("link", name="Preview").click()

        expect(page.locator("#aer-drawer-title")).to_have_text(title)
        expect(page.locator('#aer-drawer [data-field="status"]')).to_have_text("AWAITING_APPROVAL")

    def test_focus_moves_into_the_panel_and_stays_there(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/overview")
        page.locator("[data-attention]").first.get_by_role("link", name="Preview").click()
        expect(page.locator("#aer-drawer-title")).not_to_be_empty()

        assert page.evaluate(
            "document.querySelector('#aer-drawer [role=dialog]').contains(document.activeElement)"
        )

        # Round the panel and back, never out of it. Four stops is more than the panel
        # holds, so a trap that leaked would have put focus on the page behind by now.
        for _ in range(4):
            page.keyboard.press("Tab")
            assert page.evaluate(
                "document.querySelector('#aer-drawer [role=dialog]')"
                ".contains(document.activeElement)"
            ), "Tab escaped the drawer"

    def test_shift_tab_wraps_backwards_too(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/overview")
        page.locator("[data-attention]").first.get_by_role("link", name="Preview").click()
        expect(page.locator("#aer-drawer-title")).not_to_be_empty()

        for _ in range(4):
            page.keyboard.press("Shift+Tab")
            assert page.evaluate(
                "document.querySelector('#aer-drawer [role=dialog]')"
                ".contains(document.activeElement)"
            ), "Shift+Tab escaped the drawer"

    def test_escape_closes_it_and_gives_focus_back(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        """Back to the row, not to the top of the document.

        A reader who opened the third row and pressed Escape has to work back down to it
        otherwise, every time, which is the difference between a panel and an interruption.
        """
        page.goto(f"{live_server}/overview")
        trigger = page.locator("[data-attention]").first.get_by_role("link", name="Preview")
        trigger.click()
        expect(page.locator("#aer-drawer-title")).not_to_be_empty()

        page.keyboard.press("Escape")

        expect(page.locator("#aer-drawer")).to_be_hidden()
        assert page.evaluate(
            "document.activeElement === document.querySelector('[data-attention] a[hx-get]')"
        )

    def test_the_close_button_closes_it(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/overview")
        page.locator("[data-attention]").first.get_by_role("link", name="Preview").click()
        expect(page.locator("#aer-drawer-title")).not_to_be_empty()

        page.get_by_role("button", name="Close").click()

        expect(page.locator("#aer-drawer")).to_be_hidden()

    def test_the_background_does_not_scroll_while_it_is_open(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        page.goto(f"{live_server}/overview")

        assert not page.evaluate("document.documentElement.classList.contains('overflow-hidden')")
        page.locator("[data-attention]").first.get_by_role("link", name="Preview").click()
        expect(page.locator("#aer-drawer-title")).not_to_be_empty()

        assert page.evaluate("document.documentElement.classList.contains('overflow-hidden')")
        page.keyboard.press("Escape")
        assert not page.evaluate("document.documentElement.classList.contains('overflow-hidden')")

    def test_closing_empties_it(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        # A panel that kept the last run's numbers would show them for the instant before
        # the next request landed, and a reader who opened the wrong row would see the
        # right-looking answer to the wrong question.
        page.goto(f"{live_server}/overview")
        page.locator("[data-attention]").first.get_by_role("link", name="Preview").click()
        expect(page.locator("#aer-drawer-title")).not_to_be_empty()

        page.keyboard.press("Escape")

        assert page.locator("#aer-drawer-body").inner_html().strip() == ""

    def test_with_scripting_off_the_same_click_is_a_page(
        self, browser: Browser, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        """ADR 0006's rule at the one place this slice could have broken it.

        The trigger is a link before it is anything else. htmx intercepts the click when it
        is there to; when it is not, the browser follows the `href` to the run console —
        the same destination, one page further away.
        """
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/overview")

            page.locator("[data-attention]").first.get_by_role("link", name="Preview").click()

            page.wait_for_url(re.compile(r"/runs/[0-9a-f-]{36}$"))
            expect(page.locator("#aer-drawer")).to_be_hidden()
        finally:
            context.close()


class TestTheTypeface:
    """The one check on the fonts that a file cannot make.

    Every other assertion about them is on bytes: the woff2 is on disk, its hash is the
    one recorded, the stylesheet asks for it by a relative path, the server hands it back
    as `font/woff2`. None of that says the browser used it — a `unicode-range` that matched
    nothing, a `format()` the parser rejected, or a chain that resolved to the fallback
    would leave all of it true and the page set in something else entirely.
    """

    def test_the_page_is_set_in_the_typeface_it_ships(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}/overview")
        page.evaluate("document.fonts.ready")

        assert page.evaluate("getComputedStyle(document.body).fontFamily").startswith(
            '"Source Sans 3 Variable"'
        )
        # `check` is true only once the face is available for that text, so this fails if
        # the woff2 never arrived — which is the state every file-level test above passes in.
        assert page.evaluate("document.fonts.check('1em \"Source Sans 3 Variable\"')")

    @pytest.mark.parametrize(
        ("weight", "family"),
        [
            ("600", "Barlow Semi Condensed"),
            ("700", "Barlow Semi Condensed"),
            ("400", "Source Sans 3 Variable"),
            ("600", "Source Sans 3 Variable"),
            ("450", "IBM Plex Mono Var"),
            ("550", "IBM Plex Mono Var"),
        ],
    )
    def test_every_weight_the_scale_uses_is_a_real_one(
        self, page: Page, live_server: str, weight: str, family: str
    ) -> None:
        """The browser's own answer to the question `test_fonts.py` asks of the stylesheet.

        Without the weight in a file, the browser synthesises one by smearing the nearest cut
        — heavier, blurrier, and on a page of small type the difference is the page looking
        cheap. 450 and 550 are the pair that decided which mono this repository vendors: no
        static IBM Plex Mono has them, so a static family here would pass every file-level
        check and fake two of the four weights the type scale is built on.

        `load` rather than `check`, because `check` answers "is this face already downloaded",
        and a face no template has used yet never is — which would make this test pass only
        after the tranche that renders it, exactly when it stops being the interesting check.
        `load` fetches the woff2 and resolves to the faces that matched, so an empty list is a
        404, a rejected `format()`, or a weight outside the declared axis.
        """
        page.goto(f"{live_server}/overview")

        matched = page.evaluate(
            f"""document.fonts.load('{weight} 1em "{family}"').then(faces => faces.length)"""
        )

        assert matched, (
            f"{family} at {weight} matched no vendored face, so the browser will synthesise "
            "it. Either the woff2 did not load or its axis does not reach that weight."
        )

    def test_nothing_was_fetched_from_anybody_else(self, page: Page, live_server: str) -> None:
        # The failure this whole arrangement exists to prevent, watched at the only level
        # that can see it. A page that reached a font host would still render perfectly.
        requested: list[str] = []
        page.on("request", lambda request: requested.append(request.url))

        page.goto(f"{live_server}/overview")
        page.evaluate("document.fonts.ready")

        strangers = [url for url in requested if not url.startswith(live_server)]
        assert not strangers, f"the page fetched from elsewhere: {strangers}"


# The width at which the index stops being a disclosure and becomes the persistent rail.
# 960px is the design system's workbench breakpoint and the one place this number lives in a
# test; the CSS gets it from `min-[60rem]:`.
RAIL = 960
NARROW = 380


class TestTheIndex:
    """One DOM, two layouts, and never two trees.

    A `<details>` at compact and rail widths; the same node is the persistent index from
    960px. Rendering it twice would put two `id="aer-badge-approvals"` nodes on every page,
    and an out-of-band swap targets an id — the first would fill and the second would sit
    there showing nothing for ever.

    **It ships `open` and script closes it when narrow** (decision B7). Everything below is
    browser behaviour a server cannot send, and the scripting-off cases are the ones no
    amount of reading the template can establish.
    """

    def test_at_the_rail_width_every_destination_is_already_on_the_screen(
        self, page: Page, live_server: str
    ) -> None:
        """The exit criterion: one action away, not two.

        A menu that has to be opened first costs a click on every navigation, on the screen
        that has the room not to.
        """
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server}")

        index = page.locator('nav[aria-label="Main"]')
        for label in ("Overview", "Requests", "Active run", "Reports", "Portfolio", "Settings"):
            expect(index.get_by_role("link", name=label, exact=True)).to_be_visible()

    def test_the_summary_is_gone_at_the_rail_width(self, page: Page, live_server: str) -> None:
        """Removed from the visual flow rather than left as a control that closes the rail —
        an index the operator can collapse into nothing on a 1280px screen is one they will
        collapse by accident and not find again."""
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server}")

        expect(page.locator("#aer-menu summary")).to_be_hidden()

    def test_it_is_shut_when_the_window_is_too_narrow_to_hold_it(
        self, page: Page, live_server: str
    ) -> None:
        page.set_viewport_size({"width": NARROW, "height": 800})
        page.goto(f"{live_server}")

        expect(page.locator('nav[aria-label="Main"]')).to_be_hidden()

    def test_opening_it_reveals_every_section(self, page: Page, live_server: str) -> None:
        page.set_viewport_size({"width": NARROW, "height": 800})
        page.goto(f"{live_server}")
        page.locator("#aer-menu summary").click()

        # By role, because "Overview" is both a section and the item inside it and a text
        # match resolves to both.
        for label in ("Overview", "Research", "Portfolio", "Platform"):
            expect(
                page.locator('nav[aria-label="Main"]').get_by_role("heading", name=label)
            ).to_be_visible()

    def test_it_does_not_reopen_itself_after_the_operator_shuts_it(
        self, page: Page, live_server: str
    ) -> None:
        """A control that undid the operator's own decision on the next resize would be a
        control that ignores the person using it."""
        page.set_viewport_size({"width": NARROW, "height": 800})
        page.goto(f"{live_server}")
        page.locator("#aer-menu summary").click()
        expect(page.locator('nav[aria-label="Main"]')).to_be_visible()

        page.locator("#aer-menu summary").click()
        page.set_viewport_size({"width": 1100, "height": 800})

        expect(page.locator("#aer-menu")).not_to_have_attribute("open", "")

    def test_the_page_you_are_on_is_marked(self, page: Page, live_server: str) -> None:
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server}/reports")

        current = page.locator('nav[aria-label="Main"] a[aria-current="page"]')
        expect(current).to_have_count(1)
        expect(current).to_have_text("Reports")

    def test_where_you_are_is_written_down_where_the_index_is_closed(
        self, page: Page, live_server: str
    ) -> None:
        """At 320px with scripting on the index is shut, so its `aria-current` item is not on
        the screen — and the page itself becomes the only thing saying which page it is. That
        means opening the menu to find out where you already are."""
        page.set_viewport_size({"width": NARROW, "height": 800})
        page.goto(f"{live_server}/reports")

        expect(page.get_by_text("Research · Reports")).to_be_visible()

    def test_it_does_not_push_the_content_sideways_at_the_rail_width(
        self, page: Page, live_server: str
    ) -> None:
        """At the rail width the index is a column beside the content rather than over it, so
        the working area starts after it and nothing is covered."""
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server}")

        rail = page.locator("#aer-nav").bounding_box()
        main = page.locator("main").bounding_box()

        assert rail is not None
        assert main is not None
        assert main["x"] >= rail["x"] + rail["width"] - 1

    def test_one_badge_slot_and_not_two(self, page: Page, live_server: str) -> None:
        """The reason the index is one element rather than a pair.

        Two copies would mean two nodes with one id, and an out-of-band swap targets an id:
        the first would fill and the second would sit there showing nothing for ever.
        """
        page.goto(f"{live_server}")

        expect(page.locator("#aer-badge-approvals")).to_have_count(1)

    @pytest.mark.parametrize("width", [320, 1440])
    def test_with_scripting_off_every_destination_is_reachable(
        self, browser: Browser, live_server: str, width: int
    ) -> None:
        """The exit criterion, at both ends. **It fails open by design.**

        Shipping the disclosure closed would need author CSS to reveal a *closed*
        `<details>`, which is not reliably specified across engines — Chromium's
        `::details-content` carries `content-visibility: hidden`, which a `display` override
        on the child does not defeat. Shipping it open needs no reveal at all, and when the
        script never runs every link stays on the screen at every width. The cost is a panel
        above the content at 320px; the cost of failing the other way is a menu button that
        does not open.
        """
        context = browser.new_context(
            java_script_enabled=False, viewport={"width": width, "height": 900}
        )
        try:
            page = context.new_page()
            page.goto(f"{live_server}")

            index = page.locator('nav[aria-label="Main"]')
            for label in ("Overview", "Requests", "Reports", "Portfolio", "Settings"):
                expect(index.get_by_role("link", name=label, exact=True)).to_be_visible()
        finally:
            context.close()

    @pytest.mark.parametrize("width", [320, 1440])
    def test_with_scripting_off_both_preference_forms_still_submit(
        self, browser: Browser, live_server: str, width: int
    ) -> None:
        """Both are server state under ADR 0077, and a preference that needs JavaScript is a
        preference the operator cannot set on the page load where the script failed."""
        context = browser.new_context(
            java_script_enabled=False, viewport={"width": width, "height": 900}
        )
        try:
            page = context.new_page()
            page.goto(f"{live_server}")

            page.locator("#aer-theme-dark").click()
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")

            page.locator("#aer-guidance-toggle").click()
            expect(page.locator("body")).to_have_attribute("data-guidance", "on")
        finally:
            context.close()

    def test_the_skip_link_is_the_first_thing_focus_reaches(
        self, page: Page, live_server: str
    ) -> None:
        """Without it, reaching the content past a nine-item index costs a keyboard user nine
        tab stops on every single navigation."""
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server}")
        page.keyboard.press("Tab")

        focused = page.evaluate("() => document.activeElement.getAttribute('href')")

        assert focused == "#aer-main"

    def test_a_focused_link_in_the_index_is_visible_against_it(
        self, page: Page, live_server: str
    ) -> None:
        """ADR 0088, in the place it was written for. The index keeps the dark scheme's
        colours whatever the page is doing, and the *light* focus ring measures 2.04:1 on it —
        a WCAG 2.2 SC 1.4.11 failure, in the default theme, on the first link a keyboard user
        reaches."""
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server}")
        page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
        page.focus('nav[aria-label="Main"] a')

        ring = page.evaluate(
            "sel => getComputedStyle(document.querySelector(sel)).outlineColor",
            'nav[aria-label="Main"] a',
        )

        # The dark accent, #b5ecf0, at 11.43:1 on the rail. Not the light one at 2.04:1.
        assert ring == "rgb(181, 236, 240)", ring


class TestTheLauncher:
    def test_the_front_page_leads_with_every_tool(self, page: Page, live_server: str) -> None:
        page.goto(f"{live_server}")

        expect(page.locator("[data-tool]")).to_have_count(9)
        expect(page.locator('[data-tool="research"][data-status="Working"]')).to_be_visible()
        # Portfolio shipped, so the launcher's claim about it changed. That the front page
        # is where a status change becomes visible is the whole point of the row being data.
        expect(page.locator('[data-tool="portfolio"][data-status="Working"]')).to_be_visible()

    def test_a_planned_tool_is_reachable_and_says_what_it_waits_for(
        self, page: Page, live_server: str
    ) -> None:
        page.goto(f"{live_server}")

        page.locator('[data-tool="watchlist"] [data-field="open"]').click()

        page.wait_for_url("**/watchlist")
        expect(page.get_by_text("What it needs first")).to_be_visible()

    def test_the_common_action_is_one_click_from_the_front_door(
        self, page: Page, live_server: str
    ) -> None:
        """The old landing page had this button and the launcher took it away.

        A browser test noticed, which is what that test is for. It is back as a field on
        the working tool's row rather than as a line in this template, so the second tool's
        action appears when its row grows one.
        """
        page.goto(f"{live_server}")

        page.locator('[data-tool="research"] [data-field="action"]').click()

        page.wait_for_url("**/requests/new")

    def test_a_tool_that_cannot_be_used_offers_no_action(
        self, page: Page, live_server: str
    ) -> None:
        # A button on a tool that does not exist is a button that goes nowhere, which is
        # the failure the placeholder pages avoid rather than relocate.
        page.goto(f"{live_server}")

        expect(page.locator('[data-tool="watchlist"] [data-field="action"]')).to_have_count(0)

    def test_the_working_tool_leads_to_its_own_pages(self, page: Page, live_server: str) -> None:
        # The one card that is not a placeholder. A launcher whose only working entry led
        # to another placeholder would be a menu of nothing.
        page.goto(f"{live_server}")

        page.locator('[data-tool="research"] [data-field="open"]').click()

        page.wait_for_url("**/requests")
        expect(page.get_by_role("heading", name="Requests").first).to_be_visible()


class TestWithScriptingOff:
    def test_the_nav_renders_and_the_slot_stays_empty(
        self, browser: Browser, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        """ADR 0006's rule, at the one place this slice could have broken it.

        The count is the tool's, so losing it costs a hint. What must not happen is an
        empty pill painted beside every nav item on a browser that never ran the fetch.
        """
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/reports")

            expect(page.get_by_role("link", name="Requests")).to_be_visible()
            expect(page.locator("#aer-badge-approvals")).to_be_hidden()
        finally:
            context.close()


class TestTheFragmentIsNotADestination:
    def test_it_is_reachable_but_holds_no_page(
        self, page: Page, live_server: str, stopped_runs: StoppedRuns
    ) -> None:
        # Named in `UNLISTED` rather than in the nav, and this is what that excuse means:
        # opening it yields spans, not a page with a way back.
        page.goto(f"{live_server}/_shell/badges")

        assert "aer-badge-approvals" in page.content()
        assert page.locator("header nav").count() == 0


def test_the_badge_is_scoped_to_the_operator(live_server: str, database_url: str) -> None:
    """A second operator's stopped runs are not this one's number.

    Asserted against the counter rather than the browser: the live server seeds one user
    and signs every request in as them, so a browser has no way to be somebody else. What
    a browser could not show, the query can.
    """
    StoppedRuns(database_url, count=1)

    async def counted() -> tuple[int, int]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                user = await session.scalar(select(User))
                assert user is not None
                return (
                    await awaiting_approval_count(session, user_id=user.id),
                    await awaiting_approval_count(session, user_id=uuid.uuid4()),
                )
        finally:
            await engine.dispose()

    mine, theirs = run_async(counted())

    assert mine == 1
    assert theirs == 0
