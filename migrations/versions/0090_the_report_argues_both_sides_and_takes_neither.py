"""The report argues both sides and takes neither (ADR 0135).

Two sections asked the model for a view the platform says it does not state. The executive
summary's ``thesis`` field was *"the central view, in two or three sentences"*; the investment
thesis section's ``thesis_statement`` was *"the central view and why it is held"*, beside
supporting pillars for it and what would change it. The operator decided on 25 September
2026 that the report takes no side: the view is theirs, written in a thesis and a decision
after the report is read.

So, as new versions rather than edits — contracts are pinned by the runs that used them, and
a report already rendered must not re-render differently:

* ``executive_summary`` v3 is v2 with ``thesis`` replaced, in the same place, by ``summary``:
  what the evidence shows, and no view on the shares. Read from the stored v2 rather than
  restated, so whatever the contract has accrued since it was seeded is kept.
* ``investment_thesis`` v2 is **The Case For and the Case Against**: the question the two
  cases turn on, then each case in the renderer's prose-block shape, each point free to name
  one lever from the run's list, and beside each case a table the platform fills with what
  its levers give. The key is kept because a key is an identity: a refresh compares sections
  by key, and the version says which contract wrote each one.

Revision ID: 0090
Revises: 0089
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

_SUMMARY_KEY = "executive_summary"
_SUMMARY_FROM = 2
_SUMMARY_TO = 3

_CASES_KEY = "investment_thesis"
_CASES_FROM = 1
_CASES_TO = 2
_CASES_TITLE = "The Case For and the Case Against"

_SUMMARY_FIELD: dict[str, Any] = {
    "type": "string",
    "title": "Summary",
    "description": (
        "What the evidence shows about the business, in two or three sentences, and the "
        "question it leaves open. State no view on the shares: the report sets out the case "
        "for and the case against in their own section, and takes neither side."
    ),
}


def _point(side: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["lead_in", "text"],
        "properties": {
            "lead_in": {
                "type": "string",
                "description": f"The point of {side} in a few words, as a heading would put it.",
            },
            "text": {
                "type": "string",
                "description": (
                    f"The argument of {side}, from the evidence, as its strongest advocate "
                    "would make it, in two to four sentences. Cite what it rests on."
                ),
            },
            "calculation_id": {"type": "string"},
            "source_document_id": {"type": "string"},
            "lever": {
                "type": "string",
                "description": (
                    "Optional. The one lever from this run's list that the point turns on, by "
                    "its key exactly as listed. The platform strikes the valuation with it and "
                    "prints what it gives; write no figure for it."
                ),
            },
        },
    }


def _priced(side: str) -> dict[str, Any]:
    return {
        "type": "array",
        "title": f"{side}, Priced",
        "description": (
            "Filled by the platform: each lever the case names, its value on the record, and "
            "the value per share by each terminal method with that one input moved."
        ),
        "platform_filled": True,
        "items": {
            "type": "object",
            "required": ["point", "label", "value", "unit"],
            "properties": {
                "point": {"type": "string"},
                "label": {"type": "string"},
                "value": {"type": "string"},
                "unit": {"type": "string"},
                "calculation_id": {"type": "string"},
            },
        },
    }


# Declared order is display order (the contract column is ``json``, which preserves it).
_CASES_CONTRACT: dict[str, Any] = {
    "type": "object",
    "title": _CASES_TITLE,
    "required": ["the_question", "case_for", "case_against"],
    "properties": {
        "the_question": {
            "type": "string",
            "title": "What the Two Cases Turn On",
            "description": (
                "One or two sentences naming the question the two cases answer differently. "
                "Take neither side and weigh neither case: the reader decides."
            ),
        },
        "case_for": {
            "type": "array",
            "title": "The Case For",
            "description": (
                "The strongest case for owning the shares, from the evidence, as its advocate "
                "would make it."
            ),
            "minItems": 2,
            "maxItems": 4,
            "items": _point("the case for"),
        },
        "case_for_priced": _priced("The Case For"),
        "case_against": {
            "type": "array",
            "title": "The Case Against",
            "description": (
                "The strongest case against owning the shares, from the evidence, as its "
                "advocate would make it."
            ),
            "minItems": 2,
            "maxItems": 4,
            "items": _point("the case against"),
        },
        "case_against_priced": _priced("The Case Against"),
    },
}


def _summary_contract(bind: sa.Connection) -> dict[str, Any]:
    """The stored v2 contract with ``thesis`` replaced, in place, by ``summary``."""
    stored = bind.execute(
        sa.text(
            "SELECT output_contract::text FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = :version"
        ),
        {"key": _SUMMARY_KEY, "version": _SUMMARY_FROM},
    ).scalar_one()
    contract: dict[str, Any] = json.loads(stored)
    properties = contract.get("properties") or {}
    contract["properties"] = {
        ("summary" if name == "thesis" else name): (_SUMMARY_FIELD if name == "thesis" else field)
        for name, field in properties.items()
    }
    contract["required"] = [
        "summary" if name == "thesis" else name for name in contract.get("required") or []
    ]
    return contract


def upgrade() -> None:
    bind = op.get_bind()
    # Everything but the contract — and, for the cases, the title — is copied from the
    # version before, so each inherits whatever policy it had accrued.
    bind.execute(
        sa.text(
            "INSERT INTO section_definitions "
            "  (key, version, origin, title, position, required, output_contract, "
            "   evidence_policy, token_budget, allowed_tools, applicability) "
            "SELECT key, :to_version, origin, title, position, required, "
            "       CAST(:contract AS json), "
            "       evidence_policy, token_budget, allowed_tools, applicability "
            "FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = :from_version"
        ),
        {
            "key": _SUMMARY_KEY,
            "from_version": _SUMMARY_FROM,
            "to_version": _SUMMARY_TO,
            "contract": json.dumps(_summary_contract(bind)),
        },
    )
    bind.execute(
        sa.text(
            "INSERT INTO section_definitions "
            "  (key, version, origin, title, position, required, output_contract, "
            "   evidence_policy, token_budget, allowed_tools, applicability) "
            "SELECT key, :to_version, origin, :title, position, required, "
            "       CAST(:contract AS json), "
            "       evidence_policy, token_budget, allowed_tools, applicability "
            "FROM section_definitions "
            "WHERE key = :key AND origin = 'builtin' AND version = :from_version"
        ),
        {
            "key": _CASES_KEY,
            "from_version": _CASES_FROM,
            "to_version": _CASES_TO,
            "title": _CASES_TITLE,
            "contract": json.dumps(_CASES_CONTRACT),
        },
    )


def _refuse_if_a_report_cites_it(bind: sa.Connection, key: str, version: int) -> None:
    """Refuse before Postgres does, because only one of the two answers "now what?".

    ``report_sections.section_definition_id`` is ``ON DELETE RESTRICT``: a stored report's
    own content is not a migration's to delete, and what the database would return is a
    constraint name rather than a remedy.
    """
    cited = bind.execute(
        sa.text(
            "SELECT count(*) FROM report_sections rs "
            "JOIN section_definitions sd ON sd.id = rs.section_definition_id "
            "WHERE sd.key = :key AND sd.origin = 'builtin' AND sd.version = :version"
        ),
        {"key": key, "version": version},
    ).scalar_one()
    if cited:
        message = (
            f"{cited} stored report section(s) cite {key!r} at version {version}, so this "
            "downgrade would delete a definition a report still rests on. Clear the research "
            "data first -- `just reset-research` empties report_sections and leaves "
            "section_definitions alone -- or downgrade a database that has produced no "
            "reports."
        )
        raise RuntimeError(message)


def downgrade() -> None:
    bind = op.get_bind()
    for key, version in ((_CASES_KEY, _CASES_TO), (_SUMMARY_KEY, _SUMMARY_TO)):
        _refuse_if_a_report_cites_it(bind, key, version)
        bind.execute(
            sa.text(
                "DELETE FROM section_definitions "
                "WHERE key = :key AND origin = 'builtin' AND version = :version"
            ),
            {"key": key, "version": version},
        )
