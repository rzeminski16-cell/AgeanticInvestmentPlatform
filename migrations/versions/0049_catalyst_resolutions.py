"""Catalyst resolutions: what the operator says happened when a window closed.

`docs/knowledge-graph.md` K4. "The stated window has passed" is a statement about the
calendar and always was; whether the event occurred is not knowable from rows, and a
model asserting it would be a factual claim with no citation. The answer is recorded by
a person: an outcome from a closed set, a mandatory reason, who and when.

One row per ``(company, label)`` — the catalyst node's own identity — and re-recording
updates it: operator bookkeeping, not a gate decision. The type is created here and
dropped on the way down; no existing column joins it, so the downgrade is a plain drop.

Revision ID: 0049
Revises: 0048
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_OUTCOMES = ("occurred", "did_not_occur", "superseded")


def upgrade() -> None:
    sa.Enum(*_OUTCOMES, name="catalyst_outcome").create(op.get_bind(), checkfirst=True)
    # `create_type=False`: the type was created above, and the column definition must not
    # try to create it a second time inside CREATE TABLE.
    outcome = postgresql.ENUM(*_OUTCOMES, name="catalyst_outcome", create_type=False)

    op.create_table(
        "catalyst_resolutions",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("outcome", outcome, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("company_id", "label", name="uq_catalyst_resolutions_per_catalyst"),
        sa.CheckConstraint("char_length(btrim(reason)) > 0", name="reason_is_not_blank"),
        sa.CheckConstraint("char_length(btrim(label)) > 0", name="label_is_not_blank"),
    )


def downgrade() -> None:
    op.drop_table("catalyst_resolutions")
    sa.Enum(name="catalyst_outcome").drop(op.get_bind(), checkfirst=True)
