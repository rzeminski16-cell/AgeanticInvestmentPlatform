"""Each model-written section declares what evidence it most wants — as data.

Gap A39's fix ranks a section's facts by concept preference and its excerpts by keyword
affinity. The first cut held those preferences in code, keyed by section — and the
hardcoded-key guard refused it, correctly: sections are rows, and a module that names one
has made the next section a code change. So the preferences move into the rows they
describe, merged into each seeded definition's ``evidence_policy``:

- ``concept_priority``: canonical concepts (``aer.core.concepts``) in the order this
  section wants them. Facts ranked by position here, then recency; unnamed canonical
  concepts follow, unmapped tags last. An absent or empty list means the shared default
  order in ``aer.sections.evidence`` — the statements' lines ahead of the alphabet.
- ``excerpt_keywords``: lowercase substrings scored against extracted excerpts. They
  rank; they never filter.

A new section arrives with its preferences the same way it arrives at all: as a row.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_INCOME = [
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_expenses",
    "sg_and_a",
    "research_and_development",
    "operating_income",
    "interest_expense",
    "interest_income",
    "pre_tax_income",
    "income_tax_expense",
    "net_income",
    "earnings_per_share_basic",
    "earnings_per_share_diluted",
]
_BALANCE = [
    "cash_and_equivalents",
    "short_term_investments",
    "accounts_receivable",
    "inventory",
    "current_assets",
    "property_plant_and_equipment",
    "goodwill",
    "intangible_assets",
    "assets",
    "accounts_payable",
    "deferred_revenue",
    "short_term_debt",
    "current_liabilities",
    "long_term_debt",
    "lease_liabilities",
    "liabilities",
    "total_debt",
    "retained_earnings",
    "equity",
]
_CASH_FLOW = [
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capital_expenditure",
    "depreciation_and_amortisation",
    "share_based_compensation",
    "share_repurchases",
    "dividends_paid",
    "proceeds_from_debt",
    "repayments_of_debt",
    "net_change_in_cash",
]
_SHARES = [
    "shares_outstanding",
    "diluted_shares_outstanding",
    "basic_shares_outstanding",
    "dividends_per_share",
]

_PREFERENCES: dict[str, dict[str, list[str]]] = {
    "executive_summary": {
        "concept_priority": _INCOME + _CASH_FLOW + _BALANCE,
        "excerpt_keywords": ["revenue", "operating income", "outlook", "growth", "segment"],
    },
    "investment_thesis": {
        "concept_priority": _INCOME + _CASH_FLOW + _BALANCE,
        "excerpt_keywords": ["growth", "margin", "competitive", "strategy", "outlook"],
    },
    "business_overview": {
        "concept_priority": _INCOME + _SHARES,
        "excerpt_keywords": ["segment", "products", "services", "customers", "business"],
    },
    "segment_analysis": {
        "concept_priority": _INCOME,
        "excerpt_keywords": ["segment", "revenue", "operating income", "disaggregat"],
    },
    "industry_landscape": {
        "concept_priority": _INCOME,
        "excerpt_keywords": ["competit", "market", "industry", "demand", "pricing"],
    },
    "management_governance": {
        "concept_priority": _SHARES + _INCOME,
        "excerpt_keywords": [
            "director",
            "officer",
            "board",
            "compensation",
            "governance",
            "proxy",
        ],
    },
    "historical_financial_analysis": {
        "concept_priority": _INCOME + _BALANCE + _CASH_FLOW,
        "excerpt_keywords": [
            "revenue",
            "cost of revenue",
            "operating expenses",
            "operating income",
            "net income",
            "results of operations",
        ],
    },
    "earnings_quality": {
        "concept_priority": _CASH_FLOW + _INCOME + _BALANCE,
        "excerpt_keywords": ["accru", "deferred", "recognition", "estimate", "judgment"],
    },
    "balance_sheet_liquidity": {
        "concept_priority": _BALANCE + _CASH_FLOW,
        "excerpt_keywords": [
            "debt",
            "maturit",
            "liquidity",
            "credit facilit",
            "commercial paper",
            "lease",
            "unearned revenue",
            "cash and cash equivalents",
        ],
    },
    "cash_flow_analysis": {
        "concept_priority": _CASH_FLOW + _INCOME + _BALANCE,
        "excerpt_keywords": [
            "cash flow",
            "operating activities",
            "investing activities",
            "financing activities",
            "capital expenditure",
            "property and equipment",
        ],
    },
    "capital_allocation": {
        "concept_priority": _CASH_FLOW + _SHARES + _BALANCE,
        "excerpt_keywords": [
            "dividend",
            "repurchase",
            "buyback",
            "acquisition",
            "capital expenditure",
        ],
    },
    "growth_outlook": {
        "concept_priority": _INCOME + _CASH_FLOW,
        "excerpt_keywords": ["growth", "outlook", "guidance", "demand", "backlog", "obligation"],
    },
    "valuation_dcf": {
        "concept_priority": _CASH_FLOW + _INCOME + _BALANCE + _SHARES,
        "excerpt_keywords": ["cash flow", "capital expenditure", "share", "discount", "growth"],
    },
    "scenarios_sensitivities": {
        "concept_priority": _INCOME + _CASH_FLOW,
        "excerpt_keywords": ["risk", "growth", "margin", "demand", "assumption"],
    },
    "key_risks": {
        "concept_priority": _BALANCE + _INCOME + _CASH_FLOW,
        "excerpt_keywords": [
            "risk",
            "litigation",
            "regulat",
            "competit",
            "contingenc",
            "proceedings",
        ],
    },
    "catalysts": {
        "concept_priority": _INCOME + _CASH_FLOW,
        "excerpt_keywords": ["announce", "launch", "guidance", "outlook", "agreement"],
    },
}


def upgrade() -> None:
    # Bound, not interpolated. The keys and payloads here are module constants, so nothing
    # hostile could reach the string — but the hand-rolled quote doubling it replaced is the
    # kind of escaping that is correct until the day a value contains something it did not
    # anticipate, and the driver already does this properly.
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy || CAST(:merged AS jsonb) "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key, preferences in _PREFERENCES.items():
        bind.execute(statement, {"merged": json.dumps(preferences), "key": key})


def downgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = (evidence_policy - 'concept_priority') - 'excerpt_keywords' "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _PREFERENCES:
        bind.execute(statement, {"key": key})
