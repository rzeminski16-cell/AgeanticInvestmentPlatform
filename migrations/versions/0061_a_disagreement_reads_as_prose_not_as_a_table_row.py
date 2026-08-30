"""A disagreement reads as prose, not as a table row.

Roadmap §2.4. The live document put a two-hundred-word red-team challenge in a narrow
table column: one row spanned three pages and neither position could be read. The column
layout was the v2 contract's decision (migration 0036), made when the challenge became a
record — right about the record, wrong about the page. A challenge is an argument, and an
argument is prose.

``validation_disagreements`` v4 therefore declares the ``disagreements`` array in the
renderer's prose-block shape — objects of ``text`` with an optional ``lead_in`` — which
the section renderer lays out as paragraphs rather than as a table. Each recorded conflict
becomes a short run of them: the challenge under its identity, its basis, its resolution.
The evidence still rides the citation keys, exactly as the table rows carried them, so gap
R5's guarantees hold unchanged: the statement appears once, and no UUID reaches a reader.

A new version rather than an edit, for the rule ``registry.py`` states: contracts are
pinned by running jobs, and a report already rendered must not re-render differently.

Revision ID: 0061
Revises: 0060
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

_KEY = "validation_disagreements"
_NEW_VERSION = 4

# Declared order is display order (the contract column is ``json``, which preserves it).
# The v3 shape with one change: ``disagreements`` items are prose blocks, not rows.
_CONTRACT_V4: dict[str, object] = {
    "type": "object",
    "title": "Validation & Disagreements",
    "required": ["summary", "validations"],
    "properties": {
        "summary": {
            "type": "string",
            "title": "Summary",
            "description": "What the run's validators measured, in one paragraph.",
        },
        "validations": {
            "type": "array",
            "title": "Validation Metrics",
            "items": {
                "type": "object",
                "required": ["metric", "score", "threshold", "verdict"],
                "properties": {
                    "metric": {"type": "string"},
                    "score": {"type": "string"},
                    "threshold": {"type": "string"},
                    "verdict": {"type": "string"},
                },
            },
        },
        "failed_check_findings": {
            "type": "array",
            "title": "What the Failed Checks Found",
            "items": {
                "type": "object",
                "required": ["metric", "finding"],
                "properties": {
                    "metric": {"type": "string"},
                    "finding": {"type": "string"},
                },
            },
        },
        "disagreements": {
            "type": "array",
            "title": "Disagreements",
            "description": (
                "Prose blocks, a short run per recorded conflict: the challenge under its "
                "identity, then its basis, then its resolution. The renderer's prose-block "
                "convention lays each out as a paragraph with the lead-in emphasised."
            ),
            "items": {
                "type": "object",
                "required": ["text"],
                "properties": {
                    "lead_in": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
        },
    },
}


def upgrade() -> None:
    # Everything except the contract is copied from the previous version, so the new one
    # inherits whatever policy merges the old one had accrued rather than a snapshot of
    # what an earlier migration seeded.
    op.get_bind().execute(
        sa.text(
            "INSERT INTO section_definitions "
            "  (key, version, origin, title, position, required, output_contract, "
            "   evidence_policy, token_budget, allowed_tools, applicability) "
            "SELECT key, :version, origin, title, position, required, "
            "       CAST(:contract AS json), "
            "       evidence_policy, token_budget, allowed_tools, applicability "
            "FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = 3"
        ),
        {"key": _KEY, "version": _NEW_VERSION, "contract": json.dumps(_CONTRACT_V4)},
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
    _refuse_if_a_report_cites_it(bind, _KEY)
    bind.execute(
        sa.text(
            "DELETE FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = :version"
        ),
        {"key": _KEY, "version": _NEW_VERSION},
    )
