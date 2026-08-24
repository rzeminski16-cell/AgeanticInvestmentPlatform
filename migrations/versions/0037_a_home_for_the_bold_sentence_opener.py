"""A structured home for what the model bolds a sentence opener to say.

Gap R6, second half. The live note's Growth Outlook and Scenarios sections opened
paragraphs with ``**Base case.**`` and ``**What the evidence supports.**`` — markdown
emphasis in a flat prose field, printed to the reader as literal asterisks. The renderer
now strips inline markdown from prose deterministically; this migration gives the urge a
proper field, so the emphasis survives as structure instead of dying as notation.

``commentary`` on the two offending sections becomes an array of prose blocks — each a
``text`` with an optional short ``lead_in`` the renderer emphasises. New versions rather
than edits, because contracts are pinned by running jobs (the rule ``registry.py``
states); everything except the one property is read from the live row, so the policy
merges of 0029, 0031, 0034 and the applicability of 0035 carry over untouched.

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_KEYS = ("growth_outlook", "scenarios_sensitivities")
_NEW_VERSION = 2

_BLOCK_ITEMS: dict[str, Any] = {
    "type": "object",
    "required": ["text"],
    "properties": {
        "lead_in": {
            "type": "string",
            "title": "Lead-in",
            "description": (
                "A short label this block opens with — 'Base case', 'What the evidence "
                "supports'. The renderer emphasises it; never write markdown emphasis "
                "in prose."
            ),
        },
        "text": {"type": "string", "title": "Text", "description": "The block's prose."},
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

        # json.loads preserves the declared property order the column exists to keep;
        # only the one property is replaced, in place, so the rendered order is v1's.
        contract = json.loads(stored)
        prose = contract["properties"]["commentary"]
        contract["properties"]["commentary"] = {
            "type": "array",
            "title": str(prose.get("title") or "Commentary"),
            "description": (
                f"{prose.get('description', '')} Written as blocks, each an optional "
                "lead-in and its prose."
            ).strip(),
            "items": _BLOCK_ITEMS,
        }

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
