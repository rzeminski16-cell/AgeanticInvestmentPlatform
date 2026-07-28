"""Loading the SEC fixtures, and building facts by hand.

Two kinds of input here. :func:`fixture_bytes` reads a recorded-shape JSON payload for the
parser tests; :func:`make_fact` builds a :class:`~aer.core.schemas.facts.RawFact` directly
for the point-in-time tests.

The second exists because point-in-time selection is about *relationships between filing
dates*, and expressing "these two facts describe the same period and were filed two years
apart" through a JSON fixture buries the one thing the test is about under forty lines of
structure. The parsers are tested against real shapes; the selector is tested against
facts stated plainly.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from aer.core.schemas.facts import RawFact

FIXTURE_ROOT: Final = Path(__file__).parent / "fixtures" / "sec"

# Microsoft's real CIK and its zero-padded form. Used as a literal in the tests because a
# constant that is only ever compared against itself proves nothing.
MSFT_CIK: Final = "0000789019"


def fixture_bytes(name: str) -> bytes:
    """Read a fixture exactly as a fetch would deliver it."""
    return (FIXTURE_ROOT / name).read_bytes()


DERIVE_START: Final = "..."
"""Sentinel: make the period a year ending at ``period_end``.

A fixed default start date would contradict every test that overrides the end date, and
the resulting "period runs backwards" error says nothing about what the test was checking.
``None`` cannot serve as the sentinel because ``None`` is a meaningful value here — it is
what makes a fact an instant measure.
"""


def make_fact(
    *,
    concept: str = "revenue",
    value: str | int = 100,
    filed: str = "2021-01-01",
    period_end: str = "2020-06-30",
    period_start: str | None = DERIVE_START,
    unit: str = "USD",
    accession: str = "0000789019-20-000039",
    form: str = "10-K",
    fiscal_period: str | None = "FY",
    fiscal_year: int | None = 2020,
    raw_concept: str = "Revenues",
    taxonomy: str = "us-gaap",
) -> RawFact:
    """A fact with sensible defaults, so a test only states what it is about."""
    ends = date.fromisoformat(period_end)
    if period_start is DERIVE_START:
        starts: date | None = ends.replace(year=ends.year - 1) + timedelta(days=1)
    else:
        starts = date.fromisoformat(period_start) if period_start else None

    return RawFact(
        concept=concept,
        raw_concept=raw_concept,
        taxonomy=taxonomy,
        unit=unit,
        value=Decimal(str(value)),
        period_start=starts,
        period_end=ends,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        form=form,
        accession=accession,
        filed_date=date.fromisoformat(filed),
    )
