"""A price move is a finding, and a watched listing carries its threshold (F11).

F11's second alert kind. `finding_kind` gains `price_move` — the design's M5, and the one
one-way door in the set: Postgres cannot remove an enum value, so the downgrade below leaves
it in place, as 0078 did for its two. A price move is a finding because it is what the
monitor already stores well: a measurement in `observed`, a sentence, and an appended act
with a reason to close it. What it is *not* is a premise test: price is an outcome
(ADR 0079), so the row carries no status and can never open the thesis gate.

Three things the design's M6 did not say, each decided here by an ADR that outranks it:

- **`findings.user_id`**, not only `security_id`. ADR 0120 §1 lists findings among the rows
  that carry the account directly, and a price move on a watched listing has no thesis to
  reach a user through. Backfilled from each finding's thesis — every existing finding has
  one — and then made NOT NULL, so the scoping query stops joining theses at all.
- **`thesis_id` becomes nullable**, with a check that a kind names its subject: a price
  move names a listing, everything else a thesis. A watched company need not have a thesis.
- **No `dismissed_at` and no `dismissed_reason`.** ADR 0078: a resolution is an appended
  row, never a flag on the finding, and `finding_resolutions` already takes a dismissal
  with a required reason. The design's columns are corrected in `07-data-model.md`.

M7 as designed: `watchlist_entries` gains the cadence for the premise kind, the threshold
and window for the price kind, and the two check timestamps — stored rather than computed,
so a missed window is visible rather than inferred. The threshold is NULL for the account
default, so a listing followed before the default changed follows the change.

Revision ID: 0083
Revises: 0082
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The enum value first and outside the transaction, as 0078 did: Postgres refuses to
    # add an enum value inside a transaction block that then uses it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE finding_kind ADD VALUE IF NOT EXISTS 'price_move'")

    # -- findings: whose, and about what -------------------------------------------------
    op.add_column(
        "findings",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # Every finding so far has a thesis, and the thesis knows whose it is.
    op.execute(
        "UPDATE findings SET user_id = theses.user_id "
        "FROM theses WHERE findings.thesis_id = theses.id"
    )
    op.alter_column("findings", "user_id", nullable=False)
    op.add_column(
        "findings",
        sa.Column(
            "security_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.alter_column("findings", "thesis_id", nullable=True)
    op.create_index(
        "ix_findings_user_id_created_at", "findings", ["user_id", sa.text("created_at DESC")]
    )
    op.create_index(
        "ix_findings_security_id_created_at",
        "findings",
        ["security_id", sa.text("created_at DESC")],
    )
    op.create_check_constraint(
        "finding_kind_names_its_subject",
        "findings",
        "(kind = 'price_move' AND security_id IS NOT NULL) "
        "OR (kind <> 'price_move' AND thesis_id IS NOT NULL)",
    )

    # -- watchlist_entries: cadence, threshold, window, timestamps ---------------------------
    op.add_column(
        "watchlist_entries",
        sa.Column("cadence", sa.String(16), nullable=False, server_default="monthly"),
    )
    op.add_column(
        "watchlist_entries",
        sa.Column("price_move_threshold_pct", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "watchlist_entries",
        sa.Column("price_move_window_days", sa.Integer(), nullable=False, server_default="7"),
    )
    op.add_column(
        "watchlist_entries",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "watchlist_entries",
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "watchlist_cadence_is_one_of_two",
        "watchlist_entries",
        "cadence IN ('monthly', 'quarterly')",
    )
    op.create_check_constraint(
        "watchlist_threshold_is_positive",
        "watchlist_entries",
        "price_move_threshold_pct IS NULL OR price_move_threshold_pct > 0",
    )
    op.create_check_constraint(
        "watchlist_window_is_at_least_a_day",
        "watchlist_entries",
        "price_move_window_days >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("watchlist_window_is_at_least_a_day", "watchlist_entries", type_="check")
    op.drop_constraint("watchlist_threshold_is_positive", "watchlist_entries", type_="check")
    op.drop_constraint("watchlist_cadence_is_one_of_two", "watchlist_entries", type_="check")
    op.drop_column("watchlist_entries", "next_check_at")
    op.drop_column("watchlist_entries", "last_checked_at")
    op.drop_column("watchlist_entries", "price_move_window_days")
    op.drop_column("watchlist_entries", "price_move_threshold_pct")
    op.drop_column("watchlist_entries", "cadence")

    # A price move has no thesis, and the column is about to require one again. The rows
    # are the outcome of a schedule that can be run again; the downgrade says so rather
    # than failing halfway on the constraint.
    op.execute("DELETE FROM findings WHERE kind = 'price_move'")
    op.drop_constraint("finding_kind_names_its_subject", "findings", type_="check")
    op.drop_index("ix_findings_security_id_created_at", table_name="findings")
    op.drop_index("ix_findings_user_id_created_at", table_name="findings")
    op.alter_column("findings", "thesis_id", nullable=False)
    op.drop_column("findings", "security_id")
    op.drop_column("findings", "user_id")
    # `price_move` stays on `finding_kind`: Postgres cannot remove an enum value, and no row
    # carries it after the delete above.
