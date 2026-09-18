"""The section about the competitive landscape may name a competitor.

Every run since the peer gate existed has confirmed a set of comparable companies, each
with a written reason, approved by a person. A live report then discussed competition for
four hundred words and named nobody, because the names were nowhere the writer could see
them — the comps step consumed them and the evidence pack has never carried anything that
is not a fact, a calculation or an excerpt.

They are none of those. A peer's name and the reason it was chosen are a judgement
somebody holds (ADR 0074), so they reach the writer as context beside the pack and carry
no id: there is nothing to cite, and the prompt says in words that this research holds no
figures for these companies — the numeral rule would refuse a number attributed to one,
and nothing else would refuse a sentence.

Set on `industry_landscape` and on no other row, for the reason ADR 0118's carve-out is
set on one: a per-section property is cheap, and keeping it on one section is the
discipline. `tests/test_section_spine.py` asserts the count.

Revision ID: 0080
Revises: 0079
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

_KEY = "industry_landscape"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE section_definitions "
            "SET evidence_policy = evidence_policy || CAST('{\"names_peers\": true}' AS jsonb) "
            "WHERE key = :key AND origin = 'builtin'"
        ),
        {"key": _KEY},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE section_definitions "
            "SET evidence_policy = evidence_policy - 'names_peers' "
            "WHERE key = :key AND origin = 'builtin'"
        ),
        {"key": _KEY},
    )
