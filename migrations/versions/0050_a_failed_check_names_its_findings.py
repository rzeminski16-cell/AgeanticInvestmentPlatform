"""A failed check names its findings where the failure is announced.

Gap A60. The live run's ``presentation_integrity`` failed, the coverage notice named the
metric — which is right — and nothing anywhere named the *finding*, so the operator could
not act on the failure without opening the approval page and digging into a JSONB column.
The findings were already recorded: every metric stores its failure strings in the
evaluation row's details. What was missing is the surface.

``validation_disagreements`` v3 therefore declares a ``failed_check_findings`` table —
one row per finding, metric beside it — rendered between the metric table and the
disagreements. Empty on a clean run, so the section's shape does not change for a report
with nothing to confess. A new version rather than an edit, for the rule ``registry.py``
states: contracts are pinned by running jobs, and a report already rendered must not
re-render differently.

Revision ID: 0050
Revises: 0049
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

_KEY = "validation_disagreements"
_NEW_VERSION = 3

# Declared order is display order (the contract column is ``json``, which preserves it).
# The v2 shape with one addition: the findings of the checks that failed, placed directly
# under the metric table that announces the failure.
_CONTRACT_V3: dict[str, object] = {
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
            "items": {
                "type": "object",
                "required": ["topic", "resolution"],
                "properties": {
                    "topic": {"type": "string"},
                    "severity": {"type": "string"},
                    "challenge": {"type": "string"},
                    "basis": {"type": "string"},
                    "kind": {"type": "string"},
                    "resolution": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}


def upgrade() -> None:
    # Everything except the contract is copied from the highest version the database
    # holds, so the new version inherits whatever policy merges the old one had accrued
    # rather than a snapshot of what an earlier migration seeded.
    op.get_bind().execute(
        sa.text(
            "INSERT INTO section_definitions "
            "  (key, version, origin, title, position, required, output_contract, "
            "   evidence_policy, token_budget, allowed_tools, applicability) "
            "SELECT key, :version, origin, title, position, required, "
            "       CAST(:contract AS json), "
            "       evidence_policy, token_budget, allowed_tools, applicability "
            "FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = 2"
        ),
        {"key": _KEY, "version": _NEW_VERSION, "contract": json.dumps(_CONTRACT_V3)},
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
