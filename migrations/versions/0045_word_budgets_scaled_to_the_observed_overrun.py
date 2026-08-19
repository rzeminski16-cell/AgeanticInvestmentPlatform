"""Word budgets scaled to the observed overrun.

Polish P7. Seven sections of the first complete run were refused for length at ratios
1.38 to 1.63 — mean 1.46x — against a ceiling factor of 1.25. A model missing a target by
a consistent multiplier is not being careless; it is working to a different scale than
the one it was given. Two explanations fit, and they are distinguishable by one
experiment: multiply every built-in ``word_budget`` by 1.45 and change nothing else. If
the overruns vanish, the budgets were below what these schemas can be written in; if they
rescale — the model again writing ~1.46x the new number — the prompt's ``target_words``
does not bind, and the fix is a hard constraint instead of larger numbers. The next
complete run answers it, and the answer should not be guessed at.

One statement over every built-in row carrying a budget, rather than 0034's key list, so
both versions of a revised definition (0044 copied the budget onto ``valuation_dcf`` v2)
and any later-seeded section scale identically. The deterministic sections scale too —
their budgets are inert, and a uniform experiment is easier to read than a carve-out.

The downgrade divides by the same factor. Round-tripping is exact for every seeded value
(they are multiples of 50, and ROUND undoes ROUND at this factor), which was checked by
enumeration rather than assumed.

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

# The observed mean overrun was 1.46x; the applied factor is stated once, and bound into
# the statements rather than interpolated.
_FACTOR = "1.45"

_SCALE = sa.text(
    "UPDATE section_definitions "
    "SET evidence_policy = jsonb_set(evidence_policy, '{word_budget}', "
    "    to_jsonb(ROUND((evidence_policy->>'word_budget')::numeric "
    "             * CAST(:factor AS numeric))::int)) "
    "WHERE origin = 'builtin' AND evidence_policy ? 'word_budget'"
)

_UNSCALE = sa.text(
    "UPDATE section_definitions "
    "SET evidence_policy = jsonb_set(evidence_policy, '{word_budget}', "
    "    to_jsonb(ROUND((evidence_policy->>'word_budget')::numeric "
    "             / CAST(:factor AS numeric))::int)) "
    "WHERE origin = 'builtin' AND evidence_policy ? 'word_budget'"
)


def upgrade() -> None:
    op.get_bind().execute(_SCALE, {"factor": _FACTOR})


def downgrade() -> None:
    op.get_bind().execute(_UNSCALE, {"factor": _FACTOR})
