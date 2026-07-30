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
    "IFRS_ALIASES",
    "UK_FRC_ALIASES",
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


# IFRS tags to canonical concepts.
#
# **This is the taxonomy an LSE-listed company reports under.** UK listed issuers apply
# IFRS as adopted for use in the UK, so their inline XBRL is tagged from `ifrs-full`. The
# FRC's `uk-*` taxonomies are for the FRS 101/102 regime that smaller and private companies
# use, and they are not what this platform's universe files under — a handful of the most
# common `uk-core` spellings are mapped below anyway, because a UK filer occasionally tags a
# statutory line from them alongside the IFRS ones.
#
# Where IFRS names a concept differently from US GAAP rather than merely spelling it
# differently, the mapping is to the canonical name whose *definition* matches, not the one
# whose words look closest. `ProfitLossFromOperatingActivities` is operating income;
# `FinanceCosts` is the interest line; `CashFlowsFromUsedInOperatingActivities` is operating
# cash flow. Getting one of those wrong puts a real number on the wrong line, which is worse
# than leaving it unmapped and visible.
IFRS_ALIASES: Final[dict[str, str]] = {
    # -- Income statement ---------------------------------------------------------------
    "Revenue": "revenue",
    "RevenueFromContractsWithCustomers": "revenue",
    "CostOfSales": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    "ProfitLossFromOperatingActivities": "operating_income",
    "OperatingProfitLoss": "operating_income",
    "ResearchAndDevelopmentExpense": "research_and_development",
    "FinanceCosts": "interest_expense",
    "InterestExpense": "interest_expense",
    "IncomeTaxExpenseContinuingOperations": "income_tax_expense",
    "ProfitLoss": "net_income",
    "ProfitLossAttributableToOwnersOfParent": "net_income",
    "DilutedEarningsLossPerShare": "earnings_per_share_diluted",
    # -- Balance sheet ------------------------------------------------------------------
    "Assets": "assets",
    "CurrentAssets": "current_assets",
    "Liabilities": "liabilities",
    "CurrentLiabilities": "current_liabilities",
    "Equity": "equity",
    "EquityAttributableToOwnersOfParent": "equity",
    "CashAndCashEquivalents": "cash_and_equivalents",
    "Inventories": "inventory",
    "NoncurrentPortionOfNoncurrentBorrowings": "long_term_debt",
    "BorrowingsNoncurrent": "long_term_debt",
    "CurrentPortionOfNoncurrentBorrowings": "short_term_debt",
    "BorrowingsCurrent": "short_term_debt",
    # -- Cash flow ----------------------------------------------------------------------
    "CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
    "DepreciationAndAmortisationExpense": "depreciation_and_amortisation",
    "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss": (
        "depreciation_and_amortisation"
    ),
    "PurchaseOfPropertyPlantAndEquipment": "capital_expenditure",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
    "DividendsPaid": "dividends_paid",
    "DividendsPaidClassifiedAsFinancingActivities": "dividends_paid",
    # -- Share counts -------------------------------------------------------------------
    "WeightedAverageNumberOfDilutedSharesOutstanding": "diluted_shares_outstanding",
    "NumberOfSharesOutstanding": "shares_outstanding",
}


# The FRC's own taxonomies. A very short list on purpose: these appear alongside IFRS tags in
# a UK filing's cover-page and statutory sections rather than in its primary statements.
UK_FRC_ALIASES: Final[dict[str, str]] = {
    "TurnoverRevenue": "revenue",
    "Turnover": "revenue",
    "GrossProfitLoss": "gross_profit",
    "OperatingProfitLoss": "operating_income",
    "ProfitLossForPeriod": "net_income",
}


# Which alias table each taxonomy prefix uses. A dictionary rather than a chain of `if`s so
# that adding a taxonomy is a data change, and so a reader can see the whole supported set at
# once rather than reconstructing it from control flow.
_ALIAS_TABLES: Final[dict[str, dict[str, str]]] = {
    "us-gaap": US_GAAP_ALIASES,
    "dei": US_GAAP_ALIASES,
    "ifrs-full": IFRS_ALIASES,
    "ifrs": IFRS_ALIASES,
    "uk-core": UK_FRC_ALIASES,
    "uk-bus": UK_FRC_ALIASES,
    "core": UK_FRC_ALIASES,
}


def canonical_concept(taxonomy: str, tag: str) -> str | None:
    """The canonical concept a filer's tag means, or ``None`` if it is unmapped.

    Args:
        taxonomy: The taxonomy the tag belongs to — ``us-gaap``, ``dei``, ``ifrs-full``, one
            of the FRC's ``uk-*`` prefixes, or a filer's own extension namespace.
        tag: The element name exactly as it appears in the filing.

    A tag from a filer's own extension namespace is by definition not in a shared taxonomy,
    so there is nothing to map it onto — it is returned unmapped and surfaced to the
    operator. **That is the normal case for a UK filing**, where extensions are common, and
    it is why :mod:`aer.extract.ixbrl` treats an extraction with unmapped tags as needing a
    human rather than as a failure.
    """
    table = _ALIAS_TABLES.get(taxonomy.strip().lower())
    if table is None:
        return None
    return table.get(tag)


def is_canonical_concept(name: str) -> bool:
    """Whether ``name`` is one of the canonical concepts."""
    return name in CANONICAL_CONCEPTS
