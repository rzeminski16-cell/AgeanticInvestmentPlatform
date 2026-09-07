"""The review pass over the judgement layer corrects three shapes.

Three findings of one review, each a column or constraint that disagreed with the rule
the service enforces.

``theses.written_at`` — the date the operator said a view was formed, which the form
asked for since ADR 0102 and the row never kept: it went into the audit payload alone,
and the thesis page showed the platform's ``created_at`` as "written". The two clocks
are ADR 0075's distinction, and this is the operator's.

``findings.judgement_id`` — ``ON DELETE SET NULL`` contradicted the check
``finding_reading_names_its_premise``, so deleting a premise (a thesis or user cascade)
failed on the check rather than cascading. A finding is a record of what the monitor
saw *about a premise*; without the premise it names nothing, and it goes with it.

``watchlist_entries`` — "already followed" was refused by a read alone; the partial unique
index makes a double submit a constraint rather than a race.

Revision ID: 0071
Revises: 0070
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("theses", sa.Column("written_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_constraint("fk_findings_judgement_id_premises", "findings", type_="foreignkey")
    op.create_foreign_key(
        "fk_findings_judgement_id_premises",
        "findings",
        "premises",
        ["judgement_id"],
        ["judgement_id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "uq_watchlist_entries_one_active_listing",
        "watchlist_entries",
        ["user_id", "ticker", "exchange"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_watchlist_entries_one_active_listing", table_name="watchlist_entries")
    op.drop_constraint("fk_findings_judgement_id_premises", "findings", type_="foreignkey")
    op.create_foreign_key(
        "fk_findings_judgement_id_premises",
        "findings",
        "premises",
        ["judgement_id"],
        ["judgement_id"],
        ondelete="SET NULL",
    )
    op.drop_column("theses", "written_at")
