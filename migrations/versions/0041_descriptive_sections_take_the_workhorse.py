"""The descriptive sections bill at the workhorse route.

Gap O1. The live run's draft step cost £5.61 of £7.34 — sixteen sections on Opus at high
effort. The judgement sections plausibly need that model; the descriptive ones do not,
and which is which is a property of the section, so it lives on the definition row:
``evidence_policy.writer_role`` names a configured route, and the writer bills there
while keeping the ``report_writer`` role's capabilities untouched.

The route itself (``section_writer_workhorse``) is configuration — ``AER_MODEL_ROUTES``
overrides it wholesale, so changing the cost profile stays a config edit, never a code
change. A row naming a route the router does not configure costs the saving, not the
section.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

# The sections whose job is to describe rather than to judge. The summary, the thesis,
# the statement analyses, the valuation, the scenarios and the risks stay on the
# report_writer route.
_KEYS = (
    "business_overview",
    "segment_analysis",
    "industry_landscape",
    "management_governance",
    "capital_allocation",
    "catalysts",
)


def upgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy || "
        'CAST(\'{"writer_role": "section_writer_workhorse"}\' AS jsonb) '
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _KEYS:
        bind.execute(statement, {"key": key})


def downgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy - 'writer_role' "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _KEYS:
        bind.execute(statement, {"key": key})
