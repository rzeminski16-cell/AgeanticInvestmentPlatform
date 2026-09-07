"""A thesis is premises, and a premise is a judgement.

ADR 0074's fifth record class, and the first tables holding what the operator *thinks*
rather than what they hold or what was filed.

Three tables and two enum types.

``judgements`` is the supertype: who held the view, when (as they state it) and when the
platform was told, on what basis, and a supersession link. ``basis`` is NOT NULL for the
reason ``assumptions.justification`` is — a view with no stated grounds is a guess wearing
a label. A withdrawal must carry a reason, and a row is superseded at most once.

``theses`` is the container: one person's premises about one subject, written against one
report. The subject is a kind and an id with no foreign key (ADR 0072's shape), so a thesis
outlives the company row it was about.

``premises`` is the one subtype, keyed on the judgement's own id: a premise *is* a
judgement seen from its thesis. Its checks carry ADR 0079's model — a predicate is all four
of metric, comparator, threshold and unit or none of them, and a premise with no predicate
must name the date a person will review it by, so nothing here is a view the platform
would silently stop asking about.

**Nothing references ``judgements`` but ``premises``**, and that is the enforcement of the
single most important rule in the expansion: a judgement is never a source reference.
``claims`` gains no column here, and ``tests/test_theses.py`` walks the metadata to keep it
that way.

The downgrade drops the three tables and the two types. Nothing existing references them.

Revision ID: 0065
Revises: 0064
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

_JUDGEMENT_KINDS = ("premise",)
_COMPARATORS = ("at_least", "at_most", "above", "below")


def upgrade() -> None:
    sa.Enum(*_JUDGEMENT_KINDS, name="judgement_kind").create(op.get_bind(), checkfirst=True)
    sa.Enum(*_COMPARATORS, name="premise_comparator").create(op.get_bind(), checkfirst=True)

    # `create_type=False`: the types were created above, and a column definition inside
    # CREATE TABLE would otherwise try to create each a second time.
    judgement_kind = postgresql.ENUM(*_JUDGEMENT_KINDS, name="judgement_kind", create_type=False)
    comparator = postgresql.ENUM(*_COMPARATORS, name="premise_comparator", create_type=False)

    op.create_table(
        "judgements",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", judgement_kind, nullable=False),
        sa.Column("held_by", sa.Text(), nullable=False),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("judgements.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("char_length(btrim(held_by)) > 0", name="judgement_holder_is_not_blank"),
        sa.CheckConstraint("char_length(btrim(basis)) > 0", name="judgement_basis_is_not_blank"),
        sa.CheckConstraint("id <> supersedes_id", name="judgement_does_not_supersede_itself"),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_reason IS NULL)",
            name="judgement_withdrawal_carries_a_reason",
        ),
        sa.UniqueConstraint("supersedes_id", name="uq_judgements_supersedes_once"),
    )
    op.create_index("ix_judgements_held_at", "judgements", ["held_at"])

    op.create_table(
        "theses",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(32), nullable=False, server_default="company"),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retirement_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("char_length(btrim(title)) > 0", name="thesis_title_is_not_blank"),
        sa.CheckConstraint(
            "(retired_at IS NULL) = (retirement_reason IS NULL)",
            name="thesis_retirement_carries_a_reason",
        ),
    )
    op.create_index(
        "ix_theses_user_id_created_at", "theses", ["user_id", sa.text("created_at DESC")]
    )
    op.create_index("ix_theses_subject", "theses", ["subject_kind", "subject_id"])

    op.create_table(
        "premises",
        sa.Column(
            "judgement_id",
            sa.Uuid(),
            sa.ForeignKey("judgements.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "thesis_id",
            sa.Uuid(),
            sa.ForeignKey("theses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=True),
        sa.Column("comparator", comparator, nullable=True),
        sa.Column("threshold", sa.Numeric(38, 12), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("review_by", sa.Date(), nullable=True),
        sa.UniqueConstraint("thesis_id", "position", name="uq_premises_position_per_thesis"),
        sa.CheckConstraint(
            "char_length(btrim(statement)) > 0", name="premise_statement_is_not_blank"
        ),
        sa.CheckConstraint(
            "(metric IS NULL) = (comparator IS NULL) "
            "AND (metric IS NULL) = (threshold IS NULL) "
            "AND (metric IS NULL) = (unit IS NULL)",
            name="premise_predicate_is_whole_or_absent",
        ),
        sa.CheckConstraint(
            "metric IS NOT NULL OR review_by IS NOT NULL",
            name="premise_without_a_predicate_is_reviewed",
        ),
    )
    op.create_index("ix_premises_thesis_id", "premises", ["thesis_id"])
    op.create_index("ix_premises_review_by", "premises", ["review_by"])


def downgrade() -> None:
    op.drop_table("premises")
    op.drop_table("theses")
    op.drop_table("judgements")
    sa.Enum(name="premise_comparator").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="judgement_kind").drop(op.get_bind(), checkfirst=True)
