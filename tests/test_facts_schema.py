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
    IFRS_ALIASES,
    MAGNITUDE_CONCEPTS,
    UK_FRC_ALIASES,
    US_GAAP_ALIASES,
    canonical_concept,
    is_canonical_concept,
    is_magnitude,
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

    def test_the_vocabulary_is_the_sixty_the_plan_asks_for(self):
        # docs/PLAN.md names the concept long tail as Phase 3's main risk and prescribes the
        # top sixty plus a visible "unmapped" surface rather than chasing completeness. A
        # count is a crude assertion, and it is the one that notices the list shrinking.
        assert len(CANONICAL_CONCEPTS) >= 60

    def test_every_alias_table_points_only_at_concepts_that_exist(self):
        targets = (
            set(US_GAAP_ALIASES.values())
            | set(IFRS_ALIASES.values())
            | set(UK_FRC_ALIASES.values())
        )

        assert targets <= CANONICAL_CONCEPTS

    def test_the_concepts_with_no_ifrs_tag_are_a_recorded_decision(self):
        """Seven concepts have a us-gaap tag and no IFRS one, and that is deliberate.

        ``ifrs-full`` has no element this project is confident means "restructuring costs",
        "preferred dividends" or "change in working capital" — the nearest candidates are
        provisions and adjustment lines that mean something adjacent. The module's own rule
        is that unmapped-and-visible beats wrongly-mapped, so they are absent. The four
        bank captions (gap A62) joined with us-gaap tags only: an IFRS bank's income
        statement uses its own interest-revenue elements, and mapping them is a
        determination for a UK or European bank run to force, not a guess to make now.
        Pinned here so that closing any of these is a decision somebody takes rather
        than a diff nobody reads, and so that the list not *growing* is noticed.
        """
        assert CANONICAL_CONCEPTS - set(IFRS_ALIASES.values()) == {
            "change_in_working_capital",
            "interest_and_dividend_income",
            "net_interest_income",
            "noninterest_income",
            "preferred_dividends",
            "provision_for_credit_losses",
            "restructuring_costs",
        }

    def test_a_subtotal_that_means_something_else_is_left_unmapped(self):
        """`TotalAssetsLessCurrentLiabilities` is not non-current assets, and must not become it.

        The Companies Act format's subtotal is fixed assets plus current assets less
        creditors falling due within one year. Mapped to the nearest-looking concept it would
        put a figure wrong by (current assets less current liabilities) onto a balance sheet
        that still appeared to balance.
        """
        assert canonical_concept("uk-core", "TotalAssetsLessCurrentLiabilities") is None

    def test_the_two_variants_of_the_cash_movement_are_not_conflated(self):
        """One includes the currency effect and one does not. Only the first is mapped.

        Mapping both onto ``net_change_in_cash`` would make the cash-flow roll-forward hold
        for a filer with no foreign cash and fail for an otherwise identical one that has
        some — an error correlated with exactly the companies it matters most for.
        """
        including = (
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
            "PeriodIncreaseDecreaseIncludingExchangeRateEffect"
        )
        excluding = including.replace("Including", "Excluding")

        assert canonical_concept("us-gaap", including) == "net_change_in_cash"
        assert canonical_concept("us-gaap", excluding) is None
        assert (
            canonical_concept("ifrs-full", "IncreaseDecreaseInCashAndCashEquivalents")
            == "net_change_in_cash"
        )
        assert (
            canonical_concept(
                "ifrs-full",
                "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges",
            )
            is None
        )

    def test_a_split_out_expense_line_is_absent_rather_than_double_counted(self):
        # A filer tagging selling and administrative expenses separately has reported two
        # components, not SG&A. Mapping both would give two facts claiming to be the same
        # concept for one period, and the disagreement ladder would have to arbitrate
        # between two halves of a total.
        assert canonical_concept("us-gaap", "SellingGeneralAndAdministrativeExpense") == "sg_and_a"
        assert canonical_concept("us-gaap", "SellingAndMarketingExpense") is None
        assert canonical_concept("us-gaap", "GeneralAndAdministrativeExpense") is None


class TestTheSignConvention:
    """The trap that makes free cash flow twice the right number if it is got wrong."""

    def test_capital_expenditure_is_a_magnitude(self):
        # Tagged `PaymentsToAcquirePropertyPlantAndEquipment` and reported positive: it is a
        # payment of that size, not a negative cash flow. So free cash flow subtracts it.
        assert is_magnitude("capital_expenditure")

    def test_operating_cash_flow_is_not(self):
        assert not is_magnitude("operating_cash_flow")

    def test_every_magnitude_concept_is_in_the_vocabulary(self):
        assert MAGNITUDE_CONCEPTS <= CANONICAL_CONCEPTS

    def test_the_outflow_concepts_are_all_declared(self):
        # Every cash-flow concept whose tag names a payment. A new one added to the
        # vocabulary and forgotten here is a sign error waiting for a capital-intensive
        # company.
        for concept in (
            "dividends_paid",
            "share_repurchases",
            "repayments_of_debt",
            "interest_paid",
            "income_taxes_paid",
        ):
            assert is_magnitude(concept), concept

    def test_an_inflow_is_not_a_magnitude(self):
        assert not is_magnitude("proceeds_from_debt")


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

    def test_an_edgar_accession_is_normalised_to_its_dashed_form(self):
        """The undashed form appears in archive URLs and the dashed one in the API. A caller
        holding either should not have to know which."""
        assert make_fact(accession="000156459022026876").accession == "0001564590-22-026876"

    def test_a_filing_identifier_that_is_not_edgar_shaped_is_accepted(self):
        """**Changed deliberately in task 17.** Companies House issues base64-ish transaction
        IDs, and a shared fact type demanding eighteen digits would make every UK fact
        unrepresentable — a schema asserting a fact about the SEC rather than about facts.

        EDGAR's shape is still enforced; it moved to where EDGAR facts are built. See the test
        below, which is what says the guarantee was relocated rather than dropped.
        """
        fact = make_fact(accession="MzM1NTk4NDI3NmFkaXF6a2N4")

        assert fact.accession == "MzM1NTk4NDI3NmFkaXF6a2N4"

    def test_an_empty_filing_identifier_is_still_refused(self):
        """A fact that cannot say which filing it came from is not a fact."""
        with pytest.raises(ValueError, match="name the filing"):
            make_fact(accession="   ")

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

    def test_two_segments_are_never_rivals(self):
        # Two segments' revenue for one period are two numbers. A selection treating them
        # as rival accounts of one would keep a segment and silently drop the rest.
        americas = make_fact().model_copy(
            update={
                "dimension_axis": "us-gaap:StatementBusinessSegmentsAxis",
                "dimension_member": "aapl:AmericasSegmentMember",
            }
        )
        europe = americas.model_copy(update={"dimension_member": "aapl:EuropeSegmentMember"})

        assert americas.period_key != europe.period_key
        assert americas.period_key != make_fact().period_key

    def test_a_dimension_names_both_halves_or_neither(self):
        with pytest.raises(ValueError, match="both the axis and the member"):
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
                dimension_axis="us-gaap:StatementBusinessSegmentsAxis",
            )

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
