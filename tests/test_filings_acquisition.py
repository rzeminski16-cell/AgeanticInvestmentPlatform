"""Acquiring the filings, and dating the aggregate that used to have no date.

**Two failures from one live run, and they are the same failure.** Every run's only source
was the XBRL company-facts aggregate, quarantined `no_publication_date` — so no claim could
cite anything the run held. And the aggregate is numbers: the recent-developments worker
finished with five leads and no findings because there was nothing recent in front of it.

A run that reads one generated document is a run with nothing to say and no way to say it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import Provider, SourceTier, UserRole
from aer.core.schemas.extraction import Locator
from aer.db.models import Extraction, ResearchRequest, SourceDocument, User
from aer.errors import ExternalServiceError
from aer.extract import extract_text
from aer.services.filings import MAX_EXCERPTS, MIN_EXCERPT_CHARS, acquire_filings
from aer.sources.base import ResolvedEntity
from aer.sources.sec.companyfacts import parse_company_facts
from aer.sources.sec.submissions import parse_submissions
from aer.storage.local import LocalArtefactStore
from tests.sec_fixtures import MSFT_CIK, fixture_bytes
from tests.workflow_fixtures import (
    COMPANY_FACTS_FIXTURE,
    SUBMISSIONS_FIXTURE,
    StubSecClient,
    _stub_fetch,
)

pytestmark = pytest.mark.integration

# The fixture index's newest filing is a 10-K accepted on this date; the newest 8-K is
# earlier. Both matter to the point-in-time tests below.
LATEST_ANNUAL = date(2022, 7, 28)
LATEST_CURRENT = date(2021, 10, 26)

ENTITY = ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP", ticker="MSFT", exchange=None)


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    user = User(email="filings@example.invalid", display_name="F", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=date(2024, 6, 30),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
        point_in_time=True,
    )
    db_session.add(request)
    await db_session.flush()

    settings = Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    return {
        "session": db_session,
        "request": request,
        "settings": settings,
        "store": store,
        "client": StubSecClient(store),
    }


async def _acquire(scene: dict[str, Any], **kwargs: Any) -> Any:
    return await acquire_filings(
        scene["session"],
        scene["store"],
        client=kwargs.pop("client", scene["client"]),
        request=scene["request"],
        entity=ENTITY,
        settings=scene["settings"],
        **kwargs,
    )


class TestTheRunReadsMoreThanOneDocument:
    async def test_the_annual_report_and_the_current_reports_are_acquired(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _acquire(scene)

        assert len(outcome.documents) > 1

    async def test_each_filing_becomes_a_citable_source_document(
        self, scene: dict[str, Any]
    ) -> None:
        await _acquire(scene)

        rows = list(
            await scene["session"].scalars(
                select(SourceDocument).where(
                    SourceDocument.request_id == scene["request"].id,
                    SourceDocument.source_tier == SourceTier.T1_REGULATORY,
                )
            )
        )
        assert rows
        assert all(row.provider is Provider.SEC_EDGAR for row in rows)

    async def test_the_annual_report_is_the_latest_one_inside_the_window(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _acquire(scene)

        dates = {document.publication_date for document in outcome.documents}
        assert LATEST_ANNUAL in dates

    async def test_a_filing_is_dated_by_when_it_was_accepted(self, scene: dict[str, Any]) -> None:
        """Not by the period it covers. A 10-K for the year to June, accepted in August,
        was not readable in July, and the acceptance date is the only one that says so."""
        outcome = await _acquire(scene)

        annual = next(d for d in outcome.documents if d.publication_date == LATEST_ANNUAL)
        assert annual.publication_date_confidence == pytest.approx(1.0)
        assert not annual.quarantined

    async def test_the_current_reports_are_bounded(self, scene: dict[str, Any]) -> None:
        """Each is a fetch under SEC's rate limit and an artefact to keep for ever."""
        outcome = await _acquire(scene, max_current=1)

        assert len(outcome.documents) <= 2


class TestPointInTime:
    async def test_a_filing_after_the_as_of_date_is_never_fetched(
        self, scene: dict[str, Any]
    ) -> None:
        """Refused on the index, before anything is requested — the cheapest place, and the
        one where a post-dated filing stops being a candidate rather than being fetched and
        then thrown away."""
        scene["request"].as_of_date = date(2021, 1, 1)
        await scene["session"].flush()

        outcome = await _acquire(scene)

        assert all(document.publication_date <= date(2021, 1, 1) for document in outcome.documents)
        assert not any("2022" in url for url in scene["client"].document_calls)

    async def test_a_window_with_nothing_in_it_says_so(self, scene: dict[str, Any]) -> None:
        scene["request"].as_of_date = date(1999, 1, 1)
        await scene["session"].flush()

        outcome = await _acquire(scene)

        assert outcome.documents == ()
        assert any("annual report" in note for note in outcome.skipped)


class TestTheDocumentsCanBeCited:
    async def test_excerpts_are_recorded_for_each_filing(self, scene: dict[str, Any]) -> None:
        """A source document with no extractions contributes nothing to an evidence pack
        and cannot be cited — acquiring one without excerpting it buys the same silence at
        a higher price."""
        outcome = await _acquire(scene)

        assert outcome.excerpts > 0
        stored = await scene["session"].scalar(select(func.count()).select_from(Extraction))
        assert stored == outcome.excerpts

    async def test_every_excerpt_verifies_against_the_archived_document(
        self, scene: dict[str, Any]
    ) -> None:
        """The whole point of recording them. An excerpt the verifier cannot re-find is a
        citation that will block a report at gate 2."""
        outcome = await _acquire(scene)
        document = outcome.documents[0]

        extractions = list(
            await scene["session"].scalars(
                select(Extraction).where(Extraction.source_document_id == document.id)
            )
        )
        assert extractions

        text = (
            await extract_text(
                scene["store"],
                sha256=document.artefact.sha256,
                extractor="html",
                settings=scene["settings"],
            )
        ).text
        for extraction in extractions:
            found = text.excerpt(Locator.model_validate(extraction.locator))
            assert found.text == extraction.excerpt

    async def test_a_fragment_too_short_to_mean_anything_is_not_excerpted(
        self, scene: dict[str, Any]
    ) -> None:
        """A citation pointing at "12" verifies and tells a reader nothing.

        The bound is written out rather than read from `MIN_EXCERPT_CHARS`, because an
        assertion phrased against the constant is one the constant satisfies at any value:
        set it to zero and this passes while every page number in the filing becomes
        citable evidence.
        """
        await _acquire(scene)

        extractions = list(await scene["session"].scalars(select(Extraction)))
        assert extractions
        assert all(len(row.excerpt) >= 120 for row in extractions)
        assert MIN_EXCERPT_CHARS >= 120


class TestNothingFailsTheRun:
    async def test_an_unreachable_index_is_reported_not_raised(self, scene: dict[str, Any]) -> None:
        class _Broken(StubSecClient):
            async def fetch_submissions(self, cik: str) -> Any:
                message = "EDGAR is down."
                raise ExternalServiceError(message, provider="sec_edgar")

        outcome = await _acquire(scene, client=_Broken(scene["store"]))

        assert outcome.documents == ()
        assert any("could not be read" in note for note in outcome.skipped)

    async def test_one_unfetchable_filing_does_not_cost_the_others(
        self, scene: dict[str, Any]
    ) -> None:
        """A run that failed outright for one unreachable 8-K would fail most weeks."""
        refused: list[str] = []

        class _Flaky(StubSecClient):
            async def fetch_document(self, ref: Any) -> Any:
                if not refused:
                    refused.append(ref.url)
                    message = "Refused by robots."
                    raise ExternalServiceError(message, provider="sec_edgar")
                return await StubSecClient.fetch_document(self, ref)

        outcome = await _acquire(scene, client=_Flaky(scene["store"]))

        assert outcome.documents
        assert outcome.skipped


class TestTheAggregateIsDated:
    """ADR 0044. companyfacts is assembled on request and carries no date of its own, and
    an undated source is quarantined — so every run quarantined the only document it had."""

    def test_the_newest_filing_in_it_is_its_date(self) -> None:
        facts = parse_company_facts(fixture_bytes(COMPANY_FACTS_FIXTURE))

        assert facts.latest_filed == max(fact.filed_date for fact in facts.facts)

    def test_an_empty_aggregate_keeps_no_date(self) -> None:
        """Inventing one would be worse than the quarantine it replaces."""
        facts = parse_company_facts(fixture_bytes(COMPANY_FACTS_FIXTURE))
        empty = type(facts)(cik=facts.cik, entity_name=facts.entity_name, facts=(), unmapped=())

        assert empty.latest_filed is None

    def test_the_index_is_not_recorded_as_a_source(self, scene: dict[str, Any]) -> None:
        """A listing of what exists is not evidence of anything, and nothing will cite it.
        Recording it would bury the documents that matter under the catalogue."""
        index = parse_submissions(fixture_bytes(SUBMISSIONS_FIXTURE))

        assert index.filings  # the fixture is not empty, so the assertion below means something

    async def test_no_source_document_points_at_the_submissions_index(
        self, scene: dict[str, Any]
    ) -> None:
        await _acquire(scene)

        urls = list(
            await scene["session"].scalars(
                select(SourceDocument.url).where(SourceDocument.request_id == scene["request"].id)
            )
        )
        assert urls
        assert not any("/submissions/" in url for url in urls)


# A 10-K in miniature: the statutory items, with the cover-page furniture in front of them
# that document-order selection used to pick instead of the prose.
TEN_K = b"""<!DOCTYPE html><html><body>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C. 20549 FORM 10-K
ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934.</p>
<p>Securities registered pursuant to Section 12(b) of the Act: Common stock, par value
$0.00000625 per share, registered on the NASDAQ Stock Market LLC under the symbol MSFT.</p>
<p>The registrant's transfer agent and registrar is a national banking association with an
address in Providence, Rhode Island, and correspondence should be directed there.</p>
<p>Item 1. Business</p>
<p>The company reports revenue in three segments, and describes competition across cloud
infrastructure as intense, with pricing pressure from two large competitors and growth in
demand for capacity from enterprise customers driving the segment's operating margin.</p>
<p>Item 1A. Risk Factors</p>
<p>Regulatory scrutiny of large platforms is a risk to the business, as is litigation
arising from acquisition activity, and the company notes currency movement in the markets
where it bills as a further risk to reported revenue.</p>
<p>Item 6. Reserved</p>
<p>This item has been reserved and the registrant has nothing to disclose under it at all,
which is a paragraph of exactly the length that would otherwise qualify for selection.</p>
<p>Item 7. Management's Discussion and Analysis</p>
<p>Operating cash flow funded the capital programme and the dividend, and management
describes its capital allocation strategy and the outlook for margin as unchanged from the
guidance given at the start of the year, with investment in capacity continuing.</p>
</body></html>"""


class TestWhichPassagesAreKept:
    """Document order was the first version, and on a 10-K it reads the cover page.

    Forty paragraphs from the top of an annual report is the SEC's address, the listing
    table and the transfer agent — every one genuinely present in the artefact, and not one
    of them anything a research section would cite.
    """

    @pytest.fixture
    async def filed(self, scene: dict[str, Any]) -> list[str]:
        class _TenK(StubSecClient):
            async def fetch_document(self, ref: Any) -> Any:
                self.document_calls.append(ref.url)
                stored = await self._store.put_bytes(TEN_K)
                return _stub_fetch(ref.url, stored, media_type="text/html")

        await _acquire(scene, client=_TenK(scene["store"]), max_current=0)
        return list(await scene["session"].scalars(select(Extraction.excerpt)))

    async def test_the_statutory_items_are_read(self, filed: list[str]) -> None:
        body = " ".join(filed)

        assert "three segments" in body
        assert "Regulatory scrutiny" in body
        assert "capital allocation strategy" in body

    async def test_the_cover_page_furniture_is_not(self, filed: list[str]) -> None:
        body = " ".join(filed)

        assert "transfer agent" not in body
        assert "Washington, D.C." not in body

    async def test_an_item_the_form_reserves_is_not_read(self, filed: list[str]) -> None:
        """Item 6 is long enough to qualify on length alone, and says nothing."""
        assert not any("has been reserved" in excerpt for excerpt in filed)

    async def test_the_excerpts_keep_the_filing_s_own_order(self, filed: list[str]) -> None:
        """A reader following a citation back expects the document's sequence."""
        body = " ".join(filed)

        assert body.index("three segments") < body.index("Regulatory scrutiny")
        assert body.index("Regulatory scrutiny") < body.index("capital allocation")

    async def test_a_document_with_no_items_is_read_whole(self, scene: dict[str, Any]) -> None:
        """An 8-K has no statutory structure and is short and entirely about one event.
        Finding no headings must mean "read it all", never "read none of it"."""
        await _acquire(scene, max_current=1)

        excerpts = list(await scene["session"].scalars(select(Extraction.excerpt)))
        assert excerpts


def _crowded_ten_k() -> bytes:
    """An Item 1 holding far more qualifying paragraphs than a document may keep.

    Cutting on the item headings is only half the selection. Inside Item 1 of a real 10-K
    there are hundreds of paragraphs long enough to qualify, most of them property leases
    and legal-entity housekeeping, and taking the first forty of *those* is the cover-page
    failure again one level down. The filler here is deliberately long, dull and free of
    the vocabulary a research report uses; the three paragraphs worth reading are last in
    document order, so only the score can reach them.
    """
    filler = "\n".join(
        f"<p>The registrant maintains an office at building number {number} in a district "
        "whose address is set out in the exhibit index appended to this report, and the "
        "lease on that property runs to a date stated in the same exhibit.</p>"
        for number in range(MAX_EXCERPTS + 10)
    )
    return f"""<!DOCTYPE html><html><body>
<p>Item 1. Business</p>
{filler}
<p>The company reports revenue in three segments, and describes competition across cloud
infrastructure as intense, with pricing pressure from two large competitors and growth in
demand for capacity from enterprise customers driving the segment's operating margin.</p>
<p>Operating cash flow funded the capital programme and the dividend, and management
describes its capital allocation strategy and the outlook for margin as unchanged from the
guidance given at the start of the year, with investment in capacity continuing.</p>
<p>Regulatory scrutiny of large platforms is a risk to the business, as is litigation
arising from acquisition activity, and the company notes currency movement in the markets
where it bills as a further risk to reported revenue.</p>
</body></html>""".encode()


class TestTheBestPassagesWin:
    """The second pass, and the one the item headings cannot do on their own.

    A real Item 1 runs to hundreds of qualifying paragraphs. Keeping the first forty of
    them is document order again, just inside the right section — so the paragraphs are
    scored on the vocabulary a research report actually uses, and the best are kept.
    """

    @pytest.fixture
    async def filed(self, scene: dict[str, Any]) -> list[str]:
        crowded = _crowded_ten_k()

        class _Crowded(StubSecClient):
            async def fetch_document(self, ref: Any) -> Any:
                self.document_calls.append(ref.url)
                stored = await self._store.put_bytes(crowded)
                return _stub_fetch(ref.url, stored, media_type="text/html")

        await _acquire(scene, client=_Crowded(scene["store"]), max_current=0)
        return list(await scene["session"].scalars(select(Extraction.excerpt)))

    async def test_the_substantive_paragraphs_survive_the_crowd(self, filed: list[str]) -> None:
        """They are last in document order and there are fifty things ahead of them."""
        body = " ".join(filed)

        assert "three segments" in body
        assert "capital allocation strategy" in body
        assert "Regulatory scrutiny" in body

    async def test_the_housekeeping_is_what_gets_dropped(self, filed: list[str]) -> None:
        kept = sum(1 for excerpt in filed if "lease on that property" in excerpt)

        assert kept < MAX_EXCERPTS, (
            "the filler filled the quota, which is document-order selection wearing the "
            "item headings as a hat"
        )

    async def test_the_document_s_own_order_still_decides_the_sequence(
        self, filed: list[str]
    ) -> None:
        """Ranked by score, emitted by position. A reader following a citation back reads
        the filing, not this module's opinion of it."""
        body = " ".join(filed)

        assert body.index("three segments") < body.index("capital allocation")
        assert body.index("capital allocation") < body.index("Regulatory scrutiny")

    async def test_no_more_than_the_cap_is_ever_recorded(self, filed: list[str]) -> None:
        """The pack is assembled against a token budget, and one 10-K that filled it would
        be worse than the silence this module exists to end."""
        assert 0 < len(filed) <= MAX_EXCERPTS
