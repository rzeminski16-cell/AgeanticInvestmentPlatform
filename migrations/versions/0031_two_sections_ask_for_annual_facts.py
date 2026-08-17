"""The two sections built on full-year arithmetic declare an annual fact basis.

The ratio suite and the earnings-quality signals are computed on fiscal-year facts only —
deliberately, in the analysis pass. But the evidence gatherer offered every section its
facts newest-period-first regardless of basis, so the sections *discussing* those annual
figures were handed quarterly and year-to-date rows to put beside them. Page 11 of the
live AAPL report compared a quarterly revenue against an annual EBITDA in a single
sentence and called the result a margin.

``fact_basis`` joins the section's evidence preferences (migration 0029): "annual" for
the historical analysis and earnings quality — the sections whose argument *is* the
full-year record — and undeclared (meaning "any") for everything else, where the newest
quarter is legitimately the subject. Additive and builtin-scoped, like 0029: custom
sections are untouched, and a section that never declares a basis behaves exactly as
before.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_ANNUAL_SECTIONS = ("historical_financial_analysis", "earnings_quality")


def upgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy || CAST(:merged AS jsonb) "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _ANNUAL_SECTIONS:
        bind.execute(statement, {"merged": '{"fact_basis": "annual"}', "key": key})


def downgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy - 'fact_basis' "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _ANNUAL_SECTIONS:
        bind.execute(statement, {"key": key})
