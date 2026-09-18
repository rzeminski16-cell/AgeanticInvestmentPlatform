"""The section about segments may read segments (ADR 0118).

ADR 0058 excluded dimensioned facts from every reader of `visible_facts`, and was right
about five of them: each assumes one value per concept-period, and a segment winning a
period from the aggregate makes every ratio downstream divide a fraction by the whole. It
was wrong about the sixth. Segment Analysis — the section whose entire subject *is* the
breakdown — was handed an evidence pack with every segment row removed and then wrote,
truthfully, that no segment-level figures were available to cite, on every run this
platform has ever made, while the store held 626 dimensioned facts across the three
audited subjects.

The carve-out is a property of the section, so it is set on the section's row rather than
keyed to a section name in code. Exactly one row gets it, and `tests/test_section_spine.py`
is what keeps that true — a second section opting in is a decision with its own evidence,
not a door this migration leaves open.

`evidence_policy` is merged into rather than replaced, as migration 0041 does: the row
already carries this section's concept preferences, its excerpt keywords and its writer
route, and none of them are this change's business.

Revision ID: 0079
Revises: 0078
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

# The one section, and the value `aer.services.facts.Dimensions` reads. A row naming
# anything else falls back to the exclusion in `policy_of_definition`, so a typo here costs
# the carve-out rather than letting an unbounded breakdown into a section.
_KEY = "segment_analysis"
_INCLUDE_SINGLE_AXIS = "single_axis"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE section_definitions "
            "SET evidence_policy = evidence_policy || CAST(:carve_out AS jsonb) "
            "WHERE key = :key AND origin = 'builtin'"
        ),
        {"key": _KEY, "carve_out": f'{{"dimensions": "{_INCLUDE_SINGLE_AXIS}"}}'},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE section_definitions "
            "SET evidence_policy = evidence_policy - 'dimensions' "
            "WHERE key = :key AND origin = 'builtin'"
        ),
        {"key": _KEY},
    )
