"""Every exhibit stands beside the analysis it supports.

Gap N1. The live note rendered its charts as a pack at the back, pages away from the
sections discussing the same figures — a reader met the segment analysis on page nine
and the segment mix chart on page thirty-one. Each section that discusses an exhibit's
subject now claims it by key in its ``evidence_policy``, and the assembler renders a
claimed chart directly after that section, footnotes numbered in reading order. A chart
no section claims keeps the pack at the back, so nothing is ever dropped for want of a
claim.

Seeded as rows because the exhibit-to-section mapping is presentation data, and the
repository rule holds: no section key in code. Additive and builtin-scoped, and version-
unqualified so the 0037 revisions inherit their claims too.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

_EXHIBITS: dict[str, list[str]] = {
    "historical_financial_analysis": ["revenue_margin_history"],
    "segment_analysis": ["segment_mix"],
    "scenarios_sensitivities": ["scenario_bridge", "sensitivity_heatmap"],
    "valuation_dcf": ["football_field"],
}


def upgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy || CAST(:merged AS jsonb) "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key, charts in _EXHIBITS.items():
        bind.execute(statement, {"merged": json.dumps({"exhibits": charts}), "key": key})


def downgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy - 'exhibits' "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _EXHIBITS:
        bind.execute(statement, {"key": key})
