"""A catalyst is an event, not a date in the reporting calendar.

Report-quality R7. A live note's catalyst section listed nothing but scheduled SEC
filings, each dated by extrapolation from the previous two, and spent its prose explaining
what a filing is. The refusal now lives in code (``routine_filing_catalysts``), but a rule
a writer only meets *after* it has spent its budget is a rule that costs a redraft every
time. ``catalysts`` v2 says the same thing in the contract the writer is given: what
qualifies, what does not, and that an empty list is the honest answer rather than a
failure to try.

The shape is unchanged — same fields, same order, same requirements — so a report already
rendered renders identically. A new version rather than an edit, for the rule
``registry.py`` states: contracts are pinned by running jobs.

Revision ID: 0052
Revises: 0051
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

_KEY = "catalysts"
_NEW_VERSION = 2

_CONTRACT_V2: dict[str, object] = {
    "type": "object",
    "title": "Catalysts",
    "required": ["commentary"],
    "properties": {
        "commentary": {
            "type": "string",
            "title": "Commentary",
            "description": (
                "What could move the shares towards or away from the view, and when. "
                "Where the evidence dates no catalyst, say that in a sentence and name "
                "what would have to be disclosed for one to exist. Two sentences is a "
                "complete answer to this section; padding it is not."
            ),
        },
        "catalysts": {
            "type": "array",
            "title": "Catalysts",
            "description": (
                "Dated events the evidence discloses: an announced investor day, a "
                "decision with a statutory deadline, a transaction with an expected "
                "completion, a contract or facility with a stated expiry, a disposal "
                "whose comparatives clear on a known date. Leave the list empty when the "
                "evidence dates none — an empty list is the correct answer, not a gap. "
                "A scheduled periodic filing is not a catalyst and is refused: that a "
                "company will publish a 10-Q, an annual report or a results announcement "
                "is certain and tells a reader nothing, and what those documents will say "
                "is not knowable in advance. A date inferred from how often the company "
                "has filed before is refused for the same reason."
            ),
            "items": {
                "type": "object",
                "required": ["label", "expected_timing", "rationale"],
                "properties": {
                    "label": {"type": "string"},
                    "expected_timing": {"type": "string"},
                    "direction": {"type": "string"},
                    "rationale": {"type": "string"},
                    "source_document_id": {"type": "string"},
                },
            },
        },
    },
}


def upgrade() -> None:
    # Everything but the contract is copied from the version the database holds, so the
    # new version inherits whatever policy the old one had accrued rather than a snapshot
    # of what an earlier migration seeded.
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
