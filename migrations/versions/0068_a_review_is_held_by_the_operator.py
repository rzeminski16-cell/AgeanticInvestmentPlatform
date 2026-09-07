"""A review is proposed by the reviewer and held by the operator.

Roadmap §3.8, under ADRs 0081 and 0105. One value on an existing type, two new types, and
two tables.

``judgement_kind`` gains ``review``: the third judgement subtype, in the shape 0102 left
for it. Added in an autocommit block, as every added enum value here has been.

``reviews`` is keyed on the judgement's own id — the holder is the operator, the basis is
theirs — and names the closed position (a security in a book, and the date its holding
returned to nil; unique together), the thesis its decisions acted on, the reviewer's pass,
the operator's ``process_quality`` and lessons, the platform-filled ``outcome`` as JSON
naming its calculations, and the reviewer's ``proposal`` as it arrived.

``review_verdicts`` is one premise per row as the review found it, with the premise's
statement copied so the verdict survives the thesis moving on.

The downgrade drops the two tables and the two types. The ``judgement_kind`` value stays,
as every added enum value does.

Revision ID: 0068
Revises: 0067
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

_VERDICTS = ("held", "partially_held", "failed", "untested", "unobservable")
_QUALITIES = ("sound", "questionable", "flawed")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE judgement_kind ADD VALUE IF NOT EXISTS 'review'")

    sa.Enum(*_VERDICTS, name="premise_verdict").create(op.get_bind(), checkfirst=True)
    sa.Enum(*_QUALITIES, name="process_quality").create(op.get_bind(), checkfirst=True)
    verdict = postgresql.ENUM(*_VERDICTS, name="premise_verdict", create_type=False)
    quality = postgresql.ENUM(*_QUALITIES, name="process_quality", create_type=False)

    op.create_table(
        "reviews",
        sa.Column(
            "judgement_id",
            sa.Uuid(),
            sa.ForeignKey("judgements.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "portfolio_id",
            sa.Uuid(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "security_id",
            sa.Uuid(),
            sa.ForeignKey("securities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("closed_on", sa.Date(), nullable=False),
        sa.Column(
            "thesis_id",
            sa.Uuid(),
            sa.ForeignKey("theses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("process_quality", quality, nullable=False),
        sa.Column("lessons", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint(
            "portfolio_id", "security_id", "closed_on", name="uq_reviews_one_per_closed_position"
        ),
        sa.CheckConstraint("closed_on >= opened_on", name="review_closes_after_it_opens"),
    )
    op.create_index(
        "ix_reviews_portfolio_id_closed_on",
        "reviews",
        ["portfolio_id", sa.text("closed_on DESC")],
    )
    op.create_index("ix_reviews_thesis_id", "reviews", ["thesis_id"])

    op.create_table(
        "review_verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "review_id",
            sa.Uuid(),
            sa.ForeignKey("reviews.judgement_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "premise_id",
            sa.Uuid(),
            sa.ForeignKey("premises.judgement_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("verdict", verdict, nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("review_id", "position", name="uq_review_verdicts_position"),
    )
    op.create_index("ix_review_verdicts_premise_id", "review_verdicts", ["premise_id"])


def downgrade() -> None:
    op.drop_table("review_verdicts")
    op.drop_table("reviews")
    sa.Enum(name="process_quality").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="premise_verdict").drop(op.get_bind(), checkfirst=True)
    # `judgement_kind` keeps 'review'. See the module docstring.
