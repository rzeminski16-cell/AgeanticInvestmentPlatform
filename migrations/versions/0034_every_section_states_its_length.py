"""Every built-in section states its length, in words, on its own row.

Gap O4. The live note ran to forty pages for a company with three quarters of data in
evidence, because length was bounded only by the token ceiling — an economic limit, not
an editorial one. The budget joins the section's evidence preferences (migration 0029):
stated to the writer as a target, refused in code past a headroom factor, and a row that
never declares one stays unbounded exactly as before. Additive and builtin-scoped, like
0029 and 0031.

The numbers weight the note the way an institutional reader does: the summary is a page,
the history and the valuation carry the analysis, and the administrative sections say
their piece briefly.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_WORD_BUDGETS: dict[str, int] = {
    "executive_summary": 250,
    "investment_thesis": 400,
    "business_overview": 400,
    "segment_analysis": 350,
    "industry_landscape": 350,
    "management_governance": 300,
    "historical_financial_analysis": 500,
    "earnings_quality": 350,
    "balance_sheet_liquidity": 350,
    "cash_flow_analysis": 350,
    "capital_allocation": 300,
    "growth_outlook": 350,
    "valuation_dcf": 500,
    "scenarios_sensitivities": 350,
    "key_risks": 400,
    "catalysts": 250,
    "prior_research_comparison": 250,
    "validation_disagreements": 250,
}


def upgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy || CAST(:merged AS jsonb) "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key, budget in _WORD_BUDGETS.items():
        bind.execute(statement, {"merged": f'{{"word_budget": {budget}}}', "key": key})


def downgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy - 'word_budget' "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _WORD_BUDGETS:
        bind.execute(statement, {"key": key})
