"""Publication dates: extracted rather than trusted, and what admissibility reads from them.

A document's date is evidence about the document, and the extractor scores it rather than
believing the first thing it finds. The corpus in :mod:`tests.publication_date_fixtures` writes
the answer down in advance: documents dated after :data:`AS_OF`, each hiding its date in a
different place; documents dated on or before it; and documents nothing can date.

**The date decides nothing about admissibility on its own.** A rule refusing a document
published after the run's date was retired with the date it compared against (ADR 0113). What
admissibility reads is whether a date exists at all, and only where the run refuses undated
sources (ADR 0111); what a date still does is stand on the record — best estimate and
conservative bound both — for the reader weighing the source.

**The claim-time check reads the row's verdict.** A quarantined document, or a prior run's own
output, fails a citation whatever the quote says. Both checks are tested here against the
database, after the pure functions they rest on.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import ClaimKind, Provider, SourceTier
from aer.db.models import Artefact, Citation, WorkOrder
from aer.errors import ConflictError, ValidationError
from aer.extract.dates import (
    DateCandidate,
    DateEvidence,
    PublicationDate,
    choose,
    extract_publication_date,
    from_headers,
    from_metadata,
    from_text,
)
from aer.services.acquisition import acquisition_root
from aer.services.citations import record_citation, record_claim
from aer.services.sources import (
    NO_PUBLICATION_DATE,
    decide_quarantine,
    override_admissibility,
    record_source_document,
)
from aer.sources.tiering import DocumentKind, tier_for
from aer.storage.local import LocalArtefactStore
from aer.verify.citations import verify
from tests.publication_date_fixtures import ADMISSIBLE, AS_OF, POST_DATED, UNDATABLE, Planted
from tests.scene_fixtures import build_scene


def _extract(case: Planted) -> PublicationDate | None:
    return extract_publication_date(
        index_date=case.index_date,
        metadata=case.metadata,
        text=case.text,
        headers=case.headers,
    )


# -- The planted corpus ----------------------------------------------------------------------------


class TestTheCorpus:
    """Every case dated as the fixture says, and admitted or refused as the policy says."""

    @pytest.mark.parametrize("case", POST_DATED, ids=lambda c: c.name)
    def test_every_later_dated_document_is_dated_correctly(self, case: Planted) -> None:
        found = _extract(case)

        assert found is not None, "no date could be established at all"
        assert found.value == case.expected

    @pytest.mark.parametrize("case", POST_DATED, ids=lambda c: c.name)
    def test_a_document_dated_after_the_run_is_admitted_all_the_same(self, case: Planted) -> None:
        """ADR 0113. The run's date is the day it was commissioned, so a document dated after
        it is a mis-dated document, recorded as such — not hindsight to refuse. Six documents,
        six places to hide a date, none of them a reason."""
        found = _extract(case)
        assert found is not None
        assert found.latest > AS_OF

        decision = decide_quarantine(
            publication_date=found.latest,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert not decision.quarantined, f"{case.name} was refused: {decision.reason}"

    @pytest.mark.parametrize("case", ADMISSIBLE, ids=lambda c: c.name)
    def test_every_admissible_document_is_admitted(self, case: Planted) -> None:
        found = _extract(case)
        assert found is not None
        assert found.value == case.expected

        decision = decide_quarantine(
            publication_date=found.latest,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert not decision.quarantined, f"{case.name} was refused: {decision.reason}"

    @pytest.mark.parametrize("case", UNDATABLE, ids=lambda c: c.name)
    def test_an_undatable_document_yields_no_date(self, case: Planted) -> None:
        """``None`` rather than a guess. "Undatable" and "probably July" need different
        responses, and a parser that produced the second for the first would hide the problem."""
        assert _extract(case) is None

    @pytest.mark.parametrize("case", UNDATABLE, ids=lambda c: c.name)
    def test_an_undatable_document_is_quarantined_where_the_run_refuses_them(
        self, case: Planted
    ) -> None:
        decision = decide_quarantine(
            publication_date=None,
            source_tier=SourceTier.T1_REGULATORY,
            undated_sources_admissible=False,
        )

        assert decision.quarantined
        assert decision.reason == NO_PUBLICATION_DATE
        assert _extract(case) is None

    @pytest.mark.parametrize("case", UNDATABLE, ids=lambda c: c.name)
    def test_an_undatable_document_is_admitted_by_default(self, case: Planted) -> None:
        """The platform's default (ADR 0111): a page nothing can date is worth reading. What
        keeps that honest is the tier cap, asserted below rather than here."""
        decision = decide_quarantine(
            publication_date=None,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert not decision.quarantined
        assert _extract(case) is None

    @pytest.mark.parametrize("case", UNDATABLE, ids=lambda c: c.name)
    def test_an_undatable_document_is_never_primary(self, case: Planted) -> None:
        """The other half of admitting it. A regulator's filing nobody can date is a page
        asserting a filing, and it counts for what such a page counts for."""
        assert SourceTier.T1_REGULATORY.as_evidence(dated=False) is SourceTier.T5_SECONDARY
        assert not SourceTier.T1_REGULATORY.as_evidence(dated=False).is_primary
        assert _extract(case) is None


# -- The date extractor ----------------------------------------------------------------------------


class TestTheOrderOfTrust:
    """Which evidence wins, and why it is not the order the plan listed.

    The plan's sentence puts HTTP headers first. ``Last-Modified`` describes a file on a server
    — a CDN re-upload moves it years after publication — so the module inverts that and says so.
    These tests are what make the inversion a decision rather than a slip.
    """

    def test_the_filing_index_beats_everything(self) -> None:
        found = extract_publication_date(
            index_date=date(2022, 7, 28),
            metadata={"CreationDate": "D:20220803000000Z"},
            text="Published 3 August 2022",
            headers={"Last-Modified": "Fri, 04 Nov 2022 11:00:00 GMT"},
        )

        assert found is not None
        assert found.value == date(2022, 7, 28)
        assert found.chosen.evidence is DateEvidence.FILING_INDEX

    def test_metadata_beats_text_and_headers(self) -> None:
        found = extract_publication_date(
            metadata={"article:published_time": "2022-07-28"},
            text="Published 3 August 2022",
            headers={"Last-Modified": "Fri, 04 Nov 2022 11:00:00 GMT"},
        )

        assert found is not None
        assert found.chosen.evidence is DateEvidence.DOCUMENT_METADATA

    def test_text_beats_headers(self) -> None:
        """A date printed on a cover page is evidence about the document. ``Last-Modified`` is
        evidence about a file."""
        found = extract_publication_date(
            text="Published 3 August 2022",
            headers={"Last-Modified": "Fri, 04 Nov 2022 11:00:00 GMT"},
        )

        assert found is not None
        assert found.chosen.evidence is DateEvidence.IN_DOCUMENT_TEXT

    def test_a_header_is_used_when_it_is_all_there_is(self) -> None:
        """Scored low, not discarded. A page datable only from its header is still datable, and
        dropping the evidence would record it as undatable for no reason."""
        found = extract_publication_date(headers={"Last-Modified": "Fri, 04 Nov 2022 11:00:00 GMT"})

        assert found is not None
        assert found.value == date(2022, 11, 4)
        assert found.confidence < 0.5

    def test_confidence_rises_with_the_quality_of_the_evidence(self) -> None:
        index = extract_publication_date(index_date=date(2022, 7, 28))
        header = extract_publication_date(
            headers={"Last-Modified": "Thu, 28 Jul 2022 11:00:00 GMT"}
        )

        assert index is not None
        assert header is not None
        assert index.confidence > header.confidence


class TestConfidenceIsExplicable:
    def test_every_candidate_is_kept_not_only_the_winner(self) -> None:
        """A confidence of 0.48 is a number a reviewer cannot act on. The losing candidates are
        what turn it into an argument they can check."""
        found = extract_publication_date(
            index_date=date(2022, 7, 28),
            metadata={"CreationDate": "D:20220803000000Z"},
        )

        assert found is not None
        assert len(found.candidates) == 2
        assert {c.evidence for c in found.candidates} == {
            DateEvidence.FILING_INDEX,
            DateEvidence.DOCUMENT_METADATA,
        }

    def test_disagreement_lowers_confidence(self) -> None:
        agreeing = extract_publication_date(index_date=date(2022, 7, 28))
        disputed = extract_publication_date(
            index_date=date(2022, 7, 28), metadata={"CreationDate": "D:20221103000000Z"}
        )

        assert agreeing is not None
        assert disputed is not None
        assert disputed.confidence < agreeing.confidence
        assert disputed.disputed
        assert not agreeing.disputed

    def test_a_days_difference_is_not_a_disagreement(self) -> None:
        """One day either side is a timezone, not two different events."""
        found = extract_publication_date(
            index_date=date(2022, 7, 28), headers={"Last-Modified": "Wed, 27 Jul 2022 23:00:00 GMT"}
        )

        assert found is not None
        assert not found.disputed

    def test_the_explanation_names_every_candidate(self) -> None:
        found = extract_publication_date(
            index_date=date(2022, 7, 28), metadata={"CreationDate": "D:20220803000000Z"}
        )
        assert found is not None

        explanation = found.explain()
        assert "2022-07-28" in explanation
        assert "2022-08-03" in explanation
        assert "filing_index" in explanation

    def test_the_raw_string_is_kept_so_a_parse_can_be_checked(self) -> None:
        found = extract_publication_date(text="Filed on 28 July 2022 with the Commission.")

        assert found is not None
        assert "28 July 2022" in found.chosen.raw


class TestTheConservativeBound:
    """``latest`` versus ``chosen``: both on the record, neither a verdict."""

    def test_latest_is_the_newest_candidate_not_the_chosen_one(self) -> None:
        found = extract_publication_date(
            index_date=date(2022, 7, 28), text="Published 3 September 2022"
        )

        assert found is not None
        assert found.value == date(2022, 7, 28)
        assert found.latest == date(2022, 9, 3)

    def test_neither_date_decides_admissibility(self) -> None:
        """The index says July and the text says September. Once, the bound decided the
        document's admissibility against the run's date; since ADR 0113 both dates are
        provenance, and the decision reads only that a date exists."""
        found = extract_publication_date(
            index_date=date(2022, 7, 28), text="Published 3 September 2022"
        )
        assert found is not None

        on_the_bound = decide_quarantine(
            publication_date=found.latest, source_tier=SourceTier.T1_REGULATORY
        )
        on_the_estimate = decide_quarantine(
            publication_date=found.value, source_tier=SourceTier.T1_REGULATORY
        )

        assert not on_the_bound.quarantined
        assert not on_the_estimate.quarantined


class TestParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2022-07-28", date(2022, 7, 28)),
            ("2022-07-28T16:05:00Z", date(2022, 7, 28)),
            ("2022-07-28T16:05:00+01:00", date(2022, 7, 28)),
        ],
    )
    def test_iso_metadata(self, raw: str, expected: date) -> None:
        assert from_metadata({"dcterms.date": raw}) == [
            DateCandidate(
                value=expected, evidence=DateEvidence.DOCUMENT_METADATA, raw=f"dcterms.date={raw}"
            )
        ]

    def test_a_pdf_document_info_date(self) -> None:
        found = from_metadata({"CreationDate": "D:20220728160500+01'00'"})

        assert [c.value for c in found] == [date(2022, 7, 28)]

    def test_an_rfc_2822_http_header(self) -> None:
        found = from_headers({"Last-Modified": "Thu, 28 Jul 2022 16:05:00 GMT"})

        assert [c.value for c in found] == [date(2022, 7, 28)]

    @pytest.mark.parametrize(
        "raw",
        [
            "Filed 28 July 2022",
            "Filed 28th July, 2022",
            "Filed July 28, 2022",
            "Filed Jul. 28 2022",
            "Filed 2022-07-28",
        ],
    )
    def test_dates_printed_in_prose(self, raw: str) -> None:
        found = from_text(raw)

        assert date(2022, 7, 28) in [c.value for c in found]

    def test_an_ambiguous_all_numeric_date_is_not_parsed(self) -> None:
        """``03/04/2022`` is 3 April to a UK filing and 4 March to a US one, and this platform
        reads both. A date that could be either is not evidence, and guessing would put a silent
        one-month error into the record."""
        assert from_text("Dated 03/04/2022 in the register.") == []

    def test_an_impossible_date_is_ignored_rather_than_raising(self) -> None:
        """A reference like ``2022-13-45`` appears in prose. Raising would turn an unremarkable
        filing into a failed extraction."""
        assert from_text("Reference 2022-13-45 refers.") == []

    def test_a_year_before_the_plausible_range_is_ignored(self) -> None:
        assert from_text("Founded 1 January 1886 in Ohio.") == []

    def test_unparseable_values_are_skipped_quietly(self) -> None:
        assert from_metadata({"dcterms.date": "not a date at all"}) == []
        assert from_headers({"Last-Modified": "yesterday"}) == []

    def test_a_key_that_is_not_a_date_field_is_ignored(self) -> None:
        assert from_metadata({"author": "2022-07-28"}) == []

    def test_only_the_start_of_a_document_is_read(self) -> None:
        """A publication date is on the cover. Reading a whole annual report would collect every
        period end in it and then have to choose between them."""
        buried = ("x" * 10_000) + " Published 28 July 2022"

        assert from_text(buried) == []

    def test_nothing_at_all_yields_none(self) -> None:
        assert extract_publication_date() is None
        assert choose([]) is None

    def test_a_date_after_the_retrieval_moment_is_discarded(self) -> None:
        """A document cannot have been published after it was fetched, so a "date" in the future
        is a misparse — a period end, a coupon date — and keeping it would put a date on the
        record that is not true."""
        found = extract_publication_date(
            text="Notes mature on 15 March 2031. Published 28 July 2022.",
            not_after=date(2022, 8, 1),
        )

        assert found is not None
        assert found.latest == date(2022, 7, 28)

    def test_duplicate_candidates_are_collapsed(self) -> None:
        found = extract_publication_date(
            metadata={"dcterms.date": "2022-07-28", "dc.date": "2022-07-28"}
        )

        assert found is not None
        assert len(found.candidates) == 1

    def test_ties_within_one_kind_of_evidence_take_the_earliest(self) -> None:
        """Metadata routinely carries a creation *and* a modification date, and the modification
        is a later edit of the same document. The newer date is still on the record as
        ``latest``, so nothing is lost by being sensible here."""
        found = extract_publication_date(
            metadata={"CreationDate": "D:20220728000000Z", "ModDate": "D:20220803000000Z"}
        )

        assert found is not None
        assert found.value == date(2022, 7, 28)
        assert found.latest == date(2022, 8, 3)


# -- Tiering ---------------------------------------------------------------------------------------


class TestTiering:
    def test_a_regulatory_filing_is_tier_one(self) -> None:
        assert (
            tier_for(Provider.SEC_EDGAR, DocumentKind.REGULATORY_FILING) is SourceTier.T1_REGULATORY
        )

    def test_an_issuer_publication_stays_tier_two_even_from_a_regulator(self) -> None:
        """The annual report attached to a filing was written by the company. The regulator
        hosting it does not audit it."""
        assert tier_for(Provider.SEC_EDGAR, DocumentKind.ISSUER_PUBLICATION) is SourceTier.T2_ISSUER

    def test_one_provider_spans_tiers_by_kind(self) -> None:
        """The reason the table is keyed on a pair. An issuer's annual report and its blog post
        come from the same domain and are not the same kind of evidence."""
        assert tier_for(Provider.ISSUER_IR, DocumentKind.ISSUER_PUBLICATION) is SourceTier.T2_ISSUER
        assert (
            tier_for(Provider.ISSUER_IR, DocumentKind.ISSUER_MARKETING) is SourceTier.T5_SECONDARY
        )

    def test_a_user_supplied_filing_is_not_promoted_for_being_supplied(self) -> None:
        """If it is a 10-K it should come from EDGAR, where its hash can be checked against the
        regulator's copy. A file on a desk has no such record behind it."""
        assert (
            tier_for(Provider.USER_SUPPLIED, DocumentKind.REGULATORY_FILING)
            is SourceTier.T5_SECONDARY
        )

    def test_an_unrecognised_pair_falls_to_the_bottom_tier(self) -> None:
        """The least favourable tier that fits, never the most. A new adapter that forgets to
        declare its kinds produces sources nobody can build a report on, loudly."""
        assert tier_for(Provider.FRED, DocumentKind.COMMENTARY) is SourceTier.T6_UNVERIFIED
        assert tier_for(Provider.EODHD, DocumentKind.UNKNOWN) is SourceTier.T6_UNVERIFIED

    def test_the_default_kind_is_unknown_and_therefore_uncitable(self) -> None:
        assert tier_for(Provider.WEB_SEARCH) is SourceTier.T6_UNVERIFIED

    def test_every_provider_has_at_least_one_entry(self) -> None:
        """So a provider cannot be added and silently left with no way to produce a citable
        source."""
        covered = {
            provider
            for provider in Provider
            for kind in DocumentKind
            if tier_for(provider, kind) is not SourceTier.T6_UNVERIFIED
        }

        # One documented exception: a prior run's own output exists precisely to have no
        # citable path (section 2.8 rule 4) — its absence from the tier map is the point,
        # and the citation verifier hard-rejects it besides.
        deliberately_uncitable = {Provider.INTERNAL_PRIOR_RUN}
        assert covered == set(Provider) - deliberately_uncitable

    def test_the_bottom_tier_is_not_citable(self) -> None:
        """The property the fallback relies on: falling back is safe only because a tier-6
        source cannot support a claim."""
        assert not SourceTier.T6_UNVERIFIED.is_citable


# -- Both checks, against the database -------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        http_user_agent="Tracework Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
    )


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    """A run with one drafted section, one archived filing, and one extracted excerpt.

    The same scene the citation tests use, built from the shared builder rather than imported as
    a fixture — pytest allows importing one and then reports a redefinition at every call site
    that names it.
    """
    return await build_scene(db_session, store)


async def _fresh_artefact(session: AsyncSession, tag: str) -> Any:
    """Distinct bytes for one recording under test.

    These tests each record a document and assert the quarantine decision that recording
    produced. They once shared the scene's artefact, which gap C4 now merges — one record
    per artefact per request — so a shared artefact answers with the scene's admissible
    document instead of the state under test.
    """
    payload = f"<html><body>dated {tag}</body></html>".encode()
    artefact = Artefact(
        sha256=hashlib.sha256(payload).hexdigest(),
        media_type="text/html",
        size_bytes=len(payload),
        storage_key=f"dated/{tag}",
    )
    session.add(artefact)
    await session.flush()
    return artefact


async def _cited_claim(session: AsyncSession, scene: dict[str, Any]) -> Citation:
    claim = await record_claim(
        session, section=scene["section"], kind=ClaimKind.FACTUAL, text="Revenue grew."
    )
    return await record_citation(
        session,
        claim=claim,
        source_document_id=scene["document"].id,
        extraction_id=scene["extraction"].id,
    )


async def _refusing_undated(session: AsyncSession, request: Any) -> WorkOrder:
    """This run's acquisition root, set to the strict datability policy.

    ADR 0111 made admitting an undated document the default, so a test about refusing one
    has to say so — which is the shape of the decision: the strict rule is still there and
    is chosen rather than inherited from a mode flag.
    """
    root = await acquisition_root(session, request)
    root.undated_sources_admissible = False
    await session.flush()
    return root


@pytest.mark.integration
class TestAtAcquisitionTime:
    """The first of the two checks. What can be decided when the bytes arrive."""

    async def test_an_undated_source_is_admitted_and_capped(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """ADR 0111, at the service. The run reads the page and caps what it may carry.

        Both halves in one assertion set, because they are one decision: the document is
        admissible, and the tier an evidence policy reads is 5 rather than the 1 its
        provider earned. Splitting them would let either half regress alone.
        """
        source = await record_source_document(
            db_session,
            work_order=await acquisition_root(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "undated-admitted"),
            url="https://example.invalid/undated-admitted.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert not source.quarantined
        assert source.is_admissible
        assert not source.is_dated
        assert source.source_tier is SourceTier.T1_REGULATORY
        assert source.evidence_tier is SourceTier.T5_SECONDARY
        assert not source.evidence_tier.is_primary

    async def test_an_undated_source_is_refused_where_the_run_says_so(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The other side of the same policy, and the reason it is a column."""
        source = await record_source_document(
            db_session,
            work_order=await _refusing_undated(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "undated-refused"),
            url="https://example.invalid/undated-refused.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        assert source.quarantined
        assert source.quarantine_reason == NO_PUBLICATION_DATE
        assert not source.is_admissible

    async def test_a_source_dated_after_the_run_is_recorded_and_admitted(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """ADR 0113 at the service: the date is written down, and nothing turns on it."""
        as_of = scene["request"].work_order.as_of_date
        found = extract_publication_date(index_date=as_of + timedelta(days=12))
        assert found is not None

        source = await record_source_document(
            db_session,
            work_order=await acquisition_root(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "late"),
            url="https://example.invalid/late.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            published=found,
        )

        assert not source.quarantined
        assert source.is_admissible
        assert source.publication_date == as_of + timedelta(days=12)

    async def test_the_candidates_are_stored_so_the_confidence_can_be_argued_with(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        found = extract_publication_date(
            index_date=date(2022, 5, 28), metadata={"CreationDate": "D:20220530000000Z"}
        )
        assert found is not None

        source = await record_source_document(
            db_session,
            work_order=await acquisition_root(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "report"),
            url="https://example.invalid/report.pdf",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            published=found,
        )

        assert source.publication_date == date(2022, 5, 28)
        assert source.publication_date_latest == date(2022, 5, 30)
        assert source.publication_date_source == DateEvidence.FILING_INDEX.value
        assert source.publication_date_candidates is not None
        assert len(source.publication_date_candidates) == 2

    async def test_the_service_records_the_bound_beside_the_estimate(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The index puts this document a month before the run's date and its own text a
        month after. Both survive the trip through the service, on their own columns, and
        the document is admitted: a reader can see the evidence disagrees, which is what the
        bound is for now that it decides nothing (ADR 0113).
        """
        as_of = scene["request"].work_order.as_of_date
        found = extract_publication_date(
            index_date=as_of - timedelta(days=30),
            text=f"Published {(as_of + timedelta(days=30)).strftime('%d %B %Y')}",
        )
        assert found is not None
        assert found.value < as_of
        assert found.latest > as_of

        source = await record_source_document(
            db_session,
            work_order=await acquisition_root(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "disputed"),
            url="https://example.invalid/disputed.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            published=found,
        )

        assert not source.quarantined
        assert source.publication_date == found.value
        assert source.publication_date_latest == found.latest

    async def test_an_admissible_source_is_not_quarantined(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        a_week_early = scene["request"].work_order.as_of_date - timedelta(days=7)
        found = extract_publication_date(index_date=a_week_early)
        assert found is not None

        source = await record_source_document(
            db_session,
            work_order=await acquisition_root(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "tenk"),
            url="https://example.invalid/10-k.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            published=found,
        )

        assert not source.quarantined
        assert source.is_admissible


@pytest.mark.integration
class TestTheOverride:
    async def test_an_override_makes_a_source_usable_without_clearing_the_flag(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Both facts stay on the record. Clearing the quarantine would erase the first, and a
        reader of the finished report would have no way to know a judgement had been made."""
        source = await record_source_document(
            db_session,
            work_order=await _refusing_undated(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "undated"),
            url="https://example.invalid/undated.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )
        assert source.quarantined
        assert not source.is_admissible

        await override_admissibility(
            db_session,
            source=source,
            actor=scene["user"],
            reason="Dated by hand from the covering letter.",
        )

        assert source.quarantined, "the override must not clear the flag"
        assert source.quarantine_reason == NO_PUBLICATION_DATE
        assert source.is_admissible
        assert source.admissibility_override_by_id == scene["user"].id
        assert source.admissibility_overridden_at is not None

    async def test_an_override_needs_a_reason(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        source = await record_source_document(
            db_session,
            work_order=await _refusing_undated(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "undated2"),
            url="https://example.invalid/undated2.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
        )

        with pytest.raises(ValidationError, match="records a click"):
            await override_admissibility(
                db_session, source=source, actor=scene["user"], reason="   "
            )

    async def test_a_source_that_was_never_refused_cannot_be_overridden(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        source = await record_source_document(
            db_session,
            work_order=await acquisition_root(db_session, scene["request"]),
            artefact=await _fresh_artefact(db_session, "fine"),
            url="https://example.invalid/fine.htm",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=scene["request"].work_order.as_of_date - timedelta(days=7),
        )
        assert not source.quarantined

        with pytest.raises(ConflictError, match="nothing to override"):
            await override_admissibility(
                db_session, source=source, actor=scene["user"], reason="Because."
            )


@pytest.mark.integration
class TestAtClaimTime:
    """The second check reads the first's verdict.

    Acquisition screens what it fetches and writes the decision on the row. A claim made
    later cannot rest on a document that decision refused, and the verifier says so before
    it re-reads a word of the text.
    """

    async def test_a_citation_on_a_quarantined_source_fails(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        citation = await _cited_claim(db_session, scene)
        scene["document"].quarantined = True
        scene["document"].quarantine_reason = NO_PUBLICATION_DATE
        await db_session.flush()

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
        assert "quarantined" in (outcome.reason or "")

    async def test_a_recorded_override_lets_a_quarantined_source_be_cited(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """ "Usable only after a recorded override", stated as the test for it."""
        citation = await _cited_claim(db_session, scene)
        scene["document"].quarantined = True
        scene["document"].quarantine_reason = NO_PUBLICATION_DATE
        await db_session.flush()

        refused = await verify(db_session, scene["store"], citation=citation, settings=settings)
        assert refused.failed

        await override_admissibility(
            db_session,
            source=scene["document"],
            actor=scene["user"],
            reason="Dated from the covering letter.",
        )

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.verified

    async def test_a_source_dated_after_the_run_is_still_citable(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """ADR 0113 at claim time. The check that once compared the source's latest date
        against the run's is gone, and a citation on a document dated after the run stands
        or falls on its excerpt alone."""
        as_of = scene["request"].work_order.as_of_date
        scene["document"].publication_date_latest = as_of + timedelta(days=90)
        await db_session.flush()

        citation = await _cited_claim(db_session, scene)
        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.verified
