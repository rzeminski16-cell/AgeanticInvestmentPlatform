"""Fact selection: the latest filing's word wins, and every loser is accounted for.

The most consequential logic in the ingestion layer, and the easiest to get subtly wrong
in a way that never raises. Every test here is a statement about which of a filer's
figures a report rests on, and why the others do not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from aer.core.enums import FactBasis
from aer.errors import ValidationError
from aer.sources.sec.selection import (
    DUPLICATE_TAGGING_IN_SAME_FILING,
    SUPERSEDED_BY_LATER_FILING,
    select_latest,
)
from tests.sec_fixtures import make_fact

ORIGINAL_ACCESSION = "0000789019-20-000039"
RESTATEMENT_ACCESSION = "0000789019-22-000010"


@pytest.fixture
def restated_pair():
    """The same period, reported twice, two years apart, with different values."""
    original = make_fact(value=143015000000, filed="2020-07-30", accession=ORIGINAL_ACCESSION)
    restatement = make_fact(value=142000000000, filed="2022-07-28", accession=RESTATEMENT_ACCESSION)
    return original, restatement


class TestTheRestatementCase:
    """The rule this module exists for, on the fixture the spec asks for."""

    def test_the_later_filing_s_value_is_chosen(self, restated_pair):
        original, restatement = restated_pair

        selection = select_latest([original, restatement])

        assert len(selection.chosen) == 1
        assert selection.chosen[0].value == Decimal("142000000000")
        assert selection.chosen[0].accession == RESTATEMENT_ACCESSION
        assert selection.chosen[0].filed_date == date(2022, 7, 28)

    def test_the_superseded_original_names_what_replaced_it(self, restated_pair):
        # "Not chosen" and "replaced by that filing" are different facts about a run, and a
        # reviewer asking why a figure is missing needs the second one.
        original, restatement = restated_pair

        selection = select_latest([original, restatement])

        assert len(selection.rejected) == 1
        assert selection.rejected[0].fact.accession == ORIGINAL_ACCESSION
        assert selection.rejected[0].reason == SUPERSEDED_BY_LATER_FILING
        assert selection.rejected[0].superseded_by == RESTATEMENT_ACCESSION

    def test_input_order_does_not_decide_the_restatement(self, restated_pair):
        original, restatement = restated_pair

        forwards = select_latest([original, restatement])
        backwards = select_latest([restatement, original])

        assert forwards.chosen == backwards.chosen


class TestThePartition:
    """Every input fact appears exactly once. A selector that loses one is a selector
    whose output cannot be reconciled with its input."""

    def test_no_fact_is_lost(self):
        facts = [
            make_fact(value=1, filed="2020-01-01"),
            make_fact(value=2, filed="2021-01-01"),
            make_fact(value=3, filed="2023-01-01"),
            make_fact(concept="net_income", value=4, filed="2020-01-01"),
            make_fact(concept="assets", value=5, filed="2024-01-01"),
        ]

        selection = select_latest(facts)

        accounted = len(selection.chosen) + len(selection.rejected)
        assert accounted == len(facts)

    def test_no_fact_appears_twice(self):
        facts = [
            make_fact(value=1, filed="2020-01-01"),
            make_fact(value=2, filed="2021-01-01"),
            make_fact(concept="assets", value=3, filed="2021-06-01", period_start=None),
        ]

        selection = select_latest(facts)

        seen = [*selection.chosen, *(r.fact for r in selection.rejected)]
        assert len(seen) == len(set(seen))


class TestGrouping:
    def test_different_concepts_are_selected_independently(self):
        revenue = make_fact(concept="revenue", value=100, filed="2020-07-30")
        income = make_fact(concept="net_income", value=20, filed="2020-07-30")

        selection = select_latest([revenue, income])

        assert {f.concept for f in selection.chosen} == {"revenue", "net_income"}

    def test_different_periods_are_selected_independently(self):
        fy2020 = make_fact(period_end="2020-06-30", value=143, filed="2020-07-30")
        fy2021 = make_fact(period_end="2021-06-30", value=168, filed="2021-07-29")

        selection = select_latest([fy2020, fy2021])

        assert len(selection.chosen) == 2
        assert {f.period_end for f in selection.chosen} == {
            date(2020, 6, 30),
            date(2021, 6, 30),
        }

    def test_the_same_number_in_different_units_is_not_a_rival_account(self):
        # A value in dollars and a value in shares are not two answers to one question, so
        # one must not supersede the other. Leaving unit out of the grouping key would
        # silently drop whichever came second.
        in_dollars = make_fact(unit="USD", value=143015000000, filed="2020-07-30")
        in_shares = make_fact(unit="shares", value=7571000000, filed="2020-07-30")

        selection = select_latest([in_dollars, in_shares])

        assert len(selection.chosen) == 2
        assert selection.rejected == ()

    def test_a_quarter_does_not_supersede_the_full_year(self):
        # Same concept, same period end, different fiscal period. Q4 and FY both end on
        # the year end date and mean entirely different things.
        annual = make_fact(fiscal_period="FY", value=143, filed="2020-07-30")
        quarterly = make_fact(fiscal_period="Q4", value=37, filed="2020-07-30")

        selection = select_latest([annual, quarterly])

        assert len(selection.chosen) == 2


class TestDeterminism:
    def test_a_same_day_tie_is_broken_by_accession(self):
        # Two filings accepted on one day -- a 10-K and a same-day amendment. The higher
        # accession sequence was accepted later and is the company's more recent word.
        # Without this, the winner would depend on input ordering.
        first = make_fact(value=100, filed="2021-02-01", accession="0000789019-21-000010")
        amended = make_fact(
            value=110, filed="2021-02-01", accession="0000789019-21-000011", form="10-K/A"
        )

        forwards = select_latest([first, amended])
        backwards = select_latest([amended, first])

        assert forwards.chosen[0].value == Decimal("110")
        assert backwards.chosen[0].value == Decimal("110")

    def test_input_order_never_changes_the_result(self):
        facts = [
            make_fact(value=1, filed="2020-01-01", accession="0000789019-20-000001"),
            make_fact(value=2, filed="2021-01-01", accession="0000789019-21-000001"),
            make_fact(value=3, filed="2019-01-01", accession="0000789019-19-000001"),
        ]

        forwards = select_latest(facts)
        backwards = select_latest(list(reversed(facts)))

        assert forwards.chosen == backwards.chosen

    def test_two_tags_for_one_number_in_one_filing_resolve_deterministically(self):
        # A taxonomy-transition year: the filer tags revenue as both `Revenues` and the
        # ASC 606 name, reporting one number under two labels. Without a tiebreak on the
        # tag, the winner would depend on the order the parser walked the taxonomy in.
        legacy = make_fact(raw_concept="Revenues", value=143015000000)
        current = make_fact(
            raw_concept="RevenueFromContractWithCustomerExcludingAssessedTax",
            value=143015000000,
        )

        forwards = select_latest([legacy, current])
        backwards = select_latest([current, legacy])

        assert len(forwards.chosen) == 1
        assert forwards.chosen == backwards.chosen

    def test_a_duplicate_tagging_is_not_called_a_supersession(self):
        # The two facts arrived in the same filing, so nothing superseded anything.
        # Recording it as supersession would put a false statement in the audit trail.
        legacy = make_fact(raw_concept="Revenues")
        current = make_fact(raw_concept="RevenueFromContractWithCustomerExcludingAssessedTax")

        selection = select_latest([legacy, current])

        assert len(selection.rejected) == 1
        assert selection.rejected[0].reason == DUPLICATE_TAGGING_IN_SAME_FILING

    def test_a_genuine_restatement_is_still_called_a_supersession(self):
        # The distinction is the accession, not the tag: different filings, so one really
        # did replace the other.
        original = make_fact(filed="2020-07-30", accession="0000789019-20-000039")
        later = make_fact(filed="2022-07-28", accession="0000789019-22-000010")

        selection = select_latest([original, later])

        assert selection.rejected[0].reason == SUPERSEDED_BY_LATER_FILING


class TestBasis:
    def test_the_selection_records_the_basis_it_produced(self):
        selection = select_latest([make_fact()])

        assert selection.basis is FactBasis.AS_REPORTED

    @pytest.mark.parametrize("basis", [FactBasis.RESTATED, FactBasis.VENDOR_STANDARDISED])
    def test_any_other_basis_is_refused(self, basis):
        # Not merely unimplemented. A figure no filing reported has no artefact behind it,
        # and the refusal says so rather than returning an empty result.
        with pytest.raises(ValidationError, match="no filing reported"):
            select_latest([make_fact()], basis=basis)


class TestHelpers:
    def test_latest_returns_the_most_recent_period(self):
        facts = [
            make_fact(period_end="2020-06-30", value=143, filed="2020-07-30"),
            make_fact(period_end="2021-06-30", value=168, filed="2021-07-29"),
            make_fact(period_end="2019-06-30", value=125, filed="2019-08-01"),
        ]

        selection = select_latest(facts)

        latest = selection.latest("revenue")
        assert latest is not None
        assert latest.period_end == date(2021, 6, 30)

    def test_latest_returns_none_for_a_concept_with_no_chosen_facts(self):
        selection = select_latest([make_fact()])

        assert selection.latest("free_cash_flow") is None

    def test_for_concept_returns_the_history_oldest_first(self):
        facts = [
            make_fact(period_end="2021-06-30", value=168, filed="2021-07-29"),
            make_fact(period_end="2019-06-30", value=125, filed="2019-08-01"),
            make_fact(period_end="2020-06-30", value=143, filed="2020-07-30"),
        ]

        selection = select_latest(facts)

        history = selection.for_concept("revenue")
        assert [f.period_end.year for f in history] == [2019, 2020, 2021]


class TestEmptyInput:
    def test_no_facts_produces_an_empty_selection_rather_than_an_error(self):
        selection = select_latest([])

        assert selection.chosen == ()
        assert selection.rejected == ()
