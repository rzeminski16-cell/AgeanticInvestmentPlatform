"""Authoring a skill in a browser: write, validate, save, enable, dry-run.

The §2.12 loop, driven the way an operator drives it. Everything here is also covered
in-process by ``tests/test_skills_surface.py``; what a browser adds is proof that the
*page* works — that the form posts what it displays, that the composed-policy panel and
the clamp receipts are in the served HTML rather than assembled by a script, and that the
dry-run button reaches a rendered section.

**No JavaScript is exercised deliberately.** Every assertion below would hold with
scripting disabled, which is the property the editor is built for: a page whose errors
need a script silently accepts anything the moment the script fails to load, and this one
commissions spending.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aer.agents.custom_section import CustomSectionDraft
from aer.db.models import User
from aer.providers.fake import FakeProvider
from aer.storage.local import LocalArtefactStore
from tests.db_fixtures import run_async
from tests.test_skills_surface import SKILL_SOURCE, _draft_from, _seed_finished_run

pytestmark = pytest.mark.e2e

# A file asking for less than the floor allows, so the editor has clamps to show.
GREEDY = SKILL_SOURCE.replace("min_sources: 1", "min_sources: 0").replace(
    "token_budget: 12000", "token_budget: 900000"
)


@pytest.fixture
def scripted_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    """Replace the provider the web process would build for a dry run.

    Patched at :func:`aer.api.deps.get_provider`'s own factory rather than through a
    setting, because a configuration switch that swapped in a fake would exist in
    production too — and a deployment that ran happily and produced nothing real is the
    one failure `build_provider` refuses by design.
    """
    holder: dict[str, FakeProvider] = {}

    def answer(schema: type) -> Any:
        # A subclass, not the class: the call narrows `content` to the pinned contract.
        assert issubclass(schema, CustomSectionDraft), f"unexpected schema {schema.__name__}"
        return _draft_from(holder["provider"].calls[-1]["messages"][0]["content"])

    provider = FakeProvider(answer)
    holder["provider"] = provider
    monkeypatch.setattr("aer.api.deps.build_provider", lambda _settings: provider)
    return provider


@pytest.fixture
def seeded_run(live_server: str, database_url: str, tmp_path: Any) -> str:
    """A finished run in the live server's database, for the dry run to borrow evidence from.

    Seeded after ``live_server`` has reset and seeded the user, and attached to that user
    so the dry run's ownership check passes.
    """

    async def seed() -> None:
        engine = create_async_engine(database_url)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                user = await session.scalar(select(User).limit(1))
                assert user is not None, "the live server seeds one user"
                store = LocalArtefactStore(tmp_path / "artefacts", max_bytes=10_000_000)
                scene = await _seed_finished_run(session, store=store, email=user.email)
                # The seeder makes its own author; the run must belong to the browser's
                # user, who is the only one the pages authenticate as.
                scene["request"].user_id = user.id
                await session.commit()
        finally:
            await engine.dispose()

    run_async(seed())
    return live_server


class TestAuthoringASkillInABrowser:
    def test_the_library_offers_a_way_in(self, live_server: str, page: Page) -> None:
        page.goto(f"{live_server}/skills")

        expect(page.locator("#no-skills")).to_be_visible()
        expect(page.locator("#new-skill")).to_be_visible()

    def test_validating_shows_the_clamps_before_anything_is_saved(
        self, live_server: str, page: Page
    ) -> None:
        page.goto(f"{live_server}/skills/new")
        page.fill("#source", GREEDY)
        page.click("#validate")

        expect(page.locator("#clamps")).to_be_visible()
        expect(page.locator("#clamps")).to_contain_text("token_budget")
        # The effective policy, not the request: the file asked for 0 sources and a
        # 900,000-token budget.
        expect(page.locator("#min-sources")).to_have_text("1")
        expect(page.locator("#token-budget")).to_have_text("12000")

        page.goto(f"{live_server}/skills")
        expect(page.locator("#no-skills")).to_be_visible()

    def test_an_invalid_file_shows_its_errors_against_their_lines(
        self, live_server: str, page: Page
    ) -> None:
        page.goto(f"{live_server}/skills/new")
        page.fill("#source", SKILL_SOURCE.replace("max_tier: 4", "max_tier: 9"))
        page.click("#validate")

        expect(page.locator("#issues")).to_be_visible()
        expect(page.locator("#issues")).to_contain_text("max_tier")
        expect(page.locator("#issues")).to_contain_text("line ")

    def test_write_save_enable_and_dry_run(
        self, seeded_run: str, page: Page, scripted_provider: FakeProvider
    ) -> None:
        """The whole §2.12 loop, in a browser, without a line of JavaScript."""
        page.goto(f"{seeded_run}/skills/new")
        page.fill("#source", SKILL_SOURCE)
        page.click("#save")

        # Saved, and the editor is open on it with the composed policy shown.
        expect(page).to_have_url(re.compile(r"/skills/moat_durability$"))
        expect(page.locator("#composed")).to_be_visible()

        # Enabling is its own decision, made from the library.
        page.goto(f"{seeded_run}/skills")
        expect(page.locator("#skill-moat_durability")).to_contain_text("disabled")
        page.click("#toggle-moat_durability")
        expect(page.locator("#skill-moat_durability")).to_contain_text("enabled")

        # And the dry run renders the section against a finished run's evidence.
        page.goto(f"{seeded_run}/skills/moat_durability")
        page.click("#run-dry-run")

        expect(page.locator("#dry-run-result")).to_be_visible()
        expect(page.locator("#dry-run-status")).to_have_text("generated")
        expect(page.locator("#dry-run-markdown")).to_contain_text("198,270")
        assert scripted_provider.calls, "the dry run must have made a real call"
