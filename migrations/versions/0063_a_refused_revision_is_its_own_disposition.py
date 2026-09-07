"""A refused revision is its own disposition, and the draft it tried to improve survives.

ADR 0098. The critique loop deleted a section's claims and redrafted over its content, so
a refused revision left `FAILED` where a validated, paid-for draft had been. The fix is in
code; this migration widens the disposition the record can carry, so a run can say the
attempt happened and did not stand up rather than leaving the spend invisible.

Revision ID: 0063
Revises: 0062
"""

from __future__ import annotations

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

_CONSTRAINT = "disposition_is_known"
_TABLE = "revision_notes"

_WITH_REFUSED = "disposition IN ('revised', 'revision_refused', 'stood', 'skipped_custom')"
_WITHOUT_REFUSED = "disposition IN ('revised', 'stood', 'skipped_custom')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _WITH_REFUSED)


def downgrade() -> None:
    # Rows recorded under the new value would violate the narrower constraint, and there
    # is no truthful older value for them: a refused revision is not `revised`, and it is
    # not `stood` either. They are deleted rather than misdescribed.
    op.execute("DELETE FROM revision_notes WHERE disposition = 'revision_refused'")
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _WITHOUT_REFUSED)
