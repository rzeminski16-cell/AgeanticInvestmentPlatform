"""A scenario is a shock the operator states.

Roadmap §3.9, under ADRs 0080 and 0106. One new type and two tables.

``risk_scenarios`` is a named scenario belonging to a book, with who stated it and when it
was withdrawn. ``risk_scenario_shocks`` are its rows: a kind from ``shock_kind`` naming
what the shock reaches, the band label it reaches, and the fraction. Neither table holds a
figure: the profit and loss a scenario produces is a recorded calculation over the book's
values and these rows.

The downgrade drops the two tables and the type.

Revision ID: 0069
Revises: 0068
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

_KINDS = ("book", "sector", "currency", "country", "holding")


def upgrade() -> None:
    sa.Enum(*_KINDS, name="shock_kind").create(op.get_bind(), checkfirst=True)
    kind = postgresql.ENUM(*_KINDS, name="shock_kind", create_type=False)

    op.create_table(
        "risk_scenarios",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "portfolio_id",
            sa.Uuid(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stated_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="risk_scenario_name_is_not_blank"),
    )
    op.create_index("ix_risk_scenarios_portfolio_id", "risk_scenarios", ["portfolio_id"])

    op.create_table(
        "risk_scenario_shocks",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "scenario_id",
            sa.Uuid(),
            sa.ForeignKey("risk_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("target", sa.Text(), nullable=False, server_default=""),
        sa.Column("shock", sa.Numeric(10, 6), nullable=False),
        sa.UniqueConstraint("scenario_id", "position", name="uq_risk_scenario_shocks_position"),
        sa.CheckConstraint(
            "shock > -1 AND shock <> 0", name="shock_moves_something_and_leaves_something"
        ),
        sa.CheckConstraint(
            "kind = 'book' OR char_length(btrim(target)) > 0", name="shock_names_its_target"
        ),
    )


def downgrade() -> None:
    op.drop_table("risk_scenario_shocks")
    op.drop_table("risk_scenarios")
    sa.Enum(name="shock_kind").drop(op.get_bind(), checkfirst=True)
