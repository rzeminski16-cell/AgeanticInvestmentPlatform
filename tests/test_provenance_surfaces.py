"""The surfaces that make the evidence chain readable: sources, claims, and the drill-down.

The phase's user-visible outcome, so the tests that matter are the ones about what a reader
is *told* rather than about what the code computed.

* **A quarantined source is visibly quarantined, with its reason.** The failure mode is a
  page that filters refused sources out and therefore reads as though nothing was doubtful.
* **An unverified citation is visibly unverified.** The failure mode is a drill-down that
  shows the excerpt and lets a reader assume it was checked.
* **Both pages work with JavaScript off**, asserted by parsing the served HTML rather than
  by driving a browser — a page whose content arrives by fetch renders empty here.
* **Two clicks.** From the rendered report, following links only, a reader reaches the exact
  words behind a claim. Asserted by walking the hrefs, so a broken link fails it.

Evidence comes from ``tests.provenance_fixtures``, which builds it through the real services
and runs the real verifier. A hand-written verification flag would make all of this pass
while proving nothing.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.enums import ClaimKind, SourceTier
from aer.db.models import Extraction
from aer.services import provenance
from aer.services.citations import override_citation
from aer.storage.local import LocalArtefactStore
from tests.api_fixtures import build_app, client_for
from tests.provenance_fixtures import (
    FABRICATED,
    SUPPORTED_SENTENCE,
    build_evidence,
    committed_evidence,
)

EMAIL = "provenance@example.invalid"


@pytest.fixture
def settings(api_settings: Settings) -> Settings:
    return api_settings


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def evidence(
    db_session: AsyncSession, store: LocalArtefactStore, settings: Settings
) -> dict[str, Any]:
    return await build_evidence(db_session, store, settings, email=EMAIL)


@pytest.fixture
async def clean_slate(db_engine: Any) -> AsyncIterator[None]:
    """Empty everything these tests write, before **and** after each one.

    The application commits for real, so its writes outlive the test that made them.
    Before, so a test's result never depends on which tests ran first. After, because the
    evidence carries a company keyed by its listing and its CIK — and the last test in
    this file would otherwise leave that row for the next module that researches the same
    company to collide with.
    """
    await _truncate_evidence(db_engine)
    yield
    await _truncate_evidence(db_engine)


async def _truncate_evidence(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text(
                "TRUNCATE citations, claims, extractions, report_sections, calculations, "
                "assumptions, financial_facts, companies, source_documents, artefacts, "
                "jobs, research_requests, audit_events, users RESTART IDENTITY CASCADE"
            )
        )
        # The evidence carries a skill-authored section, and skills outlive the run
        # tables — `section_definitions` rows come from migrations, so the table is not
        # truncated. A skill left behind would show up as a second row in every later
        # module that counts them.
        await connection.execute(text("DELETE FROM section_definitions WHERE origin = 'skill'"))
        await connection.execute(text("DELETE FROM skill_versions"))
        await connection.execute(text("DELETE FROM skills"))


@pytest.fixture
async def served(
    clean_slate: None,
    db_engine: Any,
    fake_redis: Any,
    store: LocalArtefactStore,
    settings: Settings,
) -> Any:
    """A client over an application that can see committed evidence."""
    built = await committed_evidence(db_engine, store, settings, email=EMAIL)
    async for client in client_for(build_app(settings, engine=db_engine, redis=fake_redis)):
        yield client, built


# -- The read model ----------------------------------------------------------------------------


@pytest.mark.integration
class TestSourcesForARun:
    async def test_every_acquired_document_is_listed(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        sources = await provenance.sources_for_run(db_session, evidence["job"].id)

        assert len(sources) == 2

    async def test_a_quarantined_source_is_not_filtered_out(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        # The whole point of the table. "What did this run refuse to use?" is unanswerable
        # from a view that only shows what it used.
        sources = await provenance.sources_for_run(db_session, evidence["job"].id)

        refused = [source for source in sources if source.quarantined]
        assert len(refused) == 1
        assert refused[0].quarantine_reason
        assert not refused[0].is_admissible

    async def test_the_more_authoritative_source_comes_first(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        sources = await provenance.sources_for_run(db_session, evidence["job"].id)

        assert sources[0].source_tier is SourceTier.T1_REGULATORY

    async def test_the_artefact_digest_is_carried(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        # What makes the table an audit trail rather than a bibliography: a reader can take
        # the digest and confirm the bytes are the bytes.
        sources = await provenance.sources_for_run(db_session, evidence["job"].id)

        assert all(len(source.sha256) == 64 for source in sources)
        assert all(source.short_hash == source.sha256[:12] for source in sources)

    async def test_the_licence_note_is_carried(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        sources = await provenance.sources_for_run(db_session, evidence["job"].id)

        assert all(source.licence_note for source in sources)

    async def test_an_overridden_quarantine_is_admissible_and_still_says_it_was_quarantined(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        document = evidence["quarantined"]
        document.admissibility_override_by_id = evidence["user"].id
        document.admissibility_override_reason = "Dated from the RNS index by hand."
        document.admissibility_overridden_at = evidence["job"].started_at
        await db_session.flush()

        sources = await provenance.sources_for_run(db_session, evidence["job"].id)
        overridden = next(source for source in sources if source.id == document.id)

        assert overridden.is_admissible
        # Both facts survive. A page saying only "admissible" would erase the judgement.
        assert overridden.quarantined
        assert overridden.override_reason


@pytest.mark.integration
class TestTheDrillDown:
    async def test_a_verified_citation_reports_the_excerpt_and_the_verdict(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        view = await provenance.claim_view(db_session, evidence["supported_claim"].id)

        assert view is not None
        citation = view.citations[0]
        assert citation.state == "verified"
        assert citation.excerpt.excerpt == SUPPORTED_SENTENCE
        assert citation.verification_method
        assert view.is_supported

    async def test_an_unverifiable_citation_says_so(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        view = await provenance.claim_view(db_session, evidence["unsupported_claim"].id)

        assert view is not None
        citation = view.citations[0]
        assert citation.state == "unverified"
        assert not citation.is_admissible
        assert citation.verification_error
        assert not view.is_supported

    async def test_the_failed_excerpt_is_still_shown(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        # Hiding it would leave a reader unable to see *what* could not be found, which is
        # the only way to tell a reflowed paragraph from a fabrication.
        view = await provenance.claim_view(db_session, evidence["unsupported_claim"].id)

        assert view is not None
        assert view.citations[0].excerpt.excerpt == FABRICATED

    async def test_an_override_is_a_third_state_rather_than_a_second_verified(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        await override_citation(
            db_session,
            citation=evidence["bad_citation"],
            actor=evidence["user"],
            reason="The filing was reflowed; checked by hand against the PDF.",
        )

        view = await provenance.claim_view(db_session, evidence["unsupported_claim"].id)

        assert view is not None
        citation = view.citations[0]
        assert citation.state == "overridden"
        assert citation.is_admissible
        # Still not verified. Collapsing the two would lose the distinction a research
        # report most needs to make.
        assert not citation.verified

    async def test_an_unknown_claim_is_absent_rather_than_an_error(
        self, db_session: AsyncSession
    ) -> None:
        assert await provenance.claim_view(db_session, uuid.uuid4()) is None

    async def test_a_claim_carries_the_source_behind_its_citation(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        view = await provenance.claim_view(db_session, evidence["supported_claim"].id)

        assert view is not None
        source = view.citations[0].source
        assert source.id == evidence["filing"].id
        assert source.source_tier is SourceTier.T1_REGULATORY


@pytest.mark.integration
class TestClaimsForARun:
    async def test_claims_are_listed_with_their_support(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        claims = await provenance.claims_for_run(db_session, evidence["job"].id)

        assert len(claims) == 2
        assert sum(1 for claim in claims if not claim.is_supported) == 1

    async def test_an_opinion_is_not_marked_unsupported_for_having_no_citation(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        # A badge that fires on every opinion is a badge readers learn to ignore, which
        # would cost the numeric claims their warning.
        from aer.services.citations import record_claim  # noqa: PLC0415 -- one call site

        await record_claim(
            db_session,
            section=evidence["section"],
            kind=ClaimKind.OPINION,
            text="The margin looks durable.",
        )

        claims = await provenance.claims_for_run(db_session, evidence["job"].id)
        opinion = next(claim for claim in claims if claim.kind is ClaimKind.OPINION)

        assert opinion.citation_count == 0
        assert opinion.is_supported


@pytest.mark.integration
class TestARealRunFillsTheTable:
    """The bug this suite found: a run whose sources are attributed to no run.

    ``source_documents.job_id`` is nullable, because a document can be supplied by hand or
    gathered while planning. The acquire step was not setting it, so every document a run
    fetched was attributable to a *request* and to no particular run — and the sources
    table, which asks "what did this run acquire?", was empty for every real run while every
    other test passed.
    """

    async def test_the_acquire_step_attributes_its_documents_to_the_run(
        self, db_session: AsyncSession, evidence: dict[str, Any]
    ) -> None:
        from aer.db.models import SourceDocument  # noqa: PLC0415 -- one call site

        orphans = await db_session.scalars(
            select(SourceDocument).where(
                SourceDocument.request_id == evidence["request"].id,
                SourceDocument.job_id.is_(None),
            )
        )

        assert list(orphans) == []


# -- The JSON API --------------------------------------------------------------------------------


@pytest.mark.integration
class TestTheSourcesEndpoint:
    async def test_it_returns_the_provenance_record(self, served: Any) -> None:
        client, built = served

        body = (await client.get(f"/api/runs/{built['job'].id}/sources")).json()

        assert len(body["sources"]) == 2
        first = body["sources"][0]
        assert first["source_tier"] == SourceTier.T1_REGULATORY.value
        assert len(first["sha256"]) == 64
        assert first["licence_note"]
        assert first["retrieved_at"]

    async def test_it_counts_the_refused_separately_from_the_still_refused(
        self, served: Any
    ) -> None:
        client, built = served

        body = (await client.get(f"/api/runs/{built['job'].id}/sources")).json()

        assert body["quarantined"] == 1
        assert body["inadmissible"] == 1

    async def test_a_quarantined_source_carries_its_reason(self, served: Any) -> None:
        client, built = served

        body = (await client.get(f"/api/runs/{built['job'].id}/sources")).json()
        refused = [source for source in body["sources"] if source["quarantined"]]

        assert len(refused) == 1
        assert refused[0]["quarantine_reason"]
        assert refused[0]["admissible"] is False

    async def test_an_unknown_run_is_a_404(self, served: Any) -> None:
        client, _ = served

        assert (await client.get(f"/api/runs/{uuid.uuid4()}/sources")).status_code == 404


@pytest.mark.integration
class TestTheClaimEndpoint:
    async def test_it_returns_the_excerpt_and_the_verdict(self, served: Any) -> None:
        client, built = served

        body = (await client.get(f"/api/claims/{built['supported_claim'].id}")).json()

        assert body["supported"] is True
        citation = body["citations"][0]
        assert citation["state"] == "verified"
        assert citation["excerpt"]["excerpt"] == SUPPORTED_SENTENCE
        assert citation["source"]["sha256"]

    async def test_it_does_not_hide_a_failure(self, served: Any) -> None:
        client, built = served

        body = (await client.get(f"/api/claims/{built['unsupported_claim'].id}")).json()

        assert body["supported"] is False
        citation = body["citations"][0]
        assert citation["state"] == "unverified"
        assert citation["verification_error"]
        # And the excerpt that could not be found is carried too. Omitting it on failure
        # would leave a caller unable to tell a reflowed paragraph from a fabrication,
        # which is the only judgement worth making about a failed citation.
        assert citation["excerpt"]["excerpt"] == FABRICATED
        assert citation["excerpt"]["locator"]

    async def test_an_unknown_claim_is_a_404(self, served: Any) -> None:
        client, _ = served

        assert (await client.get(f"/api/claims/{uuid.uuid4()}")).status_code == 404

    async def test_another_users_claim_is_a_404(
        self, served: Any, db_engine: Any, store: LocalArtefactStore, settings: Settings
    ) -> None:
        """The API's own ownership check, not the page's.

        The two are separate implementations of the same rule — see ADR 0024 — so a test of
        one says nothing about the other.
        """
        client, _ = served
        other = await committed_evidence(
            db_engine, store, settings, email="somebody-else-api@example.invalid"
        )

        response = await client.get(f"/api/claims/{other['supported_claim'].id}")

        assert response.status_code == 404

    async def test_the_run_claims_index_counts_the_unsupported(self, served: Any) -> None:
        client, built = served

        body = (await client.get(f"/api/runs/{built['job'].id}/claims")).json()

        assert len(body["claims"]) == 2
        assert body["unsupported"] == 1


# -- The pages -----------------------------------------------------------------------------------


@pytest.mark.integration
class TestTheSourcesPage:
    async def test_it_renders_without_javascript(self, served: Any) -> None:
        client, built = served

        response = await client.get(f"/runs/{built['job'].id}/sources")
        html = response.text

        assert response.status_code == 200
        # The rows are in the served HTML, not fetched by a script afterwards.
        assert 'id="sources-table"' in html
        assert str(built["filing"].id) in html
        assert _body_scripts(html) == 0

    async def test_a_quarantined_source_is_visibly_quarantined_with_its_reason(
        self, served: Any
    ) -> None:
        client, built = served

        html = (await client.get(f"/runs/{built['job'].id}/sources")).text

        assert "Quarantined:" in html
        assert built["quarantine_reason"] in html

    async def test_the_tier_is_printed_and_not_only_coloured(self, served: Any) -> None:
        # Colour is an aid. A reader who cannot distinguish the greens still has to be able
        # to tell a regulator from a blog.
        #
        # Matched *inside the badge*, because the row also carries the tier in a `data-`
        # attribute and a bare substring check would pass on an empty badge.
        client, built = served

        html = (await client.get(f"/runs/{built['job'].id}/sources")).text

        badges = re.findall(r'data-field="tier"[^>]*>\s*([A-Z0-9_]+)\s*<', html)
        assert SourceTier.T1_REGULATORY.value in badges
        assert SourceTier.T2_ISSUER.value in badges

    async def test_the_artefact_digest_is_shown(self, served: Any) -> None:
        client, built = served

        html = (await client.get(f"/runs/{built['job'].id}/sources")).text

        assert built["filing_sha256"][:12] in html

    async def test_an_unknown_run_is_a_404(self, served: Any) -> None:
        client, _ = served

        response = await client.get(f"/runs/{uuid.uuid4()}/sources")

        assert response.status_code == 404

    async def test_another_users_run_is_a_404(
        self, served: Any, db_engine: Any, store: LocalArtefactStore, settings: Settings
    ) -> None:
        """Somebody else's evidence is not readable, and answers the same as a missing one.

        A second, later-created user: ``get_current_user`` returns the earliest, so the
        client is still the first user and this run is genuinely somebody else's.
        """
        client, _ = served
        other = await committed_evidence(
            db_engine, store, settings, email="somebody-else@example.invalid"
        )

        sources = await client.get(f"/runs/{other['job'].id}/sources")
        claim = await client.get(f"/claims/{other['supported_claim'].id}")

        assert sources.status_code == 404
        assert claim.status_code == 404


@pytest.mark.integration
class TestTheClaimPage:
    async def test_it_shows_the_exact_excerpt(self, served: Any) -> None:
        client, built = served

        html = (await client.get(f"/claims/{built['supported_claim'].id}")).text

        assert SUPPORTED_SENTENCE in html
        assert 'data-field="excerpt"' in html

    async def test_a_verified_citation_says_verified(self, served: Any) -> None:
        client, built = served

        html = (await client.get(f"/claims/{built['supported_claim'].id}")).text

        assert 'data-state="verified"' in html
        assert "Confirmed by" in html

    async def test_an_unverified_citation_is_visibly_unverified(self, served: Any) -> None:
        # The test that matters most on this page. Showing the words without the verdict
        # would imply a check that never happened.
        client, built = served

        html = (await client.get(f"/claims/{built['unsupported_claim'].id}")).text

        assert 'data-state="unverified"' in html
        assert "Not confirmed against the archived document." in html
        assert 'id="claim-unsupported"' in html

    async def test_it_renders_without_javascript(self, served: Any) -> None:
        client, built = served

        html = (await client.get(f"/claims/{built['supported_claim'].id}")).text

        assert _body_scripts(html) == 0

    async def test_the_excerpt_is_rendered_as_text_rather_than_as_markup(
        self, served: Any, db_engine: Any
    ) -> None:
        """A filing nobody vetted must not become markup on a page that can reach the
        database.

        The excerpt comes out of a fetched document, so it is exactly the sort of content an
        injected payload arrives in. Jinja escapes it, and this proves the escaping is on
        rather than trusting that autoescaping was configured.
        """
        client, built = served

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            extraction = await session.get(Extraction, built["good_citation"].extraction_id)
            assert extraction is not None
            extraction.excerpt = "<script>alert('xss')</script>"
            await session.commit()

        html = (await client.get(f"/claims/{built['supported_claim'].id}")).text

        assert "<script>alert('xss')</script>" not in html
        assert "&lt;script&gt;" in html
        assert _body_scripts(html) == 0

    async def test_an_unknown_claim_is_a_404(self, served: Any) -> None:
        client, _ = served

        assert (await client.get(f"/claims/{uuid.uuid4()}")).status_code == 404


@pytest.mark.integration
class TestTwoClicks:
    async def test_a_reader_reaches_the_excerpt_from_the_report_in_two_clicks(
        self, served: Any
    ) -> None:
        """The phase's acceptance criterion, walked link by link.

        Nothing is guessed: each step follows an ``href`` that is actually in the previous
        page's HTML, so a link that was renamed or removed fails this rather than passing on
        the strength of a URL the test happens to know.
        """
        client, built = served
        job_id = built["job"].id

        # The report page stands in for "a rendered report"; the run console carries the same
        # links, and both are one hop from the claim index.
        console = (await client.get(f"/runs/{job_id}")).text
        claims_href = _href(console, "claims-link")
        assert claims_href == f"/runs/{job_id}/claims"

        # Click one.
        index = (await client.get(claims_href)).text
        hrefs = _claim_hrefs(index)
        assert len(hrefs) == 2

        # Click two, from every claim on the index rather than from a chosen one: the
        # criterion is "any claim", so following only the convenient link would prove less
        # than it appears to.
        excerpts: list[str] = []
        for href in hrefs:
            detail = await client.get(href)
            assert detail.status_code == 200
            assert 'data-field="excerpt"' in detail.text
            assert 'data-field="verdict"' in detail.text
            excerpts.append(detail.text)

        assert any(SUPPORTED_SENTENCE in page for page in excerpts)
        assert any(FABRICATED in page for page in excerpts)

    async def test_the_sources_table_is_one_click_from_the_console(self, served: Any) -> None:
        client, built = served

        console = (await client.get(f"/runs/{built['job'].id}")).text
        href = _href(console, "sources-link")

        response = await client.get(href)

        assert response.status_code == 200
        assert 'id="sources-table"' in response.text


def _body_scripts(html: str) -> int:
    """Scripts the page itself adds, ignoring the two the shell loads in ``<head>``.

    These pages must add none. Combined with asserting the content is in the served bytes,
    that is the whole "works with JavaScript off" claim: nothing on the page is fetched, and
    nothing on it is drawn by a script.
    """
    _, _, body = html.partition("</head>")
    return len(re.findall(r"<script", body))


def _href(html: str, element_id: str) -> str:
    match = re.search(rf'<a\s[^>]*href="([^"]+)"[^>]*id="{element_id}"', html)
    if match is None:
        match = re.search(rf'<a\s[^>]*id="{element_id}"[^>]*href="([^"]+)"', html)
    assert match is not None, f"no link with id={element_id!r} in the page"
    return match.group(1)


def _claim_hrefs(html: str) -> list[str]:
    return re.findall(r'href="(/claims/[0-9a-f-]+)"', html)
