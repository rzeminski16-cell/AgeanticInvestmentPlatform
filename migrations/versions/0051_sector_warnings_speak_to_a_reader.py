"""Sector warnings speak to a reader, not about the build that wrote them.

Report-quality R1 to R6, the register work, applied to the two warnings that reach a
reader through the report's sector block. Both said a valuation "is not implemented in
this build" — a sentence about the platform's own construction, in a document that should
be entirely about a company. What a reader needs is the same fact without the machinery:
the right model for this company is X, and this note does not offer one.

The rendered warnings come from :data:`aer.core.sectors.SECTOR_PROFILES`, so the code
constants are what a report actually shows; these rows are the parallel record the Phase 3
gate queries, and ``test_the_seed_matches_the_constants`` is what keeps the two in step.
This migration is that step.

Revision ID: 0051
Revises: 0050
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


# The two profiles whose warnings named the build, with the wording before and after. Keyed
# so the downgrade is the same operation with the pairs reversed.
_BIOTECH_OLD = (
    "Risk-adjusted NPV over the pipeline is the right model and is not implemented in this build."
)
_BIOTECH_NEW = (
    "Risk-adjusted NPV over the pipeline is the right model for this company, and "
    "no such valuation is offered here."
)
_HOLDCO_OLD = (
    "A consolidated model of a holding company values an accounting artefact. Sum "
    "of the parts is the right approach and is not implemented in this build."
)
_HOLDCO_NEW = (
    "A consolidated model of a holding company values an accounting artefact. Sum "
    "of the parts is the right approach, and no such valuation is offered here."
)


def _rewrite(pairs: list[tuple[str, str, str]]) -> None:
    """Replace one warning in one profile's ``warnings`` array, leaving the rest alone.

    Read-modify-write rather than a whole-array overwrite: the array's other entries are
    not this migration's business, and rewriting them wholesale would silently revert any
    other change made to them.
    """
    connection = op.get_bind()
    for key, before, after in pairs:
        row = connection.execute(
            sa.text("SELECT warnings FROM sector_profiles WHERE key = :key"), {"key": key}
        ).first()
        if row is None:
            continue
        warnings = list(row[0] or [])
        if before not in warnings:
            continue
        warnings[warnings.index(before)] = after
        connection.execute(
            sa.text("UPDATE sector_profiles SET warnings = :warnings WHERE key = :key"),
            {"key": key, "warnings": json.dumps(warnings)},
        )


def upgrade() -> None:
    _rewrite(
        [
            ("biotech_pre_revenue", _BIOTECH_OLD, _BIOTECH_NEW),
            ("holding_companies", _HOLDCO_OLD, _HOLDCO_NEW),
        ]
    )


def downgrade() -> None:
    _rewrite(
        [
            ("biotech_pre_revenue", _BIOTECH_NEW, _BIOTECH_OLD),
            ("holding_companies", _HOLDCO_NEW, _HOLDCO_OLD),
        ]
    )
