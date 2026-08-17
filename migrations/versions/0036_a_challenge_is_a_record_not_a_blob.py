"""A challenge is a record, not a blob stuffed into the rationale.

Gap R5. The live note's disagreements appendix printed each red-team challenge three
times — once truncated in the topic, once in the summary, once behind a "Challenge:"
label — followed by a comma-separated list of raw UUIDs, because the recording service
composed the statement, the basis and the evidence ids into ``resolution_rationale`` as
one string. A blob can only ever be reprinted; a record can be laid out.

Two changes, one per half of the fix:

* ``disagreements.detail`` — nullable JSONB carrying the structured parts of a challenge
  (statement, basis, severity, dimension, evidence ids by kind). Nullable because most
  disagreements are source conflicts with nothing beyond the ladder's own rationale, and
  an empty object on every one of those rows would imply structure that is not there.

* ``validation_disagreements`` v2 — the section contract now declares the structured
  columns (topic, severity, challenge, basis) beside the original four, so the renderer
  lays a red-team row out as a table row with footnotes rather than a paragraph of ids.
  A new version rather than an edit, because contracts are pinned by running jobs and a
  report already rendered must not re-render differently (the rule ``registry.py`` states).

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_KEY = "validation_disagreements"
_NEW_VERSION = 2

# Declared order is display order (the contract column is ``json``, which preserves it).
# ``required`` names only what every row shape shares: a red-team challenge carries
# severity/challenge/basis and no rationale, a source conflict the reverse.
_CONTRACT_V2: dict[str, object] = {
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
    op.add_column("disagreements", sa.Column("detail", JSONB, nullable=True))

    # Everything except the contract is copied from the row a fresh database already
    # carries — policy merges from 0031 and 0034 included — so the new version inherits
    # whatever the old one had accrued rather than a snapshot of what 0023 seeded.
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
        {"key": _KEY, "version": _NEW_VERSION, "contract": json.dumps(_CONTRACT_V2)},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = :version"
        ),
        {"key": _KEY, "version": _NEW_VERSION},
    )
    op.drop_column("disagreements", "detail")
