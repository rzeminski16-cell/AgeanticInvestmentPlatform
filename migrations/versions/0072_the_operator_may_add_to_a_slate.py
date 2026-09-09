"""The operator may add to a slate they are being asked to confirm.

Both conditional slates — the peer set and the theme set — were a model's proposal or a
deterministic floor, and a person's only options were to approve them or not. The first
acceptance pass asked for the third: *"it would be good to add the ability to add custom
themes, similarly with the peers, allow the user to add peers of their own."*

**Two tables rather than one with a kind column.** A theme addition carries a slug, a
label and a rationale; a peer addition names a company row. They share a shape only in the
sense that every row does.

**A peer is a company this platform already holds, not a ticker to go and resolve.** The
web process has no source client and should not: only `aer.fetch` reaches the network, and
acquisition is the worker's. The deterministic floor draws from the same pool and skips a
candidate with no stored financial facts, because a peer with no period end cannot be
aligned against the subject. The operator's addition obeys the rule the floor obeys.

**Why a table at all, rather than appending to the step's output.** A step's recorded
output is what that step produced, and a run's record stops being a record the moment
something else writes into it. The addition is a separate act by a separate actor and it
gets its own row — which is also what lets the gate's hash cover it, because the payload
the page renders is assembled from both.

Revision ID: 0072
Revises: 0071
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_themes",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        # Slugged before it is stored, by the same function the proposal path uses, so a
        # theme an operator founds and one a model founds cannot differ in identity.
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("added_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_operator_themes_job_id_jobs",
            ondelete="CASCADE",
        ),
        # One addition of a key per run. A double submit is a constraint rather than a
        # race, which is the lesson migration 0071 recorded for the watchlist.
        sa.UniqueConstraint("job_id", "key", name="uq_operator_themes_job_key"),
        sa.CheckConstraint("char_length(key) > 0", name="operator_theme_has_a_key"),
        sa.CheckConstraint("char_length(label) > 0", name="operator_theme_has_a_label"),
        sa.CheckConstraint("char_length(rationale) > 0", name="operator_theme_has_a_rationale"),
    )

    op.create_table(
        "operator_peers",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        # A company this platform already holds, not a ticker to go and resolve. The free
        # floor draws from exactly this pool and skips a candidate with no stored facts,
        # because a peer with no period end cannot be aligned against the subject and
        # would be excluded a step later anyway. The operator's addition obeys the rule
        # the floor obeys.
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("added_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_operator_peers_job_id_jobs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_operator_peers_company_id_companies",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("job_id", "company_id", name="uq_operator_peers_job_company"),
        sa.CheckConstraint("char_length(rationale) > 0", name="operator_peer_has_a_rationale"),
    )

    op.create_index("ix_operator_themes_job_id", "operator_themes", ["job_id"])
    op.create_index("ix_operator_peers_job_id", "operator_peers", ["job_id"])
    op.create_index("ix_operator_peers_company_id", "operator_peers", ["company_id"])


def downgrade() -> None:
    op.drop_table("operator_peers")
    op.drop_table("operator_themes")
