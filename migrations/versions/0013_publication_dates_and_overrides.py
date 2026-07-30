"""Record how a publication date was established, and who overrode a quarantine.

Six columns on ``source_documents``. Threat T13, look-ahead bias.

**Why a second date column.** ``publication_date`` is the best estimate and is what a page
shows. ``publication_date_latest`` is the newest date any evidence supports, and is what the
point-in-time rule is decided on. The two answer different questions: "when was this published?"
and "can this be shown to predate the as-of date?". Where a filing index says July and the
document's own text says September, the honest answer to the second is no — and admitting it as
at 31 July would be exactly the mistake the rule exists to prevent. In the ordinary case, where
the candidates agree, the two columns hold the same date.

**Why the candidates are kept.** A confidence of 0.48 is a number a reviewer cannot act on.
"The index said July, the PDF's metadata said August, and they disagree" is something they can
go and check. Storing the losing candidates is what turns the score into an argument.

**The override does not clear the quarantine.** It sits beside it, so the record still says the
document could not be dated *and* says who decided to use it anyway and why. Clearing the flag
would erase the first half, and a reader of the finished report would have no way to know a
judgement had been made at all. Same shape as a citation override: a person, a reason, a time,
and check constraints so it cannot be two of the three.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_documents", sa.Column("publication_date_source", sa.Text(), nullable=True)
    )
    op.add_column(
        "source_documents",
        sa.Column(
            "publication_date_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "source_documents", sa.Column("publication_date_latest", sa.Date(), nullable=True)
    )
    op.add_column(
        "source_documents", sa.Column("admissibility_override_by_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "source_documents", sa.Column("admissibility_override_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "source_documents",
        sa.Column("admissibility_overridden_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Existing rows have exactly one date, so it is both the estimate and the bound. Backfilled
    # rather than left NULL: a NULL here would read as "no evidence of a later date", which is
    # true, but leaving it out would make the column mean two things depending on when the row
    # was written.
    op.execute(
        sa.text(
            "UPDATE source_documents SET publication_date_latest = publication_date"
            " WHERE publication_date IS NOT NULL"
        )
    )

    op.create_foreign_key(
        op.f("fk_source_documents_admissibility_override_by_id_users"),
        "source_documents",
        "users",
        ["admissibility_override_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_source_documents_override_is_whole",
        "source_documents",
        "(admissibility_override_by_id IS NULL) = (admissibility_override_reason IS NULL)"
        " AND (admissibility_override_by_id IS NULL) = (admissibility_overridden_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_source_documents_override_reason_is_present",
        "source_documents",
        "admissibility_override_reason IS NULL OR char_length(admissibility_override_reason) > 0",
    )
    op.create_check_constraint(
        "ck_source_documents_override_needs_a_quarantine",
        "source_documents",
        "admissibility_override_reason IS NULL OR quarantined",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_source_documents_override_needs_a_quarantine", "source_documents", type_="check"
    )
    op.drop_constraint(
        "ck_source_documents_override_reason_is_present", "source_documents", type_="check"
    )
    op.drop_constraint("ck_source_documents_override_is_whole", "source_documents", type_="check")
    op.drop_constraint(
        op.f("fk_source_documents_admissibility_override_by_id_users"),
        "source_documents",
        type_="foreignkey",
    )
    op.drop_column("source_documents", "admissibility_overridden_at")
    op.drop_column("source_documents", "admissibility_override_reason")
    op.drop_column("source_documents", "admissibility_override_by_id")
    op.drop_column("source_documents", "publication_date_latest")
    op.drop_column("source_documents", "publication_date_candidates")
    op.drop_column("source_documents", "publication_date_source")
