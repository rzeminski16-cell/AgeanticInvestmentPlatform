"""Every marker in a rendered document walks back to bytes — the task 52 surface.

The document the preview serves carries footnote markers; each marker must resolve to a
server-rendered drill-down that answers with the claim's evidence: the excerpt verbatim,
the verifier's verdict and ratio, the artefact digest, the tier and the licence note. A
calculation marker continues to the calculation walk. An unresolvable citation is stated
in the drill-down in exactly the words the document used — a reader who follows a broken
marker must not be told a softer story than the document told them.

Evidence comes from ``tests.provenance_fixtures``: built through the real services,
verified by the real verifier, and cited from a *skill-origin* section — so the walk is
proved for a custom section's figures, which is what the phase's acceptance line means by
"regardless of which section it came from".
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import Provider, SourceTier
from aer.db.models import Artefact, Claim, SourceDocument
from aer.extract.html import extract_html
from aer.render.document import UnresolvedFootnote
from aer.services.calculations import lineage
from aer.services.citations import record_citation
from aer.services.extractions import record_excerpt
from aer.storage.local import LocalArtefactStore
from tests.api_fixtures import build_app, client_for
from tests.provenance_fixtures import (
    FABRICATED,
    MISSING_CALC_ID,
    MISSING_SOURCE_ID,
    SUPPORTED_SENTENCE,
    WALK_FORMULA,
    WALK_SECTION_KEY,
    committed_evidence,
)

pytestmark = pytest.mark.integration

EMAIL = "drilldown@example.invalid"

# The inline markers of the HTML notation. ``title`` (the CSS-only hover preview) comes
# before ``href``, so the anchor's final attribute is the in-document link.
_MARKERS = re.compile(r'<sup class="fn-ref"[^>]*><a[^>]*href="#fn-(\d+)">')

_TAGS = re.compile(r"<[^>]+>")


@pytest.fixture
def settings(api_settings: Settings) -> Settings:
    return api_settings


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def clean_slate(db_engine: Any) -> AsyncIterator[None]:
    """Before **and** after: the evidence carries a company keyed by its listing and its
    CIK, and the last test in this file would otherwise leave it for the next module that
    researches the same company to collide with."""
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
    """A client over an application that can see committed evidence with markers."""
    built = await committed_evidence(db_engine, store, settings, email=EMAIL)
    async for client in client_for(build_app(settings, engine=db_engine, redis=fake_redis)):
        yield client, built


class TestEveryMarkerResolves:
    async def test_each_marker_in_the_preview_reaches_a_drilldown(self, served: Any) -> None:
        """The task's headline test: markers are not decoration, they are doors."""
        client, built = served
        job_id = built["job"].id

        preview = await client.get(f"/runs/{job_id}/preview")
        assert preview.status_code == 200
        markers = sorted({int(number) for number in _MARKERS.findall(preview.text)})
        # The section's four, then the exhibit's — numbered unbroken across the boundary.
        assert markers == list(range(1, len(markers) + 1))
        assert len(markers) > 4, "the exhibit's caption markers must be in the sweep too"

        for number in markers:
            answer = await client.get(f"/runs/{job_id}/footnotes/{number}")
            assert answer.status_code in (200, 303), f"note {number} did not resolve"
            if answer.status_code == 303:
                followed = await client.get(answer.headers["location"])
                assert followed.status_code == 200, f"note {number} redirected to a dead page"

    async def test_the_marker_count_matches_the_document_footnotes(self, served: Any) -> None:
        """One past the end is refused with the honest count, so a stale link cannot
        silently show a different note's evidence."""
        client, built = served
        job_id = built["job"].id

        preview = await client.get(f"/runs/{job_id}/preview")
        count = len({int(number) for number in _MARKERS.findall(preview.text)})

        beyond = await client.get(f"/runs/{job_id}/footnotes/{count + 1}")
        assert beyond.status_code == 404
        assert f"This document has {count} note(s)" in beyond.text

        nothing = await client.get(f"/runs/{job_id}/footnotes/0")
        assert nothing.status_code == 404

    async def test_an_exhibit_caption_walks_back_the_same_way(self, served: Any) -> None:
        """A chart's caption carries markers like any other cited content, and they lead
        to the same drill-down. An exhibit whose provenance were a special case would be
        the one figure in the report a reader could not check."""
        client, built = served
        job_id = built["job"].id

        preview = await client.get(f"/runs/{job_id}/preview")
        exhibits = preview.text[preview.text.index('id="section-exhibits"') :]
        caption = re.search(r"<figcaption>(.*?)</figcaption>", exhibits, flags=re.DOTALL)
        assert caption is not None, "the exhibit pack must render a caption"

        numbers = [int(found) for found in _MARKERS.findall(caption.group(1))]
        assert numbers, "the caption must carry at least one marker"
        for number in numbers:
            answer = await client.get(f"/runs/{job_id}/footnotes/{number}")
            assert answer.status_code in (200, 303)
            assert f'href="/runs/{job_id}/footnotes/{number}"' in preview.text

        # And the caption's markers preview on hover exactly as a paragraph's do. The
        # golden document carries no exhibits, so this is the only place the exhibit
        # path's hover titles are held.
        hovers = re.findall(r'<sup class="fn-ref"[^>]*><a title="([^"]*)"', caption.group(1))
        assert len(hovers) == len(numbers), "every caption marker must carry its preview"
        assert all("Follow the note" in hover for hover in hovers)

    async def test_the_citing_section_renders_as_custom_origin(self, served: Any) -> None:
        """The figures whose markers just resolved came from a skill-authored section —
        provenance does not depend on who authored the section, and the acceptance line's
        "regardless of which section it came from" is proved on the custom one."""
        client, built = served

        preview = await client.get(f"/runs/{built['job'].id}/preview")

        assert f'id="section-{WALK_SECTION_KEY}"' in preview.text
        section = re.search(
            rf'<section class="report-section custom"\s+id="section-{WALK_SECTION_KEY}">',
            preview.text,
        )
        assert section is not None, "the cited section must render under the custom origin"


class TestTheSourceDrilldown:
    async def test_it_answers_with_the_evidence(self, served: Any) -> None:
        client, built = served
        job_id = built["job"].id

        page = await client.get(f"/runs/{job_id}/footnotes/{built['markers']['source']}")
        assert page.status_code == 200

        # The artefact digest, in full — the identity of the bytes the run read.
        assert built["filing_sha256"] in page.text
        # The excerpt, verbatim, with the verifier's verdict and ratio beside it.
        assert SUPPORTED_SENTENCE in page.text
        assert 'data-state="verified"' in page.text
        assert "match ratio" in page.text
        # The tier and the licence note.
        assert "T1_REGULATORY" in page.text
        assert "US government work" in page.text

    async def test_the_failed_excerpt_is_shown_failed(self, served: Any) -> None:
        """The unverified citation appears on the same page, visibly unverified — the
        drill-down must not become a gallery of only the confirmations."""
        client, built = served
        job_id = built["job"].id

        page = await client.get(f"/runs/{job_id}/footnotes/{built['markers']['source']}")

        assert FABRICATED in page.text
        assert 'data-state="unverified"' in page.text
        assert "Not confirmed" in page.text

    async def test_it_links_each_claim_to_its_own_page(self, served: Any) -> None:
        client, built = served
        job_id = built["job"].id

        page = await client.get(f"/runs/{job_id}/footnotes/{built['markers']['source']}")

        assert f"/claims/{built['supported_claim'].id}" in page.text
        assert f"/claims/{built['unsupported_claim'].id}" in page.text

    async def test_it_shows_only_the_citations_that_name_this_source(
        self, served: Any, db_engine: Any, store: LocalArtefactStore
    ) -> None:
        """A claim resting on two documents is walked one document at a time.

        Each document has its own marker, and each marker's page must answer for its own
        document. Showing the other one's excerpt here would tell a reader this source
        supports a sentence it was never checked against.
        """
        client, built = served
        job_id = built["job"].id

        elsewhere = b"<!DOCTYPE html><html><body><p>A second filing entirely.</p></body></html>"
        other_sentence = "A second filing entirely."
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            stored = await store.put_bytes(elsewhere)
            artefact = Artefact(
                sha256=stored.sha256,
                media_type="text/html",
                size_bytes=stored.size_bytes,
                storage_key=store.storage_key_for(stored.sha256),
            )
            session.add(artefact)
            await session.flush()
            second = SourceDocument(
                work_order_id=built["request"].id,
                request_id=built["request"].id,
                job_id=job_id,
                artefact_id=artefact.id,
                url="https://www.sec.gov/Archives/edgar/data/789019/msft-10q.htm",
                title="Microsoft Corporation Form 10-Q",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                retrieved_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
                publication_date=date(2026, 6, 13),
                quarantined=False,
            )
            session.add(second)
            await session.flush()

            extracted = extract_html(elsewhere).text
            located = extracted.locate(other_sentence)
            assert located is not None
            extraction = await record_excerpt(
                session, source_document_id=second.id, extracted=extracted, excerpt=located
            )
            claim = await session.get(Claim, built["supported_claim"].id)
            assert claim is not None
            await record_citation(
                session,
                claim=claim,
                source_document_id=second.id,
                extraction_id=extraction.id,
            )
            await session.commit()

        page = await client.get(f"/runs/{job_id}/footnotes/{built['markers']['source']}")

        assert SUPPORTED_SENTENCE in page.text
        assert other_sentence not in page.text, "the other document's excerpt leaked in"

    async def test_it_returns_to_the_marker_it_came_from(self, served: Any) -> None:
        client, built = served
        job_id = built["job"].id
        number = built["markers"]["source"]

        page = await client.get(f"/runs/{job_id}/footnotes/{number}")

        assert f"/runs/{job_id}/preview#fn-{number}" in page.text


class TestTheCalculationMarker:
    async def test_it_continues_to_the_calculation_walk(self, served: Any) -> None:
        client, built = served
        job_id = built["job"].id

        answer = await client.get(f"/runs/{job_id}/footnotes/{built['markers']['calculation']}")
        assert answer.status_code == 303
        assert answer.headers["location"] == f"/calculations/{built['calculation'].id}"

        walk = await client.get(answer.headers["location"])
        assert walk.status_code == 200
        assert WALK_FORMULA in walk.text
        assert 'id="formula"' in walk.text

    async def test_the_walk_reaches_only_facts_and_assumptions(
        self, served: Any, db_engine: Any
    ) -> None:
        """The DAG bottoms out in evidence, never in more arithmetic.

        A walk that ended on a calculation would be presenting an incomplete chain as a
        complete one. Asserted against the tree the page renders from, and then against
        the page, so neither the resolver nor the template can drop a leaf quietly.
        """
        client, built = served
        calculation_id = built["calculation"].id

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            tree = await lineage(session, calculation_id)

        kinds = {node.kind for node in tree.leaves}
        assert kinds <= {"fact", "assumption"}, (
            f"the walk stopped on {kinds - {'fact', 'assumption'}}"
        )
        assert kinds == {"fact", "assumption"}, "the chain must exercise both leaf kinds"
        assert all(node.is_resolved for node in tree.leaves)
        # Two levels: the root's inputs include a calculation that itself has inputs.
        assert any(node.kind == "calculation" for node in tree.inputs)

        walk = await client.get(f"/calculations/{calculation_id}")
        for leaf in tree.leaves:
            assert leaf.label in walk.text, f"the page omits the {leaf.kind} leaf {leaf.label!r}"
        # The assumption states its justification in place, and the fact leaf carries on
        # to the document it was reported in — the walk ends at bytes, not at a label.
        assert "nominal GDP" in walk.text
        assert f'href="/runs/{built["job"].id}/sources#source-{built["filing"].id}"' in walk.text

    async def test_the_fact_leaf_reaches_the_artefact_digest(self, served: Any) -> None:
        """Following the walk's last link lands on the row carrying the source's hash."""
        client, built = served

        walk = await client.get(f"/calculations/{built['calculation'].id}")
        anchor = re.search(r'href="(/runs/[^"]+/sources#source-[^"]+)"', walk.text)
        assert anchor is not None

        path, _, fragment = anchor.group(1).partition("#")
        sources = await client.get(path)
        assert sources.status_code == 200
        assert f'id="{fragment}"' in sources.text
        assert built["filing_sha256"][:12] in sources.text


class TestTheUnresolvedCitation:
    async def test_the_drilldown_states_the_documents_own_words(self, served: Any) -> None:
        """A broken chain reads identically wherever the reader meets it: the drill-down's
        statement is, character for character, the document's footnote with its markup
        stripped."""
        client, built = served
        job_id = built["job"].id

        preview = await client.get(f"/runs/{job_id}/preview")
        for number, identifier, kind_label in (
            (built["markers"]["missing_source"], MISSING_SOURCE_ID, "source document"),
            (built["markers"]["missing_calc"], MISSING_CALC_ID, "calculation"),
        ):
            expected = UnresolvedFootnote(
                number=number, kind_label=kind_label, identifier=str(identifier)
            ).statement

            # The drill-down's statement is exactly the shared sentence.
            page = await client.get(f"/runs/{job_id}/footnotes/{number}")
            assert page.status_code == 200
            drill = re.search(
                r'<p[^>]*id="unresolved-note"[^>]*>(.*?)</p>', page.text, flags=re.DOTALL
            )
            assert drill is not None
            assert " ".join(_TAGS.sub("", drill.group(1)).split()) == expected

            # And the document's footnote, tags stripped, carries the same sentence.
            entry = re.search(
                rf'<li id="fn-{number}"[^>]*>(.*?)</li>', preview.text, flags=re.DOTALL
            )
            assert entry is not None
            assert expected in " ".join(_TAGS.sub("", entry.group(1)).split())

    async def test_an_unresolved_calculation_is_not_redirected_into_a_404(
        self, served: Any
    ) -> None:
        """A calculation marker whose target is gone renders the honest state here rather
        than bouncing the reader to a walk page that cannot exist."""
        client, built = served
        job_id = built["job"].id

        page = await client.get(f"/runs/{job_id}/footnotes/{built['markers']['missing_calc']}")

        assert page.status_code == 200
        assert "calculation" in page.text
        assert str(MISSING_CALC_ID) in page.text


class TestTheDocumentCarriesTheWay:
    async def test_markers_carry_a_css_only_hover_preview(self, served: Any) -> None:
        client, built = served

        preview = await client.get(f"/runs/{built['job'].id}/preview")

        first = re.search(r'<sup class="fn-ref" id="fnref-1"><a title="([^"]+)"', preview.text)
        assert first is not None
        assert "Follow the note" in first.group(1)

    async def test_every_footnote_carries_its_drill_link(self, served: Any) -> None:
        client, built = served
        job_id = built["job"].id

        preview = await client.get(f"/runs/{job_id}/preview")

        for number in (1, 2, 3, 4):
            assert f'href="/runs/{job_id}/footnotes/{number}"' in preview.text

    async def test_the_footnote_keeps_its_return_link(self, served: Any) -> None:
        """The no-JS pair: marker jumps down, footnote jumps back. The drill link is an
        addition to that navigation, never a replacement for it."""
        client, built = served

        preview = await client.get(f"/runs/{built['job'].id}/preview")

        assert 'class="backref" href="#fnref-1"' in preview.text


class TestOwnership:
    async def test_another_account_cannot_reach_the_drilldown(
        self,
        served: Any,
        db_engine: Any,
        fake_redis: Any,
        settings: Settings,
        store: LocalArtefactStore,
    ) -> None:
        """A footnote URL contains a job id somebody could guess at; the answer for a run
        that is not yours is the answer for a run that does not exist."""
        _, built = served
        job_id = built["job"].id

        other = await committed_evidence(
            db_engine, store, settings, email="zz-other@example.invalid"
        )
        assert other["job"].id != job_id

        # The app authenticates as the earliest-created user; the first client sees the
        # first account's run and must not see the second's.
        client, _ = served
        theirs = await client.get(f"/runs/{other['job'].id}/footnotes/1")
        assert theirs.status_code == 404
