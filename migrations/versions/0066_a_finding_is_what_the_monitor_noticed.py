"""A finding is what the monitor noticed, and a resolution is what a person did about it.

Roadmap §3.6, under ADRs 0078, 0079 and 0103. Two tables, three enum types, and one value
added to a fourth.

``findings`` is the record ADR 0078 wanted kept apart from an approval: the thesis and the
premise, the pass that wrote it, a kind (``reading`` or ``stopped``), for a reading a status
from ADR 0079's closed enum, the justification, the source document ids it names, what code
measured, the window it covered, and whether it opens a gate — pinned by a check to
``status = 'contradicted'`` so the tier is a fact of the row.

``finding_resolutions`` is append-only: what a person did (dismissed, withdrew the premise,
reopened), the reason, the actor, and the approval row where the act was a gate decision.
Nothing on ``findings`` is updated when it is resolved.

``gate_kind`` gains ``THESIS``, the one gate no research run opens. Added in an autocommit
block, as revision 0062 did for ``transaction_kind``: Postgres refuses "unsafe use of new
value" when anything in the same transaction names a value the type gained in it.

The downgrade drops the two tables and the three types. The ``gate_kind`` value stays:
Postgres cannot remove one without rebuilding the type, and a value no row carries is inert
(revisions 0016, 0025 and 0062 say the same).

Revision ID: 0066
Revises: 0065
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

_KINDS = ("reading", "stopped")
_STATUSES = ("unchanged", "weakened", "strengthened", "contradicted", "unobservable")
_ACTIONS = ("dismissed", "withdrawn", "reopened")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE gate_kind ADD VALUE IF NOT EXISTS 'THESIS'")

    sa.Enum(*_KINDS, name="finding_kind").create(op.get_bind(), checkfirst=True)
    sa.Enum(*_STATUSES, name="premise_status").create(op.get_bind(), checkfirst=True)
    sa.Enum(*_ACTIONS, name="finding_action").create(op.get_bind(), checkfirst=True)

    # `create_type=False`: the types were created above, and a column definition inside
    # CREATE TABLE would otherwise try to create each a second time.
    kind = postgresql.ENUM(*_KINDS, name="finding_kind", create_type=False)
    status = postgresql.ENUM(*_STATUSES, name="premise_status", create_type=False)
    action = postgresql.ENUM(*_ACTIONS, name="finding_action", create_type=False)

    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "thesis_id",
            sa.Uuid(),
            sa.ForeignKey("theses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "judgement_id",
            sa.Uuid(),
            sa.ForeignKey("premises.judgement_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", kind, nullable=False),
        sa.Column("status", status, nullable=True),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "source_document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("observed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("window_from", sa.Date(), nullable=True),
        sa.Column("window_to", sa.Date(), nullable=True),
        sa.Column("opens_gate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(justification)) > 0", name="finding_justification_is_not_blank"
        ),
        sa.CheckConstraint(
            "(kind = 'reading') = (status IS NOT NULL)", name="finding_reading_carries_a_status"
        ),
        sa.CheckConstraint(
            "kind <> 'reading' OR judgement_id IS NOT NULL",
            name="finding_reading_names_its_premise",
        ),
        sa.CheckConstraint(
            "opens_gate = (status = 'contradicted')", name="finding_gate_follows_the_status"
        ),
    )
    op.create_index(
        "ix_findings_thesis_id_created_at",
        "findings",
        ["thesis_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_findings_judgement_id", "findings", ["judgement_id"])
    op.create_index("ix_findings_job_id", "findings", ["job_id"])

    op.create_table(
        "finding_resolutions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "finding_id",
            sa.Uuid(),
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", action, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "approval_id",
            sa.Uuid(),
            sa.ForeignKey("approvals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("char_length(btrim(reason)) > 0", name="resolution_reason_is_not_blank"),
        sa.CheckConstraint("char_length(btrim(actor)) > 0", name="resolution_actor_is_not_blank"),
    )
    op.create_index(
        "ix_finding_resolutions_finding_id_resolved_at",
        "finding_resolutions",
        ["finding_id", "resolved_at"],
    )


def downgrade() -> None:
    op.drop_table("finding_resolutions")
    op.drop_table("findings")
    sa.Enum(name="finding_action").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="premise_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="finding_kind").drop(op.get_bind(), checkfirst=True)
    # `gate_kind` keeps 'THESIS'. See the module docstring.
