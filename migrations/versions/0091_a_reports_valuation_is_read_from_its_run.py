"""A report's valuation is read from its run, never copied onto it (ROADMAP §3.19 item 76).

``reports`` carried ``valuation_low``, ``valuation_base``, ``valuation_high`` and
``valuation_currency`` from revision 0006, with a check that the low did not exceed the high.
Nothing ever wrote them, so every surface that read them printed nothing: the reports list,
the company page, the planner's digest of prior research, the vault note, the API and the
comparison section's prior column. The shape was wrong as well as empty — a range with no
method attached, where a discounted cash flow's two terminal methods give two answers
(ADR 0132).

What a report's run gave is already recorded, one base-case row per method, with its formula,
its inputs and the code that produced it. ``aer.services.report_valuation`` reads those rows
back, so the four columns go rather than being filled: a copy would be a second source for one
number, and the first surface to read the copy while another read the row would print two.

The upgrade refuses if any report holds a figure in them. None was written by the platform,
so one found there was put there by hand, and a migration is not the place to discard it.

Revision ID: 0091
Revises: 0090
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None

_COLUMNS = ("valuation_low", "valuation_base", "valuation_high", "valuation_currency")

# Revision 0006 wrote each name with the convention's prefix already on it, and the
# convention added its own, so this is what every database built by this chain holds.
_CURRENCY_CHECK = "ck_reports_ck_reports_valuation_currency_iso4217"
_RANGE_CHECK = "ck_reports_ck_reports_valuation_range_runs_forwards"


def _refuse_if_a_report_holds_one(bind: sa.Connection) -> None:
    held = bind.execute(
        sa.text(
            "SELECT count(*) FROM reports WHERE valuation_low IS NOT NULL "
            "OR valuation_base IS NOT NULL OR valuation_high IS NOT NULL "
            "OR valuation_currency IS NOT NULL"
        )
    ).scalar_one()
    if held:
        message = (
            f"{held} report(s) hold a figure in reports.valuation_low, valuation_base, "
            "valuation_high or valuation_currency, which nothing in the platform writes, so "
            "somebody put it there by hand and this revision would delete it. Copy the "
            "figures somewhere if they matter, set the four columns to NULL, and migrate "
            "again."
        )
        raise RuntimeError(message)


def upgrade() -> None:
    _refuse_if_a_report_holds_one(op.get_bind())
    # The two checks go with their columns: Postgres drops a table constraint along with any
    # column it involves, whatever name a database gave it.
    for column in _COLUMNS:
        op.drop_column("reports", column)


def downgrade() -> None:
    op.add_column("reports", sa.Column("valuation_low", sa.Numeric(18, 4), nullable=True))
    op.add_column("reports", sa.Column("valuation_base", sa.Numeric(18, 4), nullable=True))
    op.add_column("reports", sa.Column("valuation_high", sa.Numeric(18, 4), nullable=True))
    op.add_column("reports", sa.Column("valuation_currency", sa.String(3), nullable=True))
    op.create_check_constraint(
        op.f(_CURRENCY_CHECK),
        "reports",
        "valuation_currency IS NULL OR char_length(valuation_currency) = 3",
    )
    op.create_check_constraint(
        op.f(_RANGE_CHECK),
        "reports",
        "valuation_low IS NULL OR valuation_high IS NULL OR valuation_low <= valuation_high",
    )
