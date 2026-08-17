"""Inline XBRL: the numbers inside a UK filing, and the gate for the ones nobody can map.

Two things under test, and the first is a security control rather than a feature.

**arelle must not reach the network.** An iXBRL document names its taxonomy by URL, and
arelle's default is to go and fetch it — a component other than :mod:`aer.fetch` making an
outbound request, driven by a URL inside an untrusted document, past every allowlist in the
platform. :class:`TestItNeverReachesTheNetwork` asserts that loading a document naming a remote
schema opens no socket, which is the whole reason ``workOffline`` is set before any load.

**The confirmation gate is the phase's named risk, mitigated.** UK filers extend the taxonomy
routinely. An extension element carrying the company's headline profit measure is not something
to map by guessing, so a filing containing one is reported as needing a person rather than
extracted silently and half-understood.
"""

from __future__ import annotations

import socket
from datetime import date
from decimal import Decimal

import pytest

from aer.core.concepts import CANONICAL_CONCEPTS, IFRS_ALIASES, UK_FRC_ALIASES, canonical_concept
from aer.extract.errors import ParseFailedError, UnextractableError
from aer.extract.ixbrl import EXTRACTOR, VERSION, IxbrlFact, extract_ixbrl
from tests.ixbrl_fixtures import (
    CLEAN_IFRS,
    CLEAN_IFRS_TRUTH,
    EXTENSION_TAG,
    NOT_TAGGED,
    PERIOD_END,
    PERIOD_START,
    REMOTE_TAXONOMY_ONLY,
    SEGMENT_AXIS,
    SEGMENT_TRUTH,
    TAXONOMY_URL,
    WITH_EXTENSION,
    WITH_SEGMENTS,
)


class TestItNeverReachesTheNetwork:
    """The control. Everything else in this module is a feature; this is an invariant.

    Only ``aer.fetch`` makes outbound requests. arelle would happily make one on behalf of a
    document it was handed, which is both a bypass of the allowlist and an SSRF vector whose
    target is chosen by the filing.
    """

    def test_loading_a_document_naming_a_remote_taxonomy_opens_no_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fixture names a real IFRS schema URL. If ``workOffline`` were not set before the
        load, arelle would fetch it — and this test would see the connection."""
        attempts: list[object] = []

        def refuse(self: socket.socket, address: object) -> None:
            attempts.append(address)
            message = "arelle attempted a network connection"
            raise OSError(message)

        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(socket.socket, "connect_ex", refuse)

        extraction = extract_ixbrl(REMOTE_TAXONOMY_ONLY)

        assert attempts == [], f"arelle tried to reach {attempts}"
        assert extraction.facts, "and it still extracted the facts"

    def test_the_taxonomy_url_is_in_the_fixture(self) -> None:
        """So the test above is not vacuously passing against a document with nothing to fetch."""
        assert TAXONOMY_URL.encode() in REMOTE_TAXONOMY_ONLY
        assert TAXONOMY_URL.startswith("https://")


class TestExtractingFacts:
    def test_every_tagged_figure_matches_the_truth_set(self) -> None:
        """Concept for concept and penny for penny, against what the fixture states."""
        extraction = extract_ixbrl(CLEAN_IFRS)

        found = {fact.concept: int(fact.value) for fact in extraction.facts if fact.concept}
        assert found == CLEAN_IFRS_TRUTH

    def test_the_documents_own_scale_is_applied(self) -> None:
        """A UK report states figures in thousands and tags ``scale="3"``. The stored number is
        pounds. Getting this wrong is a thousandfold error that looks entirely plausible."""
        extraction = extract_ixbrl(CLEAN_IFRS)

        revenue = next(f for f in extraction.facts if f.concept == "revenue")
        assert revenue.value == Decimal("198270000")

    def test_a_duration_fact_carries_the_period_the_accounts_print(self) -> None:
        """**arelle reports an exclusive end.** A year ending 30 June comes back as 1 July, and
        storing that would move every UK fiscal year end by a day — an error that survives
        review because it looks almost right."""
        extraction = extract_ixbrl(CLEAN_IFRS)

        revenue = next(f for f in extraction.facts if f.concept == "revenue")
        assert revenue.period_start == PERIOD_START
        assert revenue.period_end == PERIOD_END

    def test_an_instant_fact_has_no_start_date(self) -> None:
        """A balance sheet line is a fact about a moment. Giving it a start would make it look
        like a flow."""
        extraction = extract_ixbrl(CLEAN_IFRS)

        assets = next(f for f in extraction.facts if f.concept == "assets")
        assert assets.period_start is None
        assert assets.period_end == PERIOD_END

    def test_the_unit_is_carried(self) -> None:
        extraction = extract_ixbrl(CLEAN_IFRS)

        assert {fact.unit for fact in extraction.facts} == {"GBP"}

    def test_the_entity_identifier_ties_facts_to_a_company(self) -> None:
        """The Companies House number, out of the document itself, so a fact is tied to the
        company it is about without trusting a filename."""
        extraction = extract_ixbrl(CLEAN_IFRS)

        assert {fact.entity_identifier for fact in extraction.facts} == {"01234567"}
        assert all(
            fact.entity_scheme == "http://www.companieshouse.gov.uk/" for fact in extraction.facts
        )

    def test_decimals_are_kept(self) -> None:
        extraction = extract_ixbrl(CLEAN_IFRS)

        assert all(fact.decimals == -3 for fact in extraction.facts)

    def test_the_extractor_names_itself_and_its_version(self) -> None:
        assert EXTRACTOR == "ixbrl"
        assert VERSION


class TestTheConfirmationGate:
    """The mitigation for this phase's named risk: UK taxonomy variability."""

    def test_a_filing_using_only_shared_taxonomies_needs_no_confirmation(self) -> None:
        """The half that keeps the gate meaningful. A gate that fired on every filing would be
        a badge nobody reads."""
        extraction = extract_ixbrl(CLEAN_IFRS)

        assert not extraction.needs_confirmation
        assert extraction.unmapped_tags == ()

    def test_a_filer_extension_raises_the_gate(self) -> None:
        extraction = extract_ixbrl(WITH_EXTENSION)

        assert extraction.needs_confirmation
        assert extraction.unmapped_tags == (EXTENSION_TAG,)

    def test_the_extension_is_extracted_rather_than_dropped(self) -> None:
        """Kept under its raw tag, like the SEC parser does. Discarding it would lose real data
        and leave no trace that a number was there at all."""
        extraction = extract_ixbrl(WITH_EXTENSION)

        adjusted = next(f for f in extraction.facts if f.qname == EXTENSION_TAG)
        assert adjusted.value == Decimal("91204000")
        assert adjusted.concept is None

    def test_the_mapped_facts_are_usable_without_a_decision(self) -> None:
        """An extension does not poison the filing. The standard tags beside it are still
        standard, and a reviewer is being asked about one element rather than about the whole
        document."""
        extraction = extract_ixbrl(WITH_EXTENSION)

        assert {f.concept for f in extraction.mapped_facts} == {"revenue", "operating_income"}
        assert len(extraction.mapped_facts) < len(extraction.facts)

    def test_the_gate_is_on_tags_not_on_a_count(self) -> None:
        """One extension raises it. The reason is that only a person can tell whether the
        unmapped element is the company's headline profit measure or a segment breakdown nobody
        asked for, and a threshold would decide that by arithmetic."""
        extraction = extract_ixbrl(WITH_EXTENSION)

        assert len(extraction.unmapped_tags) == 1
        assert extraction.needs_confirmation


class TestDimensions:
    """A segment's revenue must never look like the company's.

    The first version of the extractor read no dimensions at all, so a dimensioned fact
    entered the fact set indistinguishable from the consolidated line — and anything
    downstream choosing "the" revenue for a period could pick one segment's slice.
    """

    def test_a_segment_fact_carries_its_axis_and_member(self) -> None:
        extraction = extract_ixbrl(WITH_SEGMENTS)

        dimensioned = [fact for fact in extraction.facts if fact.is_dimensioned]
        assert {fact.dimensions[0][0] for fact in dimensioned} == {SEGMENT_AXIS}
        assert {fact.dimensions[0][1]: int(fact.value) for fact in dimensioned} == SEGMENT_TRUTH

    def test_the_consolidated_figure_carries_none(self) -> None:
        extraction = extract_ixbrl(WITH_SEGMENTS)

        consolidated = [fact for fact in extraction.facts if not fact.is_dimensioned]
        assert len(consolidated) == 1
        assert int(consolidated[0].value) == CLEAN_IFRS_TRUTH["revenue"]

    def test_the_segment_facts_share_the_consolidated_concept(self) -> None:
        """The tag is the same ``Revenue`` element; only the context differs. What keeps
        them apart is the dimension, which is the whole reason it is captured."""
        extraction = extract_ixbrl(WITH_SEGMENTS)

        assert {fact.concept for fact in extraction.facts} == {"revenue"}


class TestWhatCannotBeRead:
    def test_an_untagged_document_is_reported_rather_than_returned_empty(self) -> None:
        """A UK annual report that tags nothing is a real thing. Its readable text still
        extracts through the HTML extractor, which the message says."""
        with pytest.raises(UnextractableError, match="no inline XBRL facts") as raised:
            extract_ixbrl(NOT_TAGGED)

        assert "HTML extractor" in str(raised.value)

    def test_bytes_that_are_not_a_document_fail_rather_than_returning_nothing(self) -> None:
        with pytest.raises((ParseFailedError, UnextractableError)):
            extract_ixbrl(b"\x00\x01\x02 not markup at all")

    def test_a_truncated_document_does_not_yield_half_a_fact_set(self) -> None:
        """Either it reads or it raises. Returning the facts it managed before the truncation
        would put a partial fact set in front of a reviewer with nothing saying it was partial."""
        try:
            extraction = extract_ixbrl(CLEAN_IFRS[: len(CLEAN_IFRS) // 2])
        except (ParseFailedError, UnextractableError):
            return
        assert extraction.facts


class TestTheUkVocabulary:
    """The alias tables, checked for the mistakes that put a number on the wrong line."""

    def test_every_ifrs_alias_targets_a_real_canonical_concept(self) -> None:
        """A typo here creates a concept nothing else knows about, and the fact carrying it
        silently stops being comparable with its US equivalent."""
        wrong = {tag: name for tag, name in IFRS_ALIASES.items() if name not in CANONICAL_CONCEPTS}

        assert wrong == {}

    def test_every_frc_alias_targets_a_real_canonical_concept(self) -> None:
        wrong = {
            tag: name for tag, name in UK_FRC_ALIASES.items() if name not in CANONICAL_CONCEPTS
        }

        assert wrong == {}

    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("Revenue", "revenue"),
            ("ProfitLossFromOperatingActivities", "operating_income"),
            ("FinanceCosts", "interest_expense"),
            ("CashFlowsFromUsedInOperatingActivities", "operating_cash_flow"),
            ("ProfitLoss", "net_income"),
        ],
    )
    def test_ifrs_names_map_to_the_concept_whose_definition_matches(
        self, tag: str, expected: str
    ) -> None:
        """IFRS names several concepts differently from US GAAP rather than merely spelling
        them differently. ``FinanceCosts`` is the interest line; mapping on the closest-looking
        words rather than the definition puts a real number on the wrong line."""
        assert canonical_concept("ifrs-full", tag) == expected

    def test_a_filer_extension_namespace_maps_to_nothing(self) -> None:
        assert canonical_concept("acme", "AdjustedEBITDAPreExceptionalItems") is None

    def test_the_us_vocabulary_still_works(self) -> None:
        """The change that made room for IFRS must not have moved the US mappings."""
        assert canonical_concept("us-gaap", "Revenues") == "revenue"
        assert canonical_concept("us-gaap", "OperatingIncomeLoss") == "operating_income"

    def test_taxonomy_matching_is_case_insensitive_and_trimmed(self) -> None:
        assert canonical_concept(" IFRS-Full ", "Revenue") == "revenue"


class TestTheFactType:
    def test_qname_joins_the_taxonomy_and_the_tag(self) -> None:
        fact = IxbrlFact(
            taxonomy="ifrs-full",
            tag="Revenue",
            value=Decimal("1"),
            unit="GBP",
            period_end=date(2022, 6, 30),
        )

        assert fact.qname == "ifrs-full:Revenue"
        assert fact.concept == "revenue"
        assert fact.is_mapped
