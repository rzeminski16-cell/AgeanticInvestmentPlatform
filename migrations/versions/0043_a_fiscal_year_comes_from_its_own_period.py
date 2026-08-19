"""A fiscal year comes from its own period.

ADR 0062. EDGAR's ``fy`` names the fiscal frame of the *filing* an observation appeared
in, so every comparative row was stored labelled with the later report's year — the first
complete run presented a company's actual FY2021 ratios as FY2022, to the decimal. The
parser now derives a fiscal-year row's year from its own period end; this migration
applies the same rule to the rows already stored.

Only ``fiscal_period = 'FY'`` rows are touched, deliberately: an interim row's fiscal year
depends on the company's fiscal calendar, which neither this statement nor the parser
holds, and the ADR records that boundary rather than guessing across it.

Idempotent by construction — the new value is a pure function of ``period_end``, which the
statement does not change — and the first data migration in this repository, so the rule
is spelt out here as well as in ``aer.core.dates.fiscal_year_of``: the calendar year the
period ends in, except that an end in the first seven days of January belongs to the prior
year (the 52/53-week calendar landing just past 31 December).

No ``downgrade`` restoration: the old values were wrong, and the filings' own ``fy`` is
still in the hashed artefacts for anyone who needs the record of what the source said.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_RECOMPUTE = sa.text(
    "UPDATE financial_facts SET fiscal_year = CASE "
    "  WHEN EXTRACT(MONTH FROM period_end) = 1 AND EXTRACT(DAY FROM period_end) <= 7 "
    "    THEN EXTRACT(YEAR FROM period_end)::int - 1 "
    "  ELSE EXTRACT(YEAR FROM period_end)::int "
    "END "
    "WHERE fiscal_period = 'FY'"
)


def upgrade() -> None:
    op.get_bind().execute(_RECOMPUTE)


def downgrade() -> None:
    # Nothing to restore: the old labels were the defect, and the source values survive in
    # the artefacts. Reversing the schema chain past this point leaves the corrected data.
    pass
