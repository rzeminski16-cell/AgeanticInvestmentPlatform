"""The unmapped-concepts gate says what it is.

Polish P9. The ``UK_FINANCIALS`` gate fired on the first complete run — for a US filer,
because 219 unmapped tags came out of the segment sweep. The gate has nothing to do with
the United Kingdom and never did: it pauses a run whose statement lines hang on concepts
the map cannot place, whichever register the filing came from. A gate named for the wrong
thing teaches an operator to wave it through.

Renamed while there is one run's history to carry. Three renames, each reversible:

* the ``gate_kind`` enum label — ``ALTER TYPE ... RENAME VALUE`` rewrites the catalog
  entry, so every historical ``approvals`` row carries the new name with no table touched;
* the workflow step's key and idempotency key on historical ``job_steps`` rows, so a
  resumed old run matches the renamed step rather than re-running its gate;
* the ``gate`` field inside those steps' recorded output, which stored the enum's value
  as text.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def _rename(old_label: str, new_label: str, old_step: str, new_step: str) -> None:
    bind = op.get_bind()
    bind.execute(sa.text(f"ALTER TYPE gate_kind RENAME VALUE '{old_label}' TO '{new_label}'"))
    bind.execute(
        sa.text(
            "UPDATE job_steps SET "
            "  step_key = :new_step, "
            "  idempotency_key = replace(idempotency_key, :old_suffix, :new_suffix) "
            "WHERE step_key = :old_step"
        ),
        {
            "new_step": new_step,
            "old_step": old_step,
            "old_suffix": f":{old_step}",
            "new_suffix": f":{new_step}",
        },
    )
    bind.execute(
        sa.text(
            "UPDATE job_steps SET "
            "  output_ref = jsonb_set(output_ref, '{gate}', to_jsonb(CAST(:new AS text))) "
            "WHERE output_ref->>'gate' = :old"
        ),
        {"new": new_label, "old": old_label},
    )


def upgrade() -> None:
    _rename("UK_FINANCIALS", "UNMAPPED_CONCEPTS", "gate_uk_financials", "gate_unmapped_concepts")


def downgrade() -> None:
    _rename("UNMAPPED_CONCEPTS", "UK_FINANCIALS", "gate_unmapped_concepts", "gate_uk_financials")
