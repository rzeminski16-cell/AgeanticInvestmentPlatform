"""Two sections make the one-pager: the view, and what could defeat it.

Gap O8. Forty pages is a reference document; most readings want the view, the numbers
behind it and the risks. The one-page summary is a second renderer over the same
document — no new analysis — and which sections it includes is data on the definition
rows, for the same reason the exhibit claims are (0038): no section key may enter the
renderer's code.

``one_pager: true`` joins the evidence policy of the executive summary and the key
risks. The front-page numbers travel separately (the at-a-glance block, gap R10), so
the pair here is deliberately prose: the argument and its counterweight.

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

_KEYS = ("executive_summary", "key_risks")


def upgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy || CAST('{\"one_pager\": true}' AS jsonb) "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _KEYS:
        bind.execute(statement, {"key": key})


def downgrade() -> None:
    statement = sa.text(
        "UPDATE section_definitions "
        "SET evidence_policy = evidence_policy - 'one_pager' "
        "WHERE key = :key AND origin = 'builtin'"
    )
    bind = op.get_bind()
    for key in _KEYS:
        bind.execute(statement, {"key": key})
