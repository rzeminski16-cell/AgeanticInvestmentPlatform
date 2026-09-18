"""A SIC code travels with the scheme that issued it, and the profiles learn the UK one.

US SIC and UK SIC 2007 are different classifications that assign the same digits to
different industries: `631` is fire, marine and casualty insurance in one and data
processing and web portals in the other. A code stored without its scheme is a code that can
classify a company as the wrong kind of business — and a sector profile carrying only US
prefixes matches *nothing* for a UK filer, so the gate does not fire and a bank takes the
standard model. That is the hole ADR 0029 exists to forbid and the one that produced M&T's
172.1% net margin, and shipping the UK path without this would reproduce it for every UK
bank, insurer and REIT.

Two columns and a seed.

`companies.sic_scheme` defaults to the US scheme and every existing row takes it, which is
true rather than convenient: every company on the register today was resolved against EDGAR.

`sector_profiles.uk_sic_prefixes` is seeded from the **Companies House condensed SIC list**
(`resources.companieshouse.gov.uk/sic/`), read on 18 September 2026 rather than recalled.
The codes are written out in the code constant with a comment per profile saying which
classes they cover and, where the UK scheme draws a line the US one does not, which side
this platform takes: biotechnology research (72110) without pharmaceutical manufacture,
property ownership (68100, 68201-68209, 64306) without letting agency.

Revision ID: 0081
Revises: 0080
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

_SCHEME = sa.Enum("us_sic", "uk_sic_2007", name="sic_scheme")

# Written out rather than imported from `aer.core.sectors`, because a migration records what
# the table held on the day it ran: importing the constant would make this file's meaning
# change every time somebody edits the constant, and the test that holds the two together
# would then be comparing a thing with itself.
_UK_PREFIXES = {
    "banks": ["6419"],
    "insurers": ["651", "652"],
    "reits": ["64306", "6810", "6820"],
    "utilities": ["351", "352", "353", "36", "37"],
    "biotech_pre_revenue": ["7211"],
    "mining_energy": ["05", "06", "07", "08", "09"],
    "early_stage_tech": ["582", "62", "631"],
    "holding_companies": ["642"],
}


def upgrade() -> None:
    bind = op.get_bind()
    _SCHEME.create(bind, checkfirst=True)
    op.add_column(
        "companies",
        sa.Column("sic_scheme", _SCHEME, nullable=False, server_default="us_sic"),
    )
    op.add_column(
        "sector_profiles",
        sa.Column(
            "uk_sic_prefixes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    for key, prefixes in _UK_PREFIXES.items():
        bind.execute(
            sa.text(
                "UPDATE sector_profiles SET uk_sic_prefixes = CAST(:prefixes AS jsonb) "
                "WHERE key = :key"
            ),
            {"key": key, "prefixes": json.dumps(prefixes)},
        )
    # The default existed to fill the rows already there; keeping it would make the column
    # differ from `sic_prefixes` beside it, which has none, and from the model, which states
    # the empty list in Python. A row inserted without prefixes should fail rather than
    # quietly mean "matches nothing in the UK".
    op.alter_column("sector_profiles", "uk_sic_prefixes", server_default=None)


def downgrade() -> None:
    op.drop_column("sector_profiles", "uk_sic_prefixes")
    op.drop_column("companies", "sic_scheme")
    _SCHEME.drop(op.get_bind(), checkfirst=True)
