"""Erasure as an appended event, so the immutable artefact row stays immutable.

A licensed feed's subscription agreement can oblige the subscriber to destroy every copy of
the data within a month of the subscription ending. An artefact store with no delete path is
precisely a store that cannot comply, and ``artefacts`` rejects every UPDATE by trigger — so
neither "delete the row" nor "flag the row" was available.

This table takes the third option: **the payload is erased and the fact of its erasure is
appended.** The artefact row, its SHA-256, its size, its storage key, every source document
pointing at it and every citation resolved against it all survive untouched. What goes is the
bytes.

``artefact_id`` is unique, because an artefact is purged once and a second record would be a
second story about one event. The foreign key is ``RESTRICT`` rather than ``CASCADE``:
deleting the artefact row would take the explanation with it and leave a citation pointing at
nothing for no stated reason, which is the state this table exists to make impossible.

``licence_note`` is copied rather than joined. The policy that obliged the deletion may change
afterwards, and a purge has to be defensible against the terms in force when the bytes were
acquired rather than against today's.

See ``docs/adr/0031-erasure-is-an-appended-event.md``.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artefact_purges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("artefact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("licence_note", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bytes_freed", sa.BigInteger(), nullable=False),
        sa.Column(
            "purged_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["artefact_id"],
            ["artefacts.id"],
            ondelete="RESTRICT",
            name="fk_artefact_purges_artefact",
        ),
        # Named the way the model's `unique=True` names it. The drift test compares the
        # migrated schema against the metadata and caught the mismatch immediately.
        sa.UniqueConstraint("artefact_id", name="uq_artefact_purges_artefact_id"),
        sa.CheckConstraint("char_length(reason) > 0", name="artefact_purge_states_a_reason"),
        sa.CheckConstraint("bytes_freed >= 0", name="artefact_purge_freed_is_not_negative"),
    )
    op.create_index("ix_artefact_purges_purged_at", "artefact_purges", ["purged_at"])


def downgrade() -> None:
    op.drop_index("ix_artefact_purges_purged_at", table_name="artefact_purges")
    op.drop_table("artefact_purges")
