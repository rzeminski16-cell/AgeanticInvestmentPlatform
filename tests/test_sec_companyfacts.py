"""Parsing companyfacts into typed facts.

Two properties matter most here: that repeated observations of the same period are all
kept — they are the point-in-time record, not duplicates — and that values survive as
exact decimals rather than passing through a float.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from aer.core.concepts import (
    IFRS_ALIASES,
    REVENUE_TAG_PREFERENCE,
    UK_FRC_ALIASES,
    US_GAAP_ALIASES,
    revenue_tag_rank,
)
from aer.errors import ExternalServiceError
from aer.sources.sec.companyfacts import parse_company_facts
from tests.sec_fixtures import MSFT_CIK, fixture_bytes


@pytest.fixture
def facts():
    return parse_company_facts(fixture_bytes("companyfacts_msft.json"))


@pytest.fixture
def unmapped_facts():
    return parse_company_facts(fixture_bytes("companyfacts_unmapped.json"))


class TestIdentity:
    def test_the_cik_is_zero_padded(self, facts):
        assert facts.cik == MSFT_CIK

    def test_the_entity_name_is_carried(self, facts):
        assert facts.entity_name == "MICROSOFT CORPORATION"


class TestConceptAliasing:
    def test_different_revenue_tags_map_to_one_concept(self, facts):
        # ASC 606 replaced the revenue tags in 2018. Treating them as three concepts
        # leaves a hole in the history exactly where the taxonomy changed. The fixture's
        # one FY where the filer tagged both is decided by the preference order (gap
        # A62): the total is the revenue, so the ASC 606 tag is absent here and its
        # observation is kept under its own name -- tested below.
        revenue = facts.for_concept("revenue")

        assert {f.raw_concept for f in revenue} == {
            "Revenues",
            "SalesRevenueNet",
        }

    def test_the_raw_tag_is_kept_alongside_the_canonical_name(self, facts):
        # Both are needed: the canonical name for comparison, the raw tag for the
        # citation and for anyone checking the mapping was right.
        old = next(f for f in facts.facts if f.raw_concept == "SalesRevenueNet")

        assert old.concept == "revenue"
        assert old.taxonomy == "us-gaap"

    def test_a_dei_tag_maps_too(self, facts):
        shares = facts.for_concept("shares_outstanding")

        assert len(shares) == 1
        assert shares[0].taxonomy == "dei"
        assert shares[0].unit == "shares"


class TestUnmappedConcepts:
    def test_an_unmapped_us_gaap_tag_is_reported(self, unmapped_facts):
        # A gap in the alias map should be visible, not silent.
        tags = {u.tag for u in unmapped_facts.unmapped}

        assert "AllocatedShareBasedCompensationExpense" in tags

    def test_an_unmapped_tag_still_produces_facts(self, unmapped_facts):
        # Surfaced, not dropped. Discarding it would lose real reported data whenever the
        # alias map falls behind the taxonomy.
        tag = "AllocatedShareBasedCompensationExpense"
        kept = [f for f in unmapped_facts.facts if f.concept == tag]

        assert len(kept) == 1
        assert kept[0].value == Decimal("250000")
        assert kept[0].is_canonical is False

    def test_a_mapped_tag_is_not_reported_as_unmapped(self, unmapped_facts):
        assert "Revenues" not in {u.tag for u in unmapped_facts.unmapped}

    def test_the_unmapped_report_counts_observations(self, unmapped_facts):
        entry = next(
            u for u in unmapped_facts.unmapped if u.tag == "AllocatedShareBasedCompensationExpense"
        )

        assert entry.observations == 1
        assert entry.units == ("USD",)

    def test_a_filer_extension_concept_is_skipped_but_counted(self, unmapped_facts):
        # A custom element defined by one filer is meaningless to compare across
        # companies, so it produces no facts -- but "why is there no segment revenue?"
        # still needs an answer.
        assert unmapped_facts.extension_concepts == ("exmpl:SegmentRevenueNorthernRegion",)
        assert all(f.taxonomy != "exmpl" for f in unmapped_facts.facts)

    def test_unmapped_facts_can_be_excluded_on_request(self, unmapped_facts):
        excluded = parse_company_facts(
            fixture_bytes("companyfacts_unmapped.json"), include_unmapped=False
        )

        assert excluded.concepts == {"revenue"}
        # Still reported, even when not returned as facts.
        assert len(excluded.unmapped) == len(unmapped_facts.unmapped)


class TestObservations:
    def test_every_observation_of_a_period_is_kept(self, facts):
        # The repetition is the point-in-time record, not duplication to be collapsed.
        # FY2020 revenue appears twice under the Revenues tag, filed two years apart.
        fy2020 = [
            f
            for f in facts.for_concept("revenue")
            if f.period_end == date(2020, 6, 30) and f.raw_concept == "Revenues"
        ]

        assert len(fy2020) == 2
        assert {f.filed_date for f in fy2020} == {date(2020, 7, 30), date(2022, 7, 28)}

    def test_a_flow_carries_both_period_ends(self, facts):
        revenue = next(
            f for f in facts.for_concept("revenue") if f.accession == "0000789019-21-000027"
        )

        assert revenue.period_start == date(2020, 7, 1)
        assert revenue.period_end == date(2021, 6, 30)
        assert revenue.is_instant is False

    def test_a_balance_sheet_line_is_an_instant(self, facts):
        # "Cash at 30 June" is a fact about a moment. Giving it a made-up start would make
        # a stock look like a flow.
        assets = facts.for_concept("assets")[0]

        assert assets.period_start is None
        assert assets.is_instant is True

    def test_the_accession_and_form_are_carried(self, facts):
        revenue = facts.for_concept("revenue")[0]

        assert revenue.accession.count("-") == 2
        assert revenue.form == "10-K"

    def test_the_frame_is_carried_when_present_and_none_otherwise(self, facts):
        with_frame = next(f for f in facts.facts if f.frame is not None)
        assert with_frame.frame == "CY2019Q3TTM"

        without = next(
            f for f in facts.for_concept("revenue") if f.accession == "0000789019-21-000027"
        )
        assert without.frame is None


class TestNumericFidelity:
    def test_a_large_integer_survives_exactly(self, facts):
        # 143,015,000,000 is well above 2^53. Round-tripping it through a float would
        # change its last digits, and a revenue figure wrong in its last digits is worse
        # than one that is obviously missing.
        revenue = next(
            f
            for f in facts.for_concept("revenue")
            if f.accession == "0000789019-20-000039" and f.raw_concept == "Revenues"
        )

        assert revenue.value == Decimal("143015000000")
        assert isinstance(revenue.value, Decimal)

    def test_a_per_share_figure_keeps_its_decimals(self, facts):
        eps = facts.for_concept("earnings_per_share_diluted")[0]

        assert eps.value == Decimal("5.76")
        assert eps.unit == "USD/shares"

    def test_the_unit_is_never_dropped(self, facts):
        assert all(fact.unit for fact in facts.facts)


class TestMalformedObservations:
    def test_an_observation_with_no_filed_date_is_skipped(self):
        # Without it the fact cannot be point-in-time filtered, which makes it unusable
        # rather than merely incomplete.
        payload = b"""{"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
          {"start": "2020-01-01", "end": "2020-12-31", "val": 1,
           "accn": "0000000001-20-000001", "form": "10-K"}
        ]}}}}}"""

        assert parse_company_facts(payload).facts == ()

    def test_an_observation_with_a_malformed_accession_is_skipped(self):
        payload = b"""{"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
          {"end": "2020-12-31", "val": 1, "accn": "nonsense",
           "form": "10-K", "filed": "2021-01-01"}
        ]}}}}}"""

        assert parse_company_facts(payload).facts == ()

    def test_one_bad_observation_does_not_discard_the_good_ones(self):
        # A document with tens of thousands of facts should not be abandoned over one
        # unusable row.
        payload = b"""{"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
          {"end": "2020-12-31", "val": 1, "accn": "nonsense",
           "form": "10-K", "filed": "2021-01-01"},
          {"end": "2021-12-31", "val": 2, "accn": "0000000001-22-000001",
           "form": "10-K", "filed": "2022-01-01"}
        ]}}}}}"""

        facts = parse_company_facts(payload)

        assert len(facts.facts) == 1
        assert facts.facts[0].value == Decimal("2")


class TestParsingFailures:
    def test_a_response_with_no_facts_block_is_refused(self):
        with pytest.raises(ExternalServiceError, match="no cik or no facts"):
            parse_company_facts(b'{"cik": 789019, "entityName": "X"}')

    def test_html_is_refused_as_retryable(self):
        with pytest.raises(ExternalServiceError) as excinfo:
            parse_company_facts(b"<html>Rate limited</html>")

        assert excinfo.value.retryable is True


class TestTheFiscalYearIsThePeriodsOwn:
    """ADR 0062: EDGAR's ``fy`` is the filing's frame, not the observation's.

    The fixture carries the live defect in miniature: the period ending 30 June 2020
    appears twice, once tagged ``fy: 2020`` by its own 10-K and once tagged ``fy: 2022``
    by the later one that quoted it as a comparative. The first complete run stored a
    company's whole FY history one year late on exactly this mechanism.
    """

    def test_a_comparative_carries_its_own_year_not_the_filings(self, facts) -> None:
        rows = [
            fact
            for fact in facts.facts
            if fact.period_end == date(2020, 6, 30) and fact.fiscal_period == "FY"
        ]
        assert rows, "the fixture no longer holds the comparative this test pins"
        assert {row.fiscal_year for row in rows} == {2020}, (
            "a fiscal-year row must be labelled by its own period end; the filing's "
            "fy field stamps comparatives with the later report's year"
        )

    def test_every_annual_duration_is_labelled_by_its_period_end(self, facts) -> None:
        for fact in facts.facts:
            if fact.fiscal_period != "FY" or fact.period_start is None:
                continue
            assert fact.fiscal_year == fact.period_end.year

    def test_an_interim_row_keeps_the_filings_frame(self) -> None:
        """A quarter's fiscal year needs the company's calendar, which a per-document
        parser does not hold — so the filing's own frame stands, and ADR 0062 records
        the residual (a stale label on interim comparatives) rather than guessing."""
        document = {
            "cik": 789019,
            "entityName": "MICROSOFT CORP",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-10-01",
                                    "end": "2025-12-31",
                                    "val": 1,
                                    "accn": "0000950170-26-000001",
                                    "fy": 2026,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "filed": "2026-01-28",
                                }
                            ]
                        }
                    }
                }
            },
        }
        parsed = parse_company_facts(json.dumps(document).encode())
        (row,) = parsed.facts
        # A December quarter end inside a June fiscal year: the period's own calendar
        # year (2025) is the wrong answer, and the filing's frame (2026) is the right one.
        assert row.fiscal_year == 2026
        assert row.fiscal_period == "Q2"


def _bank_payload(revenue_blocks: dict[str, list[dict]]) -> bytes:
    """A minimal companyfacts document with exactly the revenue tags a test supplies."""
    return json.dumps(
        {
            "cik": 36270,
            "entityName": "M&T BANK CORP",
            "facts": {
                "us-gaap": {
                    tag: {"label": tag, "units": {"USD": entries}}
                    for tag, entries in revenue_blocks.items()
                }
            },
        }
    ).encode()


_FY = {
    "start": "2025-01-01",
    "end": "2025-12-31",
    "fy": 2025,
    "fp": "FY",
    "form": "10-K",
    "filed": "2026-02-18",
    "accn": "0000036270-26-000010",
}


class TestATotalOutranksItsComponent:
    """Gap A62: the MTB run called a $219bn bank's fee line its revenue.

    EDGAR's JSON lists tags alphabetically, which put the ASC 606 component ahead of
    ``Revenues`` at the observation-key dedupe. The preference order decides instead.
    """

    def test_the_total_is_the_revenue_and_the_component_keeps_its_tag(self) -> None:
        payload = _bank_payload(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": [{"val": 1525000000, **_FY}],
                "Revenues": [{"val": 9200000000, **_FY}],
            }
        )

        parsed = parse_company_facts(payload)
        revenue = parsed.for_concept("revenue")

        assert [f.value for f in revenue] == [Decimal(9200000000)]
        assert revenue[0].raw_concept == "Revenues"
        component = [
            f
            for f in parsed.facts
            if f.raw_concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]
        assert len(component) == 1, "the component is kept, not dropped"
        assert component[0].concept == component[0].raw_concept

    def test_the_banks_own_total_caption_outranks_the_component_too(self) -> None:
        payload = _bank_payload(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": [{"val": 1525000000, **_FY}],
                "RevenuesNetOfInterestExpense": [{"val": 9200000000, **_FY}],
            }
        )

        revenue = parse_company_facts(payload).for_concept("revenue")

        assert [f.raw_concept for f in revenue] == ["RevenuesNetOfInterestExpense"]

    def test_a_component_only_period_still_maps_and_names_the_settling(self) -> None:
        """A filer that tags nothing more total leaves the component as the best
        available revenue -- mapped, with ``raw_concept`` saying which line it is."""
        payload = _bank_payload(
            {"RevenueFromContractWithCustomerExcludingAssessedTax": [{"val": 1525000000, **_FY}]}
        )

        revenue = parse_company_facts(payload).for_concept("revenue")

        assert len(revenue) == 1
        assert revenue[0].raw_concept == "RevenueFromContractWithCustomerExcludingAssessedTax"

    def test_the_preference_is_per_period_not_per_document(self) -> None:
        """A total for one year must not demote another year's only revenue line."""
        other = dict(_FY, start="2024-01-01", end="2024-12-31", fy=2024)
        payload = _bank_payload(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": [
                    {"val": 1484000000, **other},
                    {"val": 1525000000, **_FY},
                ],
                "Revenues": [{"val": 9200000000, **_FY}],
            }
        )

        revenue = parse_company_facts(payload).for_concept("revenue")
        by_year = {f.fiscal_year: f for f in revenue}

        assert by_year[2025].raw_concept == "Revenues"
        assert by_year[2024].raw_concept == ("RevenueFromContractWithCustomerExcludingAssessedTax")


class TestThePreferenceTableCannotDrift:
    def test_every_tag_that_maps_to_revenue_is_ranked(self) -> None:
        """A future alias added to the map but not the ranking would be decided by
        arrival order again -- the exact failure the ranking exists to end."""
        mapped = {
            tag
            for aliases in (US_GAAP_ALIASES, IFRS_ALIASES, UK_FRC_ALIASES)
            for tag, concept in aliases.items()
            if concept == "revenue"
        }
        assert mapped <= set(REVENUE_TAG_PREFERENCE)
        # And a tag the table has never seen ranks last, never first.
        assert revenue_tag_rank("SomeFutureRevenueTag") == len(REVENUE_TAG_PREFERENCE)


class TestTheBankCaptionsMap:
    """The lines a depository's income statement leads with reach the vocabulary."""

    def test_net_interest_income_and_its_neighbours(self) -> None:
        payload = _bank_payload(
            {
                "InterestIncomeExpenseNet": [{"val": 6800000000, **_FY}],
                "NoninterestIncome": [{"val": 2400000000, **_FY}],
                "InterestAndDividendIncomeOperating": [{"val": 9500000000, **_FY}],
                "ProvisionForLoanAndLeaseLosses": [{"val": 550000000, **_FY}],
            }
        )

        parsed = parse_company_facts(payload)

        assert len(parsed.for_concept("net_interest_income")) == 1
        assert len(parsed.for_concept("noninterest_income")) == 1
        assert len(parsed.for_concept("interest_and_dividend_income")) == 1
        assert len(parsed.for_concept("provision_for_credit_losses")) == 1
        assert parsed.unmapped == ()
