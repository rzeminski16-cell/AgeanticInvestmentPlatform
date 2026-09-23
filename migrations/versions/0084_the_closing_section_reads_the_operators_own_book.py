"""The closing section reads the operator's own book (F3, ADR 0129).

Two things, both additive. The request's ``portfolio_context`` may now carry a
``planned_weight`` — the whole position after the trade, as a fraction of the book — and the
database refuses one outside [0, 1] on the terms it refuses the two weights already there:
a weight of 800% would silently poison every consequence downstream, and the database is
the last place that can refuse it. A ``purpose`` travels beside it, validated by Pydantic and
stored faithfully.

And a section row: ``portfolio_consequences``, the closing section, at the foot of the spine
and **applicable only to a request that states a planned weight**. A run commissioned without
one has no section — absent, not empty — which is the row's own ``applicability`` predicate
rather than a branch in the renderer. Its book figures, its horizon figures and its evidence
note are ``platform_filled``: :mod:`aer.sections.consequences` strikes them from the
operator's book and the run's own valuation, the model's schema never carries them, and the
model's one field is a commentary refused if it recommends, rates, targets or sizes.

The evidence policy is the deterministic sections': the evidence here is the run's own
rows, and an external-source floor would be a floor nothing could meet.

Revision ID: 0084
Revises: 0083
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None

_KEY = "portfolio_consequences"
_TITLE = "What this would do to your book"
_POSITION = 950


def _row(*, cited: bool) -> dict[str, Any]:
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
    "title": _TITLE,
    "required": ["commentary"],
    "properties": {
        "basis": {
            "type": "string",
            "title": "How These Figures Were Produced",
            "platform_filled": True,
        },
        "consequences": {
            "type": "array",
            "title": "Your Book, Before And After",
            "platform_filled": True,
            "items": _row(cited=True),
        },
        "horizon": {
            "type": "array",
            "title": "Your Horizon Against The Model's Payback",
            "platform_filled": True,
            "items": _row(cited=True),
        },
        "commentary": {
            "type": "string",
            "title": "Commentary",
            "description": (
                "What the figures rendered above mean for the operator's book \N{EM DASH} "
                "what moves, in which direction, and whether the stated horizon outruns the "
                "model's payback. Interpretation only, of those figures only. Issue no "
                "instruction: no buy, sell, add, trim, size, rating, target or "
                "recommendation; the decision is the operator's."
            ),
        },
        "evidence_note": {
            "type": "string",
            "title": "Where These Figures Come From",
            "platform_filled": True,
        },
        "withheld": {
            "type": "string",
            "title": "Withheld From This Copy",
            "platform_filled": True,
        },
    },
}

# The deterministic sections' policy (migration 0023): the evidence here is the run's own
# rows and the operator's own book, so an external-source floor is a floor nothing could
# meet. The length is a ceiling on the commentary alone.
_POLICY: dict[str, Any] = {
    "min_sources": 0,
    "max_tier": "T4_LICENSED_MARKET",
    "requires_primary": False,
    "allow_forward_looking": False,
    "max_words": 250,
}


def _section_definitions_table() -> sa.Table:
    return sa.table(
        "section_definitions",
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("origin", sa.Text),
        sa.column("title", sa.Text),
        sa.column("position", sa.Numeric),
        sa.column("required", sa.Boolean),
        # ``json``, never JSONB: the renderer takes its field order from this document.
        sa.column("output_contract", sa.JSON),
        sa.column("evidence_policy", JSONB),
        sa.column("token_budget", sa.Integer),
        sa.column("allowed_tools", ARRAY(sa.Text)),
        sa.column("applicability", JSONB),
    )


def upgrade() -> None:
    op.create_check_constraint(
        "planned_weight_is_a_fraction",
        "research_requests",
        """
        portfolio_context = '{}'::jsonb
        OR (
          (portfolio_context->>'planned_weight') IS NULL
          OR (
            (portfolio_context->>'planned_weight')::numeric >= 0
            AND (portfolio_context->>'planned_weight')::numeric <= 1
          )
        )
        """,
    )
    op.bulk_insert(
        _section_definitions_table(),
        [
            {
                "key": _KEY,
                "version": 1,
                "origin": "builtin",
                "title": _TITLE,
                "position": _POSITION,
                # Optional: a run with a planned weight and no book on record renders the
                # section's one honest sentence, and that is a thinner report rather than
                # a broken one.
                "required": False,
                "output_contract": _CONTRACT,
                "evidence_policy": _POLICY,
                "token_budget": 1500,
                "allowed_tools": [],
                # The predicate language is an attribute and a list of permitted values;
                # `ResearchRequest.has_planned_weight` is the attribute.
                "applicability": {"has_planned_weight": [True]},
            }
        ],
    )


def _refuse_if_a_report_cites_it(bind: sa.Connection) -> None:
    """Refuse before Postgres does, because only one of the two answers "now what?".

    ``report_sections.section_definition_id`` is ``ON DELETE RESTRICT`` deliberately: a
    stored report's own content is not a migration's to delete.
    """
    cited = bind.execute(
        sa.text(
            "SELECT count(*) FROM report_sections rs "
            "JOIN section_definitions sd ON sd.id = rs.section_definition_id "
            "WHERE sd.key = :key AND sd.origin = 'builtin'"
        ),
        {"key": _KEY},
    ).scalar_one()
    if cited:
        message = (
            f"{cited} stored report section(s) cite {_KEY!r}, so its definition cannot be "
            "removed. Delete those runs first (`aer reset-research`), or leave the row."
        )
        raise RuntimeError(message)


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_if_a_report_cites_it(bind)
    bind.execute(
        sa.text("DELETE FROM section_definitions WHERE key = :key AND origin = 'builtin'"),
        {"key": _KEY},
    )
    op.drop_constraint("planned_weight_is_a_fraction", "research_requests", type_="check")
