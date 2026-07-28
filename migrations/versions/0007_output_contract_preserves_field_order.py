"""Store ``output_contract`` as json so the author's field order survives.

JSONB normalises. It discards key order, reordering by key length and then bytewise, and
it does so silently. That is exactly right for a column you query into; it is exactly
wrong for ``section_definitions.output_contract``, because the generic renderer takes both
its field order and its table columns from that document.

The symptom found this: a section declaring ``thesis, key_points, key_risks`` rendered as
**Thesis, Key Risks, Key Points**, and a figures table declaring ``label, value, unit``
rendered its columns as **Unit, Label, Value** — key lengths 4, 5, 5 and 6, 9, 10. The
author's deliberate ordering had been replaced by an implementation detail of the storage
engine, in a document whose whole purpose is to let a section author control their own
output without writing a template.

``json`` stores the text exactly as given. Nothing queries inside this column, so the
indexing JSONB buys is worth nothing here.

The check constraint moves with the type: ``jsonb_typeof`` does not accept ``json``.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_section_definitions_output_contract_is_an_object"


def upgrade() -> None:
    # Dropped first: the constraint names a jsonb function, and altering the column type
    # underneath it would leave an expression that no longer type-checks.
    op.drop_constraint(_CONSTRAINT, "section_definitions", type_="check")

    op.alter_column(
        "section_definitions",
        "output_contract",
        type_=sa.JSON(),
        existing_type=sa.dialects.postgresql.JSONB(),
        existing_nullable=False,
        # Casting jsonb -> json cannot restore an order jsonb has already discarded. Rows
        # written before this migration keep whatever order jsonb left them in; the two
        # seeded built-ins are rewritten below so the shipped sections are correct.
        postgresql_using="output_contract::text::json",
    )

    op.create_check_constraint(
        _CONSTRAINT, "section_definitions", "json_typeof(output_contract) = 'object'"
    )

    _rewrite_builtin_contracts()


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "section_definitions", type_="check")
    op.alter_column(
        "section_definitions",
        "output_contract",
        type_=sa.dialects.postgresql.JSONB(),
        existing_type=sa.JSON(),
        existing_nullable=False,
        postgresql_using="output_contract::text::jsonb",
    )
    op.create_check_constraint(
        _CONSTRAINT, "section_definitions", "jsonb_typeof(output_contract) = 'object'"
    )


# The built-in contracts, as their authors wrote them. Restated here rather than imported
# from migration 0006: a migration describes the state of the world at its own revision,
# and one that reached into another would change meaning when that other one changed.
_EXECUTIVE_SUMMARY_CONTRACT = """{
  "type": "object",
  "title": "Executive Summary",
  "required": ["thesis", "key_points"],
  "properties": {
    "thesis": {
      "type": "string",
      "title": "Thesis",
      "description": "The central view, in two or three sentences."
    },
    "key_points": {"type": "array", "title": "Key Points", "items": {"type": "string"},
                   "minItems": 1},
    "key_risks": {"type": "array", "title": "Key Risks", "items": {"type": "string"}}
  }
}"""

_HISTORICAL_ANALYSIS_CONTRACT = """{
  "type": "object",
  "title": "Historical Financial Analysis",
  "required": ["commentary", "figures"],
  "properties": {
    "commentary": {
      "type": "string",
      "title": "Commentary",
      "description": "What the reported history shows."
    },
    "figures": {
      "type": "array",
      "title": "Figures",
      "description": "Each figure must resolve to a calculation or a financial fact.",
      "items": {
        "type": "object",
        "required": ["label", "value", "unit"],
        "properties": {
          "label": {"type": "string"},
          "value": {"type": "string"},
          "unit": {"type": "string"},
          "calculation_id": {"type": "string"},
          "source_document_id": {"type": "string"}
        }
      }
    }
  }
}"""


def _rewrite_builtin_contracts() -> None:
    """Restore the declared order for the two seeded sections.

    Only the built-ins, and only the latest version of each. A user-authored contract
    written before this migration cannot be recovered — jsonb kept no record of the order
    it discarded — and inventing one would be worse than leaving it as it is.
    """
    bind = op.get_bind()
    for key, contract in (
        ("executive_summary", _EXECUTIVE_SUMMARY_CONTRACT),
        ("historical_financial_analysis", _HISTORICAL_ANALYSIS_CONTRACT),
    ):
        bind.execute(
            sa.text(
                "UPDATE section_definitions SET output_contract = CAST(:contract AS json) "
                "WHERE key = :key AND origin = 'builtin'"
            ),
            {"contract": contract, "key": key},
        )
