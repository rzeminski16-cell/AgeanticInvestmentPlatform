"""The fact schema and the canonical concept vocabulary.

Pure core: no I/O, no clock, no database. The tests are correspondingly cheap, and the
one that matters most is the consistency check — an alias pointing at a concept that does
not exist would produce facts nothing downstream could ever query for, and nothing would
raise.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from aer.core.concepts import (
    CANONICAL_CONCEPTS,
    US_GAAP_ALIASES,
    canonical_concept,
    is_canonical_concept,
)
from aer.core.schemas.facts import RawFact, format_accession
from tests.sec_fixtures import make_fact


class TestTheConceptVocabulary:
    def test_every_alias_points_at_a_concept_that_exists(self):
        # The check that stops a typo becoming a silent data gap: a fact tagged with a
        # concept nobody queries for is a fact nobody ever sees again.
        unknown = {
            target for target in US_GAAP_ALIASES.values() if target not in CANONICAL_CONCEPTS
        }

        assert unknown == set()

    def test_every_canonical_concept_has_at_least_one_alias(self):
        # A concept with no tag mapping onto it can never be populated from EDGAR, which
        # makes it vocabulary that does nothing.
        unreachable = CANONICAL_CONCEPTS - set(US_GAAP_ALIASES.values())

        assert unreachable == set()

    @pytest.mark.parametrize(
        "tag",
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
    )
    def test_the_three_revenue_tags_map_to_one_concept(self, tag):
        # ASC 606 replaced the revenue tags in 2018, so filings either side of it use
        # different names for the same line.
        assert canonical_concept("us-gaap", tag) == "revenue"

    def test_including_and_excluding_assessed_tax_are_both_mapped_deliberately(self):
        # They differ by one word and by whether sales tax is in the number. Both are
        # mapped, and neither is inferred from the spelling -- a fuzzy match would be
        # wrong in a way nobody would notice until the figures were in a report.
        assert "RevenueFromContractWithCustomerIncludingAssessedTax" in US_GAAP_ALIASES

    def test_an_unknown_tag_is_unmapped_rather_than_guessed(self):
        assert canonical_concept("us-gaap", "SomeTagInventedNextYear") is None

    def test_a_filer_extension_namespace_is_never_mapped(self):
        # A tag in a filer's own namespace is by definition not in a shared taxonomy, so
        # there is nothing to map it onto -- even if it happens to share a name.
        assert canonical_concept("msft", "Revenues") is None

    def test_a_dei_tag_maps(self):
        assert (
            canonical_concept("dei", "EntityCommonStockSharesOutstanding") == "shares_outstanding"
        )

    def test_is_canonical_concept_recognises_the_vocabulary(self):
        assert is_canonical_concept("revenue")
        assert not is_canonical_concept("Revenues")


class TestAccessionNumbers:
    @pytest.mark.parametrize(
        "value", ["0000789019-20-000039", "000078901920000039", " 0000789019-20-000039 "]
    )
    def test_either_form_normalises_to_the_dashed_one(self, value):
        # The API uses dashes and the archive URLs do not. A caller holding one should not
        # have to know which.
        assert format_accession(value) == "0000789019-20-000039"

    @pytest.mark.parametrize("value", ["nonsense", "0000789019-20", "", "0000789019-20-00003X"])
    def test_anything_else_is_refused(self, value):
        with pytest.raises(ValueError, match="not an EDGAR accession number"):
            format_accession(value)


class TestRawFact:
    def test_a_fact_is_frozen(self):
        # Deduplicating facts across overlapping filings is a set operation, and it is
        # much harder to get wrong when the elements cannot be edited underneath it.
        fact = make_fact()

        with pytest.raises(ValueError, match="frozen"):
            fact.value = Decimal("1")  # type: ignore[misc]

    def test_a_backwards_period_is_refused(self):
        # A period that runs backwards is a parsing error, not a fact.
        with pytest.raises(ValueError, match="runs backwards"):
            RawFact(
                concept="revenue",
                raw_concept="Revenues",
                taxonomy="us-gaap",
                unit="USD",
                value=Decimal("1"),
                period_start=date(2021, 1, 1),
                period_end=date(2020, 1, 1),
                form="10-K",
                accession="0000789019-20-000039",
                filed_date=date(2021, 1, 1),
            )

    def test_a_malformed_accession_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="accession"):
            make_fact(accession="not-an-accession")

    def test_an_unknown_field_is_refused(self):
        # extra="forbid". A typo in a keyword would otherwise be silently discarded, and
        # the fact would carry a default nobody chose.
        with pytest.raises(ValueError, match="Extra inputs"):
            RawFact(
                concept="revenue",
                raw_concept="Revenues",
                taxonomy="us-gaap",
                unit="USD",
                value=Decimal("1"),
                period_end=date(2020, 6, 30),
                form="10-K",
                accession="0000789019-20-000039",
                filed_date=date(2021, 1, 1),
                filed="2021-01-01",
            )

    def test_the_period_key_is_what_makes_two_facts_rivals(self):
        original = make_fact(value=1, filed="2020-07-30")
        restatement = make_fact(value=2, filed="2022-07-28")

        assert original.period_key == restatement.period_key

    def test_a_different_unit_is_a_different_key(self):
        assert make_fact(unit="USD").period_key != make_fact(unit="shares").period_key

    def test_is_canonical_reflects_the_alias_table(self):
        assert make_fact(raw_concept="Revenues").is_canonical is True
        assert make_fact(raw_concept="SomethingCustom").is_canonical is False

    def test_a_fact_with_no_start_is_an_instant(self):
        assert make_fact(period_start=None).is_instant is True
        assert make_fact().is_instant is False

    def test_a_fact_reads_legibly(self):
        # The repr ends up in log lines and assertion failures; a fact whose string form
        # omits the filed date hides the one field that decides admissibility.
        rendered = str(make_fact(value=143015000000))

        assert "revenue=143015000000 USD" in rendered
        assert "filed 2021-01-01" in rendered
