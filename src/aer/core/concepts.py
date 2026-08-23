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

**One tag per concept per filing, and that is why some obvious aliases are absent.** A filer
that tags ``SellingAndMarketingExpense`` and ``GeneralAndAdministrativeExpense`` separately
has not reported SG&A; it has reported two components of it. Mapping both onto
``sg_and_a`` would produce two facts claiming to be the same concept for the same period,
which the disagreement ladder would then have to arbitrate between — two halves of a total,
neither wrong. So only the combined tag maps, and a filer who splits them leaves the concept
absent and visible. ``docs/archive/PLAN.md`` names the long tail as this phase's main risk and
prescribes exactly this: ship the top sixty and surface what is missing.

**Signs are a trap and are documented rather than assumed.** See :data:`MAGNITUDE_CONCEPTS`.

This module is pure vocabulary — no I/O, no dependencies beyond the standard library —
which is why it lives in ``aer.core`` rather than inside the SEC adapter. The same
canonical names are the target of the UK adapter's alias map, and the point of a canonical
name is that there is exactly one of it.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CANONICAL_CONCEPTS",
    "IFRS_ALIASES",
    "MAGNITUDE_CONCEPTS",
    "REVENUE_TAG_PREFERENCE",
    "UK_FRC_ALIASES",
    "US_GAAP_ALIASES",
    "canonical_concept",
    "is_canonical_concept",
    "is_magnitude",
    "revenue_tag_rank",
]


# The canonical vocabulary. Every name here is defined precisely enough that two adapters
# mapping onto it produce comparable numbers; a vocabulary that grows faster than that
# definition work is a vocabulary that lies.
#
# Grouped by where the line lives, because that is how a reader checks the list for holes.
CANONICAL_CONCEPTS: Final[frozenset[str]] = frozenset(
    {
        # -- Income statement -----------------------------------------------------------
        "revenue",
        "cost_of_revenue",
        "gross_profit",
        "sg_and_a",
        "research_and_development",
        "operating_expenses",
        "operating_income",
        "impairment",
        "restructuring_costs",
        "interest_expense",
        "interest_income",
        # The lines a depository institution's income statement actually leads with (gap
        # A62). Distinct from `interest_income`/`interest_expense`, which are an
        # industrial company's non-operating lines: a bank's interest income *is* its
        # operating revenue, and folding the two together would make "interest income"
        # mean different statements for different filers.
        "net_interest_income",
        "interest_and_dividend_income",
        "noninterest_income",
        "provision_for_credit_losses",
        "pre_tax_income",
        "income_tax_expense",
        "net_income",
        "noncontrolling_interest_income",
        "preferred_dividends",
        "earnings_per_share_basic",
        "earnings_per_share_diluted",
        # -- Balance sheet: assets --------------------------------------------------------
        "cash_and_equivalents",
        "short_term_investments",
        "accounts_receivable",
        "inventory",
        "current_assets",
        "property_plant_and_equipment",
        "goodwill",
        "intangible_assets",
        "noncurrent_assets",
        "assets",
        # -- Balance sheet: liabilities and equity ----------------------------------------
        "accounts_payable",
        "accrued_liabilities",
        "deferred_revenue",
        "short_term_debt",
        "current_liabilities",
        "long_term_debt",
        "lease_liabilities",
        "noncurrent_liabilities",
        "liabilities",
        "total_debt",
        "retained_earnings",
        "treasury_stock",
        "noncontrolling_interests",
        "equity",
        # -- Cash flow --------------------------------------------------------------------
        "operating_cash_flow",
        "investing_cash_flow",
        "financing_cash_flow",
        "depreciation_and_amortisation",
        "share_based_compensation",
        "deferred_income_tax_expense",
        "change_in_working_capital",
        "capital_expenditure",
        "share_repurchases",
        "dividends_paid",
        "proceeds_from_debt",
        "repayments_of_debt",
        "interest_paid",
        "income_taxes_paid",
        # The two lines the cash-flow roll-forward needs. Without them the identity
        # `docs/archive/PLAN.md` asks for -- that the three cash flows and the currency effect reach
        # the movement in cash -- cannot be written down, so the statement with the most room
        # for a mapping error would be the only one with nothing checking it.
        "effect_of_exchange_rate_on_cash",
        "net_change_in_cash",
        # -- Per share and share counts ---------------------------------------------------
        "dividends_per_share",
        "basic_shares_outstanding",
        "diluted_shares_outstanding",
        "shares_outstanding",
    }
)


# Concepts a filer reports as a **positive magnitude** even though the thing itself is an
# outflow or a deduction.
#
# This is the sign trap, and it is worth a table rather than a convention. Capital
# expenditure is tagged ``PaymentsToAcquirePropertyPlantAndEquipment`` and reported as a
# positive number: it is a *payment* of that size, not a negative cash flow. Free cash flow
# is therefore ``operating_cash_flow - capital_expenditure`` and not a sum, and a model that
# added them would produce a figure roughly twice the right one for a capital-intensive
# company and only slightly wrong for an asset-light one — which is the worst possible
# distribution of an error, because it looks fine in the cases used to check it.
#
# Anything not listed here carries the sign the filer reported, including losses.
MAGNITUDE_CONCEPTS: Final[frozenset[str]] = frozenset(
    {
        "capital_expenditure",
        "dividends_paid",
        "preferred_dividends",
        "share_repurchases",
        "repayments_of_debt",
        "interest_paid",
        "income_taxes_paid",
        "treasury_stock",
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
    # A bank's total-revenue caption (gap A62). The MTB run had no route from its
    # filings to "revenue" except RevenueFromContractWithCustomer*, which for a bank is
    # fee income alone -- interest income is not ASC 606 revenue -- and the front page
    # reported a $219bn bank's quarter at $442m of "revenue".
    "RevenuesNetOfInterestExpense": "revenue",
    "InterestIncomeExpenseNet": "net_interest_income",
    "InterestAndDividendIncomeOperating": "interest_and_dividend_income",
    "NoninterestIncome": "noninterest_income",
    # The pre-CECL provision tag. The CECL-era element is deliberately absent until a
    # live run's unplaced list names its exact spelling (the A55 curation loop) --
    # nothing here is guessed from a tag's shape, per this module's rule.
    "ProvisionForLoanAndLeaseLosses": "provision_for_credit_losses",
    "CostOfRevenue": "cost_of_revenue",
    "CostOfGoodsAndServicesSold": "cost_of_revenue",
    "CostOfGoodsSold": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    # Only the combined tag; see the module docstring on why the components are absent.
    "SellingGeneralAndAdministrativeExpense": "sg_and_a",
    "ResearchAndDevelopmentExpense": "research_and_development",
    "OperatingExpenses": "operating_expenses",
    "CostsAndExpenses": "operating_expenses",
    "OperatingIncomeLoss": "operating_income",
    "AssetImpairmentCharges": "impairment",
    "GoodwillImpairmentLoss": "impairment",
    "ImpairmentOfIntangibleAssetsExcludingGoodwill": "impairment",
    "RestructuringCharges": "restructuring_costs",
    "InterestExpense": "interest_expense",
    "InterestExpenseNonoperating": "interest_expense",
    "InvestmentIncomeInterest": "interest_income",
    "InterestIncomeOperating": "interest_income",
    (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems"
        "NoncontrollingInterest"
    ): "pre_tax_income",
    (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterest"
        "AndIncomeLossFromEquityMethodInvestments"
    ): "pre_tax_income",
    "IncomeTaxExpenseBenefit": "income_tax_expense",
    "NetIncomeLoss": "net_income",
    "ProfitLoss": "net_income",
    "NetIncomeLossAttributableToNoncontrollingInterest": "noncontrolling_interest_income",
    "PreferredStockDividendsAndOtherAdjustments": "preferred_dividends",
    "DividendsPreferredStock": "preferred_dividends",
    "EarningsPerShareBasic": "earnings_per_share_basic",
    "EarningsPerShareDiluted": "earnings_per_share_diluted",
    # -- Balance sheet: assets ------------------------------------------------------------
    "CashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
    "ShortTermInvestments": "short_term_investments",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent": "short_term_investments",
    "AccountsReceivableNetCurrent": "accounts_receivable",
    "ReceivablesNetCurrent": "accounts_receivable",
    "InventoryNet": "inventory",
    "AssetsCurrent": "current_assets",
    "PropertyPlantAndEquipmentNet": "property_plant_and_equipment",
    "Goodwill": "goodwill",
    "IntangibleAssetsNetExcludingGoodwill": "intangible_assets",
    "FiniteLivedIntangibleAssetsNet": "intangible_assets",
    "AssetsNoncurrent": "noncurrent_assets",
    "Assets": "assets",
    # -- Balance sheet: liabilities and equity ----------------------------------------------
    "AccountsPayableCurrent": "accounts_payable",
    "AccruedLiabilitiesCurrent": "accrued_liabilities",
    "ContractWithCustomerLiabilityCurrent": "deferred_revenue",
    "DeferredRevenueCurrent": "deferred_revenue",
    "ShortTermBorrowings": "short_term_debt",
    "LongTermDebtCurrent": "short_term_debt",
    "LiabilitiesCurrent": "current_liabilities",
    "LongTermDebtNoncurrent": "long_term_debt",
    "LongTermDebt": "long_term_debt",
    "OperatingLeaseLiability": "lease_liabilities",
    "OperatingLeaseLiabilityNoncurrent": "lease_liabilities",
    "LiabilitiesNoncurrent": "noncurrent_liabilities",
    "Liabilities": "liabilities",
    "DebtLongtermAndShorttermCombinedAmount": "total_debt",
    "RetainedEarningsAccumulatedDeficit": "retained_earnings",
    "TreasuryStockValue": "treasury_stock",
    "MinorityInterest": "noncontrolling_interests",
    "StockholdersEquity": "equity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "equity",
    # -- Cash flow ----------------------------------------------------------------------
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "NetCashProvidedByUsedInInvestingActivities": "investing_cash_flow",
    "NetCashProvidedByUsedInFinancingActivities": "financing_cash_flow",
    # The movement in the cash balance, *including* the currency effect -- which is what
    # reconciles to the balance sheet, and what a reader means by "net change in cash". The
    # `...ExcludingExchangeRateEffect` element is a different figure and is deliberately left
    # unmapped: mapping both here would make the roll-forward hold for one filer and fail for
    # an otherwise identical one with foreign cash.
    (
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
        "PeriodIncreaseDecreaseIncludingExchangeRateEffect"
    ): "net_change_in_cash",
    "CashAndCashEquivalentsPeriodIncreaseDecrease": "net_change_in_cash",
    (
        "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    ): "effect_of_exchange_rate_on_cash",
    "EffectOfExchangeRateOnCashAndCashEquivalents": "effect_of_exchange_rate_on_cash",
    "DepreciationDepletionAndAmortization": "depreciation_and_amortisation",
    "DepreciationAmortizationAndAccretionNet": "depreciation_and_amortisation",
    # The plainest spelling of the same combined line, and common among large filers.
    # Only tags meaning *depreciation and amortisation together* belong here: bare
    # `Depreciation` is a smaller number and mapping it would understate the driver
    # wherever a company reports the two separately.
    "DepreciationAndAmortization": "depreciation_and_amortisation",
    "ShareBasedCompensation": "share_based_compensation",
    "DeferredIncomeTaxExpenseBenefit": "deferred_income_tax_expense",
    "IncreaseDecreaseInOperatingCapital": "change_in_working_capital",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
    # The broader element, used by filers whose capital spending is not only property and
    # equipment. Same line of the cash-flow statement, so it competes with the tag above
    # rather than adding to it — which is correct, since a filer uses one or the other.
    "PaymentsToAcquireProductiveAssets": "capital_expenditure",
    "PaymentsForRepurchaseOfCommonStock": "share_repurchases",
    "PaymentsOfDividendsCommonStock": "dividends_paid",
    "PaymentsOfDividends": "dividends_paid",
    "ProceedsFromIssuanceOfLongTermDebt": "proceeds_from_debt",
    "ProceedsFromNotesPayable": "proceeds_from_debt",
    "RepaymentsOfLongTermDebt": "repayments_of_debt",
    "RepaymentsOfDebt": "repayments_of_debt",
    "InterestPaidNet": "interest_paid",
    "InterestPaid": "interest_paid",
    "IncomeTaxesPaidNet": "income_taxes_paid",
    "IncomeTaxesPaid": "income_taxes_paid",
    # -- Per share and share counts -------------------------------------------------------
    "CommonStockDividendsPerShareDeclared": "dividends_per_share",
    "WeightedAverageNumberOfSharesOutstandingBasic": "basic_shares_outstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding": "diluted_shares_outstanding",
    "CommonStockSharesOutstanding": "shares_outstanding",
    "EntityCommonStockSharesOutstanding": "shares_outstanding",
}


# Which tag is "revenue" when a filer reports more than one that maps to it, most total
# first. ASC 606's contract-with-customer elements are *components* for any filer whose
# income statement has lines outside ASC 606's scope -- a bank's interest income, most
# obviously -- so where a total and a component coexist for the same period, the total
# is the revenue and the component keeps its own tag (gap A62). EDGAR's companyfacts
# JSON lists tags alphabetically, which put RevenueFromContractWithCustomer* ahead of
# Revenues at the observation-key dedupe and handed the MTB run fee income as revenue.
REVENUE_TAG_PREFERENCE: Final[tuple[str, ...]] = (
    "Revenues",
    "RevenuesNetOfInterestExpense",
    "SalesRevenueNet",
    "Revenue",
    "Turnover",
    "TurnoverRevenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "RevenueFromContractsWithCustomers",
    "SalesRevenueGoodsNet",
    "SalesRevenueServicesNet",
)


def revenue_tag_rank(tag: str) -> int:
    """The tag's place in :data:`REVENUE_TAG_PREFERENCE`; unknown tags rank last.

    Unknown-last, because a tag this table has never ranked cannot be *known* to be more
    total than one it has -- and ranking it best by accident is exactly the alphabetical
    failure this ordering exists to end.
    """
    try:
        return REVENUE_TAG_PREFERENCE.index(tag)
    except ValueError:
        return len(REVENUE_TAG_PREFERENCE)


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
#
# **IFRS 16 removed the lessee's operating/finance distinction**, so `lease_liabilities` is
# one concept here and the us-gaap operating-lease tags map onto it. A comparison of leverage
# across the two regimes is a comparison a reader has to make knowingly, and giving the two a
# different canonical name would only hide that.
IFRS_ALIASES: Final[dict[str, str]] = {
    # -- Income statement ---------------------------------------------------------------
    "Revenue": "revenue",
    "RevenueFromContractsWithCustomers": "revenue",
    "CostOfSales": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    "SellingGeneralAndAdministrativeExpense": "sg_and_a",
    "ResearchAndDevelopmentExpense": "research_and_development",
    "OperatingExpense": "operating_expenses",
    "ProfitLossFromOperatingActivities": "operating_income",
    "OperatingProfitLoss": "operating_income",
    "ImpairmentLossRecognisedInProfitOrLoss": "impairment",
    "ImpairmentLossRecognisedInProfitOrLossGoodwill": "impairment",
    "FinanceCosts": "interest_expense",
    "InterestExpense": "interest_expense",
    "FinanceIncome": "interest_income",
    "RevenueFromInterest": "interest_income",
    "ProfitLossBeforeTax": "pre_tax_income",
    "IncomeTaxExpenseContinuingOperations": "income_tax_expense",
    "ProfitLoss": "net_income",
    "ProfitLossAttributableToOwnersOfParent": "net_income",
    "ProfitLossAttributableToNoncontrollingInterests": "noncontrolling_interest_income",
    "BasicEarningsLossPerShare": "earnings_per_share_basic",
    "DilutedEarningsLossPerShare": "earnings_per_share_diluted",
    # -- Balance sheet: assets ------------------------------------------------------------
    "CashAndCashEquivalents": "cash_and_equivalents",
    "OtherCurrentFinancialAssets": "short_term_investments",
    "CurrentInvestments": "short_term_investments",
    "TradeAndOtherCurrentReceivables": "accounts_receivable",
    "CurrentTradeReceivables": "accounts_receivable",
    "Inventories": "inventory",
    "CurrentAssets": "current_assets",
    "PropertyPlantAndEquipment": "property_plant_and_equipment",
    "Goodwill": "goodwill",
    "IntangibleAssetsOtherThanGoodwill": "intangible_assets",
    "NoncurrentAssets": "noncurrent_assets",
    "Assets": "assets",
    # -- Balance sheet: liabilities and equity ----------------------------------------------
    "TradeAndOtherCurrentPayables": "accounts_payable",
    "CurrentTradePayables": "accounts_payable",
    "CurrentAccruedExpensesAndOtherCurrentLiabilities": "accrued_liabilities",
    "CurrentContractLiabilities": "deferred_revenue",
    "CurrentDeferredIncome": "deferred_revenue",
    "CurrentPortionOfNoncurrentBorrowings": "short_term_debt",
    "BorrowingsCurrent": "short_term_debt",
    "CurrentLiabilities": "current_liabilities",
    "NoncurrentPortionOfNoncurrentBorrowings": "long_term_debt",
    "BorrowingsNoncurrent": "long_term_debt",
    "LeaseLiabilities": "lease_liabilities",
    "NoncurrentLeaseLiabilities": "lease_liabilities",
    "NoncurrentLiabilities": "noncurrent_liabilities",
    "Liabilities": "liabilities",
    "Borrowings": "total_debt",
    "RetainedEarnings": "retained_earnings",
    "TreasuryShares": "treasury_stock",
    "NoncontrollingInterests": "noncontrolling_interests",
    "Equity": "equity",
    "EquityAttributableToOwnersOfParent": "equity",
    # -- Cash flow ----------------------------------------------------------------------
    "CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
    "CashFlowsFromUsedInInvestingActivities": "investing_cash_flow",
    "CashFlowsFromUsedInFinancingActivities": "financing_cash_flow",
    # As in us-gaap: the movement including the currency effect. IFRS's
    # `...BeforeEffectOfExchangeRateChanges` is the excluding variant and is left unmapped for
    # the same reason.
    "IncreaseDecreaseInCashAndCashEquivalents": "net_change_in_cash",
    "EffectOfExchangeRateChangesOnCashAndCashEquivalents": "effect_of_exchange_rate_on_cash",
    "DepreciationAndAmortisationExpense": "depreciation_and_amortisation",
    (
        "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"
    ): "depreciation_and_amortisation",
    "AdjustmentsForShareBasedPayments": "share_based_compensation",
    "AdjustmentsForDeferredTaxExpense": "deferred_income_tax_expense",
    "PurchaseOfPropertyPlantAndEquipment": "capital_expenditure",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
    "PaymentsForRepurchaseOfEntitysOwnEquityInstruments": "share_repurchases",
    "DividendsPaid": "dividends_paid",
    "DividendsPaidClassifiedAsFinancingActivities": "dividends_paid",
    "ProceedsFromBorrowings": "proceeds_from_debt",
    "ProceedsFromBorrowingsClassifiedAsFinancingActivities": "proceeds_from_debt",
    "RepaymentsOfBorrowings": "repayments_of_debt",
    "RepaymentsOfBorrowingsClassifiedAsFinancingActivities": "repayments_of_debt",
    "InterestPaidClassifiedAsOperatingActivities": "interest_paid",
    "IncomeTaxesPaidClassifiedAsOperatingActivities": "income_taxes_paid",
    # -- Per share and share counts -------------------------------------------------------
    "DividendsPaidOrdinarySharesPerShare": "dividends_per_share",
    # The ifrs-full spellings differ from the us-gaap ones, and the diluted count is
    # "adjusted" rather than "diluted" — a filing tagged correctly under IFRS would miss the
    # us-gaap name entirely.
    "WeightedAverageNumberOfOrdinarySharesOutstanding": "basic_shares_outstanding",
    "AdjustedWeightedAverageNumberOfOrdinarySharesOutstanding": "diluted_shares_outstanding",
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
    "ProfitLossOnOrdinaryActivitiesBeforeTax": "pre_tax_income",
    "TaxOnProfitOrLossOnOrdinaryActivities": "income_tax_expense",
    # `TotalAssetsLessCurrentLiabilities` is deliberately **not** mapped. It is the Companies
    # Act format's subtotal -- fixed assets plus current assets less creditors falling due
    # within one year -- and it is not non-current assets, nor total assets, nor anything else
    # in this vocabulary. Mapping it to the nearest-looking concept would put a figure that is
    # wrong by (current assets - current liabilities) into a balance sheet that would still
    # appear to balance. Left unmapped, it reaches the UK financials gate and a person.
    "Equity": "equity",
    "CashBankOnHand": "cash_and_equivalents",
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


def is_magnitude(concept: str) -> bool:
    """Whether this concept is reported as a positive magnitude rather than a signed flow.

    See :data:`MAGNITUDE_CONCEPTS`. Callers combining cash-flow lines must consult this
    rather than assuming a sign — free cash flow subtracts capital expenditure because the
    filer reported a payment, not a negative.
    """
    return concept in MAGNITUDE_CONCEPTS
