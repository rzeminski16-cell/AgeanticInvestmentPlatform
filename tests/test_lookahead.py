"""Look-ahead bias: dates extracted rather than trusted, and checked twice.

Threat T13, and the quietest failure in the platform. A report citing a document published after
its own as-of date reads exactly like one that does not; nothing in the prose gives it away. So
the target in §2.10 is **100% recall on the planted corpus**, and it is asserted here against
:mod:`tests.lookahead_fixtures`, where the answer for each document is written down in advance.

**Both halves of the corpus matter.** A system that quarantined everything would score 100% on
:data:`~tests.lookahead_fixtures.POST_DATED` and be worthless, because it would also refuse the
filings a report is made of. :data:`~tests.lookahead_fixtures.ADMISSIBLE` is what stops that
being the answer, and it includes the boundary — published *on* the as-of date is admissible, and
an off-by-one there rejects a whole quarter of real filings.

**The check runs twice, and both are tested separately.** Once at acquisition, where the source
is recorded, and once at claim time, where a citation is verified. The two know different things:
acquisition cannot know what a claim will later rest on, and cannot see an as-of date that moves
afterwards.
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
    PUBLISHED_AFTER_AS_OF,
    decide_quarantine,
    override_admissibility,
    record_source_document,
)
from aer.sources.tiering import DocumentKind, tier_for
from aer.storage.local import LocalArtefactStore
from aer.verify.citations import verify
from tests.lookahead_fixtures import ADMISSIBLE, AS_OF, POST_DATED, UNDATABLE, Planted
from tests.scene_fixtures import build_scene


def _extract(case: Planted) -> PublicationDate | None:
    return extract_publication_date(
        index_date=case.index_date,
        metadata=case.metadata,
        text=case.text,
        headers=case.headers,
    )


# -- Recall on the planted corpus ------------------------------------------------------------------


class TestTheCorpus:
    """The numbers §2.10 asks for, as tests rather than as a claim."""

    @pytest.mark.parametrize("case", POST_DATED, ids=lambda c: c.name)
    def test_every_post_dated_document_is_dated_correctly(self, case: Planted) -> None:
        found = _extract(case)

        assert found is not None, "no date could be established at all"
        assert found.value == case.expected

    @pytest.mark.parametrize("case", POST_DATED, ids=lambda c: c.name)
    def test_every_post_dated_document_is_refused(self, case: Planted) -> None:
        """Recall, stated as the test that decides it. Five documents, five mechanisms, no
        exceptions."""
        found = _extract(case)
        assert found is not None

        decision = decide_quarantine(
            publication_date=found.latest,
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
            as_of_date=AS_OF,
        )

        assert decision.quarantined
        assert decision.reason == PUBLISHED_AFTER_AS_OF

    @pytest.mark.parametrize("case", ADMISSIBLE, ids=lambda c: c.name)
    def test_every_admissible_document_is_admitted(self, case: Planted) -> None:
        """The half that keeps the rule honest: refusing everything would score perfectly on the
        planted half and refuse the filings a report is made of."""
        found = _extract(case)
        assert found is not None
        assert found.value == case.expected

        decision = decide_quarantine(
            publication_date=found.latest,
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
            as_of_date=AS_OF,
        )

        assert not decision.quarantined, f"{case.name} was refused: {decision.reason}"

    @pytest.mark.parametrize("case", UNDATABLE, ids=lambda c: c.name)
    def test_an_undatable_document_yields_no_date(self, case: Planted) -> None:
        """``None`` rather than a guess. "Undatable" and "probably July" need different
        responses, and a parser that produced the second for the first would hide the problem."""
        assert _extract(case) is None

    @pytest.mark.parametrize("case", UNDATABLE, ids=lambda c: c.name)
    def test_an_undatable_document_is_quarantined_under_point_in_time(self, case: Planted) -> None:
        decision = decide_quarantine(
            publication_date=None,
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
            as_of_date=AS_OF,
        )

        assert decision.quarantined
        assert decision.reason == NO_PUBLICATION_DATE
        assert _extract(case) is None

    @pytest.mark.parametrize("case", UNDATABLE, ids=lambda c: c.name)
    def test_an_undatable_document_is_fine_when_point_in_time_is_off(self, case: Planted) -> None:
        """The rule is point-in-time's, not a general one. With it off, an undated document is
        just an undated document."""
        decision = decide_quarantine(
            publication_date=None,
            point_in_time=False,
            source_tier=SourceTier.T1_REGULATORY,
            as_of_date=AS_OF,
        )

        assert not decision.quarantined
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
        dropping the evidence would quarantine it for no reason."""
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
    """``latest`` versus ``chosen``, which is the whole reason both exist."""

    def test_latest_is_the_newest_candidate_not_the_chosen_one(self) -> None:
        found = extract_publication_date(
            index_date=date(2022, 7, 28), text="Published 3 September 2022"
        )

        assert found is not None
        assert found.value == date(2022, 7, 28)
        assert found.latest == date(2022, 9, 3)

    def test_a_document_with_any_later_evidence_is_refused(self) -> None:
        """The index says July and the text says September. The honest answer to "can this be
        shown to predate 31 July?" is no, and admitting it is exactly the mistake the rule
        exists to prevent."""
        found = extract_publication_date(
            index_date=date(2022, 7, 28), text="Published 3 September 2022"
        )
        assert found is not None

        decision = decide_quarantine(
            publication_date=found.latest,
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
            as_of_date=AS_OF,
        )

        assert decision.quarantined
        assert decision.reason == PUBLISHED_AFTER_AS_OF

    def test_the_same_document_is_admitted_on_the_best_estimate_alone(self) -> None:
        """Stated as its own test because it is what the conservative rule is *costing*: judged
        on the best estimate this document would be let through. That is the trade, and it is
        made deliberately — see the module docstring in ``aer.extract.dates``."""
        found = extract_publication_date(
            index_date=date(2022, 7, 28), text="Published 3 September 2022"
        )
        assert found is not None

        lenient = decide_quarantine(
            publication_date=found.value,
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
            as_of_date=AS_OF,
        )

        assert not lenient.quarantined


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
        one-month error into the look-ahead check."""
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
        is a misparse — a period end, a coupon date — and keeping it would quarantine the
        document for a reason that is not true."""
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
        is a later edit of the same document. The conservative direction is handled by
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
    payload = f"<html><body>lookahead {tag}</body></html>".encode()
    artefact = Artefact(
        sha256=hashlib.sha256(payload).hexdigest(),
        media_type="text/html",
        size_bytes=len(payload),
        storage_key=f"lookahead/{tag}",
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


@pytest.mark.integration
class TestAtAcquisitionTime:
    """The first of the two checks. What can be decided when the bytes arrive."""

    async def test_a_post_dated_source_is_quarantined_when_recorded(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        found = extract_publication_date(index_date=date(2022, 8, 12))
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

        assert source.quarantined
        assert source.quarantine_reason == PUBLISHED_AFTER_AS_OF
        assert not source.is_admissible

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

    async def test_the_service_quarantines_on_the_latest_date_not_the_estimate(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The conservative bound has to survive the trip through the service.

        The index puts this document comfortably before the as-of date and its own text puts it
        after. Judged on the best estimate it is admitted; judged on the bound it is refused, and
        the bound is what the rule is for. Asserted here rather than only against
        ``decide_quarantine``, because the service is what chooses which of the two to pass.
        """
        as_of = scene["request"].as_of_date
        found = extract_publication_date(
            index_date=as_of - timedelta(days=30),
            text=f"Published {(as_of + timedelta(days=30)).strftime('%d %B %Y')}",
        )
        assert found is not None
        assert found.value < as_of, "the estimate should be admissible on its own"
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

        assert source.quarantined
        assert source.quarantine_reason == PUBLISHED_AFTER_AS_OF

    async def test_an_admissible_source_is_not_quarantined(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        a_week_early = scene["request"].as_of_date - timedelta(days=7)
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
            work_order=await acquisition_root(db_session, scene["request"]),
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
            work_order=await acquisition_root(db_session, scene["request"]),
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
            publication_date=scene["request"].as_of_date - timedelta(days=7),
        )
        assert not source.quarantined

        with pytest.raises(ConflictError, match="nothing to override"):
            await override_admissibility(
                db_session, source=source, actor=scene["user"], reason="Because."
            )


@pytest.mark.integration
class TestAtClaimTime:
    """The second check, and the reason there are two.

    Acquisition screens what it fetches. It cannot know what a claim will later rest on, and it
    cannot see an as-of date that moves after the fetch. Every test here starts from a source
    that **passed** acquisition and is inadmissible by the time a claim is made.
    """

    async def test_a_citation_on_a_source_published_after_the_as_of_date_fails(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        citation = await _cited_claim(db_session, scene)
        scene["document"].publication_date_latest = scene["request"].as_of_date + timedelta(days=10)
        await db_session.flush()

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
        assert citation.excerpt_verified is False
        assert "after the run's as-of date" in (outcome.reason or "")

    async def test_moving_the_as_of_date_earlier_invalidates_a_citation(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """**The case acquisition cannot catch.** The document was admissible when it was
        fetched. The operator then moved the as-of date back, and the same citation is now
        resting on information nobody had — which only this check can see."""
        scene["document"].publication_date = date(2022, 6, 1)
        scene["document"].publication_date_latest = date(2022, 6, 1)
        await db_session.flush()

        citation = await _cited_claim(db_session, scene)
        passing = await verify(db_session, scene["store"], citation=citation, settings=settings)
        assert passing.verified

        # On the work order, which is where a run's clock lives since ADR 0072. The
        # mandate carries a copy for one more revision and nothing reads it: two answers to
        # "what date is this run dated to" is exactly what moving the clock avoided.
        work_order = await db_session.get(WorkOrder, scene["request"].id)
        work_order.as_of_date = date(2022, 5, 1)
        await db_session.flush()

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
        assert citation.excerpt_verified is False

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

    async def test_the_check_is_skipped_when_point_in_time_is_off(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The rule belongs to point-in-time mode. With it off, a recent document is just a
        recent document."""
        work_order = await db_session.get(WorkOrder, scene["request"].id)
        work_order.point_in_time = False
        scene["document"].publication_date_latest = scene["request"].as_of_date + timedelta(days=90)
        await db_session.flush()

        citation = await _cited_claim(db_session, scene)
        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.verified

    async def test_a_source_published_on_the_as_of_date_is_still_citable(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The boundary, at claim time as well as at acquisition."""
        scene["document"].publication_date_latest = scene["request"].as_of_date
        await db_session.flush()

        citation = await _cited_claim(db_session, scene)
        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.verified

    async def test_the_latest_date_decides_not_the_best_estimate(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The conservative bound is what the check reads, here as at acquisition."""
        scene["document"].publication_date = date(2022, 6, 1)
        scene["document"].publication_date_latest = scene["request"].as_of_date + timedelta(days=30)
        await db_session.flush()

        citation = await _cited_claim(db_session, scene)
        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
