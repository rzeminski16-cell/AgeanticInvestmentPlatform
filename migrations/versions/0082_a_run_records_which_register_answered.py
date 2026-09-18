"""A run records which register answered: `research_requests.register` (ADR 0121).

The venue decides which register `acquire` asks — Companies House for a London listing, the
SEC for a US one — and that decision is recorded on the request beside `company_id` rather
than recomputed later. A replay two years from now should read what actually answered this
run, not what the code of the day would choose; and if a venue is ever reclassified, the
difference between those two answers is exactly what a reader needs to see.

NULL until the run resolves, on the same terms as `company_id`: before `acquire`, nothing
has been asked of anybody, and a default would assert an answer nobody gave. Existing rows
keep NULL for the same reason — the three stored subjects were resolved against EDGAR, but
this column is a record of the step's decision and that step did not make one.

No new type: `provider` already exists, and every source document in the database is already
labelled with it.

Revision ID: 0082
Revises: 0081
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

_PROVIDERS = (
    "sec_edgar",
    "companies_house",
    "fca_nsm",
    "eodhd",
    "fred",
    "ons",
    "ecb",
    "issuer_ir",
    "web_search",
    "user_supplied",
    "internal_prior_run",
)


def upgrade() -> None:
    op.add_column(
        "research_requests",
        sa.Column(
            "register",
            # `create_type=False`: the type is the one the source documents already use, and
            # a column definition that tried to create it would fail on every database that
            # has ever recorded a fetch.
            postgresql.ENUM(*_PROVIDERS, name="provider", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("research_requests", "register")
