"""An operator can ask for a section to be redrafted at the final gate.

Roadmap §3.19 item 75. A check that refuses a sentence at the final gate left two ways on:
approve against the check, which the round's own definition counts as a rescue, or reject
the run and pay for another. Redrafting the one section, with the refusal in front of the
writer, is the third — and it is recorded where every other redraft is, as a revision note,
under a scope of its own so the critique loop's record and the operator's stay apart.

``requested`` is the note between the operator's request and the revise step that answers
it; the step then records ``revised`` or ``revision_refused`` as it does for its own.

The downgrade deletes the final gate's notes, which the narrower constraints cannot hold.

Revision ID: 0088
Revises: 0087
"""

from __future__ import annotations

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None

_TABLE = "revision_notes"

_SCOPE = "scope_is_known"
_SCOPE_WIDE = "scope IN ('plan', 'draft', 'final_gate')"
_SCOPE_NARROW = "scope IN ('plan', 'draft')"

_DISPOSITION = "disposition_is_known"
_DISPOSITION_WIDE = (
    "disposition IN ('revised', 'revision_refused', 'stood', 'skipped_custom', 'requested')"
)
_DISPOSITION_NARROW = "disposition IN ('revised', 'revision_refused', 'stood', 'skipped_custom')"


def upgrade() -> None:
    op.drop_constraint(_SCOPE, _TABLE, type_="check")
    op.create_check_constraint(_SCOPE, _TABLE, _SCOPE_WIDE)
    op.drop_constraint(_DISPOSITION, _TABLE, type_="check")
    op.create_check_constraint(_DISPOSITION, _TABLE, _DISPOSITION_WIDE)


def downgrade() -> None:
    # A final-gate note has no truthful older scope: it is neither the plan's nor the
    # critique loop's. Deleted rather than misdescribed, as 0063's downgrade did.
    op.execute("DELETE FROM revision_notes WHERE scope = 'final_gate'")
    op.drop_constraint(_DISPOSITION, _TABLE, type_="check")
    op.create_check_constraint(_DISPOSITION, _TABLE, _DISPOSITION_NARROW)
    op.drop_constraint(_SCOPE, _TABLE, type_="check")
    op.create_check_constraint(_SCOPE, _TABLE, _SCOPE_NARROW)
