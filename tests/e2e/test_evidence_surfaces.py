"""The evidence surfaces, driven by a real browser.

What only a browser can prove: that the two clicks are actually clicks. The in-process
suite walks the ``href`` attributes, which catches a broken link but would still pass if the
link were invisible, covered, or rendered outside the page. Here the drill-down is reached
the way a reader reaches it — by clicking what they can see.

The other thing a browser proves is the "works with JavaScript off" claim in its strongest
form: with scripting disabled entirely, the table and the excerpt are still on the page.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.config import load_settings
from aer.storage.local import LocalArtefactStore
from tests.db_fixtures import run_async
from tests.provenance_fixtures import SUPPORTED_SENTENCE, build_evidence

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# The user the live server seeds. `get_current_user` returns the earliest-created user,
# so evidence built under a *second* account would be invisible to every page and every
# assertion here would fail as a 404 rather than as the thing it was checking.
EMAIL = "e2e@example.invalid"


class EvidenceFixture:
    """A run with real evidence in the live server's database."""

    def __init__(self, database_url: str) -> None:
        self._settings = load_settings()
        self._database_url = database_url
        self._store = LocalArtefactStore(
            self._settings.artefact_root, max_bytes=self._settings.max_artefact_bytes
        )
        built = run_async(self._create())
        self.job_id: uuid.UUID = built["job_id"]
        self.supported_claim_id: uuid.UUID = built["supported_claim_id"]
        self.quarantine_reason: str = built["quarantine_reason"]
        self.filing_sha256: str = built["filing_sha256"]

    async def _create(self) -> dict[str, Any]:
        # A throwaway engine per operation, pooling nothing; see `RunFixture` in
        # `test_run_console` for why an asyncpg connection must not outlive its loop.
        engine = create_async_engine(self._database_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                built = await build_evidence(session, self._store, self._settings, email=EMAIL)
                await session.commit()
                return {
                    "job_id": built["job"].id,
                    "supported_claim_id": built["supported_claim"].id,
                    "quarantine_reason": built["quarantine_reason"],
                    "filing_sha256": built["filing_sha256"],
                }
        finally:
            await engine.dispose()


@pytest.fixture
def evidence(live_server: str, database_url: str) -> EvidenceFixture:
    return EvidenceFixture(database_url)


class TestTheDrillDownIsReachableByClicking:
    def test_two_clicks_from_the_console_reach_the_excerpt(
        self, page: Page, live_server: str, evidence: EvidenceFixture
    ) -> None:
        """The phase's acceptance criterion, performed rather than inspected."""
        page.goto(f"{live_server}/runs/{evidence.job_id}")

        page.click("#claims-link")
        page.wait_for_url(f"**/runs/{evidence.job_id}/claims")

        page.get_by_text("Total revenue was $198,270 million in fiscal 2022.").click()

        expect(page.locator("#claim-text")).to_be_visible()
        expect(page.locator('[data-field="excerpt"]').first).to_contain_text(SUPPORTED_SENTENCE)

    def test_the_sources_table_is_one_click_away(
        self, page: Page, live_server: str, evidence: EvidenceFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{evidence.job_id}")

        page.click("#sources-link")

        expect(page.locator("#sources-table")).to_be_visible()
        expect(page.locator("#source-count")).to_have_text("2")


class TestWhatTheReaderIsTold:
    def test_a_quarantined_source_is_visible_with_its_reason(
        self, page: Page, live_server: str, evidence: EvidenceFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{evidence.job_id}/sources")

        expect(page.locator("#quarantined-count")).to_have_text("1")
        expect(page.locator('[data-field="quarantine-reason"]')).to_contain_text(
            evidence.quarantine_reason
        )

    def test_an_unverified_citation_is_visibly_unverified(
        self, page: Page, live_server: str, evidence: EvidenceFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{evidence.job_id}/claims")
        page.get_by_text("Total revenue was $250,000 million in fiscal 2022.").click()

        expect(page.locator("#claim-unsupported")).to_be_visible()
        expect(page.locator('[data-citation][data-state="unverified"]')).to_be_visible()
        expect(page.locator('[data-field="verdict"]').first).to_contain_text("Not confirmed")

    def test_the_tier_badge_carries_its_own_text(
        self, page: Page, live_server: str, evidence: EvidenceFixture
    ) -> None:
        # Colour alone would leave a reader who cannot distinguish it unable to tell a
        # regulator from an issuer's own page.
        page.goto(f"{live_server}/runs/{evidence.job_id}/sources")

        expect(page.locator('[data-field="tier"]').first).to_have_text("T1_REGULATORY")


class TestFromAFigureToTheBytes:
    """Task 52's acceptance walk, performed: a figure in the document to its digest.

    The in-process suite follows ``href`` attributes, which would still pass if a marker
    were invisible or a link were covered. Here a reader clicks what they can see.
    """

    def test_a_marker_in_the_preview_reaches_the_artefact_hash(
        self, page: Page, live_server: str, evidence: EvidenceFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{evidence.job_id}/preview")

        # The figure's marker: click it, land on the note it names.
        page.locator("sup.fn-ref a").first.click()
        expect(page.locator("#fn-1")).to_be_visible()

        # The note's evidence link: the drill-down, carrying the digest in full. Both
        # claims resting on this document appear, so the verified excerpt is located by
        # its own text rather than by position among them.
        page.locator("#fn-1 a.drill").click()
        page.wait_for_url(f"**/runs/{evidence.job_id}/footnotes/1")
        expect(page.locator("#source-sha256")).to_have_text(evidence.filing_sha256)
        expect(
            page.locator('[data-citation][data-state="verified"] [data-field="excerpt"]')
        ).to_contain_text(SUPPORTED_SENTENCE)

    def test_the_hover_preview_needs_no_script(
        self, browser: Browser, live_server: str, evidence: EvidenceFixture
    ) -> None:
        """The preview is a ``title`` attribute, so it survives scripting being off."""
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/runs/{evidence.job_id}/preview")

            marker = page.locator("sup.fn-ref a").first
            title = marker.get_attribute("title")
            assert title is not None
            assert "Follow the note" in title

            marker.click()
            expect(page.locator("#fn-1")).to_be_visible()
            page.locator("#fn-1 a.drill").click()
            expect(page.locator("#source-sha256")).to_have_text(evidence.filing_sha256)
        finally:
            context.close()


class TestWithScriptingOff:
    def test_the_sources_table_renders(
        self, browser: Browser, live_server: str, evidence: EvidenceFixture
    ) -> None:
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/runs/{evidence.job_id}/sources")

            expect(page.locator("#sources-table")).to_be_visible()
            expect(page.locator("#source-count")).to_have_text("2")
        finally:
            context.close()

    def test_the_excerpt_renders(
        self, browser: Browser, live_server: str, evidence: EvidenceFixture
    ) -> None:
        context = browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(f"{live_server}/claims/{evidence.supported_claim_id}")

            expect(page.locator('[data-field="excerpt"]').first).to_contain_text(SUPPORTED_SENTENCE)
            expect(page.locator('[data-citation][data-state="verified"]')).to_be_visible()
        finally:
            context.close()
