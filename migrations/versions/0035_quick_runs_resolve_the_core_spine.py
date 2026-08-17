"""A quick run resolves the core spine; the deep-dive sections ask for more depth.

Gap O5's other half. ``analysis_mode`` reached one line of the planner's prompt and
gated custom-skill applicability — a control that only whispers to a model is the same
family as a cap that only warns. The deep-dive sections now declare, on their own rows
and in the applicability language the registry already evaluates, that they apply to
standard and full runs; a quick run resolves the core spine. No code names a section
key, which is the rule that keeps sections rows.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

# The sections a quick run does without: the deep dives and the housekeeping. The core
# spine — summary, thesis, business, history, balance sheet, cash flow, valuation,
# risks, disagreements — still renders at every depth.
_DEEP_DIVE_SECTIONS = (
    "segment_analysis",
    "industry_landscape",
    "management_governance",
    "earnings_quality",
    "capital_allocation",
    "growth_outlook",
    "scenarios_sensitivities",
    "catalysts",
    "prior_research_comparison",
)

_PREDICATE = '{"analysis_mode": ["standard", "full"]}'


def upgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET applicability = COALESCE(applicability, CAST('{}' AS jsonb)) "
        "|| CAST(:merged AS jsonb) "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _DEEP_DIVE_SECTIONS:
        bind.execute(statement, {"merged": _PREDICATE, "key": key})


def downgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET applicability = applicability - 'analysis_mode' "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _DEEP_DIVE_SECTIONS:
        bind.execute(statement, {"key": key})
