"""Record what a document tried, without refusing it.

Two columns on ``source_documents``. Threat T2.

**Separate from ``quarantined``, deliberately.** Quarantine is the point-in-time rule and it is
a refusal: a document whose publication date cannot be established is not admissible evidence.
An injection flag is neither of those things. Hidden text has innocent uses — a print
stylesheet, an accessibility label, a collapsed note — and refusing every filing that uses
``display:none`` would refuse most of them. The flag exists so a human at gate 2 knows where to
look, and nothing downstream depends on it: what actually contains an injected instruction is
that agents have no network tool and that their tool allowlists are class attributes checked in
Python.

Collapsing the two into one column would have made "this document is not admissible" and "this
document is worth a second look" indistinguishable, which is the distinction a reviewer needs
most.

**The check constraint keeps the flag and the findings honest.** A flag with no findings sends
a reviewer looking for something that was never recorded; findings with no flag put passages in
the database that no page will show them. Neither state means anything, so neither is allowed.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column(
            "injection_flagged",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "source_documents",
        sa.Column("injection_findings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "ck_source_documents_flagged_has_findings",
        "source_documents",
        "injection_flagged = (injection_findings IS NOT NULL"
        " AND jsonb_array_length(injection_findings) > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_source_documents_flagged_has_findings", "source_documents", type_="check"
    )
    op.drop_column("source_documents", "injection_findings")
    op.drop_column("source_documents", "injection_flagged")
