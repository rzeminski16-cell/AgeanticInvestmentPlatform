"""The IFRS alias table names elements the taxonomy actually has.

Readiness audit 2026-09, F-12: seven keys were spelt the way us-gaap spells them and no
20-F filer ever matched them. The tags below are the ones AstraZeneca files (companyfacts
for CIK 901832, archived on the first live run), so each must land on the concept the
calculations need. A tag that is not in the taxonomy can never map, and a table entry for
it is a promise the platform cannot keep.
"""

from __future__ import annotations

import pytest

from aer.core.concepts import IFRS_ALIASES, canonical_concept

# What AstraZeneca's 20-F carries, and where each line must land.
ASTRAZENECA_TAGS = [
    ("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "capital_expenditure"),
    ("AdjustmentsForSharebasedPayments", "share_based_compensation"),
    ("IncomeTaxesPaidRefundClassifiedAsOperatingActivities", "income_taxes_paid"),
    ("WeightedAverageShares", "basic_shares_outstanding"),
    ("AdjustedWeightedAverageShares", "diluted_shares_outstanding"),
    ("LongtermBorrowings", "long_term_debt"),
    ("CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "short_term_debt"),
    ("Borrowings", "total_debt"),
    ("PurchaseOfTreasuryShares", "share_repurchases"),
    ("CashFlowsFromUsedInOperatingActivities", "operating_cash_flow"),
]


@pytest.mark.parametrize(("tag", "concept"), ASTRAZENECA_TAGS)
def test_a_20f_filers_line_lands_on_its_concept(tag: str, concept: str) -> None:
    assert canonical_concept("ifrs-full", tag) == concept


@pytest.mark.parametrize(
    "spelling",
    [
        # us-gaap spellings that the IFRS taxonomy never used; each sat in the table and
        # matched nothing.
        "AdjustmentsForShareBasedPayments",
        "PurchaseOfPropertyPlantAndEquipment",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "IncomeTaxesPaidClassifiedAsOperatingActivities",
        "WeightedAverageNumberOfOrdinarySharesOutstanding",
        "AdjustedWeightedAverageNumberOfOrdinarySharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
)
def test_the_misspellings_are_gone(spelling: str) -> None:
    assert spelling not in IFRS_ALIASES


def test_the_current_portion_alone_does_not_outrank_the_combined_current_line() -> None:
    """Two tags on one concept are settled by name; the component must not be a candidate."""
    assert canonical_concept("ifrs-full", "CurrentPortionOfLongtermBorrowings") is None
