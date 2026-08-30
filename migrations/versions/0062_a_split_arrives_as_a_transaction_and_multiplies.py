"""A split arrives as a transaction, and multiplies rather than adds.

ADR 0094, closing roadmap §2.6. ``transaction_kind`` grows ``'split'``: a derived row
whose quantity is the *ratio* the share count is multiplied by, pointing at the
``corporate_actions`` row behind it through a new ``corporate_action_id`` column. The
sign constraint learns that a split is positive whichever way it points (0.1 is a
consolidation, not a negative quantity); three new checks keep a split non-trivial,
security-naming and derivation-only; and a partial unique index holds one derived row
per book per action, in migration 0018's idiom.

The enum value is added in an autocommit block: Postgres refuses "unsafe use of new
value" when a constraint in the same transaction names a value the type gained in it.

Revision ID: 0062
Revises: 0061
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE transaction_kind ADD VALUE IF NOT EXISTS 'split'")

    op.add_column(
        "transactions",
        sa.Column(
            "corporate_action_id",
            sa.Uuid(),
            sa.ForeignKey(
                "corporate_actions.id",
                ondelete="RESTRICT",
                name="fk_transactions_corporate_action_id",
            ),
            nullable=True,
        ),
    )

    # Postgres cannot alter a check constraint in place.
    op.drop_constraint("transaction_sign_matches_its_kind", "transactions", type_="check")
    op.create_check_constraint(
        "transaction_sign_matches_its_kind",
        "transactions",
        "(kind IN ('buy', 'dividend', 'deposit', 'split')) = (quantity > 0)",
    )
    op.create_check_constraint(
        "transaction_split_multiplies",
        "transactions",
        "kind <> 'split' OR quantity <> 1",
    )
    op.create_check_constraint(
        "transaction_split_names_its_security",
        "transactions",
        "kind <> 'split' OR security_id IS NOT NULL",
    )
    op.create_check_constraint(
        "transaction_split_derives_from_an_action",
        "transactions",
        "(kind = 'split') = (corporate_action_id IS NOT NULL)",
    )
    op.create_index(
        "uq_transactions_split_per_action",
        "transactions",
        ["portfolio_id", "corporate_action_id"],
        unique=True,
        postgresql_where=sa.text("corporate_action_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    derived = bind.execute(
        sa.text("SELECT count(*) FROM transactions WHERE kind = 'split'")
    ).scalar_one()
    if derived:
        message = (
            f"{derived} derived split transaction(s) exist, and removing their kind would "
            "leave a book that is silently wrong across every split it spans. Delete the "
            "derived rows (and their attestations) first, knowing the books they leave "
            "behind revert to pre-split share counts."
        )
        raise RuntimeError(message)

    op.drop_index("uq_transactions_split_per_action", table_name="transactions")
    op.drop_constraint("transaction_split_derives_from_an_action", "transactions", type_="check")
    op.drop_constraint("transaction_split_names_its_security", "transactions", type_="check")
    op.drop_constraint("transaction_split_multiplies", "transactions", type_="check")
    op.drop_constraint("transaction_sign_matches_its_kind", "transactions", type_="check")
    op.create_check_constraint(
        "transaction_sign_matches_its_kind",
        "transactions",
        "(kind IN ('buy', 'dividend', 'deposit')) = (quantity > 0)",
    )
    op.drop_column("transactions", "corporate_action_id")
    # The enum value stays: Postgres cannot remove one without rebuilding the type, and a
    # value no constraint admits and no row carries is inert.
