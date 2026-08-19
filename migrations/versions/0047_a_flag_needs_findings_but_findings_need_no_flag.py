"""A flag needs findings, but findings need no flag.

Polish P9. Migration 0012's constraint held the flag and the findings equivalent:
``injection_flagged = (findings non-empty)``. That equivalence is exactly what the
inline-XBRL downgrade breaks on purpose — a clean 10-K's hidden facts are now recorded
as *informational* findings the reviewer can still read, without lighting the badge that
should mean something.

Only one direction of the old constraint was ever the point: a flag with no findings is
a badge nobody can act on, because the page shows the passages and the badge is what
sends a reviewer to them. That direction stays. The reverse — findings force the flag —
goes, because it is now code's judgement (``aer.services.injection.record_findings``)
whether what was found means anything.

Revision ID: 0047
Revises: 0046
"""

from __future__ import annotations

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_NAME = "ck_source_documents_flagged_has_findings"
_TABLE = "source_documents"

_ONE_WAY = (
    "NOT injection_flagged OR (injection_findings IS NOT NULL"
    " AND jsonb_array_length(injection_findings) > 0)"
)

_BOTH_WAYS = (
    "injection_flagged = (injection_findings IS NOT NULL"
    " AND jsonb_array_length(injection_findings) > 0)"
)


def upgrade() -> None:
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, _ONE_WAY)


def downgrade() -> None:
    # Restoring the equivalence requires the rows to satisfy it again: any document left
    # holding informational-only findings goes back to the pre-P9 reading, flagged.
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.execute(
        "UPDATE source_documents SET injection_flagged = true "
        "WHERE injection_findings IS NOT NULL AND jsonb_array_length(injection_findings) > 0"
    )
    op.create_check_constraint(_NAME, _TABLE, _BOTH_WAYS)
