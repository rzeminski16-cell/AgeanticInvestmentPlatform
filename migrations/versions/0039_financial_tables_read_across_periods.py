"""Financial tables read across periods, not down a list of labels.

Gap R9. The live note's historical analysis carried sixteen rows shaped
"Revenue, quarter ended December 27, 2025 (unaudited) | $143,756 million | USD" — one
per figure, the period baked into the label, the trend invisible without arithmetic. A
reader of a financial table wants periods across the top and line items down the side.

The three statement sections gain a ``financials`` field: one row per line item, each
row a list of period-keyed values, every value naming its stored figure and citing its
source — the series shape the renderer lays out as a period-indexed table with a
footnote per cell. ``commentary`` and ``figures`` stay: prose is still prose, and a
one-off figure (a ratio, a ceiling) still has its row shape. New versions rather than
edits, for the pinning rule ``registry.py`` states; everything except the contract is
copied from the live row.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

_KEYS = ("historical_financial_analysis", "balance_sheet_liquidity", "cash_flow_analysis")
_NEW_VERSION = 2

_SERIES_FIELD: dict[str, Any] = {
    "type": "array",
    "title": "Financial History",
    "description": (
        "Period-indexed line items: one row per line item, the same periods on every "
        "row, oldest first, so the table reads across. Never bake a period into a "
        "label — the period belongs on the value."
    ),
    "items": {
        "type": "object",
        "required": ["label", "values"],
        "properties": {
            "label": {
                "type": "string",
                "description": (
                    "The line item — 'Revenue', 'Operating margin' — with no period in it."
                ),
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["period", "value"],
                    "properties": {
                        "period": {
                            "type": "string",
                            "description": (
                                "The reporting period and basis, e.g. 'FY2023' or 'Q1 FY2024'."
                            ),
                        },
                        "value": {"type": "string"},
                        "unit": {"type": "string"},
                        "financial_fact_id": {"type": "string"},
                        "calculation_id": {"type": "string"},
                        "source_document_id": {"type": "string"},
                    },
                },
            },
        },
    },
}


def upgrade() -> None:
    bind = op.get_bind()
    for key in _KEYS:
        stored = bind.execute(
            sa.text(
                "SELECT output_contract::text FROM section_definitions "
                "WHERE key = :key AND origin = 'builtin' AND version = 1"
            ),
            {"key": key},
        ).scalar_one()

        # Rebuilt in declared order with the series slotted directly after the prose:
        # commentary, then the statement table, then whatever one-off figures follow.
        contract = json.loads(stored)
        properties: dict[str, Any] = {}
        for name, spec in contract["properties"].items():
            properties[name] = spec
            if name == "commentary":
                properties["financials"] = _SERIES_FIELD
        contract["properties"] = properties

        bind.execute(
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
            {"key": key, "version": _NEW_VERSION, "contract": json.dumps(contract)},
        )


def _refuse_if_a_report_cites_it(bind: sa.Connection, key: str) -> None:
    """Refuse before Postgres does, because only one of the two answers "now what?".

    ``report_sections.section_definition_id`` is ``ON DELETE RESTRICT`` deliberately: a
    stored report's own content is not a migration's to delete. So once a run has written a
    section against this version the delete below cannot succeed, and what the database
    returns is a constraint name. This returns a remedy.
    """
    cited = bind.execute(
        sa.text(
            "SELECT count(*) FROM report_sections rs "
            "JOIN section_definitions sd ON sd.id = rs.section_definition_id "
            "WHERE sd.key = :key AND sd.origin = 'builtin' AND sd.version = :version"
        ),
        {"key": key, "version": _NEW_VERSION},
    ).scalar_one()
    if cited:
        message = (
            f"{cited} stored report section(s) cite {key!r} at version {_NEW_VERSION}, so "
            "this downgrade would delete a definition a report still rests on. Clear the "
            "research data first -- `just reset-research` empties report_sections and leaves "
            "section_definitions alone -- or downgrade a database that has produced no "
            "reports."
        )
        raise RuntimeError(message)


def downgrade() -> None:
    bind = op.get_bind()
    for key in _KEYS:
        _refuse_if_a_report_cites_it(bind, key)
    for key in _KEYS:
        bind.execute(
            sa.text(
                "DELETE FROM section_definitions "
                "WHERE key = :key AND origin = 'builtin' AND version = :version"
            ),
            {"key": key, "version": _NEW_VERSION},
        )
