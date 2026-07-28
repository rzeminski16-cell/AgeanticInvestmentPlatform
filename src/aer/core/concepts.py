"""Canonical financial concepts, and the filer-specific tags that mean them.

**The problem this solves.** XBRL lets a filer choose which tag to use for revenue. Some
use ``Revenues``. Some use ``RevenueFromContractWithCustomerExcludingAssessedTax``, which
became standard after ASC 606. Older filings use ``SalesRevenueNet``. All three mean the
same line on the income statement, and a comparison across companies — or across years for
one company — that treats them as three different concepts silently produces gaps where a
number plainly exists.

**The rule.** A tag maps to a canonical concept, or it does not map at all. Nothing is
guessed from a tag's spelling: ``RevenueFromContractWithCustomerExcludingAssessedTax`` and
``RevenueFromContractWithCustomerIncludingAssessedTax`` differ by one word and by whether
sales tax is in the number, and a fuzzy match that collapsed them would be wrong in a way
nobody would notice until the figures were already in a report.

**Unmapped is surfaced, never dropped.** A filer using a custom extension element for
segment revenue produces a tag this map has never seen. Discarding it would lose real
data and leave no trace; the parser keeps the fact under its raw tag and reports the tag
as unmapped, so a gap in this table is visible rather than silent.

This module is pure vocabulary — no I/O, no dependencies beyond the standard library —
which is why it lives in ``aer.core`` rather than inside the SEC adapter. The same
canonical names will be the target of a UK adapter's own alias map, and the point of a
canonical name is that there is exactly one of it.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CANONICAL_CONCEPTS",
    "US_GAAP_ALIASES",
    "canonical_concept",
    "is_canonical_concept",
]


# The canonical vocabulary. Deliberately small: every name here has to be defined
# precisely enough that two adapters mapping onto it produce comparable numbers, and a
# vocabulary that grows faster than that definition work is a vocabulary that lies.
CANONICAL_CONCEPTS: Final[frozenset[str]] = frozenset(
    {
        "assets",
        "cash_and_equivalents",
        "cost_of_revenue",
        "current_assets",
        "current_liabilities",
        "depreciation_and_amortisation",
        "diluted_shares_outstanding",
        "dividends_paid",
        "earnings_per_share_diluted",
        "equity",
        "gross_profit",
        "income_tax_expense",
        "interest_expense",
        "inventory",
        "liabilities",
        "long_term_debt",
        "net_income",
        "operating_cash_flow",
        "operating_income",
        "capital_expenditure",
        "research_and_development",
        "revenue",
        "shares_outstanding",
        "short_term_debt",
    }
)


# us-gaap (and dei) tags to canonical concepts.
#
# Several concepts have more than one tag because the taxonomy changed under the filer's
# feet: ASC 606 replaced the revenue tags in 2018, and filings either side of that use
# different names for the same line. The map is the only place that history has to be
# known.
US_GAAP_ALIASES: Final[dict[str, str]] = {
    # -- Income statement ---------------------------------------------------------------
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue",
    "SalesRevenueNet": "revenue",
    "SalesRevenueGoodsNet": "revenue",
    "SalesRevenueServicesNet": "revenue",
    "CostOfRevenue": "cost_of_revenue",
    "CostOfGoodsAndServicesSold": "cost_of_revenue",
    "CostOfGoodsSold": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "operating_income",
    "ResearchAndDevelopmentExpense": "research_and_development",
    "InterestExpense": "interest_expense",
    "InterestExpenseNonoperating": "interest_expense",
    "IncomeTaxExpenseBenefit": "income_tax_expense",
    "NetIncomeLoss": "net_income",
    "ProfitLoss": "net_income",
    "EarningsPerShareDiluted": "earnings_per_share_diluted",
    # -- Balance sheet ------------------------------------------------------------------
    "Assets": "assets",
    "AssetsCurrent": "current_assets",
    "Liabilities": "liabilities",
    "LiabilitiesCurrent": "current_liabilities",
    "StockholdersEquity": "equity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "equity",
    "CashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
    "InventoryNet": "inventory",
    "LongTermDebtNoncurrent": "long_term_debt",
    "LongTermDebt": "long_term_debt",
    "ShortTermBorrowings": "short_term_debt",
    "LongTermDebtCurrent": "short_term_debt",
    # -- Cash flow ----------------------------------------------------------------------
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "DepreciationDepletionAndAmortization": "depreciation_and_amortisation",
    "DepreciationAmortizationAndAccretionNet": "depreciation_and_amortisation",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
    "PaymentsOfDividendsCommonStock": "dividends_paid",
    "PaymentsOfDividends": "dividends_paid",
    # -- Share counts -------------------------------------------------------------------
    "WeightedAverageNumberOfDilutedSharesOutstanding": "diluted_shares_outstanding",
    "CommonStockSharesOutstanding": "shares_outstanding",
    "EntityCommonStockSharesOutstanding": "shares_outstanding",
}


def canonical_concept(taxonomy: str, tag: str) -> str | None:
    """The canonical concept a filer's tag means, or ``None`` if it is unmapped.

    Args:
        taxonomy: The taxonomy the tag belongs to, as EDGAR reports it — ``us-gaap``,
            ``dei``, ``ifrs-full``, or a filer's own extension namespace.
        tag: The element name exactly as it appears in the filing.

    Only ``us-gaap`` and ``dei`` are mapped today. A tag from a filer's own extension
    namespace is by definition not in a shared taxonomy, so there is nothing to map it
    onto — it is returned unmapped and surfaced to the operator.
    """
    if taxonomy not in {"us-gaap", "dei"}:
        return None
    return US_GAAP_ALIASES.get(tag)


def is_canonical_concept(name: str) -> bool:
    """Whether ``name`` is one of the canonical concepts."""
    return name in CANONICAL_CONCEPTS
