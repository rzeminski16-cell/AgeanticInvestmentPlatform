"""A single reported financial fact, as the filer tagged it.

One row of XBRL: this concept, in this unit, for this period, as reported in this filing
on this date. A :class:`RawFact` is deliberately close to the source — it records what the
filing said, not what a later filing said it should have said — because that fidelity is
the entire basis of point-in-time reconstruction.

**Why ``filed_date`` matters more than it looks.** A company's 2020 annual report states
FY2020 revenue. Its 2022 annual report also states FY2020 revenue, possibly a different
number, because of a restatement, a discontinued operation or a reclassification. Both are
true statements about FY2020; they differ in *when they were made*. Analysis performed as
at a date in 2021 must use the first, because the second did not exist. Taking "the latest
value" instead is look-ahead bias, and it is the specific error that makes a backtest look
brilliant and a live portfolio look nothing like it.

**Why ``unit`` is carried and never dropped.** ``143015000000`` is meaningless. It is
either dollars, shares, or dollars per share, and the difference between the first and the
third is eleven orders of magnitude. Units travel with the number through every operation
in :mod:`aer.calc`, and a mismatch raises rather than coercing.

Pure declarations. No I/O, no clock, no database.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aer.core.concepts import canonical_concept

__all__ = ["ACCESSION_PATTERN", "RawFact", "format_accession"]

# EDGAR accession numbers are ``##########-##-######``: a ten-digit filer id, a two-digit
# year, and a six-digit sequence. The dashed form is what appears in the API and in a
# citation; the undashed form is what appears in an archive URL path.
ACCESSION_PATTERN: Final = re.compile(r"\A\d{10}-\d{2}-\d{6}\Z")

# The units EDGAR reports that this platform understands. Others are kept verbatim rather
# than refused -- a filer reporting in EUR or in barrels is reporting something real -- but
# these are the ones downstream arithmetic knows how to reason about.
KNOWN_UNITS: Final[frozenset[str]] = frozenset(
    {"USD", "GBP", "EUR", "CAD", "JPY", "CHF", "AUD", "shares", "pure", "USD/shares"}
)


def format_accession(value: str) -> str:
    """Normalise an accession number to its dashed form.

    Accepts either form because the API and the archive URLs disagree about it, and a
    caller holding one should not have to know which they have.
    """
    digits = value.replace("-", "").strip()
    accession_length = 18
    if len(digits) != accession_length or not digits.isdigit():
        message = f"{value!r} is not an EDGAR accession number."
        raise ValueError(message)
    return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"


class RawFact(BaseModel):
    """One tagged value from one filing.

    Frozen so a fact can go in a set or a dictionary key: deduplicating facts across
    overlapping filings is a set operation, and it is much harder to get wrong when the
    elements cannot be edited underneath it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The canonical concept, when the tag is one this platform recognises; otherwise the
    # raw tag verbatim. ``is_canonical`` says which, so a downstream consumer can require
    # a mapped concept without having to consult the alias table itself.
    concept: str = Field(min_length=1)
    raw_concept: str = Field(min_length=1)
    taxonomy: str = Field(min_length=1)

    unit: str = Field(min_length=1)
    value: Decimal

    # NULL for an instant fact. A balance sheet line has a date, not a period: "cash at 30
    # June" is a fact about a moment, and giving it a made-up start would make it look like
    # a flow. Income and cash-flow lines have both.
    period_start: date | None = None
    period_end: date

    fiscal_year: int | None = None
    fiscal_period: str | None = None

    # NULL for the consolidated figure. Set, this is one axis of an XBRL dimensional
    # breakdown — "revenue, Americas segment" rather than "revenue" — and the two must
    # never compete for a period's number: a dimensioned fact is excluded from statement
    # assembly and default selection, and exists to feed segment-level analysis.
    dimension_axis: str | None = None
    dimension_member: str | None = None

    form: str = Field(min_length=1)
    accession: str
    filed_date: date

    # EDGAR's calendar-aligned bucket, e.g. "CY2020Q2I". Present only on facts EDGAR could
    # align; kept because it is what the frames API joins on, and absent is informative.
    frame: str | None = None

    @field_validator("accession")
    @classmethod
    def _accession_identifies_a_filing(cls, value: str) -> str:
        """The filing this fact came from, in whatever form its publisher issues.

        **Not EDGAR-specific, deliberately.** An accession number is one publisher's way of
        naming a filing; Companies House issues base64-ish transaction IDs and the FCA issues
        something else again. A shared fact type that demanded eighteen digits would make every
        UK fact unrepresentable, which is a schema asserting a fact about the SEC rather than
        about facts.

        EDGAR's own shape is still enforced — in :mod:`aer.sources.sec.companyfacts` and
        :mod:`aer.sources.sec.submissions`, where EDGAR facts are built and where a malformed
        accession is already treated as an unusable row. An EDGAR-shaped value is normalised
        here too, so a caller passing the undashed form still gets the dashed one.
        """
        cleaned = value.strip()
        if not cleaned:
            message = "A fact must name the filing it came from."
            raise ValueError(message)
        try:
            return format_accession(cleaned)
        except ValueError:
            return cleaned

    @field_validator("taxonomy", "form", "unit")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _period_runs_forwards(self) -> RawFact:
        if self.period_start is not None and self.period_start > self.period_end:
            message = (
                f"period_start {self.period_start} is after period_end {self.period_end}. "
                "A period that runs backwards is a parsing error, not a fact."
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _dimension_names_both_halves(self) -> RawFact:
        if (self.dimension_axis is None) != (self.dimension_member is None):
            message = (
                "A dimensioned fact names both the axis and the member. An axis with no "
                "member says a breakdown exists without saying of what, and a member with "
                "no axis is a label adrift from what it labels."
            )
            raise ValueError(message)
        return self

    @property
    def is_canonical(self) -> bool:
        """Whether ``concept`` is a canonical name rather than an unmapped filer tag."""
        return canonical_concept(self.taxonomy, self.raw_concept) is not None

    @property
    def is_instant(self) -> bool:
        """Whether this is a point-in-time measure rather than a flow over a period."""
        return self.period_start is None

    @property
    def period_key(self) -> tuple[str, str, date, str | None, str | None, str | None]:
        """What makes two facts statements about *the same thing*.

        Concept, unit, period end, fiscal period — and the dimension, when there is one.
        Two facts sharing this key are rival accounts of one number, and choosing between
        them is what point-in-time selection does. ``unit`` is in the key because a value
        in dollars and a value in shares are not rival accounts of anything; the dimension
        is in it because two segments' revenue are two numbers, and a selection that
        treated them as rivals would keep one segment and silently drop the rest.
        """
        return (
            self.concept,
            self.unit,
            self.period_end,
            self.fiscal_period,
            self.dimension_axis,
            self.dimension_member,
        )

    def __str__(self) -> str:
        return (
            f"{self.concept}={self.value} {self.unit} "
            f"[{self.period_end} filed {self.filed_date} {self.form}]"
        )
