"""The valuation method is rendered, not written.

ADR 0063. The first complete report's DCF section described beta regressions, bond-yield
curves and market-value weights — a methodology the run never executed — and every
existing defence passed it, because the section stated almost no figures and quoted
nothing. A section can evade the whole validation apparatus by being confidently
qualitative, and prose about method is exactly where a reader's trust is set.

This migration inserts version 2 of the ``valuation_dcf`` definition, following 0039's
pattern of versioned contracts. The method fields — how the figures were produced, every
cost-of-capital component with how it was set, the forecast drivers named as assumptions,
both terminal methods and the valuation's recorded caveats — are marked
``"platform_filled": true``: :mod:`aer.sections.valuation_method` renders them from the
calculation ledger, the model's schema never carries them, and the model's remaining
``commentary`` field is constrained to interpreting the figures rendered above it. The
old ``key_assumptions`` and ``figures`` fields go: both asked the model to restate the
record, which is the invitation this ADR withdraws.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

_KEY = "valuation_dcf"
_NEW_VERSION = 2


def _record_row(*, cited: bool) -> dict[str, Any]:
    """The shared shape of a platform-rendered row: what it is, its value, how it was set."""
    properties: dict[str, Any] = {
        "label": {"type": "string"},
        "value": {"type": "string"},
        "unit": {"type": "string"},
        "provenance": {"type": "string"},
    }
    if cited:
        properties["calculation_id"] = {"type": "string"}
    return {
        "type": "object",
        "required": ["label", "value", "unit", "provenance"],
        "properties": properties,
    }


_CONTRACT: dict[str, Any] = {
    "type": "object",
    "title": "Valuation \N{EM DASH} Discounted Cash Flow",
    "required": ["commentary"],
    "properties": {
        "method_note": {
            "type": "string",
            "title": "How These Figures Were Produced",
            "platform_filled": True,
        },
        "cost_of_capital": {
            "type": "array",
            "title": "Cost of Capital",
            "platform_filled": True,
            "items": _record_row(cited=True),
        },
        "forecast_drivers": {
            "type": "array",
            "title": "Forecast Assumptions",
            "platform_filled": True,
            "items": _record_row(cited=False),
        },
        "terminal_valuations": {
            "type": "array",
            "title": "The Two Terminal Methods",
            "platform_filled": True,
            "items": _record_row(cited=True),
        },
        "valuation_caveats": {
            "type": "array",
            "title": "Recorded Caveats",
            "platform_filled": True,
            "items": {"type": "string"},
        },
        "commentary": {
            "type": "string",
            "title": "Commentary",
            "description": (
                "What the figures rendered above conclude and what they turn on \N{EM DASH} "
                "interpretation only. The method, the inputs and their provenance are "
                "already stated above this field from the run's own records; do not "
                "describe how any input was derived or obtained, and do not name data the "
                "run does not hold, such as market prices, bond yields or return series."
            ),
        },
    },
}


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO section_definitions "
            "  (key, version, origin, title, position, required, output_contract, "
            "   evidence_policy, token_budget, allowed_tools, applicability) "
            "SELECT key, :version, origin, title, position, required, "
            "       CAST(:contract AS json), "
            "       evidence_policy, token_budget, allowed_tools, applicability "
            "FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = 1"
        ),
        {"key": _KEY, "version": _NEW_VERSION, "contract": json.dumps(_CONTRACT)},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = :version"
        ),
        {"key": _KEY, "version": _NEW_VERSION},
    )
